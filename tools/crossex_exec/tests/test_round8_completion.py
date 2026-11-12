"""Round-eight integration acceptance: frozen risk, native units and public storage."""
import json
import threading
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tools.fill_sim.core import Book, dec
from tools.crossex_exec.accounts import FeeSnapshot, local_positions
from tools.crossex_exec.api_data import position, trade
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.settings import Settings
from tools.crossex_exec.storage import Store
from test_exec import setUpModule, tearDownModule
from test_round8_universe import native
import test_round6 as previous


class CompletionTests(unittest.TestCase):
    setUp = previous.EngineBaselineTests.setUp
    trigger = previous.EngineBaselineTests.trigger
    snapshot = previous.EngineBaselineTests.snapshot
    pair = previous.EngineBaselineTests.pair

    def test_risk_configuration_freezes_for_active_positions(self):
        pair = self.engine.ingest(self.trigger())
        self.assertIsNotNone(pair)
        Settings(self.store).update(dict(max_equity_drop_usd=10, daily_loss_stop_usd=1,
                                        total_loss_stop_usd=1), source='admin_api')
        self.assertEqual(self.engine.risk_settings(), dict(max_equity_drop_usd=150,
                         daily_loss_stop_usd=20, total_loss_stop_usd=100))
        self.data['equity_by_venue']['crossex'] = '989'
        self.assertFalse(self.engine.limits.check_equity(self.snapshot()))
        self.engine.close_pair(pair, 'completion_test')
        self.assertTrue(self.engine.limits.check_equity(self.snapshot()))
        self.assertEqual(self.store.meta('state'), 'HALTING')

    def test_runtime_daily_and_total_losses_take_effect_without_positions(self):
        Settings(self.store).update(dict(daily_loss_stop_usd=2, total_loss_stop_usd=3), source='admin_api')
        with patch.object(self.engine, 'totals', return_value=(dec('-2'), dec('-2'))):
            self.engine.reconcile()
        self.assertEqual(self.engine.risk, 'STOP')
        self.assertIn('daily_loss', self.engine.stops.reasons)
        with patch.object(self.engine, 'totals', return_value=(dec('-2'), dec('-3'))):
            self.engine.reconcile()
        self.assertEqual(self.store.meta('state'), 'HALTED')

    def test_send_volume_and_oi_failures_are_counted_once_and_send_nothing(self):
        for name in ('volume_too_low', 'volume_unknown', 'oi_missing'):
            with self.subTest(reason=name):
                original = self.engine.executor.execute_pair
                def changed(intents):
                    if name == 'oi_missing':
                        pair = self.store.pairs(active=True)[0]
                        pair['open_interests'] = [None, None]
                        self.store.pair(pair)
                        return original(intents)
                    with patch.object(self.market, 'turnovers', side_effect=ValueError(name)):
                        return original(intents)
                with patch.object(self.engine.executor, 'execute_pair', side_effect=changed):
                    self.engine.ingest(self.trigger(name))
                self.assertEqual(self.store.meta('opening_skip_counts', {}).get(name), 1)
        self.assertFalse(any(row[0] == 'place' for row in self.trace))

    def test_api_writer_serializes_with_shared_admission_version(self):
        with Store(Path(self.tmp.name)/'exec.db') as writer:
            entered,done=threading.Event(),threading.Event()
            errors=[]
            def update():
                entered.set()
                try:
                    Settings(writer).update({'order_usd_max':5},source='admin_api')
                except Exception as error:
                    errors.append(error)
                finally:
                    done.set()
            original=self.engine.check_volumes
            worker=None
            def inspect(instruments,values):
                nonlocal worker
                result=original(instruments,values)
                if any(p['state']=='OPENING' for p in self.store.pairs(active=True)):
                    worker=threading.Thread(target=update)
                    worker.start()
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(done.wait(.02))
                return result
            with patch.object(self.engine,'check_volumes',side_effect=inspect):
                pair=self.engine.ingest(self.trigger('serialize'))
            self.assertIsNotNone(worker)
            worker.join(2)
            self.assertTrue(done.is_set())
            self.assertFalse(errors)
            self.assertEqual(pair['config_at_open']['version'],0)
            self.assertEqual(Settings(writer).read()['version'],1)
            self.assertEqual(len([row for row in self.trace if row[0]=='place']),2)

    def test_default_quantity_is_depth_sized_and_unconfirmed_fails_closed(self):
        self.engine.bases=()
        for i in self.market.mapping.values():
            i.category='unconfirmed'
        self.assertIsNone(self.engine.ingest(self.trigger('unknown')))
        for i in self.market.mapping.values():
            i.category='crypto'
        def thin(instruments):
            return [Book([['100' if i.market.exchange=='binance' else '102','.1']],
                         [['100' if i.market.exchange=='binance' else '102','.1']],self.now[0]) for i in instruments]
        for i in self.market.mapping.values():
            i.spec=replace(i.spec,min_notional='0')
        with patch.object(self.market,'books',side_effect=thin):
            pair=self.engine.ingest(self.trigger('depth'))
        self.assertIsNotNone(pair)
        self.assertEqual(pair['sizing']['limiting'],'depth')
        self.assertLess(dec(pair['fill']['quantity']),dec('.1'))

    def test_new_default_unrestricted_bases_and_explicit_legacy_constraint(self):
        from baseline_fixtures import seed_baseline
        rows=[native(ex,'NEW') for ex in ('binance','bitget')]
        self.market.mapping={i.market.key:i for i in rows}
        self.accounts.fee_snapshots={i.market.key:FeeSnapshot('0',self.now[0]) for i in rows}
        seed_baseline(self.store,self.now[0],base='NEW')
        self.store.set_meta('baseline_quotes',dict(at=self.now[0],mids={ex:{'NEW':'100'} for ex in ('binance','bitget','bybit')}))
        self.engine.live=self.engine.executor.live=False
        payload=dict(self.trigger('legacy'),symbol='NEW',buy_market_key=rows[0].dump()['market'],sell_market_key=rows[1].dump()['market'])
        self.engine.bases=('BTC',)
        self.assertIsNone(self.engine.ingest(payload))
        self.engine.bases=()
        pair=self.engine.ingest(dict(payload,id='default'))
        self.assertIsNotNone(pair)
        self.assertEqual(pair['base'],'NEW')
        self.assertEqual(pair['state'],'SHADOW')

    def test_every_parameter_update_preserves_an_existing_position_snapshot(self):
        from tools.crossex_exec.settings import SCHEMA
        pair=self.engine.ingest(self.trigger('all-frozen'))
        self.assertIsNotNone(pair)
        frozen=pair['config_at_open']
        for name,rule in SCHEMA.items():
            Settings(self.store).update({name:rule['min']},source='admin_api')
            self.assertEqual(self.store.pairs(active=True)[0]['config_at_open'],frozen)
        self.engine.close_pair(pair,'completion_test')
        self.assertEqual(pair['state'],'CLOSED')

    def test_stock_reference_ignores_unselected_underlying(self):
        from tools.crossex_exec.universe import Universe
        rows=list(self.market.mapping.values())+[native('bybit','BTC','listed_equity')]
        for i in rows:
            i.category='listed_equity'
        payload=self.trigger('stock')
        self.store.set_meta('mapping',dict(at=self.now[0],instruments={i.market.key:i.dump() for i in rows}))
        universe=Universe(self.store,lambda:self.now[0])
        universe.classify('BTC',dict(category='listed_equity',underlying_market='US',markets=[i.market.key for i in rows[:2]]))
        self.assertIsNone(self.engine.ingest(payload))
        self.assertEqual(self.store.meta('opening_skip_counts')['too_few_venues'],1)
        universe.classify('BTC',dict(category='listed_equity',underlying_market='US',markets=[i.market.key for i in rows]))
        pair=self.engine.ingest(dict(payload,id='stock-selected'))
        self.assertIsNotNone(pair)

    def test_mapped_native_orders_partial_repair_restart_close_and_api_both_directions(self):
        # Each direction runs in a fresh database and exercises real Engine/Executor.
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self._mapped_lifecycle(reverse)

    def _mapped_lifecycle(self, reverse):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name)/'mapped.db')
        self.addCleanup(store.close)
        rows = [native('binance', 'SHIB' if reverse else '1000SHIB'),
                native('bitget', '1000SHIB' if reverse else 'SHIB')]
        factors = [dec(1) if reverse else dec(1000), dec(1000) if reverse else dec(1)]
        for i, factor in zip(rows, factors):
            i.spec = replace(i.spec, qty_step=str(dec('.007')/factor), min_qty='0', min_notional='0')
        now = self.now[0]
        store.set_meta('mapping', dict(at=now, instruments={i.market.key:i.dump() for i in rows}))
        store.set_meta('market_tickers', {i.market.key:dict(mid=str(f),volume='9000000',at=now)
                                        for i,f in zip(rows,factors)})
        from tools.crossex_exec.universe import Universe
        universe = Universe(store, lambda: now)
        universe.discover()
        universe.decide_mapping(universe.mappings()[0]['id'], 'confirmed')
        from types import SimpleNamespace
        from baseline_fixtures import seed_baseline
        seed_baseline(store, now, base='SHIB')
        store.set_meta('baseline_quotes', dict(at=now, mids={ex:{'SHIB':'1'} for ex in ('binance','bitget','bybit')}))
        prices = [dec(1), dec('1.005')]
        def books(instruments):
            return [Book([[str(prices[n]*factors[n]),'100000']],
                         [[str(prices[n]*factors[n]),'100000']],now)
                    for i in instruments for n in (0 if i.market.exchange=='binance' else 1,)]
        # Native adapters maintain native quantities and price their own denomination.
        class Venue:
            def __init__(v, leg):
                v.leg, v.quantity, v.orders = leg, dec(0), {}
            def place(v, order, before_send):
                before_send()
                getattr(before_send,'on_send',lambda:None)()
                q = dec(order['quantity'])
                if not order['reduce_only'] and v.leg == 1:
                    q = (q*factors[v.leg]/2/dec('.007')).to_integral_value(rounding='ROUND_FLOOR')*dec('.007')/factors[v.leg]
                if order['reduce_only']:
                    q = min(q, v.quantity)
                v.quantity += q*(-1 if order['reduce_only'] else 1)
                v.orders[order['cid']] = q
                return order['cid']
            def detail(v, order, deadline=None):
                return dict(oid=order['cid'], filled=str(v.orders[order['cid']]), terminal=True,
                            rejected=False,venue_state='FILLED',average_price=str(prices[v.leg]*factors[v.leg]))
            def fills(v, order):
                return dict(vwap=str(prices[v.leg]*factors[v.leg]), fee='0', fill_ms=now*1000,
                            trade_ids=[order['cid']], accounting_pending=False)
        venues = [Venue(0), Venue(1)]
        def snapshot():
            positions = {'|'.join(('crossex' if n==0 else 'bitget', rows[n].symbol,
                                   'long' if n==0 else 'short')):v.quantity
                         for n,v in enumerate(venues) if v.quantity}
            return dict(positions=positions, margins=['4','4'], upnl='0', captured_at=now,
                        equity_by_venue=dict(crossex='1000',bitget='1000'), notionals={},liabilities=[])
        market = SimpleNamespace(mapping={i.market.key:i for i in rows}, books=books,
            funding=lambda instruments:[dict(rate='0',interval=28800,next=now+10000,
                captured_at=now,history_ok=True,max_abs_24h='0') for _ in instruments],
            turnovers=lambda instruments:['9000000']*len(instruments),
            cached_turnovers=lambda instruments:['9000000']*len(instruments),
            open_interests=lambda instruments,books:[dict(market_key=i.market.key,usd='1000000',
                at=now,unit_verified=True) for i in instruments])
        accounts = SimpleNamespace(cross=venues[0],bitget=venues[1],snapshot=snapshot,
            position_snapshot=snapshot,validate_startup=snapshot,check_instruments=lambda rows:None,
            fee_snapshots={i.market.key:FeeSnapshot('0',now) for i in rows})
        engine = Engine(store,market,accounts,config={'CROSSEX_RISK_SCOPE':'dedicated_account'},live=True,clock=lambda:now)
        self.addCleanup(engine.close)
        engine.limits.initialize(snapshot())
        engine.account_cache.publish(snapshot())
        engine.risk,engine.last_reconcile = 'RUN',now
        from test_engine import trigger
        payload = dict(trigger('mapping'),symbol='SHIB',opened_at=now,
                       buy_market_key=rows[0].dump()['market'],sell_market_key=rows[1].dump()['market'])
        pair = engine.ingest(payload)
        self.assertIsNotNone(pair, store.events()[-1])
        self.assertEqual(pair['state'], 'OPEN')
        self.assertTrue(pair['single_failure'])
        self.assertEqual(dec(pair['remaining'][0]), dec(pair['remaining'][1]))
        self.assertGreater(dec(pair['remaining'][0]), 0)
        self.assertEqual(local_positions([pair]), snapshot()['positions'])
        before = position(pair, store.all_orders(), now)
        self.assertEqual(before['symbol'], 'SHIBUSDT')
        self.assertAlmostEqual(before['legs'][0]['entry_price'],1)
        self.assertAlmostEqual(before['legs'][1]['entry_price'],1.005)
        self.assertEqual(before['config_version'],0)
        for n, leg in enumerate(before['legs']):
            self.assertAlmostEqual(leg['native_qty']*float(factors[n]),leg['qty'])
            self.assertEqual(leg['raw_symbol'],rows[n].market.raw_symbol)
        engine.save_tracking_upnl(pair,[Book(**b) for b in pair['last_books']],now)
        self.assertEqual(dec(store.meta('last_upnl')['upnl']),0)
        universe.decide_mapping(universe.mappings()[0]['id'],'rejected')
        Settings(store).update({'max_total_open_usd':0,'max_open_pairs':0},source='admin_api')
        engine.close()
        restarted = Engine(store,market,accounts,config={'CROSSEX_RISK_SCOPE':'dedicated_account'},live=True,clock=lambda:now)
        self.addCleanup(restarted.close)
        restarted.executor.recover()
        restarted.refresh_pair(pair)
        restarted.close_pair(pair,'completion_test')
        self.assertEqual(pair['state'],'CLOSED')
        self.assertFalse(any(v.quantity for v in venues))
        result=trade(pair,store.all_orders())
        self.assertAlmostEqual(result['entry_spread_bps'],50)
        self.assertAlmostEqual(result['net_usd'],0)
        self.assertEqual(result['sizing']['limiting'],'order_usd_max')


