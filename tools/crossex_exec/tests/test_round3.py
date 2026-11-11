"""Round-three regressions: in-memory venues, no credentials or external I/O."""
import copy
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from tools.fill_sim.core import Book, dec
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import checked_status
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.market import MarketData
from tools.crossex_exec.policy import review
from tools.crossex_exec.storage import Store
from test_exec import instrument, setUpModule, tearDownModule
from test_engine import FakeMarket


class Venue:
    def __init__(self, owner, leg):
        self.owner, self.leg = owner, leg
        self.quantity = dec(0)
        self.unresolved = False
        self.reject = False
        self.orders = {}

    def place(self, order, before_send):
        before_send()
        self.owner.trace.append(('place', self.leg, order['quantity'], order['reduce_only']))
        if self.reject:
            raise ValueError('injected_rejection')
        qty = min(dec(order['quantity']), self.quantity) if order['reduce_only'] else dec(order['quantity'])
        self.quantity += qty * (-1 if order['reduce_only'] else 1)
        self.orders[order['cid']] = str(qty)
        return order['cid']

    def detail(self, order, deadline=None):
        self.owner.trace.append(('detail', self.leg, order.get('oid')))
        if self.unresolved and not order['reduce_only']:
            raise ValueError('injected_unknown')
        return dict(oid=order.get('oid') or order['cid'], venue_state='FILLED',
                    filled=self.orders.get(order['cid'], order.get('filled', '0')),
                    terminal=True, rejected=False, average_price='100' if self.leg == 0 else '102')

    def cancel(self, order):
        self.owner.trace.append(('cancel', self.leg, order.get('oid')))

    def fills(self, order):
        return dict(vwap='100' if self.leg == 0 else '102', fee='0', fill_ms=self.owner.now[0]*1000,
                    accounting_pending=False, trade_ids=[order['cid']])


