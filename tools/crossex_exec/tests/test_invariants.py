"""Deterministic fault injection against the real Engine and Executor (no I/O)."""
import copy
import json
import random
import sqlite3
import threading
import unittest
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from tools.fill_sim.core import Book, day, dec
from tools.crossex_exec.accounts import FeeSnapshot
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.execution import NONTERMINAL
from tools.crossex_exec.transport import APIError
from test_engine import trigger
from test_exec import instrument, setUpModule, tearDownModule


SEEDS = (20261111, 20261112, 20261113)
ROUNDS = 2400
RECOVERY_BOUND = 1
INVARIANTS = ('1a', '1b', '1c', '1d', '2', '3', '4', '5', '6', '7')
FAULTS = ('partial', 'zero', 'timeout_ack', 'timeout_unknown', 'detail', 'positions',
          'database', 'restart', 'midnight', 'stale', 'spread', 'adverse', 'equity', 'account')


class FakeDatabase:
    """A durable-copy model; failed commits leave no durable mutation."""
    def __init__(self, durable=None):
        self.data = copy.deepcopy(durable) if durable else dict(meta={}, orders={}, pairs={}, events=[], triggers={})
        self.lock = threading.RLock()
        self.fail_order = False
        self.failures = 0

    def meta(self, key, default=None):
        with self.lock:
            return copy.deepcopy(self.data['meta'].get(key, default))

    def set_meta(self, key, value):
        with self.lock:
            self.data['meta'][key] = copy.deepcopy(value)

    def order(self, cid):
        with self.lock:
            return copy.deepcopy(self.data['orders'].get(cid))

    def all_orders(self):
        with self.lock:
            return copy.deepcopy(list(self.data['orders'].values()))

    def save_order(self, order, kind=None):
        with self.lock:
            if self.fail_order and order['leg'] == 0:
                self.fail_order = False
                self.failures += 1
                raise sqlite3.OperationalError('injected commit failure')
            self.data['orders'][order['cid']] = copy.deepcopy(order)
            if kind:
                self.event(kind, order, order['cid'])

    def pair(self, pair):
        self.data['pairs'][pair['id']] = copy.deepcopy(pair)

    def pairs(self, active=False):
        values = copy.deepcopy(list(self.data['pairs'].values()))
        return [p for p in values if p['state'] not in ('CLOSED', 'SHADOW', 'SKIPPED')] if active else values

    def event(self, kind, value=None, cid=None):
        with self.lock:
            self.data['events'].append(dict(kind=kind, value=json.dumps(value or {}), cid=cid))

    def events(self):
        return copy.deepcopy(self.data['events'])

    def claim(self, identity, value):
        if identity in self.data['triggers']:
            return False
        self.data['triggers'][identity] = copy.deepcopy(value)
        return True

    def assert_startable(self):
        if self.meta('state') == 'HALTED':
            raise ValueError('HALTED_manual_clear_required')


class SimVenue:
    def __init__(self, world, leg):
        self.world, self.leg = world, leg
        self.quantity = dec(0)
        self.orders = {}
        self.realized = dec(0)

    def place(self, order, before_send):
        before_send()
        w = self.world
        with w.lock:
            before = [v.quantity for v in w.venues]
            qty = dec(order['quantity'])
            fraction = w.fractions[self.leg]
            filled = (qty*fraction/dec('.001')).to_integral_value(rounding='ROUND_FLOOR')*dec('.001')
            if order['reduce_only']:
                filled = min(self.quantity, filled)
            self.quantity += filled*(-1 if order['reduce_only'] else 1)
            price = w.prices[self.leg]
            if order['reduce_only']:
                self.realized += filled*(price-dec((100, 102)[self.leg]))*(1 if self.leg == 0 else -1)
            self.orders[order['cid']] = dict(filled=str(filled), price=str(price), at=w.now,
                                           unknown=w.fault == 'timeout_unknown', sent_step=w.step)
            row = dict(order=copy.deepcopy(order), before=before, after=[v.quantity for v in w.venues],
                       locked=bool(w.store.meta('stop_locks', {})), live=w.engine.live,
                       batch=w.send_batches.get(order['cid']), admission=w.admissions.get(order['cid']),
                       total=sum(v.quantity*w.prices[n] for n,v in enumerate(w.venues)))
            w.sends.append(row)
            w.coverage['reduce' if order['reduce_only'] else 'open'] += 1
            w.coverage['partial_fill' if 0 < filled < qty else 'zero_fill' if filled == 0 else 'full_fill'] += 1
            if w.fault in ('timeout_ack', 'timeout_unknown'):
                w.coverage[w.fault] += 1
                raise APIError('unknown')
            return order['cid']

    def detail(self, order, deadline=None):
        w = self.world
        w.coverage['query'] += 1
        if w.fault == 'detail':
            w.coverage['query_failures'] += 1
            raise APIError('unknown')
        row = self.orders[order['cid']]
        if row['unknown'] and w.step <= row['sent_step']:
            raise APIError('unknown')
        return dict(oid=order['cid'], filled=row['filled'], terminal=True, rejected=False,
                    venue_state='FILLED', average_price=row['price'])

    def cancel(self, order):
        self.world.cancels += 1

    def fills(self, order):
        row = self.orders[order['cid']]
        return dict(vwap=row['price'], fee='0', fill_ms=row['at']*1000,
                    trade_ids=[order['cid']], accounting_pending=False)


