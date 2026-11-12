"""Round nine: offline regressions for the shared release and account latches."""
import json
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.market import MarketData, native_instrument
from tools.crossex_exec.policy import Instrument
from tools.crossex_exec.settings import Settings
from tools.crossex_exec.universe import category
from test_exec import setUpModule, tearDownModule
import test_merge_final_review as previous


class SafetyTests(unittest.TestCase):
    setUp = previous.MergeReleaseTests.setUp
    snapshot = previous.MergeReleaseTests.snapshot
    pair = previous.MergeReleaseTests.pair
    trigger = previous.MergeReleaseTests.trigger
    after_sent = previous.MergeReleaseTests.after_sent
    assert_rejected = previous.MergeReleaseTests.assert_rejected

    def cached_market(self, age=0):
        market = MarketData(self.store, clock=lambda:self.now[0])
        self.addCleanup(market.close)
        self.market.turnovers = market.turnovers
        # Bind explicitly once implemented; the red run must reach the old gate.
        if hasattr(market, 'cached_turnovers'):
            self.market.cached_turnovers = market.cached_turnovers
        market.refresh_tickers = lambda: self.fail('final release attempted a network refresh')
        rows = {i.market.key:dict(mid='100', volume='9000000', at=self.now[0]-age)
                for i in self.market.mapping.values()}
        self.store.set_meta('market_tickers', rows)
        return rows

    def test_volume_expires_during_sent_commit_neither_leg_sends(self):
        self.cached_market(299.75)
        _, errors = self.after_sent(lambda rows:self.now.__setitem__(0,self.now[0]+.5))
        self.assert_rejected(errors, 'volume_unknown')
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
        self.assertEqual(skips[-1]['stage'], 'release')

    def test_each_leg_missing_or_below_frozen_volume_denies_whole_pair(self):
        for leg, action in ((0,'missing'),(1,'missing'),(0,'low'),(1,'low')):
            with self.subTest(leg=leg,action=action):
                rows = self.cached_market()
                def change(orders):
                    key = list(self.market.mapping)[leg]
                    if action=='missing':
                        rows.pop(key)
                    else:
                        rows[key]['volume']='999999'
                    self.store.set_meta('market_tickers',rows)
                _, errors = self.after_sent(change,self.trigger('volume-'+str(leg)+action))
                self.assertEqual(errors,['volume_unknown' if action=='missing' else 'volume_too_low'])
                self.assertFalse(any(r[0]=='place' for r in self.trace))

    def test_required_third_volume_venue_is_rechecked_after_commit(self):
        from test_round8_universe import native
        payload = self.trigger()
        rows = self.cached_market()
        third = native('bybit','BTC')
        catalog = dict(self.market.mapping, **{third.market.key:third})
        self.store.set_meta('mapping',dict(at=self.now[0],instruments={k:i.dump() for k,i in catalog.items()}))
        rows[third.market.key]=dict(mid='100',volume='9000000',at=self.now[0])
        self.store.set_meta('market_tickers',rows)
        Settings(self.store).update({'min_venues_with_volume':3},source='admin_api')
        def change(orders):
            rows.pop(third.market.key)
            self.store.set_meta('market_tickers',rows)
        _, errors = self.after_sent(change,payload)
        self.assert_rejected(errors,'volume_too_low')

    def test_fresh_equal_volume_and_age_boundary_still_releases_both(self):
        rows=self.cached_market(299.5)
        for row in rows.values():
            row['volume']='1000000'
        self.store.set_meta('market_tickers',rows)
        pair,errors=self.after_sent(lambda orders:self.now.__setitem__(0,self.now[0]+.5))
        self.assertEqual(errors,[])
        self.assertEqual(pair['state'],'OPEN')

    def test_actual_wire_times_include_delay_after_shared_release(self):
        first = threading.Event()
        writes, wire = [], []
        save = self.store.save_order
        def observed(row,kind=None):
            writes.append((row['cid'],kind,len(wire)))
            return save(row,kind)
        for n,venue in enumerate(self.venues):
            original=venue.place
            def place(order,before_send,n=n,original=original):
                def released():
                    before_send()
                    if n:
                        self.assertTrue(first.wait(1))
                        self.now[0]+=.25
                    mark=getattr(before_send,'on_send',lambda:None)
                    count=len(writes)
                    mark()
                    self.assertEqual(len(writes),count,'wire timestamp performed synchronous journal I/O')
                    wire.append((n,self.now[0]*1000))
                    if not n:
                        first.set()
                return original(order,released)
            venue.place=place
        with patch.object(self.store,'save_order',side_effect=observed):
            pair=self.engine.ingest(self.trigger())
        self.assertEqual(pair['state'],'OPEN')
        self.assertEqual(pair['send_delta_ms'],250)
        entries=sorted(self.store.all_orders(reduce_only=False),key=lambda r:r['leg'])
        self.assertEqual([r['sent_ms'] for r in entries],[t for _,t in sorted(wire)])
        self.assertEqual(entries[0]['sent_persisted_ms'],entries[1]['sent_persisted_ms'])

    def test_account_refresh_and_send_barrier_obey_one_nested_lock_order(self):
        # A bounded lock auditor fails before a real AB/BA cycle can hang unittest.
        local=threading.local()
        violations=[]
        wrappers={}
        class Ordered:
            def __init__(guard,lock,rank,name):
                guard.lock,guard.rank,guard.name=lock,rank,name
            def __enter__(guard):
                held=getattr(local,'held',[])
                if held and guard not in held and max(g.rank for g in held)>guard.rank:
                    violations.append((tuple(g.name for g in held),guard.name))
                    raise RuntimeError('nested_lock_order')
                if not guard.lock.acquire(timeout=1):
                    violations.append(('timeout',guard.name))
                    raise RuntimeError('lock_timeout')
                local.held=held+[guard]
                return guard
            def __exit__(guard,*args):
                local.held.pop()
                guard.lock.release()
        # Shared RLocks get ONE identity/rank, including reentrant acquisitions.
        for owner,attr,rank in ((self.store,'lock',0),(self.engine.limits,'lock',1),
                                (self.engine,'_alert_lock',2),(self.engine.stops,'lock',3)):
            raw=getattr(owner,attr)
            wrappers.setdefault(id(raw),Ordered(raw,rank,attr))
            setattr(owner,attr,wrappers[id(raw)])
        self.data['equity_by_venue']['crossex']='800'
        self.engine.refresh_accounts()
        self.assertEqual(violations,[])
        self.assertIn('account_equity_drop',self.store.meta('stop_locks',{}))

    def test_refresh_waiting_at_sent_commit_completes_and_keeps_halt(self):
        started,done=threading.Event(),threading.Event()
        threads=[]
        def refresh():
            started.set()
            self.engine.refresh_accounts()
            done.set()
        def change(rows):
            self.data['equity_by_venue']['crossex']='800'
            thread=threading.Thread(target=refresh,daemon=True)
            threads.append(thread)
            thread.start()
            self.assertTrue(started.wait(1))
        save=self.store.save_orders
        def commit(rows,kind=None):
            save(rows,kind)
            if kind=='SENT':
                change(rows)
        with patch.object(self.store,'save_orders',side_effect=commit):
            self.engine.ingest(self.trigger())
        for thread in threads:
            thread.join(2)
        self.assertTrue(done.is_set())
        self.assertIn(self.store.meta('state'),('HALTING','HALTED'))
        self.assertIn('account_equity_drop',self.store.meta('stop_locks'))

    def test_alert_entry_interleaved_with_waiting_pair_cannot_form_ab_ba_cycle(self):
        account_in_alert,gate_has_store=threading.Event(),threading.Event()
        violations=[]
        raw_lock=self.store.lock
        class BoundedJournalLock:
            def __enter__(guard):
                if not raw_lock.acquire(timeout=1):
                    violations.append('journal_wait_timeout')
                    raise RuntimeError('journal_wait_timeout')
                if threading.current_thread().name.startswith('ioc') and account_in_alert.is_set():
                    gate_has_store.set()
                return guard
            def __exit__(guard,*args):
                raw_lock.release()
        self.store.lock=BoundedJournalLock()
        meta=self.store.meta
        paused=[]
        def inspect(key,*args):
            if (threading.current_thread().name=='account-before-gate' and key=='state'
                    and self.engine._alert_lock._is_owned() and not paused):
                paused.append(True)
                account_in_alert.set()
                # With the unified lock the gate must wait; the timeout permits
                # the account to finish. With AB/BA it owns Store and waits alert.
                gate_has_store.wait(.2)
            return meta(key,*args)
        original=self.venues[0].place
        threads=[]
        def place(order,before_send):
            if not order['reduce_only']:
                self.data['equity_by_venue']['crossex']='800'
                worker=threading.Thread(target=self.engine.refresh_accounts,name='account-before-gate',daemon=True)
                threads.append(worker)
                worker.start()
                self.assertTrue(account_in_alert.wait(1))
            return original(order,before_send)
        with patch.object(self.store,'meta',side_effect=inspect), patch.object(self.venues[0],'place',side_effect=place):
            self.engine.ingest(self.trigger())
            for worker in threads:
                worker.join(2)
                self.assertFalse(worker.is_alive())
        self.assertEqual(violations,[])
        self.assertTrue(account_in_alert.is_set())
        self.assertFalse(any(r[0]=='place' and not r[3] for r in self.trace))

    def test_snapshot_lock_recovers_only_after_three_distinct_fresh_refreshes(self):
        self.engine.alert('account_snapshot_unavailable')
        self.engine.alert('manual_review')
        for n in range(3):
            self.now[0]+=5
            self.engine.refresh_accounts()
            self.assertEqual('account_snapshot_unavailable' in self.store.meta('stop_locks'),n<2)
        self.assertIn('manual_review',self.store.meta('stop_locks'))
        events=[json.loads(e['value']) for e in self.store.events() if e['kind']=='ACCOUNT_SNAPSHOT_RECOVERED']
        self.assertEqual(len(events),1)
        self.assertGreaterEqual(events[0]['consecutive'],3)

    def test_slow_or_failed_refresh_resets_streak_and_never_extends_ten_seconds(self):
        self.engine.alert('account_snapshot_unavailable')
        self.now[0]+=1
        self.engine.refresh_accounts()
        def slow():
            result=self.snapshot()
            self.now[0]+=10.01
            return result
        with patch.object(self.accounts,'snapshot',side_effect=slow):
            self.engine.refresh_accounts()
        with self.assertRaisesRegex(ValueError,'account_snapshot_stale'):
            self.engine.account_cache.read()
        self.assertIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        for n in range(2):
            self.now[0]+=1
            self.engine.refresh_accounts()
        with patch.object(self.accounts,'snapshot',side_effect=ValueError('offline')):
            self.engine.refresh_accounts()
        for n in range(2):
            self.now[0]+=1
            self.engine.refresh_accounts()
            self.assertIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        self.now[0]+=1
        self.engine.refresh_accounts()
        self.assertNotIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        self.assertLessEqual(self.store.meta('account_refresh')['age_seconds'],10)

    def test_reconcile_cannot_unlock_after_one_fresh_snapshot(self):
        self.engine.alert('account_snapshot_unavailable')
        self.now[0]+=1
        self.engine.reconcile()
        self.assertIn('account_snapshot_unavailable',self.store.meta('stop_locks'))

    def test_same_cached_snapshot_never_counts_twice_or_rewrites_refresh_duration(self):
        self.engine.alert('account_snapshot_unavailable')
        self.now[0]+=1
        self.engine.refresh_accounts()
        first=self.store.meta('account_refresh')
        for n in range(5):
            self.now[0]+=.1
            cached=self.engine.account_cache.read()
            self.engine.observe_account_snapshot(cached,cached['captured_at'])
        self.assertEqual(self.store.meta('account_refresh'),first)
        self.assertIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        for n in range(2):
            self.now[0]+=1
            self.engine.refresh_accounts()
            self.assertEqual('account_snapshot_unavailable' in self.store.meta('stop_locks'),n==0)

    def test_liability_recovery_needs_three_clean_reads_and_preserves_halted_state(self):
        self.data['liabilities']=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='28.5')]
        self.engine.refresh_accounts()
        self.store.set_meta('state','HALTED')
        self.store.set_meta('halt_reason','total_loss')
        self.engine.alert('total_loss')
        self.data['liabilities']=[]
        for n in range(3):
            self.now[0]+=1
            self.engine.refresh_accounts()
            self.assertEqual('account_liability' in self.store.meta('stop_locks'),n<2)
            self.assertEqual(self.store.meta('state'),'HALTED')
        self.assertIn('total_loss',self.store.meta('stop_locks'))
        self.assertEqual(sum(e['kind']=='ACCOUNT_LIABILITY_RECOVERED' for e in self.store.events()),1)

    def test_failed_recovery_audit_keeps_snapshot_lock_and_restarts_streak(self):
        self.engine.alert('account_snapshot_unavailable')
        for n in range(2):
            self.now[0]+=1
            self.engine.refresh_accounts()
        original=self.store.event
        def failing(kind,*args,**kwargs):
            if kind=='ACCOUNT_SNAPSHOT_RECOVERED':
                raise OSError('disk')
            return original(kind,*args,**kwargs)
        self.now[0]+=1
        with patch.object(self.store,'event',side_effect=failing):
            self.engine.refresh_accounts()
        self.assertIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        self.assertIn('account_snapshot_unavailable',self.engine.stops.reasons)
        self.assertEqual(sum(e['kind']=='ACCOUNT_SNAPSHOT_RECOVERED' for e in self.store.events()),0)
        for n in range(3):
            self.now[0]+=1
            self.engine.refresh_accounts()
            self.assertEqual('account_snapshot_unavailable' in self.store.meta('stop_locks'),n<2)

    def test_summary_exposes_refresh_age_and_true_halt_reason_with_liability(self):
        from tools.crossex_exec.api_data import JournalView
        view=JournalView(self.tmp.name+'/exec.db',{},clock=lambda:self.now[0])
        self.store.set_meta('account_refresh',dict(captured_at=self.now[0]-.5,
                           completed_at=self.now[0],age_seconds=.5,fresh=True))
        self.store.set_meta('state','HALTED')
        self.store.set_meta('halt_reason','account_equity_drop')
        self.data['liabilities']=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='28.5')]
        self.engine.refresh_accounts()
        summary=view.get('summary',{})
        self.assertEqual(summary['halt_reason'],'account_equity_drop')
        self.assertTrue(summary['account_snapshot']['fresh'])
        self.assertEqual(summary['liabilities'],[dict(coin='USDC',exchange_type='HYPERLIQUID',liability=28.5)])
        self.assertIn('account_liability',summary['stop_locks'])
        self.now[0]+=10.001
        self.assertFalse(view.get('summary',{})['account_snapshot']['fresh'])

    def test_hyperliquid_liability_blocks_open_alerts_once_and_allows_close(self):
        pair=self.pair()
        self.data['liabilities']=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='28.5')]
        self.engine.refresh_accounts()
        self.engine.refresh_accounts()
        self.assertFalse(self.engine.can_open())
        self.assertIn('account_liability',self.store.meta('stop_locks'))
        events=[json.loads(e['value']) for e in self.store.events() if e['kind']=='ALERT']
        self.assertEqual(sum(e['reason']=='account_liability' for e in events),1)
        self.assertEqual(self.store.meta('liabilities'),self.data['liabilities'])
        self.assertNotIn(self.store.meta('state'),('HALTING','HALTED'))
        self.engine.close_pair(pair,'manual')
        self.assertEqual(pair['state'],'CLOSED')
        self.assertTrue(all(r[3] for r in self.trace if r[0]=='place'))

    def test_liability_introduced_at_sent_commit_cannot_send_either_leg(self):
        def change(rows):
            data=self.snapshot()
            data['liabilities']=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='28.5')]
            self.engine.account_cache.publish(data)
        # check_order may reject before check_release is reached.
        save=self.store.save_orders
        def commit(rows,kind=None):
            save(rows,kind)
            if kind=='SENT':
                change(rows)
        with patch.object(self.store,'save_orders',side_effect=commit):
            self.engine.ingest(self.trigger())
        self.assertFalse(any(r[0]=='place' for r in self.trace))
        self.assertIn('account_liability',self.store.meta('stop_locks'))

    def test_okx_nontradeable_rules_reject_engine_open_before_books(self):
        from test_exec import instrument
        for rule in ('pre_market','rebase_contract','unknown_new_rule'):
            payload=self.trigger(rule)
            rows=[OKXTests.instrument(rule),instrument('bitget')]
            self.market.mapping={i.market.key:i for i in rows}
            self.store.set_meta('mapping',dict(at=self.now[0],instruments={i.market.key:i.dump() for i in rows}))
            self.store.set_meta('universe_decisions',{'BTC':dict(category='crypto',markets=list(self.market.mapping))})
            payload.update(buy_market_key=rows[0].dump()['market'],sell_market_key=rows[1].dump()['market'])
            with patch.object(self.market,'books',side_effect=AssertionError('forbidden contract reached pricing')) as books:
                self.assertIsNone(self.engine.ingest(payload))
                books.assert_not_called()
            skips=[json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
            self.assertEqual(skips[-1]['reason'],'category_unconfirmed')
        self.assertEqual(self.store.all_orders(),[])


class OKXTests(unittest.TestCase):
    @staticmethod
    def instrument(rule):
        row=dict(instId='BTC-USDT-SWAP',instType='SWAP',state='live',instCategory='1',ruleType=rule,
                 ctValCcy='BTC',settleCcy='USDT',ctType='linear',ctVal='1',lotSz='.001',
                 minSz='.001',maxMktSz='1000',tickSz='.01')
        rules=[dict(symbol='OKX_FUTURE_BTC_USDT',state='live',business_type='FUTURE',
                    exchange_type='OKX',delist_time='0',tag='',support_cross='true',
                    lot_size='.001',min_size='.001',max_limit_size='1000',tick_size='.01',min_notional='0')]
        return native_instrument('okx',row,rules)

    def test_native_rule_is_persisted_and_legacy_manual_choice_cannot_erase_it(self):
        from tools.crossex_exec.storage import Store
        from tools.crossex_exec.universe import Universe
        for rule in ('pre_market','rebase_contract','unknown_new_rule'):
            with self.subTest(rule=rule), Store(':memory:') as store:
                instrument=self.instrument(rule)
                self.assertEqual(Instrument.load(instrument.dump()).rule_type,rule)
                instrument.category='crypto'  # Even a prior classification is subordinate.
                store.set_meta('mapping',dict(instruments={instrument.market.key:instrument.dump()}))
                universe=Universe(store)
                with self.assertRaisesRegex(ValueError,'invalid_classification'):
                    universe.classify('BTC',dict(category='crypto',markets=[instrument.market.key]))
                with self.assertRaisesRegex(ValueError,'category_unconfirmed'):
                    universe.eligibility(instrument)

    def test_forbidden_rule_type_has_priority_over_crypto_or_manual_classification(self):
        from test_round8_universe import native
        from tools.crossex_exec.storage import Store
        from tools.crossex_exec.universe import Universe
        for rule in ('pre_market','rebase_contract'):
            with self.subTest(rule=rule), Store(':memory:') as store:
                kind=category('okx',dict(state='live',instCategory='1',ruleType=rule,underlyingType='COIN'))
                self.assertEqual(kind,'pre_ipo')
                rows=[native('okx','NEW',kind),native('bitget','NEW')]
                store.set_meta('mapping',dict(instruments={i.market.key:i.dump() for i in rows}))
                universe=Universe(store)
                self.assertFalse(any(m['exchange']=='okx' for r in universe.describe() for m in r['markets']))
                with self.assertRaisesRegex(ValueError,'invalid_classification'):
                    universe.classify('NEW',dict(category='crypto',markets=[i.market.key for i in rows]))
                with self.assertRaisesRegex(ValueError,'category_unconfirmed'):
                    universe.resolve(rows)


class WireTimestampTests(unittest.TestCase):
    import test_exec as fixtures
    setUp=fixtures.HTTPExecutionTests.setUp
    tearDown=fixtures.HTTPExecutionTests.tearDown

    def test_transport_timestamp_follows_signing_and_failed_sign_has_no_wire_time(self):
        from test_exec import intent
        from tools.crossex_exec import transport
        now=[1000.0]
        self.exec.clock=lambda:now[0]
        real_sign=transport.gate_sign
        def slow_sign(*args):
            now[0]+=.15
            return real_sign(*args)
        with patch.object(transport,'gate_sign',side_effect=slow_sign):
            result=self.exec.execute(intent(cid='wire-time'))
        self.assertAlmostEqual(result['sent_ms']-result['sent_persisted_ms'],150,places=3)
        self.exec.confirm_timeout=.001
        with patch.object(transport,'gate_sign',side_effect=RuntimeError('signing failure')):
            unknown=self.exec.execute(intent(cid='not-on-wire'))
        self.assertEqual(unknown['state'],'UNKNOWN')
        self.assertIsNone(unknown.get('sent_ms'))