class PublicStorageTests(unittest.TestCase):
    def test_sampling_uses_rows_without_market_data_redaction_or_meta_blob(self):
        from tools.crossex_exec.baseline import Baseline
        with Store(':memory:') as store:
            with patch('tools.crossex_exec.storage.REDACTOR.clean', side_effect=AssertionError('public data redacted')):
                baseline=Baseline(store,lambda:20000)
                baseline.record({'binance':{'BTC':'100'},'bitget':{'BTC':'100.01'}},('BTC',))
                self.assertEqual(baseline.latest('BTC')['mids']['binance'],'100')
                self.assertEqual(baseline.status('BTC','binance','bitget')['count'],1)
                store.set_meta('market_tickers',{'binance|future|BTCUSDT':dict(mid='100',volume='5000000',at=20000,
                    api_key='ignored_remote_field')})
                self.assertEqual(store.meta('market_tickers')['binance|future|BTCUSDT']['volume'],'5000000')
            self.assertEqual(store.db.execute("SELECT count(*) FROM meta WHERE key IN ('baseline_quotes','baseline_status')").fetchone()[0],0)
            self.assertNotIn('ignored_remote_field',str(store.meta('market_tickers')))

    def test_old_market_meta_migrates_once_and_preserves_structural_latch(self):
        from tools.crossex_exec.baseline import Baseline
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'old.db'
            with Store(path) as store:
                store.db.execute('INSERT INTO meta VALUES (?,?)',('baseline_status',json.dumps({
                    'BTC|binance|bitget':dict(key='BTC|binance|bitget',blocked=True,checked_at=1000,
                        baseline_bps='80',mad_bps='0',count=361,first_at=0,latest_at=1000,ready=True,at=1000)})))
            with Store(path) as store:
                state=Baseline(store,lambda:1001).status('BTC','binance','bitget')
                self.assertTrue(state['blocked'])
                self.assertFalse(state['ready'])
                self.assertIsNone(store.db.execute("SELECT value FROM meta WHERE key='baseline_status'").fetchone())
