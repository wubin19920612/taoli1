"""Round 28: durable recovery liveness and quantity-only position proofs."""
import copy
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.external import ExternalUnverifiable
from tools.crossex_exec.ownership import journal_positions
from tools.crossex_exec.storage import Store
from round26_fixtures import world
from test_exec import setUpModule, tearDownModule


class InjectedCrash(BaseException):
    """Escape retry handlers, discard the engine, then reopen its SQLite file."""


def reopen(w):
    w.engine.close()
    w.store.close()
    w.store = Store(w.path)
    w.engine = w.make_engine()


def arm_halt(w, cause='total_loss', *, flag=True):
    w.engine.alert('position_mismatch')
    w.store.set_meta('position_mismatch', flag)
    w.engine.alert(cause)  # the UI reason intentionally hides the position lock
    w.store.set_meta('halt_reason', cause)
    w.store.set_meta('state', 'HALTING')


def tick_fresh(w):
    w.now = max(w.now + 1, w.engine.next_halt + 1)
    w.engine.tick()


class RecoveryLivenessTests(unittest.TestCase):
    def assert_manual_release(self, w, cause):
        self.assertEqual(w.store.meta('state'), 'HALTED')
        self.assertFalse(w.store.meta('position_mismatch'))
        self.assertNotIn('position_mismatch', w.engine.stops.reasons)
        self.assertNotIn('position_mismatch', w.store.meta('stop_locks'))
        self.assertIn(cause, w.store.meta('stop_locks'))
        self.assertEqual(w.store.meta('halt_reason'), cause)
        self.assertFalse(w.engine.can_open())
        w.engine.tick()
        self.assertEqual(w.store.meta('state'), 'HALTED')
        with self.assertRaisesRegex(ValueError, 'halt_confirmation'):
            w.engine.rebaseline(confirm='wrong_cause', reason='review', source='cli', release_halt=True)
        w.engine.rebaseline(confirm=cause, reason='verified_program_flat', source='cli', release_halt=True)
        self.assertEqual(w.store.meta('state'), 'RUNNING')
        self.assertNotIn(cause, w.store.meta('stop_locks'))
        w.advance()
        self.assertTrue(w.engine.can_open(), w.store.meta('stop_locks'))

    def test_residual_lock_with_false_flag_and_other_ui_reason_recovers_after_restart(self):
        for cause in ('total_loss', 'account_equity_drop'):
            with self.subTest(cause=cause):
                w = world(self, opened=False)
                arm_halt(w, cause, flag=False)
                reopen(w)
                self.assertFalse(w.store.meta('position_mismatch'))
                self.assertEqual(w.store.meta('stop_reason'), cause)
                for count in (1, 2, 3):
                    tick_fresh(w)
                    self.assertEqual(w.store.meta('position_match_streak'), count)
                    if count < 3:
                        self.assertEqual(w.store.meta('state'), 'HALTING')
                        self.assertIn('position_mismatch', w.store.meta('stop_locks'))
                self.assert_manual_release(w, cause)

    def test_crash_before_and_after_each_recovery_write_converges_after_reopen(self):
        # These are all durable boundaries from the third observation through
        # HALTED, including both StopLocks projections and the recovery audit.
        points = ('position_snapshot_at', 'position_match_streak', 'position_mismatch',
                  'stop_locks', 'stop_reason', 'POSITION_MATCH_RECOVERED', 'state')
        for cause in ('total_loss', 'account_equity_drop'):
            for point in points:
                for after in (False, True):
                    with self.subTest(cause=cause, point=point, after=after):
                        w = world(self)
                        w.engine.close_pair(w.store.pairs(active=True)[0], 'completed_close')
                        self.assertEqual(journal_positions(w.store), {})
                        arm_halt(w, cause)
                        for _ in range(2):
                            tick_fresh(w)
                        self.assertEqual(w.store.meta('position_match_streak'), 2)
                        before_baseline = w.store.meta('account_equity_baseline')
                        target = 'event' if point == 'POSITION_MATCH_RECOVERED' else 'set_meta'
                        original = getattr(w.store, target)
                        hits = []

                        def write(key, value=None, *args, **kwargs):
                            selected = key == point and (point != 'state' or value == 'HALTED')
                            if selected and not after:
                                hits.append(point)
                                raise InjectedCrash(point)
                            result = original(key, value, *args, **kwargs)
                            if selected and after:
                                hits.append(point)
                                raise InjectedCrash(point)
                            return result

                        with patch.object(w.store, target, side_effect=write):
                            with self.assertRaises(InjectedCrash):
                                tick_fresh(w)
                        self.assertEqual(hits, [point])
                        reopen(w)
                        if w.store.meta('state') == 'HALTED':
                            with self.assertRaisesRegex(ValueError, 'HALTED_manual_clear_required'):
                                w.engine.startup()
                        else:
                            w.engine.startup()
                        for _ in range(4):
                            if w.store.meta('state') == 'HALTED':
                                break
                            tick_fresh(w)
                        self.assertEqual(w.store.meta('account_equity_baseline'), before_baseline)
                        recovered = [r for r in w.store.events() if r['kind'] == 'POSITION_MATCH_RECOVERED']
                        self.assertEqual(len(recovered), 1)
                        self.assert_manual_release(w, cause)

    def test_failed_unlock_persistence_or_audit_keeps_halt_recoverable(self):
        for fault in ('stop_locks', 'POSITION_MATCH_RECOVERED'):
            with self.subTest(fault=fault):
                w = world(self, opened=False)
                arm_halt(w)
                for _ in range(2):
                    tick_fresh(w)
                target = 'set_meta' if fault == 'stop_locks' else 'event'
                original = getattr(w.store, target)

                def fail(key, value=None, *args, **kwargs):
                    if key == fault and (key != 'stop_locks' or 'position_mismatch' not in value):
                        raise OSError('injected recovery write failure')
                    return original(key, value, *args, **kwargs)

                with patch.object(w.store, target, side_effect=fail):
                    tick_fresh(w)
                self.assertEqual(w.store.meta('state'), 'HALTING')
                self.assertIn('position_mismatch', w.store.meta('stop_locks'))
                self.assertIn('position_mismatch', w.engine.stops.reasons)
                self.assertFalse(any(r['kind'] == 'POSITION_MATCH_RECOVERED' for r in w.store.events()))
                tick_fresh(w)
                self.assertNotIn('flatten_snapshot_unavailable', w.store.meta('stop_locks'))
                self.assert_manual_release(w, 'total_loss')

    def test_duplicate_observation_pending_accounting_and_unrelated_locks_still_block(self):
        w = world(self)
        w.engine.close_pair(w.store.pairs(active=True)[0], 'completed_close')
        arm_halt(w, flag=False)
        w.engine.alert('manual_review')
        w.engine.stops.add('position_mismatch', 'unknown_owner')
        w.now += 1
        snapshot = w.positions()
        w.engine.reconcile_positions(snapshot)
        w.engine.reconcile_positions(snapshot)
        self.assertEqual(w.store.meta('position_match_streak'), 1)
        order = next(w.store.iter_orders(reduce_only=True))
        w.store.save_order(dict(order, accounting_pending=True))
        w.now += 1
        self.assertIsNone(w.engine.reconcile_positions(w.positions()))
        self.assertEqual(w.store.meta('position_match_streak'), 0)
        w.store.save_order(order)
        for _ in range(3):
            w.now += 1
            w.engine.reconcile_positions(w.positions())
        self.assertEqual(w.engine.stops.reasons['position_mismatch'], {'unknown_owner'})
        self.assertIn('manual_review', w.engine.stops.reasons)
        self.assertIn('total_loss', w.engine.stops.reasons)
        self.assertFalse(w.engine.can_open())


