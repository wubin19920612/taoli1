"""Round 26: final flatten evidence and admin release share program ownership."""
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.transport import APIError
from tools.crossex_exec.ownership import journal_positions
from tools.crossex_exec.equity import EquityAdmin
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

    def test_halt_final_proof_recovers_confirmed_order_locks_before_process_exit(self):
        for reason in ('unknown_order', 'unknown_close', 'accounting_pending', 'fills_pending',
                       'entry_accounting_unknown', 'negative_local_position', 'close_incomplete'):
            with self.subTest(reason=reason):
                w = world(self)
                w.engine.alert(reason)  # delayed/failed observation, venue evidence is now available
                halt(w)
                self.assertEqual(w.store.meta('state'), 'HALTED')
                self.assertNotIn(reason, w.engine.stops.reasons)
                release(w)

    def test_persisted_halted_flatten_lock_requires_fresh_proof_and_can_be_released_offline(self):
        w = world(self, opened=False)
        w.engine.alert('flatten_snapshot_unavailable')
        w.store.set_meta('state', 'HALTED')
        w.store.set_meta('halt_reason', 'total_loss')
        admin = EquityAdmin(w.store, w.accounts, clock=lambda:w.now)
        with patch.object(w.accounts, 'snapshot', side_effect=APIError('timeout')):
            with self.assertRaisesRegex(ValueError, 'account_read_unavailable'):
                admin.apply(confirm='total_loss', reason='recovered_snapshot', source='cli', release_halt=True)
        self.assertIn('flatten_snapshot_unavailable', w.store.meta('stop_locks'))
        admin.apply(confirm='total_loss', reason='recovered_snapshot', source='cli', release_halt=True)
        self.assertNotIn('flatten_snapshot_unavailable', w.store.meta('stop_locks'))
        self.assertEqual(w.store.meta('state'), 'RUNNING')

    def test_old_position_snapshot_cannot_certify_a_newer_close(self):
        w = world(self)
        old = dict(positions={}, captured_at=w.now-1)
        w.engine.close_pair(w.store.pairs(active=True)[0], 'fresh_close')
        w.engine.alert('flatten_snapshot_unavailable')
        with patch.object(w.accounts, 'position_snapshot', return_value=old):
            halt(w)
        self.assertEqual(w.store.meta('state'), 'HALTING')
        self.assertIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
        w.now += 3
        w.engine.tick()
        self.assertEqual(w.store.meta('state'), 'HALTED')
        self.assertNotIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
        release(w)

    def test_offline_flatten_recovery_audit_failure_rolls_back_epoch_and_locks(self):
        w = world(self, opened=False)
        w.engine.alert('flatten_snapshot_unavailable')
        w.store.set_meta('state', 'HALTED')
        w.store.set_meta('halt_reason', 'total_loss')
        admin = EquityAdmin(w.store, w.accounts, clock=lambda:w.now)
        original = w.store.event
        revision = w.store.meta('equity_baseline_revision', 0)
        baseline = w.store.meta('account_equity_baseline')

        def event(kind, *args, **kwargs):
            if kind == 'CLOSE_OBSERVATION_RECOVERED':
                raise OSError('recovery audit failed')
            return original(kind, *args, **kwargs)

        with patch.object(w.store, 'event', side_effect=event), self.assertRaises(OSError):
            admin.apply(confirm='total_loss', reason='retry', source='cli', release_halt=True)
        self.assertEqual(w.store.meta('state'), 'HALTED')
        self.assertEqual(w.store.meta('equity_baseline_revision', 0), revision)
        self.assertEqual(w.store.meta('account_equity_baseline'), baseline)
        self.assertIn('flatten_snapshot_unavailable', w.store.meta('stop_locks'))
        admin.apply(confirm='total_loss', reason='retry', source='cli', release_halt=True)
        self.assertEqual(w.store.meta('state'), 'RUNNING')

    def test_persisted_flatten_lock_does_not_bypass_pending_orders_or_real_inventory(self):
        for fault in ('pending', 'inventory'):
            with self.subTest(fault=fault):
                w = world(self)
                if fault == 'pending':
                    w.engine.close_pair(w.store.pairs(active=True)[0], 'completed_close')
                    order = next(w.store.iter_orders(reduce_only=True))
                    w.store.save_order(dict(order, accounting_pending=True))
                w.engine.alert('flatten_snapshot_unavailable')
                w.store.set_meta('state', 'HALTED')
                w.store.set_meta('halt_reason', 'total_loss')
                with self.assertRaisesRegex(ValueError, 'local_orders_pending|local_positions'):
                    release(w)
                self.assertEqual(w.store.meta('state'), 'HALTED')
                self.assertIn('flatten_snapshot_unavailable', w.store.meta('stop_locks'))

    def test_halt_waits_for_three_distinct_matches_before_releasing_mismatch(self):
        w = world(self)
        quantity = w.venues[0].quantity
        w.venues[0].quantity -= dec('.001')
        w.advance()
        self.assertIn('position_mismatch', w.engine.stops.reasons)
        w.venues[0].quantity = quantity  # the failed remote observation has recovered
        halt(w)
        self.assertEqual(w.store.meta('state'), 'HALTING')
        for sample in range(3):
            w.now = max(w.now+1, w.engine.next_halt+1)
            w.engine.tick()
            self.assertEqual(w.store.meta('position_match_streak'), sample+1)
            if sample < 2:
                self.assertIn('position_mismatch', w.engine.stops.reasons)
                self.assertEqual(w.store.meta('state'), 'HALTING')
        self.assertNotIn('position_mismatch', w.engine.stops.reasons)
        self.assertEqual(w.store.meta('state'), 'HALTED')
        release(w)

    def test_halt_clears_only_proven_closed_pair_repair_owners(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        w.engine.close_pair(pair, 'finished_repair')
        pair = w.store.get_pair(pair['id'])
        pair['repair_stopped'] = True
        w.store.pair(pair)
        w.engine.unhedged(pair)  # persisted observation raced the final confirmation
        halt(w)
        for reason in ('unhedged_position', 'repair_stopped'):
            self.assertNotIn(reason, w.engine.stops.reasons)
        self.assertEqual(w.store.meta('state'), 'HALTED')
        release(w)

    def test_unknown_flatten_owner_cannot_bypass_release_preconditions(self):
        w = world(self, opened=False)
        w.engine.stops.add('flatten_snapshot_unavailable', 'unknown_owner')
        w.store.set_meta('state', 'HALTED')
        w.store.set_meta('halt_reason', 'total_loss')
        with self.assertRaisesRegex(ValueError, 'position_or_order_lock'):
            release(w)
        self.assertEqual(w.store.meta('state'), 'HALTED')


if __name__ == '__main__':
    unittest.main()
