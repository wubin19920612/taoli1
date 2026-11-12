"""Production catalog + minimal public GET rows; no production DB or network."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tools.crossex_exec.background import PeriodicWorker
from tools.crossex_exec.baseline import Baseline, pair_key
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.market import MarketData
from tools.crossex_exec.policy import Instrument
from tools.crossex_exec.storage import Store
from tools.crossex_exec.api_data import JournalView
from test_exec import setUpModule, tearDownModule


class BaselineRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'exec.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.fixture = json.loads((Path(__file__).parent/'fixtures/round10_public_baseline.json').read_text())
        self.now = self.fixture['captured_at']
        self.store.set_meta('mapping', dict(instruments=self.fixture['instruments']))
        self.market = MarketData(self.store, clock=lambda: self.now)
        self.addCleanup(self.market.close)
        self.market.mapping = {k: Instrument.load(v) for k, v in self.fixture['instruments'].items()}
        self.market.batch = lambda items, **kw: [self.fixture['responses'].get(url) for _, url, _ in items]
        self.accounts = Mock()
        self.engine = Engine(self.store, self.market, self.accounts, clock=lambda: self.now,
                             stop_file=Path(self.tmp.name)/'STOP')
        self.addCleanup(self.engine.close)

    def test_public_snapshot_filters_unsupported_base_before_sampling(self):
        mids, bases = self.market.sample_quotes(200000)
        self.assertEqual(bases, ('BTC',))
        self.assertEqual(set(mids), {'binance', 'bitget'})
        self.assertEqual(set(mids['binance']), {'BTC'})
        # The public catalog retains exact native identities; they are not renamed.
        self.assertIn('bitget|future|\u725b\u6765USDT', self.store.meta('market_tickers'))

    def test_refresh_keeps_valid_pairs_and_original_warmup_requirements(self):
        self.engine.refresh_baselines()
        key = pair_key('BTC', 'binance', 'bitget')
        state = self.store.baseline_state(key)
        self.assertEqual(state['count'], 1)
        self.assertFalse(state['ready'])
        self.assertEqual(JournalView(self.path, {}, clock=lambda: self.now).get('summary', {})['baseline_ready_pairs'], 0)
        rows = [(key, int((self.now-10800+n*30)//30), self.now-10800+n*30, '0') for n in range(360)]
        self.store.add_baseline_samples(rows, self.now)
        self.engine.refresh_baselines()
        self.assertTrue(self.store.baseline_state(key)['ready'])
        self.assertEqual(JournalView(self.path, {}, clock=lambda: self.now).get('summary', {})['baseline_ready_pairs'], 1)
        self.assertEqual(self.accounts.mock_calls, [])

    def test_record_isolates_bad_base_even_without_quotes_and_revokes_old_quotes(self):
        baseline = Baseline(self.store, clock=lambda: self.now)
        for invalid in ('\u725b\u6765', 'btc', 'BTC|gate', 'io:OAI', '', None, 123):
            with self.subTest(base=invalid):
                with self.assertRaises(ValueError):
                    pair_key(invalid, 'binance', 'bitget')
                baseline.record({'binance': {'BTC': '100', invalid: '1'},
                                 'bitget': {'BTC': '100', invalid: '1'}}, ('BTC', invalid))
                self.assertEqual(set(baseline.latest('BTC')['mids']), {'binance', 'bitget'})
                baseline.record({}, ('BTC', invalid))
                self.assertEqual(baseline.latest('BTC')['mids'], {})
        self.assertFalse(any(not row['key'].isascii() for row in self.store.meta('baseline_status').values()))

    def test_failed_market_round_still_revokes_references_with_unsupported_bases(self):
        self.engine.refresh_baselines()
        self.engine.bases = ('BTC', '\u725b\u6765')
        with patch.object(self.market, 'sample_quotes', side_effect=TypeError('private-response')):
            with self.assertRaises(TypeError):
                self.engine.refresh_baselines()
        self.assertEqual(self.engine.baseline.latest('BTC')['mids'], {})

    def test_background_location_type_and_throttle_without_exception_text(self):
        def first():
            raise ValueError('private-response-one')
        def second():
            raise ValueError('private-response-two')
        callbacks = iter((first, first, second))
        def callback():
            next(callbacks)()
        with patch('tools.crossex_exec.background.threading.Thread.start'):
            worker = PeriodicWorker(callback, 30, 'exec-baseline', self.engine.background_error)
        worker.stopped = Mock()
        worker.stopped.is_set.side_effect = [False, False, False, True]
        with self.assertLogs('tools.crossex_exec.background', level='ERROR') as logs:
            worker.run()
        events = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'BACKGROUND_FAILED']
        self.assertEqual(len(events), 2)
        self.assertEqual({e['error_type'] for e in events}, {'ValueError'})
        self.assertEqual({e['location'] for e in events},
                         {__name__+':'+str(fn.__code__.co_firstlineno+1) for fn in (first, second)})
        for event in events:
            self.assertEqual(set(event), {'worker', 'error_type', 'location'})
            self.assertTrue(any(event['location'] in row for row in logs.output))
        self.assertNotIn('private-response', json.dumps(events)+str(logs.output))
        self.assertNotIn(self.tmp.name, str(logs.output))