class World:
    def __init__(self, live, coverage):
        self.lock = threading.RLock()
        self.coverage = coverage
        self.live = live
        self.now = 1794355200.0
        self.step = 0
        self.fault = 'none'
        self.fractions = [dec(1), dec(1)]
        self.prices = [dec(100), dec(102)]
        self.sends, self.cancels = [], 0
        self.batches, self.recoveries = [], []
        self.send_batches, self.admissions = {}, {}
        self.venues = [SimVenue(self, n) for n in (0, 1)]
        self.instruments = [instrument(), instrument('bitget')]
        for i in self.instruments:
            i.spec = replace(i.spec, min_notional='0')
        self.store = FakeDatabase()
        self.market = SimpleNamespace(mapping={i.market.key:i for i in self.instruments}, books=self.books,
            funding=lambda instruments:[dict(rate='0', interval=28800, next=self.now+10000,
                captured_at=self.now, history_ok=True, max_abs_24h='0') for _ in instruments],
            turnovers=lambda instruments:['10000000']*len(instruments))
        self.accounts = SimpleNamespace(cross=self.venues[0], bitget=self.venues[1], snapshot=self.snapshot,
            validate_startup=self.snapshot, position_snapshot=self.positions,
            check_instruments=lambda instruments:None, fee_snapshots={})
        self.engine = self.make_engine()
        self.engine.limits.initialize(self.snapshot())
        self.store.set_meta('daily_upnl', dict(day=day(self.now), upnl='0', positions={}))
        self.store.set_meta('equity_baseline', '31000')
        self.references()
        self.engine.reconcile()

    def make_engine(self):
        engine = Engine(self.store, self.market, self.accounts, live=self.live, clock=lambda:self.now)
        engine.executor.confirm_timeout = .001
        engine.executor.poll_interval = .0002
        # No background jobs, DNS, account transports or real wall-clock inputs.
        engine.start_background = lambda bases:None
        execute_pair = engine.executor.execute_pair
        check_pair = engine.executor.check_pair
        resolve = engine.resolve_exposure

        def observe_pair(intents):
            # Classify the actual dispatch, never a pair's sticky exit_reason.
            batch = dict(before=[v.quantity for v in self.venues], step=self.step,
                         paired_close=len(intents) == 2 and {i['leg'] for i in intents} == {0, 1}
                         and all(i['reduce_only'] for i in intents), first_send=len(self.sends))
            for intent in intents:
                self.send_batches[intent['cid']] = batch
            self.batches.append(batch)
            try:
                return execute_pair(intents)
            finally:
                batch.update(after=[v.quantity for v in self.venues], last_send=len(self.sends))

        def observe_admission(intents):
            result = check_pair(intents)
            # The existing shared gate admits both entry legs before either sends.
            # An already-admitted peer is not a second, newly admitted position.
            admission = dict(before=[v.quantity for v in self.venues],
                             locked=bool(engine.stops), cids={i['cid'] for i in intents})
            for intent in intents:
                self.admissions[intent['cid']] = admission
            return result

        def observe_recovery(pair):
            self.recoveries.append(dict(step=self.step, at=self.now, pair_id=pair['id'], sends=len(self.sends)))
            return resolve(pair)

        engine.executor.execute_pair = observe_pair
        engine.executor.check_pair = observe_admission
        engine.resolve_exposure = observe_recovery
        return engine

    def books(self, instruments):
        result = []
        for i in instruments:
            leg = 1 if i.market.exchange == 'bitget' else 0
            price = self.prices[leg]
            if self.fault == 'spread':
                price = dec(100)
            result.append(Book([[str(price), '20']], [[str(price), '20']],
                               self.now-100 if self.fault == 'stale' else self.now))
        return result

    def positions(self):
        self.coverage['position_read'] += 1
        if self.fault == 'positions':
            self.coverage['position_failures'] += 1
            raise ValueError('injected position failure')
        positions, floating = {}, {}
        for n,v in enumerate(self.venues):
            if v.quantity:
                key = '|'.join(('crossex' if n == 0 else 'bitget', self.instruments[n].symbol, 'long' if n == 0 else 'short'))
                positions[key] = v.quantity
                floating[key] = str(v.quantity*(self.prices[n]-dec((100, 102)[n]))*(1 if n == 0 else -1))
        return dict(positions=positions, upnl=str(sum(map(dec, floating.values()), dec(0))),
                    upnl_by_position=floating, captured_at=self.now)

    def snapshot(self):
        if self.fault == 'account':
            self.coverage['account_failures'] += 1
            raise ValueError('injected account failure')
        result = self.positions()
        equities = {k:str(dec(base)+v.realized+v.quantity*(self.prices[n]-dec((100, 102)[n]))*(1 if n == 0 else -1))
                    for n,(k,base,v) in enumerate(zip(('crossex','bitget'), (1000,30000), self.venues))}
        if self.fault == 'equity':
            equities['crossex'] = '800'
        return dict(result, equity_by_venue=equities, equity=str(sum(map(dec, equities.values()), dec(0))),
                    margins=['4','4'], liabilities=[], notionals={})

    def references(self):
        self.store.set_meta('mapping', dict(at=self.now))
        self.accounts.fee_snapshots = {i.market.key:FeeSnapshot('.0002',self.now) for i in self.instruments}
        try:
            self.engine.account_cache.publish(self.snapshot(),self.now)
        except ValueError:
            pass

    def restart(self):
        self.engine.close()
        self.store = FakeDatabase(self.store.data)
        self.engine = self.make_engine()
        self.coverage['restarts'] += 1
        if self.store.meta('state') != 'HALTED':
            self.engine.startup()

    def open(self):
        payload = trigger('sim-'+str(self.step))
        payload['opened_at'] = self.now
        self.engine.ingest(payload)


