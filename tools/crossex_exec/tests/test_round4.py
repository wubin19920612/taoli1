"""Round-four regressions. All venue operations are in memory; sockets are guarded."""
import threading
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.fill_sim.core import Book, day, dec
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import checked_status
from tools.crossex_exec.transport import APIError
from tools.crossex_exec.background import PeriodicWorker
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.policy import send_review
import test_round3 as previous
from test_exec import instrument, setUpModule, tearDownModule


class Round4Tests(unittest.TestCase):
    snapshot = previous.Round3Tests.snapshot
    pair = previous.Round3Tests.pair
    publish = previous.Round3Tests.publish
    open_intents = previous.Round3Tests.open_intents

    def setUp(self):
        previous.Round3Tests.setUp(self)
        self.accounts.fee_snapshots = {k:SimpleNamespace(rate=v, captured_at=self.now[0])
                                       for k,v in self.accounts.fees_by_market.items()}
        self.store.set_meta('daily_upnl', dict(day=day(self.now[0]), upnl='0', positions={}))

    def placed(self):
        return [row for row in self.trace if row[0] == 'place']

    def test_r1_coarse_step_zero_fill_never_exceeds_original_exposure(self):
        pair = self.pair(('1', '.999'), steps=('.1', '.001'))
        self.venues[0].reject = True  # First proposed coarse-leg order would fill zero.
        self.engine.balance(pair)
        self.assertLessEqual(abs(self.venues[0].quantity-self.venues[1].quantity), dec('.001'))
        self.assertFalse(any(row[1] == 1 for row in self.placed()))
        self.assertEqual(self.store.meta('stop_reason'), 'unhedged_position')
        self.assertFalse(self.engine.can_open())

    def test_r1_zero_fill_stops_before_other_leg_or_retry(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        self.venues[0].reject = True
        self.engine.balance(pair)
        self.engine.balance(self.store.pairs()[0])  # Stopped repair must survive later ticks.
        self.assertEqual(len(self.placed()), 1)
        self.assertEqual(self.placed()[0][1], 0)
        self.assertEqual(list(map(dec, pair['remaining'])), [dec('.107'), dec('.1')])
        self.assertTrue(any(row[0] == 'snapshot' for row in self.trace[self.trace.index(self.placed()[0])+1:]))

    def test_r1_partial_fill_replans_from_both_remote_positions(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        original = self.venues[0].place
        def partial(order, before_send):
            oid = original(order, before_send)
            self.venues[0].quantity += dec('.002')
            self.venues[0].orders[order['cid']] = '.008'
            return oid
        with patch.object(self.venues[0], 'place', side_effect=partial):
            self.engine.balance(pair)
        self.assertEqual([(r[1], dec(r[2])) for r in self.placed()], [(0, dec('.01')), (1, dec('.001'))])
        self.assertEqual(self.venues[0].quantity, self.venues[1].quantity)
        first, second = (self.trace.index(r) for r in self.placed())
        self.assertTrue(any(r[0] == 'snapshot' for r in self.trace[first+1:second]))

    def test_r1_snapshot_failure_after_send_stops_further_repair(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        original = self.venues[0].place
        def send(order, before_send):
            oid = original(order, before_send)
            self.accounts.position_snapshot.side_effect = ValueError('positions unavailable')
            return oid
        with patch.object(self.venues[0], 'place', side_effect=send):
            self.engine.balance(pair)
        self.assertEqual(len(self.placed()), 1)
        self.assertFalse(self.engine.can_open())

    def test_r1_initial_repair_uses_current_remote_excess_leg(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        self.venues[0].quantity = dec('.09')
        self.engine.balance(pair)
        self.assertEqual([(r[1], dec(r[2])) for r in self.placed()], [(1, dec('.01'))])
        self.assertEqual(self.venues[0].quantity, self.venues[1].quantity)

    def test_r2_asymmetric_book_ages_reject_both_legs(self):
        orders = self.open_intents()
        pair = self.store.pairs()[0]
        for book, age in zip(pair['last_books'], (1.9, 2.1)):
            book['received_at'] = self.now[0]-age
        self.store.pair(pair)
        self.market.books = lambda instruments:[Book(**b) for b in pair['last_books']]
        result = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in result], ['UNSENT', 'UNSENT'])
        self.assertFalse(self.placed())

    def test_r2_late_second_worker_rejects_entire_pair(self):
        orders = self.open_intents(age=1.9)
        stale = self.store.pairs()[0]['last_books']
        self.market.books = lambda instruments:[Book(**b) for b in stale]
        first_sent = threading.Event()
        original = self.venues[0].place
        def first(order, before_send):
            try:
                return original(order, before_send)
            finally:
                first_sent.set()
        def second(order, before_send):
            # Old code lets leg 0 send at age 1.9; the shared gate must wait.
            first_sent.wait(.05)
            self.now[0] += .2
            return previous.Venue.place(self.venues[1], order, before_send)
        with (patch.object(self.venues[0], 'place', side_effect=first),
              patch.object(self.venues[1], 'place', side_effect=second)):
            result = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in result], ['UNSENT', 'UNSENT'])
        self.assertFalse(self.placed())

    def test_r2_shared_review_runs_once_after_both_workers_ready(self):
        orders = self.open_intents()
        ready = []
        originals = [v.place for v in self.venues]
        def send(n, order, before_send):
            ready.append(n)
            return originals[n](order, before_send)
        def review(*args):
            self.assertEqual(set(ready), {0, 1})
            return send_review(*args)
        with (patch.object(self.venues[0], 'place', side_effect=lambda i,before_send:send(0,i,before_send)),
              patch.object(self.venues[1], 'place', side_effect=lambda i,before_send:send(1,i,before_send)),
              patch('tools.crossex_exec.engine.send_review', side_effect=review) as reviewed):
            result = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in result], ['TERMINAL', 'TERMINAL'])
        self.assertEqual(reviewed.call_count, 1)

    def test_r2_failure_before_barrier_releases_peer_without_sending(self):
        orders = self.open_intents()
        with patch.object(self.venues[1], 'place', side_effect=ValueError('connect failed')):
            result = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in result], ['UNSENT', 'UNSENT'])
        self.assertFalse(self.placed())

    def test_r2_send_failure_does_not_revoke_shared_admission(self):
        orders = self.open_intents()
        aborts = []
        original = threading.Barrier.abort
        def abort(gate):
            aborts.append(True)
            return original(gate)
        def rejected(order, before_send):
            before_send()
            raise APIError('rejected')
        with (patch.object(self.venues[0], 'place', side_effect=rejected),
              patch.object(threading.Barrier, 'abort', abort)):
            result = self.engine.executor.execute_pair(orders)
        self.assertEqual(result[0]['state'], 'REJECTED')
        self.assertEqual(result[1]['state'], 'TERMINAL')
        self.assertFalse(aborts)

    def test_r3_old_rate_cannot_borrow_new_timestamp(self):
        i = instrument()
        now = self.now[0]
        class Interleaved:
            fee_snapshots = {i.market.key:SimpleNamespace(rate='.0002', captured_at=now-3601)}
            fee_times_by_market = {i.market.key:now-3601}
            @property
            def fees_by_market(self):
                old = {i.market.key:'.0002'}
                self.fee_times_by_market = {i.market.key:now}
                self.fee_snapshots = {i.market.key:SimpleNamespace(rate='.009', captured_at=now)}
                return old
        self.engine.accounts = Interleaved()
        try:
            rates = self.engine.fees_for([i])
        except ValueError:
            return  # Reading the old complete version correctly fails freshness.
        self.assertEqual(rates, ['.009'])

    def test_r3_published_fee_version_is_immutable_and_replaced(self):
        cross, bitget = Mock(), Mock()
        cross.fees.return_value = [dict(exchange_type='BINANCE', future_taker_fee='.0002')]
        cross.leverage.return_value = {}
        accounts = Accounts(cross, bitget, clock=lambda:self.now[0])
        i = instrument()
        accounts.fees([i])
        old = accounts.fee_snapshots[i.market.key]
        with self.assertRaises(FrozenInstanceError):
            old.captured_at = self.now[0]+1
        self.now[0] += 1
        cross.fees.return_value[0]['future_taker_fee'] = '.009'
        accounts.fees([i])
        new = accounts.fee_snapshots[i.market.key]
        self.assertEqual((old.rate, new.rate), ('.0002', '.009'))
        self.assertEqual(new.captured_at-old.captured_at, 1)

    def test_r4_order_average_uses_documented_fields_only(self):
        for gate, field in ((True, 'executed_avg_price'), (False, 'avgPrice')):
            with self.subTest(gate=gate):
                i = dict(cid='x', oid='42', quantity='1', instrument=instrument().dump())
                raw = dict(symbol=i['instrument']['symbol'], **{field:'101.2'}, avg_price='999')
                raw.update(dict(order_id='42', text='x', executed_qty='1', state='FILLED') if gate else
                           dict(orderId='42', clientOid='x', cumExecQty='1', orderStatus='filled', category='USDT-FUTURES'))
                self.assertEqual(checked_status(raw, i, gate=gate)['average_price'], '101.2')
                del raw[field]
                self.assertIsNone(checked_status(raw, i, gate=gate)['average_price'])

    def test_r5_unchanged_overnight_loss_does_not_lock_new_day(self):
        self.data['equity'] = '31000'
        self.engine.reconcile()
        self.data.update(upnl='-20', equity='30980', upnl_by_position={'long':'-25', 'short':'5'})
        self.engine.reconcile()
        self.assertEqual(self.engine.risk, 'STOP')
        self.now[0] += 86400
        self.engine.reconcile()
        self.assertEqual(self.engine.risk, 'RUN')
        self.assertIsNone(self.store.meta('daily_loss_day'))
        self.assertEqual(self.engine.totals(), (dec('0'), dec('-20')))
        self.assertEqual(self.store.meta('daily_upnl')['positions'], {'long':'-25', 'short':'5'})

    def test_r5_baseline_survives_restart_and_only_new_loss_latches(self):
        self.engine.reconcile()
        self.data['upnl'] = '-20'
        self.engine.reconcile()
        self.now[0] += 86400
        self.engine.reconcile()
        second = Engine(self.store, self.market, self.accounts, live=True, clock=lambda:self.now[0])
        self.addCleanup(second.close)
        self.data['upnl'] = '-39'
        second.reconcile()
        self.assertEqual(second.totals()[0], dec('-19'))
        self.assertEqual(second.risk, 'RUN')
        self.data['upnl'] = '-40'
        second.reconcile()
        self.assertEqual(second.risk, 'STOP')

    def test_r5_previous_day_cache_cannot_set_new_daily_baseline(self):
        self.engine.reconcile()
        self.data['upnl'] = '-20'
        self.now[0] += 86400-1
        self.engine.reconcile()
        self.engine.workers = [Mock()]
        self.now[0] += 1
        self.engine.reconcile()
        self.assertEqual(self.store.meta('daily_upnl')['day'], day(self.now[0]))
        self.assertEqual(self.store.meta('daily_upnl')['upnl'], '-20')
        self.assertFalse(self.engine.can_open())
        self.data['upnl'] = '-25'
        self.now[0] += 1
        self.publish()
        self.engine.reconcile()
        self.assertEqual(self.engine.totals()[0], dec('-5'))
        self.assertTrue(self.engine.can_open())

    def test_r5_realizing_overnight_loss_does_not_count_it_twice(self):
        pair = self.pair()
        self.engine.reconcile()
        self.data['upnl'] = '-20'
        self.engine.reconcile()
        self.now[0] += 86400
        self.engine.reconcile()
        original = self.venues[0].fills
        with patch.object(self.venues[0], 'fills', side_effect=lambda o:dict(original(o), vwap='80')):
            self.engine.close_pair(pair, 'test')
        self.data['upnl'] = '0'
        self.now[0] += 1
        self.engine.reconcile()
        self.assertEqual(self.engine.totals(), (dec('0'), dec('-20')))
        self.assertEqual(self.engine.risk, 'RUN')

    def test_r5_midnight_during_reconcile_waits_without_permanent_stop(self):
        self.now[0] += 86400-.1
        self.data['upnl'] = '-20'
        original = self.engine.reconcile_positions
        def cross_midnight(snapshot):
            matched = original(snapshot)
            self.now[0] += .2
            return matched
        with patch.object(self.engine, 'reconcile_positions', side_effect=cross_midnight):
            self.engine.reconcile()
        self.assertNotEqual(self.store.meta('stop_reason'), 'account_snapshot_unavailable')
        self.engine.reconcile()
        self.assertEqual(self.engine.totals()[0], 0)
        self.assertTrue(self.engine.can_open())

    def test_r6_pre_fill_or_equal_snapshot_never_flags_mismatch(self):
        self.pair()
        for order in self.store.all_orders():
            order['fill_ms'] = self.now[0]*1000
            self.store.save_order(order)
        self.engine.workers = [Mock()]
        for offset in (-.1, 0):
            stale = dict(self.snapshot(), positions={})
            self.engine.account_cache.publish(stale, self.now[0]+offset)
            self.engine.reconcile()
            self.assertNotEqual(self.store.meta('stop_reason'), 'position_mismatch')
            self.assertFalse(self.engine.can_open())
        self.now[0] += 1
        self.publish()
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())

    def test_r6_three_distinct_matching_snapshots_clear_and_log(self):
        self.engine.workers = [Mock()]
        self.engine.account_cache.publish(dict(self.snapshot(), positions={'foreign':dec('1')}))
        self.engine.reconcile()
        self.assertEqual(self.store.meta('stop_reason'), 'position_mismatch')
        self.now[0] += 1
        self.publish()
        for _ in range(5):
            self.engine.reconcile()  # Reusing one cached snapshot counts only once.
        self.assertEqual(self.store.meta('stop_reason'), 'position_mismatch')
        for n in range(2):
            self.now[0] += 1
            self.publish()
            self.engine.reconcile()
            if n == 0:
                self.assertFalse(self.engine.can_open())
        self.assertIsNone(self.store.meta('stop_reason'))
        self.assertTrue(self.engine.can_open())
        self.assertEqual(sum(e['kind'] == 'POSITION_MATCH_RECOVERED' for e in self.store.events()), 1)

    def test_r6_partial_fill_observation_survives_unknown_confirmation(self):
        self.pair(unknown=True)
        order = next(o for o in self.store.all_orders() if o['state'] == 'UNKNOWN')
        response = dict(oid=order['oid'], venue_state='PARTIALLY_FILLED', filled='.5', terminal=False, rejected=False)
        with patch.object(self.venues[0], 'detail', side_effect=[response, ValueError('query unavailable')]):
            self.engine.executor.confirm(order)
        stored = self.store.order(order['cid'])
        self.assertEqual(stored['state'], 'UNKNOWN')
        self.assertEqual(stored.get('fill_observed_at'), self.now[0])
        self.engine.workers = [Mock()]
        self.engine.account_cache.publish(dict(self.snapshot(), positions={}), self.now[0]-.1)
        self.engine.reconcile()
        self.assertNotEqual(self.store.meta('stop_reason'), 'position_mismatch')

    def test_r6_mismatch_resets_recovery_streak_and_preserves_other_stop(self):
        self.engine.workers = [Mock()]
        for matched in (False, True, True, False, True, True):
            self.now[0] += 1
            value = self.snapshot()
            if not matched:
                value['positions'] = {'foreign':dec('1')}
            self.engine.account_cache.publish(value)
            self.engine.reconcile()
        self.assertFalse(self.engine.can_open())
        self.engine.alert('other_stop')
        self.now[0] += 1
        self.publish()
        self.engine.reconcile()
        self.assertEqual(self.store.meta('stop_reason'), 'other_stop')
        self.assertTrue(any(e['kind'] == 'POSITION_MATCH_RECOVERED' for e in self.store.events()))

    def test_r7_equity_events_and_alerts_share_value_sensitive_dedup(self):
        self.data['equity_by_venue']['bitget'] = '29850'
        for _ in range(10):
            self.engine.limits.check_equity(self.snapshot())
        self.assertEqual(sum(e['kind'] == 'EQUITY_HALT' for e in self.store.events()), 1)
        self.assertEqual(sum(e['kind'] == 'ALERT' for e in self.store.events()), 1)
        self.assertEqual(self.sink.call_count, 1)
        self.data['equity_by_venue']['bitget'] = '29849'
        self.engine.limits.check_equity(self.snapshot())
        self.engine.limits.check_equity(self.snapshot())
        self.assertEqual(sum(e['kind'] == 'EQUITY_HALT' for e in self.store.events()), 2)
        self.assertEqual(sum(e['kind'] == 'ALERT' for e in self.store.events()), 2)
        self.assertEqual(self.sink.call_count, 2)

    def test_r7_halting_multiple_unchanged_reasons_do_not_repeat(self):
        self.store.set_meta('state', 'HALTING')
        for _ in range(5):
            self.engine.alert('flatten_retry_pending')
            self.engine.alert('flatten_snapshot_unavailable')
        self.assertEqual(self.sink.call_count, 2)
        self.assertEqual(sum(e['kind'] == 'ALERT' for e in self.store.events()), 2)

    def test_r7_unhedged_event_and_alert_change_together(self):
        pair = self.pair(('1', '.999'))
        self.store.set_meta('state', 'HALTING')
        for _ in range(5):
            self.engine.unhedged(pair)
        self.assertEqual(sum(e['kind'] == 'UNHEDGED' for e in self.store.events()), 1)
        self.assertEqual(self.sink.call_count, 1)
        pair['remaining'][1] = '.998'
        self.engine.unhedged(pair)
        self.assertEqual(sum(e['kind'] == 'UNHEDGED' for e in self.store.events()), 2)
        self.assertEqual(self.sink.call_count, 2)

    def test_r8_outer_worker_exception_logged_throttled_and_sanitized(self):
        clock = [0.0]
        def callback():
            raise ValueError('secret-response-must-not-appear')
        with (patch('tools.crossex_exec.background.threading.Thread.start'),
              patch('tools.crossex_exec.background.time.monotonic', side_effect=lambda:clock[0])):
            worker = PeriodicWorker(callback, 5, 'test-worker')
            worker.stopped = Mock()
            worker.stopped.is_set.side_effect = [False, False, False, True]
            with self.assertLogs('tools.crossex_exec.background', level='ERROR') as logs:
                worker.run()
            self.assertEqual(len(logs.output), 1)
            self.assertIn('ValueError', logs.output[0])
            self.assertNotIn('secret-response', logs.output[0])
            clock[0] = 61
            worker.stopped.is_set.side_effect = [False, True]
            with self.assertLogs('tools.crossex_exec.background', level='ERROR') as repeated:
                worker.run()
            self.assertEqual(len(repeated.output), 1)

    def test_r8_distinct_worker_errors_reach_journal_once_each(self):
        callback = Mock(side_effect=[ValueError('secret-a'), TypeError('secret-b'), ValueError('secret-c')])
        with patch('tools.crossex_exec.background.threading.Thread.start'):
            worker = PeriodicWorker(callback, 5, 'test-worker', on_error=self.engine.background_error)
        worker.stopped = Mock()
        worker.stopped.is_set.side_effect = [False, False, False, True]
        with self.assertLogs('tools.crossex_exec.background', level='ERROR') as logs:
            worker.run()
        self.assertEqual(len(logs.output), 2)
        events = [e for e in self.store.events() if e['kind'] == 'BACKGROUND_FAILED']
        self.assertEqual(len(events), 2)
        self.assertNotIn('secret-', str(events))

    def test_r8_halting_backoff_begins_after_slow_round_finishes(self):
        self.pair()
        self.store.set_meta('state', 'HALTING')
        def slow_failure(*args):
            self.now[0] += 10
            raise ValueError('close failed')
        with patch.object(self.engine, 'close_pair', side_effect=slow_failure) as close:
            self.engine.flatten_all('test')
            completed = self.now[0]
            self.assertEqual(self.engine.next_halt, completed+2)
            self.engine.tick()
            self.assertEqual(close.call_count, 1)
            self.now[0] = completed+2
            self.engine.tick()
            self.assertEqual(close.call_count, 2)
            self.assertEqual(self.engine.next_halt, self.now[0]+4)

    def test_r8_snapshot_error_also_sets_backoff_after_completion(self):
        self.store.set_meta('state', 'HALTING')
        def slow_snapshot():
            self.now[0] += 10
            raise ValueError('snapshot failed')
        self.accounts.snapshot.side_effect = slow_snapshot
        self.engine.flatten_all('test')
        self.assertEqual(self.engine.next_halt, self.now[0]+2)