class QuantityProjectionTests(unittest.TestCase):
    def test_program_flat_with_user_residual_without_pnl_reaches_halted_after_three_samples(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = world(self)
                entry = next(w.store.iter_orders(reduce_only=False))
                pair = w.store.get_pair(entry['pair_id'])
                entry = next(r for r in w.store.iter_orders(reduce_only=False) if r['leg'] == leg)
                w.venues[leg].quantity += dec('.4')
                w.engine.close_pair(pair, 'completed_close')
                self.assertEqual(journal_positions(w.store), {})
                self.assertEqual(w.venues[leg].quantity, dec('.4'))
                arm_halt(w)
                original = w.accounts.position_snapshot

                def quantities():
                    result = copy.deepcopy(original())
                    result.pop('upnl', None)
                    result.pop('upnl_by_position', None)
                    return result

                sends = len(w.sends)
                with patch.object(w.accounts, 'position_snapshot', side_effect=quantities):
                    for count in (1, 2, 3):
                        tick_fresh(w)
                        self.assertEqual(w.store.meta('position_match_streak'), count)
                        self.assertNotIn('flatten_snapshot_unavailable', w.engine.stops.reasons)
                        self.assertEqual(w.store.meta('state'), 'HALTING' if count < 3 else 'HALTED')
                status = w.store.meta('flatten_status')
                key = w.engine.position_key(pair, leg)
                self.assertTrue(status['verified'])
                self.assertTrue(status['program_flat'])
                self.assertFalse(status['account_flat'])
                self.assertEqual(dec(status['external_positions'][key]), dec('.4'))
                self.assertEqual(len(w.sends), sends)
                self.assertEqual(w.venues[leg].quantity, dec('.4'))
                w.engine.rebaseline(confirm='total_loss', reason='review', source='cli', release_halt=True)
                w.advance()
                with self.assertRaisesRegex(ValueError, 'external_contract_forbidden'):
                    w.engine.external.check_order(entry)

    def test_quantity_reconciliation_needs_no_pnl_but_still_detects_missing_leg(self):
        w = world(self)
        w.now += 1
        snapshot = dict(positions=w.positions()['positions'], captured_at=w.now)
        self.assertTrue(w.engine.reconcile_positions(snapshot))
        w.now += 1
        snapshot.update(positions={}, captured_at=w.now)
        self.assertFalse(w.engine.reconcile_positions(snapshot))
        self.assertIn('position_mismatch', w.engine.stops.reasons)

    def test_pnl_projection_rejects_missing_or_invalid_program_marks_with_safe_reason(self):
        w = world(self)
        snapshot = w.snapshot()
        key = next(iter(snapshot['positions']))
        variants = [{}, None, {k:v for k,v in snapshot['upnl_by_position'].items() if k != key}]
        variants += [dict(snapshot['upnl_by_position'], **{key:value}) for value in (None, 'NaN', 'bad')]
        for floating in variants:
            with self.subTest(floating=floating):
                with self.assertRaisesRegex(ExternalUnverifiable, '^program_income_unverifiable$'):
                    w.engine.external.split(dict(snapshot, upnl_by_position=floating))
        snapshot.pop('upnl_by_position')
        with self.assertRaisesRegex(ExternalUnverifiable, '^program_income_unverifiable$'):
            w.engine.external.split(snapshot)

    def test_quantity_projection_still_validates_raw_contract_identity(self):
        w = world(self)
        snapshot = copy.deepcopy(w.snapshot())
        snapshot.pop('upnl_by_position')
        snapshot['raw_positions']['crossex'][0]['symbol'] = 'BINANCE_FUTURE_OTHER_USDT'
        with self.assertRaisesRegex(ExternalUnverifiable, '^external_identity_unverifiable$'):
            w.engine.reconcile_positions(snapshot)


if __name__ == '__main__':
    unittest.main()
