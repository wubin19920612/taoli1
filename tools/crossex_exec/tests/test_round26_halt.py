"""Round 26: final flatten evidence and admin release share program ownership."""
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.transport import APIError
from tools.crossex_exec.ownership import journal_positions
from round26_fixtures import world, halt, release
from test_exec import setUpModule, tearDownModule


class HaltRecoveryTests(unittest.TestCase):
    def test_last_snapshot_timeout_recovers_on_tick_and_halted_can_be_released(self):
        w = world(self)
        original = w.accounts.position_snapshot
        failures = []

        def snapshot():
            if not w.store.pairs(active=True) and not failures:
                failures.append('final_snapshot')
                raise APIError('timeout')
            return original()

        with patch.object(w.accounts, 'position_snapshot', side_effect=snapshot):
            halt(w)
            self.assertEqual(failures, ['final_snapshot'])
            self.assertIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
            self.assertEqual(w.store.meta('state'), 'HALTING')
            self.assertFalse(w.engine.can_open())
            w.now += 3
            w.engine.tick()
        self.assertEqual(w.store.meta('state'), 'HALTED')
        self.assertNotIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
        self.assertNotIn('flatten_snapshot_unavailable', w.store.meta('stop_locks'))
        self.assertTrue(w.store.meta('flatten_status')['program_flat'])
        self.assertFalse(w.engine.can_open())  # an automatic proof never releases a halt
        release(w)
        w.advance()
        self.assertTrue(w.engine.can_open())

    def test_mixed_residual_is_external_to_flatten_and_admin_but_still_blocks_its_contract(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = world(self)
                entry = next(r for r in w.store.iter_orders(reduce_only=False) if r['leg'] == leg)
                w.venues[leg].quantity += dec('.4')
                w.advance()
                halt(w)
                status = w.store.meta('flatten_status')
                self.assertEqual(w.store.meta('state'), 'HALTED', status)
                self.assertTrue(status['verified'])
                self.assertTrue(status['program_flat'])
                self.assertFalse(status['account_flat'])
                self.assertEqual(journal_positions(w.store), {})
                key = w.engine.position_key(w.store.get_pair(entry['pair_id']), leg)
                self.assertEqual(dec(status['external_positions'][key]), dec('.4'))
                self.assertEqual(w.venues[leg].quantity, dec('.4'))
                release(w)
                w.advance()
                self.assertTrue(w.engine.can_open(), w.store.meta('stop_locks'))
                with self.assertRaisesRegex(ValueError, 'external_contract_forbidden'):
                    w.engine.external.check_order(entry)
                self.assertEqual(w.venues[leg].quantity, dec('.4'))

    def test_closed_pair_label_cannot_hide_confirmed_local_inventory(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        w.store.pair(dict(pair, state='CLOSED', remaining=['0','0'], remote_remaining=['0','0']))
        with patch.object(w.accounts, 'position_snapshot', return_value=dict(
                positions={}, captured_at=w.now)):
            halt(w)
        self.assertEqual(w.store.meta('state'), 'HALTING')
        self.assertFalse(w.store.meta('flatten_status')['program_flat'])
        self.assertTrue(journal_positions(w.store))
        self.assertFalse(w.engine.can_open())

    def test_flatten_audit_failure_keeps_lock_until_successful_retry(self):
        w = world(self, opened=False)
        w.engine.alert('flatten_snapshot_unavailable')
        original = w.store.event

        def event(kind, *args, **kwargs):
            if kind == 'CLOSE_OBSERVATION_RECOVERED':
                raise OSError('recovery audit failed')
            return original(kind, *args, **kwargs)

        with patch.object(w.store, 'event', side_effect=event):
            halt(w)
        self.assertIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
        self.assertEqual(w.store.meta('state'), 'HALTING')
        w.now += 3
        w.engine.tick()
        self.assertNotIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
        self.assertEqual(w.store.meta('state'), 'HALTED')
        release(w)


if __name__ == '__main__':
    unittest.main()
