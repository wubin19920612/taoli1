"""Deterministic fault injection against the real Engine and Executor (no I/O)."""
import copy
import json
import random
import sqlite3
import threading
import unittest
from collections import Counter
from contextlib import contextmanager
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
from baseline_fixtures import seed_baseline


SEEDS = (20261111, 20261112, 20261113)
RANDOM_ROUNDS = 2400
ROUNDS = RANDOM_ROUNDS+168
RECOVERY_BOUND = 1
INVARIANTS = ('1a', '1b', '1c', '1d', '2', '3', '4', '5', '6', '7')
FAULTS = ('partial', 'zero', 'timeout_ack', 'timeout_unknown', 'detail', 'positions',
          'database', 'restart', 'midnight', 'stale', 'spread', 'adverse', 'equity', 'account')
OPENING_FAULTS = ('baseline_not_flat', 'not_single_venue_outlier', 'too_few_venues',
                  'book_skew', 'book_stale', 'funding_diff')


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

    @contextmanager
    def transaction(self):
        with self.lock:
            before = copy.deepcopy(self.data)
            try:
                yield
            except BaseException:
                self.data = before
                raise

    def baseline_state(self, key):
        return self.meta('baseline_status', {}).get(key, {})

    def save_baseline_state(self, state):
        self.data['meta'].setdefault('baseline_status', {})[state['key']] = copy.deepcopy(state)

    def prune_baseline_states(self, bases):
        self.data['meta']['baseline_status'] = {k:v for k,v in self.data['meta'].get('baseline_status', {}).items()
                                              if k.split('|')[0] in bases}

    def latest_baseline(self, base):
        snapshot = self.meta('baseline_quotes', {})
        return dict(at=snapshot.get('at'), mids={ex:rows[base] for ex,rows in snapshot.get('mids', {}).items() if base in rows})

    def set_meta(self, key, value):
        with self.lock:
            self.data['meta'][key] = copy.deepcopy(value)

    def order(self, cid):
        with self.lock:
            return copy.deepcopy(self.data['orders'].get(cid))

    def all_orders(self):
        with self.lock:
            return copy.deepcopy(list(self.data['orders'].values()))

    def iter_orders(self, *, pair_id=None, states=None, exclude_states=(), sent_since=None,
                    accounting_pending=None, reduce_only=None, page_size=128):
        for row in self.all_orders():
            if (pair_id is not None and row.get('pair_id') != pair_id
                    or states is not None and row.get('state') not in states
                    or row.get('state') in exclude_states
                    or sent_since is not None and row.get('sent_ms', 0) < sent_since
                    or accounting_pending is not None and bool(row.get('accounting_pending')) != accounting_pending
                    or reduce_only is not None and bool(row.get('reduce_only')) != reduce_only):
                continue
            yield row

    def save_orders(self, orders, kind=None):
        with self.lock:
            before = copy.deepcopy(self.data)
            try:
                for order in orders:
                    self.save_order(order, kind)
            except BaseException:
                self.data = before
                raise

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
        from tools.crossex_exec.baseline import cooldown_for
        cooldown = cooldown_for(pair)
        if cooldown:
            key, until = cooldown
            rows = self.meta('pair_cooldowns', {})
            rows[key] = max(rows.get(key, 0), until)
            self.set_meta('pair_cooldowns', rows)
        self.data['pairs'][pair['id']] = copy.deepcopy(pair)

    def add_baseline_samples(self, rows, now):
        samples = self.data.setdefault('baseline_samples', {})
        for key, bucket, at, value in rows:
            samples.setdefault((key,bucket), (at,value))
        self.data['baseline_samples'] = {k:v for k,v in samples.items() if v[0]>=now-86400}

    def baseline_samples(self, key, since, until):
        return sorted(v for (k,b),v in self.data.get('baseline_samples', {}).items()
                      if k==key and since<=v[0]<=until)

    def pairs(self, active=False):
        values = copy.deepcopy(list(self.data['pairs'].values()))
        return [p for p in values if p['state'] not in ('CLOSED', 'SHADOW', 'SKIPPED')] if active else values

    def get_pair(self, pair_id):
        return copy.deepcopy(self.data['pairs'].get(pair_id))

    def iter_pairs(self, active=False, *, exclude_states=(), page_size=64):
        yield from (p for p in self.pairs(active) if p['state'] not in exclude_states)

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

    def skip(self, identity, reason, **details):
        from tools.crossex_exec.admission import SKIP_REASONS
        with self.lock:
            trigger = self.data['triggers'].setdefault(identity, {})
            if trigger.get('skip_reason'):
                return
            trigger['skip_reason'] = reason
            if reason in SKIP_REASONS:
                counts = self.meta('opening_skip_counts', {})
                counts[reason] = counts.get(reason, 0)+1
                self.set_meta('opening_skip_counts', counts)
            self.event('SKIP', dict(id=identity, reason=reason, **details))

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
            multiplier = w.multipliers[self.leg]
            qty = dec(order['quantity'])*multiplier
            fraction = w.fractions[self.leg]
            filled = (qty*fraction/dec('.001')).to_integral_value(rounding='ROUND_FLOOR')*dec('.001')
            if order['reduce_only']:
                filled = min(self.quantity, filled)
            self.quantity += filled*(-1 if order['reduce_only'] else 1)
            price = w.prices[self.leg]
            if order['reduce_only']:
                self.realized += filled*(price-w.entry_prices[self.leg])*(1 if self.leg == 0 else -1)
            self.orders[order['cid']] = dict(filled=str(filled/multiplier), price=str(price*multiplier), at=w.now,
                                           unknown=w.fault == 'timeout_unknown', sent_step=w.step)
            row = dict(order=copy.deepcopy(order), before=before, after=[v.quantity for v in w.venues], filled=filled,
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
    def __init__(self, live, coverage, opening_fault='none', mapping=None):
        self.lock = threading.RLock()
        self.coverage = coverage
        self.live = live
        self.now = 1794355200.0
        self.step = 0
        self.fault = 'none'
        self.opening_fault = opening_fault
        self.fractions = [dec(1), dec(1)]
        self.prices = [dec(100), dec(102)]
        self.multipliers = [dec(1),dec(1)]
        if mapping is not None:
            self.multipliers[mapping] = dec(1000)
            self.prices = [dec(100),dec('100.5')]
        self.entry_prices = list(self.prices)
        self.sends, self.cancels = [], 0
        self.batches, self.recoveries = [], []
        self.send_batches, self.admissions = {}, {}
        self.close_obligations = set()
        self.venues = [SimVenue(self, n) for n in (0, 1)]
        self.instruments = [instrument(), instrument('bitget')]
        for n,i in enumerate(self.instruments):
            factor = self.multipliers[n]
            raw_base = '1000BTC' if factor!=1 else 'BTC'
            i.market = replace(i.market,raw_symbol=raw_base+'USDT')
            i.symbol = raw_base+'USDT' if n==1 else 'BINANCE_FUTURE_'+raw_base+'_USDT'
            i.tick = str(dec(i.tick)*factor)
            i.spec = replace(i.spec, base=raw_base, qty_step=str(dec('.001')/factor), min_qty=str(dec(i.spec.min_qty)/factor), min_notional='0')
        self.store = FakeDatabase()
        # Normal history is nonzero and flat (MAD=0). The negative episode uses
        # real 15bp samples, below the structural ceiling but above v1's 10bp.
        seed_baseline(self.store,self.now,'15' if opening_fault == 'baseline_not_flat' else '5')
        self.market = SimpleNamespace(mapping={i.market.key:i for i in self.instruments}, books=self.books,
            funding=self.funding,
            open_interests=lambda instruments,books:[dict(market_key=i.market.key,usd='10000000',at=self.now,
                unit='base',unit_verified=True,source='fixture') for i in instruments],
            turnovers=lambda instruments:['999999' if self.opening_fault=='volume_too_low' else '10000000']*len(instruments))
        if opening_fault=='size_too_small':
            for i in self.instruments:
                i.spec=replace(i.spec,min_notional='51')
        self.accounts = SimpleNamespace(cross=self.venues[0], bitget=self.venues[1], snapshot=self.snapshot,
            validate_startup=self.snapshot, position_snapshot=self.positions,
            check_instruments=lambda instruments:None, fee_snapshots={})
        self.engine = self.make_engine()
        self.engine.limits.initialize(self.snapshot())
        self.store.set_meta('daily_upnl', dict(day=day(self.now), upnl='0', positions={}))
        self.store.set_meta('equity_baseline', '31000')
        self.references()
        if mapping is not None:
            from tools.crossex_exec.universe import Universe
            universe=Universe(self.store,lambda:self.now)
            universe.discover()
            universe.decide_mapping(universe.mappings()[0]['id'],'confirmed')
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
            if batch['paired_close']:
                self.close_obligations.add(intents[0]['pair_id'])
            try:
                return execute_pair(intents)
            finally:
                batch.update(after=[v.quantity for v in self.venues], last_send=len(self.sends))
                if not any(batch['after']):
                    for intent in intents:
                        self.close_obligations.discard(intent['pair_id'])

        def observe_admission(intents):
            result = check_pair(intents)
            # The existing shared gate admits both entry legs before either sends.
            # An already-admitted peer is not a second, newly admitted position.
            admission = dict(before=[v.quantity for v in self.venues],
                             locked=bool(engine.stops), cids={i['cid'] for i in intents},
                             baseline=self.store.meta('baseline_status')['BTC|binance|bitget'])
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
            received_at = self.now-100 if self.fault == 'stale' or self.opening_fault == 'book_stale' else self.now
            exchange_at = received_at-(.5 if self.opening_fault == 'book_skew' and leg == 1 else 0)
            factor=self.multipliers[leg]
            result.append(Book([[str(price*factor), str(dec(20)/factor)]], [[str(price*factor), str(dec(20)/factor)]],
                               received_at, exchange_at))
        return result

    def funding(self, instruments):
        # 0.6bp/h difference exceeds v1; each leg remains below 0.05%/8h.
        return [dict(rate='.00048' if self.opening_fault == 'funding_diff' and i.market.exchange == 'binance' else '0',
                     interval=28800, next=self.now+10000, captured_at=self.now,
                     history_ok=True, max_abs_24h='.00048') for i in instruments]

    def positions(self):
        self.coverage['position_read'] += 1
        if self.fault == 'positions':
            self.coverage['position_failures'] += 1
            raise ValueError('injected position failure')
        positions, floating = {}, {}
        for n,v in enumerate(self.venues):
            if v.quantity:
                key = '|'.join(('crossex' if n == 0 else 'bitget', self.instruments[n].symbol, 'long' if n == 0 else 'short'))
                positions[key] = v.quantity/self.multipliers[n]
                floating[key] = str(v.quantity*(self.prices[n]-self.entry_prices[n])*(1 if n == 0 else -1))
        return dict(positions=positions, upnl=str(sum(map(dec, floating.values()), dec(0))),
                    upnl_by_position=floating, captured_at=self.now)

    def snapshot(self):
        if self.fault == 'account':
            self.coverage['account_failures'] += 1
            raise ValueError('injected account failure')
        result = self.positions()
        equities = {k:str(dec(base)+v.realized+v.quantity*(self.prices[n]-self.entry_prices[n])*(1 if n == 0 else -1))
                    for n,(k,base,v) in enumerate(zip(('crossex','bitget'), (1000,30000), self.venues))}
        if self.fault == 'equity':
            equities['crossex'] = '800'
        return dict(result, equity_by_venue=equities, equity=str(sum(map(dec, equities.values()), dec(0))),
                    margins=['4','4'], liabilities=[], notionals={})

    def references(self):
        # Append independent synthetic public observations; never clear cooldowns
        # or replace the production baseline gate. Midnight gaps do not backfill.
        from tools.crossex_exec.baseline import Baseline
        mids = {'binance':{'BTC':str(self.prices[0])}, 'bitget':{'BTC':str(self.prices[1])}}
        if self.opening_fault != 'too_few_venues':
            mids['bybit'] = {'BTC':'101' if self.opening_fault == 'not_single_venue_outlier' else '100'}
        Baseline(self.store,lambda:self.now).record(mids,('BTC',))
        self.store.set_meta('mapping', dict(at=self.now,instruments={i.market.key:i.dump() for i in self.instruments}))
        self.store.set_meta('market_tickers',{i.market.key:dict(at=self.now,mid=str(self.prices[n]*self.multipliers[n]),volume='10000000')
                                            for n,i in enumerate(self.instruments)})
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
        payload.update(buy_market_key=self.instruments[0].dump()['market'],sell_market_key=self.instruments[1].dump()['market'])
        self.engine.ingest(payload)


class InvariantSimulationTests(unittest.TestCase):
    def test_fault_invariants_seed_20261111_2448_rounds(self):
        self.run_seed(SEEDS[0])

    def test_fault_invariants_seed_20261112_2448_rounds(self):
        self.run_seed(SEEDS[1])

    def test_fault_invariants_seed_20261113_2448_rounds(self):
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
        self.assertLessEqual(dec(sent['order']['quantity'])*dec(sent['order'].get('instrument',{}).get('quantity_multiplier','1')),
                             before[sent['order']['leg']], 'I3 oversized close')
        self.assertTrue(all(0 <= a <= b for a,b in zip(after,before)), 'I3 crossed zero')
        exercised['3'] += 1

    def run_seed(self, seed):
        rng = random.Random(seed)
        # Keep the original fault/fill draws and 48-round tail unchanged. Half
        # the random episodes additionally carry bad public admission inputs.
        opening_rng = random.Random(f'{seed}:opening')
        coverage = Counter()
        opened_pairs = set()
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
                        opening_fault = opening_rng.choice(('none',)*6+OPENING_FAULTS) if index < RANDOM_ROUNDS else 'none'
                        tail=index-RANDOM_ROUNDS
                        if tail in (120,144):
                            opening_fault='volume_too_low' if tail==120 else 'size_too_small'
                        world = World(live=index >= RANDOM_ROUNDS or (index//24)%4 != 0,
                                      coverage=coverage, opening_fault=opening_fault,
                                      mapping=0 if tail==72 else 1 if tail==96 else None)
                    w = world
                    w.step = index
                    fault = 'none' if episode_step in (1, 21, 22, 23) else rng.choice(FAULTS)
                    if episode_step == 0:
                        fault = ('none','partial','zero','timeout_ack','timeout_unknown','database','stale','spread')[(index//24)%8]
                    # Retain all original 2400 random rounds, then add two live
                    # episodes so query outage and HALTING retry cannot be vacuous
                    # merely because a randomly scheduled fault hit an empty world.
                    if index >= RANDOM_ROUNDS:
                        tail = index-RANDOM_ROUNDS
                        fault = {0:'timeout_unknown', 1:'detail', 24:'none', 25:'zero'}.get(tail, 'none')
                        if tail == 25:
                            w.store.set_meta('state', 'HALTING')
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
                                         prices=list(map(str,w.prices)), live=w.live, opening_fault=w.opening_fault))
                    coverage['scheduled_'+fault] += 1
                    first_send, first_cancel = len(w.sends), w.cancels
                    first_batch, first_recovery = len(w.batches), len(w.recoveries)
                    outstanding = [p for p in w.store.pairs() if p['id'] in w.close_obligations
                                   and w.venues[0].quantity != w.venues[1].quantity]
                    retry_due = any(w.now >= p.get('next_close', 0) for p in outstanding)
                    if fault == 'restart':
                        w.restart()
                    w.references()
                    skips_before = Counter(w.store.meta('opening_skip_counts', {}))
                    if index >= RANDOM_ROUNDS:
                        if tail == 49:
                            from tools.crossex_exec.settings import Settings
                            frozen=copy.deepcopy(w.store.pairs(active=True)[0]['config_at_open'])
                            Settings(w.store).update({'order_usd_max':25,'min_quote_volume_usd':2000000},source='admin_api')
                            self.assertEqual(w.store.pairs(active=True)[0]['config_at_open'],frozen)
                            coverage['runtime_config_changed']+=1
                        if tail == 50:
                            w.engine.close_pair(w.store.pairs(active=True)[0],'sim_reconfigure')
                        if tail == 51:
                            w.open()
                            pair=w.store.pairs(active=True)[0]
                            self.assertEqual(pair['config_at_open']['version'],1)
                            self.assertEqual(pair['config_at_open']['values']['order_usd_max'],25)
                            coverage['runtime_config_new_open']+=1
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
                            pair = next(p for p in w.store.pairs() if p['id']==order['pair_id'])
                            self.assertEqual(dec(pair['baseline_at_open']),5)
                            self.assertLessEqual(w.store.meta('pair_cooldowns',{}).get('BTC|binance|bitget',0),w.now)
                            coverage['baseline_admission'] += 1
                            self.assertFalse(sent['locked'], 'I2 opening while locked')
                            self.assertIsNotNone(sent['admission'], 'I1d missing shared admission')
                            baseline = sent['admission']['baseline']
                            self.assertLessEqual(abs(dec(baseline['baseline_bps'])), 10)
                            self.assertGreaterEqual(dec(baseline['mad_bps']), 0)
                            self.assertLessEqual(dec(baseline['mad_bps']), 10)
                            gross = (dec(pair['fill']['sell']['vwap'])/dec(pair['fill']['buy']['vwap'])-1)*10000
                            self.assertEqual(dec(pair['excess_at_open']), gross-5, 'nonzero baseline must be deducted')
                            if sent['filled'] > 0 and pair['id'] not in opened_pairs:
                                opened_pairs.add(pair['id'])
                                coverage['opened_pairs'] += 1
                                if any(m!=1 for m in w.multipliers):
                                    coverage['mapping_opened']+=1
                                    coverage['mapping_buy' if w.multipliers[0]!=1 else 'mapping_sell']+=1
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
                        for pair in w.store.pairs():
                            if pair['id'] not in w.close_obligations:
                                continue
                            self.assertNotEqual(pair['state'], 'CLOSED', 'I1c close obligation hidden by CLOSED state')
                            step = min(dec(i['spec']['qty_step'])*dec(i.get('quantity_multiplier','1')) for i in pair['instruments'])
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
                    # Count only production-requested, trigger-deduplicated SKIPs,
                    # never scheduled faults or repeated reads of old counters.
                    for reason, count in (Counter(w.store.meta('opening_skip_counts', {}))-skips_before).items():
                        coverage['skip_'+reason] += count
                    if w.store.meta('pair_cooldowns',{}).get('BTC|binance|bitget',0)>w.now:
                        coverage['cooldown_active'] += 1
                    w.store.failures = 0
                    for invariant in INVARIANTS:
                        checks[invariant] += 1
            self.assertEqual(set(checks.values()), {ROUNDS})
            for invariant in INVARIANTS:
                self.assertGreater(exercised[invariant], 0, 'vacuous invariant: '+invariant)
            for key in (*(f'scheduled_{f}' for f in FAULTS), 'open','reduce','partial_fill','zero_fill','timeout_ack','timeout_unknown',
                        'restarts','db_failures','recovery_obligation','halting_retry','halted',
                        'query_failures','position_failures','account_failures','baseline_admission','cooldown_active'):
                self.assertGreater(coverage[key], 0, 'vacuous fault coverage: '+key)
            self.assertGreater(coverage['opened_pairs'], 0, 'vacuous actual new-position coverage')
            for reason in OPENING_FAULTS:
                self.assertGreater(coverage['skip_'+reason], 0, 'vacuous opening rejection: '+reason)
            for key in ('runtime_config_changed','runtime_config_new_open','mapping_opened','mapping_buy','mapping_sell',
                        'skip_volume_too_low','skip_size_too_small'):
                self.assertGreater(coverage[key],0,'vacuous round-eight execution: '+key)
            print(f'INVARIANTS seed={seed} rounds={ROUNDS} random_rounds={RANDOM_ROUNDS} N={RECOVERY_BOUND} checks={dict(checks)} '
                  f'exercised={dict(sorted(exercised.items()))} coverage={dict(sorted(coverage.items()))}', flush=True)
        except Exception as error:
            self.fail(f'seed={seed} checked_rounds={len(sequence)} error={error}\nsteps={json.dumps(sequence)}')
        finally:
            if world:
                world.engine.close()