class InvariantSimulationTests(unittest.TestCase):
    def test_fault_invariants_seed_20261111_2400_rounds(self):
        self.run_seed(SEEDS[0])

    def test_fault_invariants_seed_20261112_2400_rounds(self):
        self.run_seed(SEEDS[1])

    def test_fault_invariants_seed_20261113_2400_rounds(self):
        self.run_seed(SEEDS[2])

    def assert_reduction(self, sent, exercised):
        before, after, batch = sent['before'], sent['after'], sent['batch']
        if batch and batch['paired_close']:
            self.assertLessEqual(abs(after[0]-after[1]), max(batch['before']), 'I1b transient bound')
            self.assertTrue(all(0 <= a <= b for a, b in zip(after, batch['before'])), 'I1b crossed zero')
            exercised['1b'] += 1
        else:
            self.assertLessEqual(abs(after[0]-after[1]), abs(before[0]-before[1]), 'I1a single reduction')
            exercised['1a'] += 1
        self.assertLessEqual(dec(sent['order']['quantity']), before[sent['order']['leg']], 'I3 oversized close')
        self.assertTrue(all(0 <= a <= b for a,b in zip(after,before)), 'I3 crossed zero')
        exercised['3'] += 1

    def run_seed(self, seed):
        rng = random.Random(seed)
        coverage = Counter()
        sequence = []
        checks = Counter()
        exercised = Counter()
        serial = iter(range(1, 1000000))
        world = None
        try:
            with patch('tools.crossex_exec.engine.uuid.uuid4', side_effect=lambda:SimpleNamespace(hex=f'{next(serial):029x}000')):
                for index in range(ROUNDS):
                    episode_step = index % 24
                    if episode_step == 0:
                        if world:
                            world.engine.close()
                        world = World(live=(index//24)%4 != 0, coverage=coverage)
                    w = world
                    w.step = index
                    fault = 'none' if episode_step in (1, 21, 22, 23) else rng.choice(FAULTS)
                    if episode_step == 0:
                        fault = ('none','partial','zero','timeout_ack','timeout_unknown','database','stale','spread')[(index//24)%8]
                    w.fault = fault
                    w.now += 61 if w.store.meta('state') == 'HALTING' else 3
                    if fault == 'midnight':
                        w.now = (int(w.now)//86400+1)*86400+1
                    w.fractions = [rng.choice([dec('.25'), dec('.5'), dec(1)]) for _ in (0,1)] if fault == 'partial' else [dec(1),dec(1)]
                    if fault == 'zero':
                        w.fractions[rng.randrange(2)] = dec(0)
                    if fault == 'adverse':
                        w.prices = [dec(1),dec(300)]
                    elif fault in ('partial','zero') and episode_step > 0:
                        # Exercise asymmetric close fills, not only partial entries.
                        w.prices = [dec(99),dec(103)]
                    if fault == 'database':
                        w.store.fail_order = True
                    sequence.append(dict(step=index, fault=fault, fractions=list(map(str,w.fractions)),
                                         prices=list(map(str,w.prices)), live=w.live))
                    coverage['scheduled_'+fault] += 1
                    first_send, first_cancel = len(w.sends), w.cancels
                    first_batch, first_recovery = len(w.batches), len(w.recoveries)
                    outstanding = [p for p in w.store.pairs(active=True) if p.get('exit_reason')
                                   and w.venues[0].quantity != w.venues[1].quantity]
                    retry_due = any(w.now >= p.get('next_close', 0) for p in outstanding)
                    if fault == 'restart':
                        w.restart()
                    w.references()
                    if w.store.meta('state') != 'HALTED':
                        if episode_step >= 21:
                            w.store.set_meta('state', 'HALTING')
                        elif episode_step == 0 or fault in ('partial','zero','timeout_ack','timeout_unknown','database','stale','spread'):
                            try:
                                w.open()
                            except sqlite3.OperationalError:
                                # An INTENT commit failure is allowed to reject admission.
                                coverage['intent_write_abort'] += 1
                        before_orders = w.engine.executor.orders()
                        needed = any(o['state'] in NONTERMINAL for o in before_orders) or w.venues[0].quantity != w.venues[1].quantity
                        events_before = len(w.store.events())
                        queries_before = coverage['query']
                        halt_attempts = w.engine.halt_attempts
                        try:
                            w.engine.tick()
                        except sqlite3.OperationalError:
                            # Match the service loop: a pre-send journal failure
                            # latches admission, then the next tick retries tracking.
                            w.engine.alert('cycle_failed_closed')
                            coverage['intent_write_abort'] += 1
                        # I6: the very next coordinator tick must enter recovery.
                        if needed:
                            entered = any(e['kind'] in ('RECOVERY_ATTEMPT','POSITION_RECOVERY') for e in w.store.events()[events_before:])
                            self.assertTrue(entered or coverage['query'] > queries_before, 'I6 recovery bound N=1')
                            coverage['recovery_obligation'] += 1
                            exercised['6'] += 1
                        # I7: HALTING either proves flat or schedules another finite retry.
                        if w.store.meta('state') == 'HALTING':
                            self.assertGreater(w.engine.next_halt, w.now, 'I7 missing retry deadline')
                            self.assertGreater(w.engine.halt_attempts, halt_attempts, 'I7 no retry progress')
                            coverage['halting_retry'] += 1
                            exercised['7'] += 1
                    for sent in w.sends[first_send:]:
                        order, before, after = sent['order'], sent['before'], sent['after']
                        if order['reduce_only']:
                            self.assert_reduction(sent, exercised)
                        else:
                            self.assertFalse(sent['locked'], 'I2 opening while locked')
                            self.assertIsNotNone(sent['admission'], 'I1d missing shared admission')
                            self.assertEqual(*sent['admission']['before'], 'I1d admitting entry with exposure')
                            self.assertFalse(sent['admission']['locked'], 'I2 admission while locked')
                            self.assertLessEqual(dec(order['quantity'])*dec(order['price']), 150, 'I4 leg cap')
                            self.assertLessEqual(sent['total'], 600, 'I4 total cap')
                            for invariant in ('1d', '2', '4'):
                                exercised[invariant] += 1
                    # I1c: inspect real recovery calls and durable deadlines, not
                    # just a RECOVERY_ATTEMPT event that could be a no-op.
                    for batch in w.batches[first_batch:]:
                        if batch['paired_close'] and batch['after'][0] != batch['after'][1]:
                            self.assertTrue(any(r['sends'] >= batch['last_send'] and r['step']-batch['step'] <= RECOVERY_BOUND
                                                for r in w.recoveries[first_recovery:]), 'I1c no recovery after close')
                            exercised['1c'] += 1
                    if retry_due:
                        self.assertGreater(len(w.recoveries), first_recovery, 'I1c due retry made no position recovery attempt')
                        exercised['1c'] += 1
                    exposure = abs(w.venues[0].quantity-w.venues[1].quantity)
                    if exposure:
                        self.assertFalse(w.engine.can_open(), 'I1d opening gate permits outstanding exposure')
                        exercised['1d'] += 1
                        for pair in w.store.pairs(active=True):
                            if not pair.get('exit_reason'):
                                continue
                            step = min(dec(i['spec']['qty_step']) for i in pair['instruments'])
                            if exposure > step:
                                self.assertIn(pair['id'], w.store.meta('stop_locks', {}).get('unhedged_position', []),
                                              'I1c residual exposure missing unhedged lock')
                                alerts = [json.loads(e['value']) for e in w.store.events() if e['kind'] == 'ALERT']
                                self.assertTrue(any(a.get('reason') == 'unhedged_position' and a.get('pair_id') == pair['id']
                                                    for a in alerts), 'I1c residual exposure missing alert')
                            self.assertGreater(pair.get('next_close', 0), w.now, 'I1c no durable retry deadline')
                            self.assertLessEqual(pair['next_close']-w.now, 60, 'I1c retry exceeds bounded backoff')
                            exercised['1c'] += 1
                    if not w.live:
                        self.assertEqual(len(w.sends)-first_send, 0, 'I5 shadow placed')
                        self.assertEqual(w.cancels-first_cancel, 0, 'I5 shadow cancelled')
                        exercised['5'] += 1
                    if w.store.meta('state') == 'HALTED':
                        self.assertTrue(all(v.quantity == 0 for v in w.venues), 'I7 halted with remote position')
                        self.assertFalse(any(o['state'] in NONTERMINAL for o in w.engine.executor.orders()), 'I7 halted with unknown')
                        coverage['halted'] += 1
                        exercised['7'] += 1
                    coverage['db_failures'] += w.store.failures
                    w.store.failures = 0
                    for invariant in INVARIANTS:
                        checks[invariant] += 1
            self.assertEqual(set(checks.values()), {ROUNDS})
            for invariant in INVARIANTS:
                self.assertGreater(exercised[invariant], 0, 'vacuous invariant: '+invariant)
            for key in (*(f'scheduled_{f}' for f in FAULTS), 'open','reduce','partial_fill','zero_fill','timeout_ack','timeout_unknown',
                        'restarts','db_failures','recovery_obligation','halting_retry','halted',
                        'query_failures','position_failures','account_failures'):
                self.assertGreater(coverage[key], 0, 'vacuous fault coverage: '+key)
            print(f'INVARIANTS seed={seed} rounds={ROUNDS} N={RECOVERY_BOUND} checks={dict(checks)} '
                  f'exercised={dict(sorted(exercised.items()))} coverage={dict(sorted(coverage.items()))}', flush=True)
        except Exception as error:
            self.fail(f'seed={seed} checked_rounds={len(sequence)} error={error}\nsteps={json.dumps(sequence)}')
        finally:
            if world:
                world.engine.close()
