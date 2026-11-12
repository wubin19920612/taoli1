"""Public observations are incrementally persisted without losing invalidations."""
import unittest
from tools.crossex_exec.storage import Store
from tools.crossex_exec.baseline import Baseline
from test_exec import setUpModule, tearDownModule


class IncrementalTests(unittest.TestCase):
    def test_identical_public_round_is_noop_and_removed_quotes_are_revoked(self):
        with Store(':memory:') as store:
            for key,value,smaller in (
                ('baseline_quotes',dict(at=100,mids={'binance':{'BTC':'100'},'bitget':{'BTC':'101'}}),
                 dict(at=101,mids={'binance':{'BTC':'100'}})),
                ('market_tickers',{'binance|future|BTCUSDT':dict(at=100,mid='100',volume='1000000'),
                                   'bitget|future|BTCUSDT':dict(at=100,mid='101',volume='1000000')},
                 {'binance|future|BTCUSDT':dict(at=101,mid='100',volume='1000000')})):
                with self.subTest(key=key):
                    store.set_meta(key,value)
                    changes=store.db.total_changes
                    store.set_meta(key,value)
                    self.assertEqual(store.db.total_changes,changes)
                    store.set_meta(key,smaller)
                    current=store.meta(key)
                    self.assertNotIn('bitget',current.get('mids',{}))
                    self.assertNotIn('bitget|future|BTCUSDT',current)
            self.assertEqual(store.db.execute("SELECT count(*) FROM meta WHERE key IN ('market_tickers','baseline_quotes')").fetchone()[0],0)

    def test_identical_baseline_state_writes_nothing_but_changed_age_revokes_ready(self):
        with Store(':memory:') as store:
            state=dict(key='BTC|binance|bitget',baseline_bps='0',mad_bps='0',count=400,
                       first_at=1,latest_at=12000,ready=True,blocked=False,checked_at=12000,at=12000)
            store.save_baseline_state(state)
            changes=store.db.total_changes
            store.save_baseline_state(state)
            self.assertEqual(store.db.total_changes,changes)
            store.save_baseline_state(dict(state,ready=False,at=12091))
            self.assertFalse(store.baseline_state(state['key'])['ready'])
