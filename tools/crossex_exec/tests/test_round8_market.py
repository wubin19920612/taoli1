"""Dynamic sampling and five-minute volume cache share the public scheduler."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.crossex_exec.market import MarketData
from tools.crossex_exec.storage import Store
from test_exec import setUpModule, tearDownModule
from test_round8_universe import native


class BatchMarketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'exec.db')
        self.addCleanup(self.store.close)
        self.now = 1000
        self.market = MarketData(self.store, clock=lambda: self.now)
        self.addCleanup(self.market.close)
        self.rows = [native(ex, base) for ex in ('binance', 'bitget') for base in ('BTC', 'ETH')]
        self.market.publish_mapping({i.market.key: i for i in self.rows}, {})

    def batch(self, items, priority=1, partial=False):
        result = []
        for ex, url, convert in items:
            if ex == 'binance':
                data = ([dict(symbol=base+'USDT', bidPrice='99', askPrice='101', time=self.now*1000) for base in ('BTC', 'ETH')]
                        if 'bookTicker' in url else
                        [dict(symbol=base+'USDT', quoteVolume=volume, closeTime=self.now*1000) for base, volume in [('BTC', '250000'), ('ETH', '199999')]])
            elif ex == 'bitget':
                data = {'code': '00000', 'data': [dict(symbol=base+'USDT', bidPr='99', askPr='101', ts=str(self.now*1000), usdtVolume='100000') for base in ('BTC', 'ETH')]}
            else:
                result.append(None)
                continue
            result.append(convert(data) if convert else data)
        return result

    def test_sampling_is_any_venue_threshold_and_keeps_other_exact_venues(self):
        with patch.object(self.market, 'batch', side_effect=self.batch) as calls:
            mids, bases = self.market.sample_quotes(200000)
        self.assertEqual(set(bases), {'BTC'})
        self.assertEqual(set(mids), {'binance', 'bitget'})
        self.assertEqual(set(mids['bitget']), {'BTC'})
        self.assertLessEqual(sum(len(call.args[0]) for call in calls.call_args_list), 8)
        self.assertTrue(all(call.kwargs['priority'] == 2 for call in calls.call_args_list))

    def test_volume_cache_is_exact_and_stale_refresh_failure_is_unknown(self):
        with patch.object(self.market, 'batch', side_effect=self.batch):
            self.market.sample_quotes(200000)
        instruments = [self.rows[0], self.rows[2]]
        with patch.object(self.market, 'batch', side_effect=ValueError('offline')) as calls:
            self.now = 1300
            self.assertEqual(self.market.turnovers(instruments), ['250000', '100000'])
            calls.assert_not_called()
            self.now = 1300.001
            with self.assertRaisesRegex(ValueError, '^volume_unknown$'):
                self.market.turnovers(instruments)

    def test_empty_bases_bootstrap_discovers_catalog_instead_of_disabling(self):
        with patch.object(self.market, 'fetch', side_effect=ValueError('fixture unavailable')) as fetch:
            self.market.bootstrap(())
        self.assertGreater(fetch.call_count, 0)
