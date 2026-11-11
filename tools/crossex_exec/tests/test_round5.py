"""Round-five state regressions; in-memory venues and a faulting local journal."""
import copy
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tools.fill_sim.core import Book, day, dec
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.transport import APIError
import test_round3 as previous
from test_exec import setUpModule, tearDownModule


class Round5Tests(unittest.TestCase):
    snapshot = previous.Round3Tests.snapshot
    pair = previous.Round3Tests.pair
    publish = previous.Round3Tests.publish

    def setUp(self):
        previous.Round3Tests.setUp(self)
        self.accounts.position_snapshot.side_effect = self.snapshot

    def open_intents(self):
        orders = previous.Round3Tests.open_intents(self)
        for venue in self.venues:
            venue.quantity = dec(0)
        self.publish()
        return orders

    def matching_snapshots(self, count=3):
        for _ in range(count):
            self.now[0] += 1
            self.engine.reconcile_positions(dict(self.snapshot(), captured_at=self.now[0]))

    def test_r1_matching_ledger_cannot_clear_failed_repair_lock(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        self.venues[0].reject = True
        self.engine.balance(pair)
        self.now[0] += 1
        self.engine.reconcile_positions(dict(self.snapshot(), positions={}, captured_at=self.now[0]))
        self.matching_snapshots()
        self.assertFalse(self.engine.can_open())
        self.assertIn('unhedged_position', self.store.meta('stop_locks', {}))
        self.assertIn('repair_stopped', self.store.meta('stop_locks', {}))
        self.assertNotIn('position_mismatch', self.store.meta('stop_locks', {}))

    def test_r1_balanced_remote_clears_only_this_pairs_repair_locks(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        self.venues[0].reject = True
        self.engine.balance(pair)
        self.engine.alert('manual_review')
        # A late confirmed reduction resolves exposure without erasing manual review.
        close = self.engine.new_intent(pair, 0, '.007', '100', True)
        close.update(state='TERMINAL', filled='.007', accounting_pending=False, fee='0', vwap='100')
        self.store.save_order(close)
        self.venues[0].quantity = dec('.1')
        self.engine.balance(pair)
        locks = self.store.meta('stop_locks', {})
        self.assertNotIn('repair_stopped', locks)
        self.assertNotIn('unhedged_position', locks)
        self.assertIn('manual_review', locks)
        self.assertFalse(pair.get('repair_stopped'))
        self.assertFalse(self.engine.can_open())

    def test_r1_lock_set_survives_restart_and_halted_is_never_auto_cleared(self):
        self.engine.alert('manual_review')
        self.engine.alert('position_mismatch')
        self.store.set_meta('position_mismatch', True)
        second = Engine(self.store, self.market, self.accounts, live=True, clock=lambda:self.now[0])
        self.addCleanup(second.close)
        self.engine = second
        self.matching_snapshots()
        self.assertIn('manual_review', self.store.meta('stop_locks', {}))
        self.store.set_meta('state', 'HALTED')
        with self.assertRaisesRegex(ValueError, 'HALTED_manual_clear_required'):
            second.startup()
        second.tick()
        self.assertEqual(self.store.meta('state'), 'HALTED')

    def test_r2_remote_snapshot_never_overwrites_fill_ledger(self):
        pair = self.pair()
        before = copy.deepcopy(self.store.all_orders())
        self.venues[0].quantity = self.venues[1].quantity = dec('.4')
        self.engine.resolve_exposure(pair)
        self.assertEqual(list(map(dec, pair['remaining'])), [dec(1), dec(1)])
        self.assertEqual(list(map(dec, pair['remote_remaining'])), [dec('.4'), dec('.4')])
        self.assertEqual(self.store.all_orders(), before)
        self.matching_snapshots()
        self.assertIn('position_mismatch', self.store.meta('stop_locks', {}))
        self.assertFalse(self.engine.can_open())

    def test_r2_pending_fill_accounting_never_advances_unlock_streak(self):
        self.pair()
        self.engine.alert('position_mismatch')
        self.store.set_meta('position_mismatch', True)
        order = self.store.all_orders()[0]
        order['accounting_pending'] = True
        self.store.save_order(order)
        self.matching_snapshots(5)
        self.assertTrue(self.store.meta('position_mismatch'))
        self.assertEqual(self.store.meta('position_match_streak', 0), 0)
        order['accounting_pending'] = False
        self.store.save_order(order)
        self.matching_snapshots()
        self.assertFalse(self.store.meta('position_mismatch'))

    def test_r2_legacy_anchor_is_not_a_local_ledger_baseline(self):
        pair = self.pair()
        pair['position_anchor'] = dict(at=self.now[0], remaining=['.2', '.2'],
                                      filled={o['cid']:o['filled'] for o in self.store.all_orders()}, unresolved=[])
        self.store.pair(pair)
        self.engine.refresh_pair(pair)
        self.assertEqual(list(map(dec, pair['remaining'])), [dec(1), dec(1)])
        self.assertNotIn('position_anchor', pair)

    def test_r3_unknown_write_failure_retains_recovery_and_retries_write(self):
        orders = self.open_intents()
        original = self.venues[0].place
        def timeout(order, before_send):
            original(order, before_send)
            self.venues[0].unresolved = True
            raise APIError('unknown')
        save = self.store.save_order
        failures = []
        def fail_once(order, kind=None):
            if order['state'] == 'UNKNOWN' and not failures:
                failures.append(order['cid'])
                raise sqlite3.OperationalError('injected write failure')
            return save(order, kind)
        with (patch.object(self.venues[0], 'place', side_effect=timeout),
              patch.object(self.store, 'save_order', side_effect=fail_once)):
            self.engine.executor.execute_pair(orders)
            self.engine.tick()
        self.assertEqual(len(failures), 1)
        self.assertTrue(any(r[0] == 'cancel' for r in self.trace))
        self.assertTrue(any(e['kind'] == 'POSITION_RECOVERY' for e in self.store.events()))
        self.assertTrue(all(v.quantity == 0 for v in self.venues))
        self.assertEqual(self.store.order(failures[0])['state'], 'UNKNOWN')
        self.assertFalse(self.engine.can_open())

    def test_r3_every_tick_recovers_sent_even_when_pair_state_and_schedule_are_stale(self):
        pair = self.pair(('.7', '.4'), unknown=True)
        entry = self.store.all_orders()[0]
        entry['state'] = 'SENT'
        self.store.save_order(entry)
        pair.update(state='OPEN', next_track=self.now[0]+9999)
        self.store.pair(pair)
        self.accounts.snapshot.side_effect = ValueError('account unavailable')
        self.trace.clear()
        self.engine.tick()
        kinds = [r[0] for r in self.trace]
        self.assertIn('cancel', kinds)
        self.assertLess(kinds.index('detail'), kinds.index('cancel'))
        self.assertLess(kinds.index('cancel'), kinds.index('snapshot'))
        self.assertTrue(all(v.quantity == 0 for v in self.venues))

    def test_r3_failed_sent_commit_never_calls_the_exchange(self):
        orders = self.open_intents()
        save = self.store.save_order
        def fail_sent(order, kind=None):
            if kind == 'SENT':
                raise sqlite3.OperationalError('injected before send')
            return save(order, kind)
        with patch.object(self.store, 'save_order', side_effect=fail_sent):
            self.engine.executor.execute_pair(orders)
        self.assertFalse([r for r in self.trace if r[0] == 'place'])
        self.assertTrue(all(o['state'] == 'UNSENT' for o in self.store.all_orders()))

    def test_r4_close_before_first_new_day_snapshot_uses_last_saved_upnl(self):
        pair = self.pair()
        self.data.update(upnl='-20', upnl_by_position={'long':'-25', 'short':'5'})
        self.now[0] += 86399
        self.engine.reconcile()
        self.now[0] += 2
        original = self.venues[0].fills
        with patch.object(self.venues[0], 'fills', side_effect=lambda o:dict(original(o), vwap='80')):
            self.engine.close_pair(pair, 'test_midnight')
        self.data.update(upnl='0', upnl_by_position={})
        self.now[0] += 1
        self.engine.reconcile()
        self.assertEqual(self.engine.totals(), (dec(0), dec('-20')))
        self.assertEqual(self.store.meta('daily_upnl')['positions'], {'long':'-25', 'short':'5'})
        self.assertEqual(self.store.meta('daily_upnl')['captured_at'], self.now[0]-4)
        self.assertNotEqual(self.store.meta('daily_loss_day'), day(self.now[0]))

    def test_r4_tracking_saves_each_position_upnl_before_midnight_and_restart(self):
        pair = self.pair()
        self.now[0] += 86399
        self.market.books = lambda instruments: [Book([['95','10']], [['107','10']], self.now[0]) for _ in instruments]
        # Keep the position alive to isolate the observation from the tested exit path.
        with patch.object(self.engine, 'close_pair'):
            self.engine.track(pair)
        saved = self.store.meta('last_upnl')
        self.assertIsNotNone(saved)
        self.assertEqual(dec(saved['upnl']), dec('-10'))
        self.assertEqual(sorted(map(dec, saved['positions'].values())), [dec('-5'), dec('-5')])
        self.now[0] += 2
        second = Engine(self.store, self.market, self.accounts, live=True, clock=lambda:self.now[0])
        self.addCleanup(second.close)
        second.roll_daily_upnl(dict(upnl='0', upnl_by_position={}), self.now[0])
        self.assertEqual(dec(self.store.meta('daily_upnl')['upnl']), dec('-10'))

    def test_r5_halting_startup_account_failure_still_retires_intent(self):
        for order in self.open_intents():
            self.engine.executor.prepare(order)
        self.store.set_meta('state', 'HALTING')
        self.accounts.validate_startup.side_effect = ValueError('account unavailable')
        self.engine.startup()
        self.assertTrue(all(o['state'] == 'UNSENT' for o in self.store.all_orders()))
        self.assertFalse([r for r in self.trace if r[0] == 'place'])
        self.assertEqual(self.store.meta('state'), 'HALTED')

    def test_r5_account_outage_does_not_block_position_only_recovery(self):
        self.pair(('.7', '.4'), unknown=True)
        self.store.set_meta('state', 'HALTING')
        self.accounts.validate_startup.side_effect = ValueError('account unavailable')
        self.accounts.snapshot.side_effect = ValueError('account unavailable')
        self.engine.startup()
        self.assertTrue(all(v.quantity == 0 for v in self.venues))
        self.assertEqual(self.store.meta('state'), 'HALTING')
        self.venues[0].unresolved = False
        entry = next(o for o in self.store.all_orders() if not o['reduce_only'] and o['leg'] == 0)
        self.venues[0].orders[entry['cid']] = '.7'
        self.now[0] += 61
        self.engine.tick()
        self.assertEqual(self.store.meta('state'), 'HALTED')

    def test_r5_position_reader_does_not_require_account_or_settings(self):
        cross, bitget = Mock(), Mock()
        cross.account.side_effect = bitget.account.side_effect = AssertionError('account must not be read')
        bitget.settings.side_effect = AssertionError('settings must not be read')
        cross.positions.return_value = bitget.positions.return_value = []
        accounts = Accounts(cross, bitget)
        self.assertEqual(accounts.position_snapshot()['positions'], {})
        cross.account.assert_not_called()
        bitget.settings.assert_not_called()