class Round3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'exec.db')
        self.addCleanup(self.store.close)
        self.now = [1794355200.0]
        self.trace = []
        self.market = FakeMarket()
        self.market.books = lambda instruments: [Book([['100' if i.market.exchange == 'binance' else '102', '10']],
                                                     [['100' if i.market.exchange == 'binance' else '102', '10']], self.now[0])
                                                for i in instruments]
        self.venues = [Venue(self, n) for n in (0, 1)]
        self.data = dict(margins=['4', '4'], upnl='0', equity_by_venue={'crossex':'1000', 'bitget':'30000'},
                         notionals={}, liabilities=[])
        self.accounts = Mock(cross=self.venues[0], bitget=self.venues[1])
        self.accounts.fees_by_market = {k:'.0002' for k in self.market.mapping}
        self.accounts.snapshot.side_effect = self.snapshot
        self.accounts.validate_startup.side_effect = self.snapshot
        self.accounts.fees.side_effect = lambda instruments: {i.market.key:'.0002' for i in instruments}
        self.sink = Mock()
        self.engine = Engine(self.store, self.market, self.accounts, live=True, clock=lambda:self.now[0],
                             stop_file=Path(self.tmp.name)/'STOP', alert_sink=self.sink)
        self.engine.executor.confirm_timeout = .002
        self.engine.executor.poll_interval = .001
        self.engine.limits.initialize(self.snapshot())
        self.engine.risk = 'RUN'
        self.engine.last_reconcile = self.now[0]
        # Cleanup remains usable on the original implementation during the red run.
        self.addCleanup(lambda: getattr(self.engine, 'close', lambda:None)())

    def snapshot(self):
        self.trace.append(('snapshot',))
        positions = {}
        for n, venue in enumerate(self.venues):
            if venue.quantity:
                i = instrument('binance' if n == 0 else 'bitget')
                positions['|'.join(('crossex' if n == 0 else 'bitget', i.symbol, 'long' if n == 0 else 'short'))] = venue.quantity
        return dict(copy.deepcopy(self.data), positions=positions)

    def pair(self, quantities=('1', '1'), unknown=False, steps=('.001', '.001'), minimum='0'):
        instruments = [instrument(), instrument('bitget')]
        for n, i in enumerate(instruments):
            i.spec = replace(i.spec, qty_step=steps[n], min_qty=steps[n], min_notional=minimum)
        books = self.market.books(instruments)
        funding = [dict(rate='0', interval=28800, next=self.now[0]+10000, captured_at=self.now[0],
                        history_ok=True, max_abs_24h='0') for _ in (0, 1)]
        pair = dict(id='p', base='BTC', state='OPEN', opened_at=self.now[0],
                    instruments=[i.dump() for i in instruments], remaining=list(quantities),
                    last_books=[b.to_dict() for b in books], last_good=[self.now[0]]*2,
                    next_track=self.now[0], single_failure=False, repair_attempts=0, funding=funding,
                    fill=dict(quantity='1', buy={'vwap':'100', 'notional':'100'}, sell={'vwap':'102', 'notional':'102'},
                              gross_bps=200, fee_a='.0002', fee_b='.0002'))
        for n, qty in enumerate(quantities):
            self.venues[n].quantity = dec(qty)
            order = self.engine.new_intent(pair, n, qty, '100' if n == 0 else '102', False)
            order.update(state='UNKNOWN' if unknown and n == 0 else 'TERMINAL', oid='entry-'+str(n),
                         filled='0' if unknown and n == 0 else qty, fee='0',
                         vwap=None if unknown and n == 0 else ('100' if n == 0 else '102'), accounting_pending=unknown and n == 0)
            self.store.save_order(order)
        self.venues[0].unresolved = unknown
        self.store.pair(pair)
        self.engine.refresh_pair(pair)
        return pair

    def test_r1_unknown_cancel_query_positions_then_close_both_legs(self):
        pair = self.pair(('.7', '.4'), unknown=True)
        self.trace.clear()
        self.engine.close_pair(pair, 'manual')
        placed = [r for r in self.trace if r[0] == 'place']
        self.assertEqual({(r[1], dec(r[2])) for r in placed}, {(0, dec('.7')), (1, dec('.4'))})
        self.assertTrue(all(r[3] for r in placed))
        kinds = [r[0] for r in self.trace]
        self.assertLess(kinds.index('cancel'), kinds.index('detail'))
        self.assertLess(kinds.index('detail'), kinds.index('snapshot'))
        self.assertLess(kinds.index('snapshot'), kinds.index('place'))
        self.assertEqual(self.trace[0][2], 'entry-0')
        self.assertTrue(all(v.quantity == 0 for v in self.venues))

    def test_r1_halting_recovers_unknown_and_retains_unresolved_order(self):
        self.pair(('.7', '.4'), unknown=True)
        self.store.set_meta('state', 'HALTING')
        self.engine.flatten_all('circuit_breaker')
        self.assertTrue(all(v.quantity == 0 for v in self.venues))
        self.assertEqual(self.store.meta('state'), 'HALTING')
        self.assertTrue(self.store.pairs(active=True))

    def test_r1_late_entry_fill_after_remote_flat_never_reopens_or_double_closes(self):
        pair = self.pair(('.7', '.4'), unknown=True)
        self.engine.close_pair(pair, 'manual')
        count = len([r for r in self.trace if r[0] == 'place'])
        entry = next(o for o in self.store.all_orders() if o['state'] == 'UNKNOWN')
        self.venues[0].unresolved = False
        self.venues[0].orders[entry['cid']] = '.7'
        self.engine.close_pair(pair, 'manual')
        self.assertEqual(pair['state'], 'CLOSED')
        self.assertEqual(list(map(dec, pair['remaining'])), [0, 0])
        self.assertEqual(len([r for r in self.trace if r[0] == 'place']), count)

    def test_r1_missing_position_snapshot_does_not_guess_a_close_quantity(self):
        pair = self.pair(unknown=True)
        self.accounts.snapshot.side_effect = ValueError('snapshot_missing')
        self.engine.close_pair(pair, 'manual')
        self.assertFalse([r for r in self.trace if r[0] == 'place'])
        self.assertEqual(pair['state'], 'UNKNOWN')

    def test_r1_regular_tracking_uses_unknown_recovery_path(self):
        pair = self.pair(('.7', '.4'), unknown=True)
        self.engine.track(pair)
        self.assertTrue(all(v.quantity == 0 for v in self.venues))
        self.assertTrue(any(r[0] == 'cancel' for r in self.trace))

    def test_r2_repair_rounds_down_then_minimizes_residual_with_both_steps(self):
        pair = self.pair(('1.006', '.99'), steps=('.01', '.001'))
        self.engine.balance(pair)
        placed = [r for r in self.trace if r[0] == 'place']
        self.assertEqual(dec(placed[0][2]), dec('.01'))
        for r in placed:
            self.assertEqual(dec(r[2]) % dec(('.01', '.001')[r[1]]), 0)
            self.assertTrue(r[3])
        self.assertEqual(*map(dec, pair['remaining']))

    def test_r2_substep_residual_reduces_other_leg_without_opening(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        self.engine.balance(pair)
        self.assertEqual(*map(dec, pair['remaining']))
        placed = [r for r in self.trace if r[0] == 'place']
        self.assertEqual({r[1] for r in placed}, {0, 1})
        self.assertTrue(all(r[3] for r in placed))

    def test_r2_untradable_dust_records_exposure_and_sends_nothing(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'), minimum='5')
        self.engine.balance(pair)
        self.assertFalse([r for r in self.trace if r[0] == 'place'])
        self.assertFalse(self.engine.can_open())
        events = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'UNHEDGED']
        self.assertEqual(dec(events[-1]['quantity']), dec('.007'))

    def test_r3_simulation_failure_never_blocks_actual_quantity_exit(self):
        pair = self.pair(('.2', '.2'))
        self.market.books = lambda instruments: [Book([['102', '.2']], [['102', '.2']], self.now[0]) for _ in instruments]
        with patch('tools.crossex_exec.engine.closing', side_effect=RuntimeError('simulation only')):
            self.engine.track(pair)
        self.assertEqual(pair['state'], 'CLOSED')
        self.assertEqual(pair['exit_reason'], 'converged')
        self.assertTrue(all(dec(r[2]) == dec('.2') for r in self.trace if r[0] == 'place'))

    def test_r4_catalog_failure_does_not_abort_startup_or_position_tracking(self):
        self.pair()
        with patch.object(self.market, 'bootstrap', side_effect=ValueError('catalog_down')):
            self.engine.startup(('BTC',))
            with patch.object(self.engine, 'track') as track:
                self.engine.tick()
                track.assert_called()

    def test_r4_blocked_catalog_runs_in_background_while_tracking(self):
        self.pair()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def bootstrap(bases):
            entered.set()
            release.wait(2)
            return self.market.mapping
        with patch.object(self.market, 'bootstrap', side_effect=bootstrap):
            started = time.monotonic()
            self.engine.startup(('BTC',))
            self.assertLess(time.monotonic()-started, .5)
            self.assertTrue(entered.wait(1))
            with patch.object(self.engine, 'track') as track:
                self.engine.tick()
                track.assert_called()
            release.set()

    def test_r4_failed_catalog_is_retried_after_backoff(self):
        self.engine.bases = ('BTC',)
        with patch.object(self.market, 'bootstrap', side_effect=[ValueError('down'), self.market.mapping]) as bootstrap:
            self.engine.refresh_reference()
            self.engine.refresh_reference()
            self.assertEqual(bootstrap.call_count, 1)
            self.now[0] += 30
            self.engine.refresh_reference()
            self.assertEqual(bootstrap.call_count, 2)

    def test_r5_halting_restart_retries_with_backoff_until_flat(self):
        self.pair()
        self.store.set_meta('state', 'HALTING')
        attempts = []
        original = self.engine.close_pair
        def fail_then_close(pair, reason):
            attempts.append(self.now[0])
            if len(attempts) <= 4:
                raise ValueError('temporary_close_failure')
            return original(pair, reason)
        with patch.object(self.engine, 'close_pair', side_effect=fail_then_close):
            self.engine.startup(('BTC',))
            self.assertEqual(self.store.meta('state'), 'HALTING')
            before = len(attempts)
            for _ in range(10):
                self.engine.tick()
            self.assertEqual(len(attempts), before)
            for _ in range(6):
                self.now[0] += 61
                self.engine.tick()
            self.assertGreaterEqual(len(attempts), 5)
            self.assertEqual(self.store.meta('state'), 'HALTED')

    def test_r5_service_never_exhausts_restart_budget(self):
        text = Path('tools/crossex_exec/deploy/crossex-exec.service').read_text(encoding='utf8')
        self.assertIn('StartLimitIntervalSec=0', text)
        self.assertNotIn('StartLimitBurst=3', text)
        self.assertIn('RestartSec=10', text)

    def test_r5_halting_startup_account_outage_stays_in_process(self):
        self.pair()
        self.store.set_meta('state', 'HALTING')
        self.accounts.validate_startup.side_effect = ValueError('account_outage')
        self.accounts.snapshot.side_effect = ValueError('account_outage')
        self.engine.startup(('BTC',))
        self.assertEqual(self.store.meta('state'), 'HALTING')
        self.accounts.snapshot.side_effect = self.snapshot
        self.now[0] += 61
        self.engine.tick()
        self.assertEqual(self.store.meta('state'), 'HALTED')

    def publish(self):
        self.engine.account_cache.publish(self.snapshot())

    def test_r7_account_admission_uses_cache_and_rejects_older_than_ten_seconds(self):
        self.publish()
        pair = self.pair()
        order = self.engine.new_intent(pair, 0, '.1', '100', False)
        self.accounts.snapshot.reset_mock()
        self.engine.limits.check_order(order)
        self.accounts.snapshot.assert_not_called()
        self.now[0] += 10.01
        with self.assertRaisesRegex(ValueError, 'account_snapshot_stale'):
            self.engine.limits.check_order(order)
        self.accounts.snapshot.assert_not_called()

    def open_intents(self, age=0):
        pair = self.pair()
        for o in self.store.all_orders():
            o.update(state='UNSENT', filled='0')
            self.store.save_order(o)
        pair['remaining'] = ['0', '0']
        pair['opening_fees'] = ['.0002', '.0002']
        for book in pair['last_books']:
            book['received_at'] -= age
        self.store.pair(pair)
        self.store.set_meta('actual_fees', dict(at=self.now[0], rates=self.accounts.fees_by_market))
        # Seed the new cache if available; the old path remains callable for its red test.
        if hasattr(self.engine, 'account_cache'):
            self.publish()
        return [self.engine.new_intent(pair, n, '.1', '100.03' if n == 0 else '101.96', False) for n in (0, 1)]

    def test_r7_twelve_second_books_send_neither_leg(self):
        orders = self.open_intents(age=12)
        result = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in result], ['UNSENT', 'UNSENT'])
        self.assertFalse([r for r in self.trace if r[0] == 'place'])

    def test_r7_net_spread_rechecked_before_either_worker_sends(self):
        orders = self.open_intents()
        pair = self.store.pairs()[0]
        pair['last_books'][1]['bids'] = [['100.01', '10']]
        self.store.pair(pair)
        result = self.engine.executor.execute_pair(orders)
        self.assertTrue(all(r['state'] == 'UNSENT' for r in result))
        self.assertFalse([r for r in self.trace if r[0] == 'place'])

    def test_r7_two_workers_reach_send_barrier_without_account_io(self):
        orders = self.open_intents()
        barrier = threading.Barrier(2)
        originals = [v.place for v in self.venues]
        def send(n, order, before_send):
            barrier.wait(1)
            return originals[n](order, before_send)
        self.accounts.snapshot.reset_mock()
        with (patch.object(self.venues[0], 'place', side_effect=lambda i,before_send:send(0,i,before_send)),
              patch.object(self.venues[1], 'place', side_effect=lambda i,before_send:send(1,i,before_send))):
            results = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in results], ['TERMINAL', 'TERMINAL'])
        self.accounts.snapshot.assert_not_called()

    def test_r7_after_limiter_wait_rechecks_book_age(self):
        orders = self.open_intents()
        barrier = threading.Barrier(2, action=lambda:self.now.__setitem__(0, self.now[0]+3))
        originals = [v.place for v in self.venues]
        def send(n, order, before_send):
            barrier.wait(1)
            return originals[n](order, before_send)
        with (patch.object(self.venues[0], 'place', side_effect=lambda i,before_send:send(0,i,before_send)),
              patch.object(self.venues[1], 'place', side_effect=lambda i,before_send:send(1,i,before_send))):
            results = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in results], ['UNSENT', 'UNSENT'])
        self.assertFalse([r for r in self.trace if r[0] == 'place'])

    def test_r7_slow_account_refresh_does_not_hold_admission_lock(self):
        self.publish()
        entered, release = threading.Event(), threading.Event()
        def snapshot():
            entered.set()
            release.wait(2)
            return self.snapshot()
        self.accounts.snapshot.side_effect = snapshot
        worker = threading.Thread(target=self.engine.refresh_accounts)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            pair = self.pair()
            order = self.engine.new_intent(pair, 0, '.1', '100', False)
            started = time.monotonic()
            self.engine.limits.check_order(order)
            self.assertLess(time.monotonic()-started, .2)
            self.now[0] += 11
            with self.assertRaises(ValueError):
                self.engine.limits.check_order(order)
        finally:
            release.set()
            worker.join(2)

    def test_r8_missing_fills_use_order_average_or_conservative_limit_for_exit(self):
        for fallback in ('average', 'limit'):
            with self.subTest(fallback=fallback):
                pair = self.pair()
                for o in self.store.all_orders():
                    o.update(vwap=None, accounting_pending=True)
                    if fallback == 'average':
                        o['average_price'] = '100'
                    else:
                        o.pop('average_price', None)
                    self.store.save_order(o)
                self.market.books = lambda instruments: [Book([['90', '10']], [['90', '10']], self.now[0]),
                                                         Book([['102', '10']], [['102', '10']], self.now[0])][:len(instruments)]
                self.engine.track(pair)
                self.assertEqual(pair['state'], 'CLOSED')
                self.assertEqual(pair['exit_reason'], 'loss_limit')
                # Separate journals prevent duplicate seed entries in the next case.
                with self.store.transaction():
                    self.store.db.execute('DELETE FROM orders')
                    self.store.db.execute('DELETE FROM pairs')

    def test_r9_daily_loss_latch_survives_recovery_restart_and_expires_utc(self):
        self.data['upnl'] = '-20'
        self.engine.reconcile()
        self.assertEqual(self.engine.risk, 'STOP')
        self.data['upnl'] = '0'
        self.engine.reconcile()
        self.assertEqual(self.engine.risk, 'STOP')
        second = Engine(self.store, self.market, self.accounts, live=True, clock=lambda:self.now[0])
        self.addCleanup(lambda:getattr(second, 'close', lambda:None)())
        second.reconcile()
        self.assertEqual(second.risk, 'STOP')
        self.now[0] += 86400
        second.reconcile()
        self.assertEqual(second.risk, 'RUN')

    def test_r10_fee_refresh_background_whitelist_and_tracking(self):
        self.pair()
        extra = instrument('bitget')
        extra.spec = replace(extra.spec, base='ETH')
        extra.market = replace(extra.market, raw_symbol='ETHUSDT')
        extra.symbol = 'ETHUSDT'
        self.market.mapping[extra.market.key] = extra
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        seen = []
        def fees(instruments):
            seen.extend(i.spec.base for i in instruments)
            entered.set()
            release.wait(2)
            return self.accounts.fees_by_market
        self.accounts.fees.side_effect = fees
        started = time.monotonic()
        self.engine.startup(('BTC',))
        self.assertLess(time.monotonic()-started, .5)
        self.assertTrue(entered.wait(1))
        self.assertEqual(set(seen), {'BTC'})
        with patch.object(self.engine, 'track') as track:
            self.engine.tick()
            track.assert_called()
        release.set()

    def test_r10_fresh_contract_fees_survive_other_contract_refresh_delay(self):
        orders = self.open_intents()
        self.accounts.fee_times_by_market = {k:self.now[0] for k in self.accounts.fees_by_market}
        self.store.set_meta('actual_fees', {'at':self.now[0]-3601, 'rates':self.accounts.fees_by_market})
        results = self.engine.executor.execute_pair(orders)
        self.assertEqual([r['state'] for r in results], ['TERMINAL', 'TERMINAL'])

    def test_r10_empty_whitelist_reads_no_contract_fees(self):
        self.engine.bases = ()
        self.engine.refresh_reference()
        self.accounts.fees.assert_not_called()
        self.assertEqual(self.accounts.fees_by_market, {})

    def test_r11_first_executor_stop_and_new_reason_reach_journal_once(self):
        pair = self.pair(unknown=True)
        order = self.store.all_orders()[0]
        self.engine.executor.confirm(order)
        self.engine.alert('unknown_order')
        self.engine.alert('unknown_order')
        self.assertEqual(self.sink.call_args_list.count(unittest.mock.call('unknown_order')), 1)
        self.engine.alert('fills_pending')
        self.engine.alert('unknown_order')
        self.assertEqual(self.sink.call_args_list.count(unittest.mock.call('unknown_order')), 2)

    def test_r11_same_reason_after_clearing_is_a_new_stop(self):
        self.engine.alert('daily_loss')
        self.store.set_meta('stop_reason', None)
        self.engine.alert('daily_loss')
        self.assertEqual(self.sink.call_count, 2)


