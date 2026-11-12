"""Runtime setting changes affect new admission, with frozen position settings."""
import json
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from test_exec import setUpModule, tearDownModule
import test_round6 as previous


class RuntimeTests(unittest.TestCase):
    setUp = previous.EngineBaselineTests.setUp
    trigger = previous.EngineBaselineTests.trigger
    snapshot = previous.EngineBaselineTests.snapshot
    pair = previous.EngineBaselineTests.pair

    def skipped(self, name):
        return [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'SKIP' and json.loads(e['value'])['reason'] == name]

    def test_runtime_volume_threshold_and_missing_oi_before_any_order(self):
        from tools.crossex_exec.settings import Settings
        settings = Settings(self.store)
        settings.update({'min_quote_volume_usd': 6000000}, source='admin_api')
        self.assertIsNone(self.engine.ingest(self.trigger('low')))
        self.assertTrue(self.skipped('volume_too_low'))
        settings.update({'min_quote_volume_usd': 1000000}, source='admin_api')
        with patch.object(self.market, 'open_interests', return_value=[None, None]):
            self.assertIsNone(self.engine.ingest(self.trigger('oi')))
        self.assertTrue(self.skipped('oi_missing'))
        self.assertFalse(self.store.all_orders())
        pair = self.engine.ingest(self.trigger('yes'))
        self.assertIsNotNone(pair)
        self.assertEqual(pair['config_at_open']['version'], 2)
        self.assertLessEqual(max(dec(o['quantity'])*dec(o['price']) for o in self.store.all_orders()), 50)

    def test_position_keeps_settings_and_size_details_after_update(self):
        from tools.crossex_exec.settings import Settings
        pair = self.engine.ingest(self.trigger())
        self.assertIsNotNone(pair)
        frozen = pair['config_at_open']
        self.assertEqual(frozen['values']['order_usd_max'], 50)
        self.assertEqual(pair['sizing']['limiting'], 'order_usd_max')
        Settings(self.store).update({'order_usd_max': 5, 'max_open_pairs': 0, 'max_total_open_usd': 0}, source='admin_api')
        saved = self.store.pairs(active=True)[0]
        self.assertEqual(saved['config_at_open'], frozen)
        self.engine.close_pair(saved, 'manual_stop_test')
        self.assertEqual(saved['state'], 'CLOSED')

    def test_version_change_before_shared_send_blocks_both_legs(self):
        from tools.crossex_exec.settings import Settings
        original = self.engine.executor.execute_pair
        def change(intents):
            Settings(self.store).update({'order_usd_max': 5}, source='admin_api')
            return original(intents)
        with patch.object(self.engine.executor, 'execute_pair', side_effect=change):
            self.engine.ingest(self.trigger())
        self.assertFalse(any(row[0] == 'place' for row in self.trace))

    def test_empty_compatibility_bases_is_unrestricted_and_manual_exclusion_blocks(self):
        from tools.crossex_exec.universe import Universe
        self.engine.bases = ()
        Universe(self.store).exclude('BTC', True)
        self.assertIsNone(self.engine.ingest(self.trigger('excluded')))
        Universe(self.store).exclude('BTC', False)
        self.assertIsNotNone(self.engine.ingest(self.trigger('allowed')))