class Round3PolicyTests(unittest.TestCase):
    def test_r6_final_sell_limit_minimum_bumps_one_common_step(self):
        instruments = [instrument(), instrument('bitget')]
        instruments[1].spec = replace(instruments[1].spec, min_notional='99.94')
        books = [Book([['99', '10']], [['100', '10']], 1000), Book([['102', '10']], [['103', '10']], 1000)]
        funding = [dict(rate='0', next=5000, interval=28800, captured_at=1000, history_ok=True, max_abs_24h='0')]*2
        result = review(instruments, books, ['.0002']*2, funding, ['5000000']*2, 1000)
        self.assertEqual(dec(result['quantity']), dec('.981'))
        self.assertGreaterEqual(dec(result['quantity'])*dec(result['limits'][1]), dec('99.94'))
        instruments[1].spec = replace(instruments[1].spec, max_qty='.980')
        with self.assertRaises(ValueError):
            review(instruments, books, ['.0002']*2, funding, ['5000000']*2, 1000)

    def test_r6_min_quantity_bumps_once_and_still_invalid_minimum_rejects(self):
        instruments = [instrument(), instrument('bitget')]
        books = [Book([['99', '10']], [['100', '10']], 1000), Book([['102', '10']], [['103', '10']], 1000)]
        funding = [dict(rate='0', next=5000, interval=28800, captured_at=1000, history_ok=True, max_abs_24h='0')]*2
        instruments[0].spec = replace(instruments[0].spec, min_qty='.981')
        self.assertEqual(dec(review(instruments, books, ['.0002']*2, funding, ['5000000']*2, 1000)['quantity']), dec('.981'))
        instruments[0].spec = replace(instruments[0].spec, min_qty='.982')
        with self.assertRaises(ValueError):
            review(instruments, books, ['.0002']*2, funding, ['5000000']*2, 1000)

    def test_r10_failed_contract_revokes_only_its_fee_and_timestamp(self):
        cross, bitget = Mock(), Mock()
        a, b = instrument(), instrument('bitget')
        cross.fees.return_value = [dict(exchange_type='BINANCE', future_taker_fee='.0002')]
        cross.leverage.return_value = {}
        bitget.fee.return_value = '.0006'
        accounts = Accounts(cross, bitget, clock=lambda:1000)
        accounts.fees([a, b])
        bitget.fee.side_effect = ValueError('down')
        rates = accounts.fees([a, b])
        self.assertEqual(rates, {a.market.key:'.0002'})
        self.assertEqual(accounts.fee_times_by_market, {a.market.key:1000})
        cross.fees.return_value = []
        bitget.fee.side_effect = None
        self.assertEqual(accounts.fees([a, b]), {b.market.key:'.0006'})

    def test_r8_checked_status_retains_venue_average_fields(self):
        for gate, field in ((True, 'avg_price'), (False, 'avgPrice')):
            i = dict(cid='x', oid='42', quantity='1', instrument=instrument().dump())
            raw = dict(symbol=i['instrument']['symbol'], **{field:'101.2'})
            raw.update(dict(order_id='42', text='x', executed_qty='1', state='FILLED') if gate else
                       dict(orderId='42', clientOid='x', cumExecQty='1', orderStatus='filled', category='USDT-FUTURES'))
            self.assertEqual(checked_status(raw, i, gate=gate)['average_price'], '101.2')

    def test_r4_one_catalog_failure_keeps_healthy_venue_and_retries(self):
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp)/'db') as store:
            market = MarketData(store, client=Mock())
            self.addCleanup(market.queue.close)
            self.addCleanup(market.pool.shutdown, wait=False)
            failed = [True]
            def fetch(ex, url, **kwargs):
                if ex == 'binance' and failed[0]:
                    raise ValueError('catalog_down')
                return {'symbols':[{'exchange':ex}]}
            def native(ex, row, rules):
                return instrument(ex)
            with (patch('tools.crossex_exec.market.CATALOGS', {'binance':'catalog-a', 'bitget':'catalog-b'}),
                  patch('tools.crossex_exec.market.native_rows', side_effect=lambda ex,data:data['symbols']),
                  patch('tools.crossex_exec.market.native_instrument', side_effect=native),
                  patch.object(market, 'fetch', side_effect=fetch)):
                market.bootstrap(('BTC',))
                self.assertEqual(set(i.market.exchange for i in market.mapping.values()), {'bitget'})
                failed[0] = False
                market.bootstrap(('BTC',))
                self.assertEqual(set(i.market.exchange for i in market.mapping.values()), {'binance', 'bitget'})

