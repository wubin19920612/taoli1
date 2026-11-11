import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from tools.fill_sim.core import Book, Market, Spec, dec
from tools.crossex_exec.accounts import Accounts, gate_margin, bitget_margin, local_positions, remote_positions
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.market import parse_history, native_instrument, turnover
from tools.crossex_exec.policy import mapped_instrument
from tools.crossex_exec.reporting import report
import test_exec as fixtures
from test_exec import instrument, intent, setUpModule, tearDownModule


class FakeMarket:
    def __init__(self):
        self.mapping={i.market.key:i for i in (instrument(),instrument('bitget'))}
        self.missing=False

    def bootstrap(self,bases=()):
        return self.mapping

    def books(self,instruments):
        if self.missing:
            return [None]*len(instruments)
        return [Book([['99','10']],[['100','10']],time.time()) if i.market.exchange=='binance'
                else Book([['102','10']],[['103','10']],time.time()) for i in instruments]

    def funding(self,instruments):
        now=time.time()
        return [dict(rate='.0001',interval=28800,next=now+4000,captured_at=now,history_ok=True,max_abs_24h='.001') for _ in instruments]

    def turnovers(self,instruments):
        return ['5000000']*len(instruments)


class FakeAccounts:
    def __init__(self,cross,bitget,market):
        self.cross,self.bitget=cross,bitget
        self.fees_by_market={k:'.0002' for k in market.mapping}
        self.data=dict(positions={},margins=['4','4'],upnl='0', equity_by_venue={'crossex':'1000','bitget':'30000'}, notionals={}, liabilities=[])

    def validate_startup(self):
        return self.snapshot()

    def check_instruments(self,instruments):
        pass

    def snapshot(self):
        return self.data

    def fees(self,instruments):
        return self.fees_by_market


def trigger(identity='shadow-1'):
    return dict(id=identity,strategy_version='shadow-v2',variant='conv-half',spread_tier='0.5–1%',
                liquidity='gt50k',fake_flag=False,symbol='BTC',opened_at=time.time(),
                buy_market_key=instrument().dump()['market'],sell_market_key=instrument('bitget').dump()['market'])


class EngineTests(unittest.TestCase):
    def setUp(self):
        fixtures.HTTPExecutionTests.setUp(self)
        self.market=FakeMarket()
        self.accounts=FakeAccounts(self.cross,self.bitget,self.market)
        self.engine=Engine(self.store,self.market,self.accounts,live=True,stop_file=Path(self.tmp.name)/'STOP')
        self.engine.executor=self.exec
        self.exec.can_open=self.engine.can_open
        self.exec.check_order=self.engine.limits.check_order
        self.engine.limits.initialize(self.accounts.snapshot())
        self.engine.risk='RUN'
        self.engine.last_reconcile=time.time()
        self.store.set_meta('mapping',{'at':time.time()})
        self.store.set_meta('actual_fees',{'at':time.time()})

    def tearDown(self):
        fixtures.HTTPExecutionTests.tearDown(self)

    def test_startup_mode_error_propagates_before_recovery_or_market(self):
        with (patch.object(self.accounts,'validate_startup',side_effect=ValueError('mode_mismatch')),
              patch.object(self.exec,'recover') as recover,
              patch.object(self.market,'bootstrap') as bootstrap):
            with self.assertRaisesRegex(ValueError,'mode_mismatch'):
                self.engine.startup()
            recover.assert_not_called()
            bootstrap.assert_not_called()

    def test_foreign_unfilled_blocks_startup_but_owned_order_recovers(self):
        self.accounts.data['open_orders']=[dict(clientOid='foreign')]
        with self.assertRaisesRegex(ValueError,'foreign_bitget'):
            self.engine.startup()
        i=dict(intent('owned',ex='bitget'),state='UNKNOWN',filled='0',leg=1)
        self.store.save_order(i)
        self.accounts.data['open_orders']=[dict(clientOid='owned')]
        with patch.object(self.exec,'recover') as recover:
            self.engine.startup()
            recover.assert_called_once()

    def test_individual_equity_drop_flattens_even_when_combined_equity_gains(self):
        self.engine.ingest(trigger())
        self.accounts.data['equity_by_venue']={'crossex':'1200','bitget':'29850'}
        self.accounts.data['positions']=local_positions(self.store.pairs(active=True))
        original=self.engine.close_pair
        def close(pair,reason):
            original(pair,reason)
            self.accounts.data['positions']={}
        with patch.object(self.engine,'close_pair',side_effect=close):
            self.engine.reconcile()
        self.assertEqual(self.store.meta('state'),'HALTED')
        closes=[o for o in self.store.all_orders() if o['reduce_only']]
        self.assertEqual(len(closes),2)
        self.assertTrue(all(o['state']=='TERMINAL' for o in closes))

    def test_admission_equity_drop_blocks_both_apis_and_halts(self):
        self.accounts.data['equity_by_venue']['bitget']='29850'
        with (patch.object(self.cross,'place') as cross_place,
              patch.object(self.bitget,'place') as bitget_place):
            self.engine.ingest(trigger())
            cross_place.assert_not_called()
            bitget_place.assert_not_called()
        self.assertEqual(self.store.meta('state'),'HALTED')

    def test_restart_equity_halt_flattens_before_catalog_bootstrap(self):
        self.engine.ingest(trigger())
        self.accounts.data['equity_by_venue']['bitget']='29850'
        with patch.object(self.market,'bootstrap',side_effect=AssertionError('must flatten first')):
            self.engine.startup()
        self.assertEqual(self.store.meta('state'),'HALTED')
        self.assertFalse(self.store.pairs(active=True))

    def test_full_shadow_pipeline_persists_prices_without_private_writes(self):
        self.engine.live=self.exec.live=False
        with (patch.object(self.cross,'place',side_effect=AssertionError('place')),
              patch.object(self.bitget,'place',side_effect=AssertionError('place')),
              patch.object(self.cross,'cancel',side_effect=AssertionError('cancel')),
              patch.object(self.bitget,'cancel',side_effect=AssertionError('cancel'))):
            p=self.engine.ingest(trigger())
            self.assertEqual(p['state'],'SHADOW')
            orders=self.store.all_orders()
            self.assertEqual(len(orders),2)
            self.assertTrue(all(dec(o['quantity'])>0 and dec(o['price'])>0 for o in orders))
        self.assertFalse(self.gate.calls+self.bg.calls)

    def test_two_legs_send_concurrently(self):
        self.gate.barrier=self.bg.barrier=threading.Barrier(2)
        p=self.engine.ingest(trigger())
        self.assertEqual(p['state'],'OPEN')
        self.assertEqual(len(self.store.all_orders()),2)

    def test_single_leg_excess_reduced_only(self):
        self.bg.fill='0.4'
        p=self.engine.ingest(trigger())
        self.assertEqual(list(map(dec,p['remaining'])),[dec('.4')]*2)
        repair=[c for c in self.gate.calls if c[0]=='POST'][1][2]
        self.assertEqual(repair['reduce_only'],'true')
        self.assertEqual(repair['side'],'SELL')
        self.assertEqual(self.store.meta('single_failures'),1)

    def test_repair_failure_three_tries_stops_opening(self):
        self.bg.fill='0.4'
        self.gate.reject_reduce=True
        p=self.engine.ingest(trigger())
        self.assertEqual(sum(c[0]=='POST' for c in self.gate.calls),4)
        self.assertEqual(self.store.meta('stop_reason'),'unhedged_position')
        self.assertFalse(self.engine.can_open())
        self.assertNotEqual(*map(dec,p['remaining']))

    def test_stop_file_blocks_new_but_allows_reduce(self):
        p=self.engine.ingest(trigger())
        self.engine.stop_file.touch()
        self.assertIsNone(self.engine.ingest(trigger('second')))
        self.engine.close_pair(p,'manual_stop_test')
        self.assertEqual(p['state'],'CLOSED')
        self.assertTrue(all(o['reduce_only'] for o in self.store.all_orders()[2:]))

    def test_duplicate_symbol_and_three_pair_limit(self):
        self.engine.ingest(trigger())
        self.engine.ingest(trigger('second'))
        self.assertEqual(len(self.store.pairs()),1)
        p=self.store.pairs()[0]
        for n in range(2):
            self.store.pair(dict(p,id='placeholder'+str(n),base='OTHER'+str(n)))
        with patch('tools.crossex_exec.engine.review',side_effect=AssertionError('capacity must precede review')):
            self.assertIsNone(self.engine.ingest(trigger('third')))

    def test_reconciliation_mismatch_latches(self):
        self.accounts.data['positions']={'bitget|BTCUSDT|long':dec(1)}
        self.engine.reconcile()
        self.assertFalse(self.engine.can_open())
        self.assertEqual(self.store.meta('stop_reason'),'position_mismatch')

    def test_restart_recovers_terminal_journal_into_pair(self):
        p=self.engine.ingest(trigger())
        p.update(remaining=['0','0'],state='OPENING')
        self.store.pair(p)
        self.engine.refresh_pair(p)
        self.assertEqual(p['state'],'OPEN')
        self.assertGreater(dec(p['remaining'][0]),0)

    def test_restart_unknown_never_resends(self):
        self.gate.timeout_once=self.gate.not_found=True
        p=self.engine.ingest(trigger())
        before=sum(c[0]=='POST' for c in self.gate.calls)
        self.exec.recover()
        self.assertEqual(sum(c[0]=='POST' for c in self.gate.calls),before)
        self.assertEqual(p['state'],'UNKNOWN')

    def test_stale_books_force_close_with_stored_prices(self):
        p=self.engine.ingest(trigger())
        self.market.missing=True
        p['last_good']=[time.time()-61]*2
        self.engine.track(p)
        self.assertEqual(p['state'],'CLOSED')
        self.assertEqual(p['exit_reason'],'stale_exit')

    def test_halt_flattens_then_latches_without_restart(self):
        self.engine.ingest(trigger())
        self.accounts.data['upnl']='-101'
        self.accounts.data['positions']=local_positions(self.store.pairs(active=True))
        original=self.engine.close_pair
        def close(pair,reason):
            original(pair,reason)
            self.accounts.data['positions']={}
        with patch.object(self.engine,'close_pair',side_effect=close):
            self.engine.reconcile()
        self.assertEqual(self.store.meta('state'),'HALTED')
        with self.assertRaises(ValueError):
            self.store.assert_startable()

    def test_residual_position_prevents_halted_exit(self):
        self.engine.ingest(trigger())
        self.gate.reject_reduce=True
        self.store.set_meta('state','HALTING')
        self.engine.flatten_all('circuit_breaker')
        self.assertEqual(self.store.meta('state'),'HALTING')
        self.assertTrue(self.store.pairs(active=True))

    def test_report_distinguishes_shadow_and_actual(self):
        p=self.engine.ingest(trigger())
        self.engine.close_pair(p,'test')
        r=report(self.store)
        self.assertEqual(r['live_pairs'],1)
        self.assertIsNotNone(r['trades'][0]['send_delta_ms'])
        self.assertIsNotNone(r['trades'][0]['orders'][0]['slippage_bps'])
        self.assertLess(dec(r['trades'][0]['net_usd']),0)

    def test_margin_flatten_and_daily_loss_stop(self):
        p=self.engine.ingest(trigger())
        self.accounts.data['positions']=local_positions(self.store.pairs(active=True))
        self.accounts.data['upnl']='-20'
        self.engine.reconcile()
        self.assertEqual(self.engine.risk,'STOP')
        self.assertEqual(p['state'],'OPEN')
        self.accounts.data['upnl']='0'
        self.accounts.data['margins']=['4','1.99']
        self.engine.reconcile()
        self.assertFalse(self.store.pairs(active=True))

    def test_three_consecutive_single_legs_and_reject_rate_block_immediately(self):
        self.store.set_meta('single_failures',3)
        self.assertFalse(self.engine.can_open())
        self.store.set_meta('single_failures',0)
        i=dict(intent(),state='REJECTED',sent_ms=time.time()*1000,accounting_pending=False)
        self.store.save_order(i)
        self.assertFalse(self.engine.can_open())

    def test_no_repair_more_than_three_after_tracking_or_restart(self):
        self.bg.fill='.4'
        self.gate.reject_reduce=True
        p=self.engine.ingest(trigger())
        before=len(self.gate.calls)
        self.engine.balance(p)
        self.assertEqual(len(self.gate.calls),before)

    def test_actual_fees_missing_or_low_volume_never_sends(self):
        with patch.object(self.accounts,'check_instruments',side_effect=ValueError('missing_fees')):
            self.engine.ingest(trigger())
        with patch.object(self.market,'turnovers',return_value=['4999999','9000000']):
            self.engine.ingest(trigger('low-volume'))
        self.assertFalse(self.gate.calls+self.bg.calls)

    def test_equity_delta_catches_unjournaled_loss(self):
        self.accounts.data['equity']='200'
        self.engine.reconcile()
        self.accounts.data['equity']='99'
        self.engine.reconcile()
        self.assertEqual(self.store.meta('state'),'HALTED')

    def test_live_to_shadow_with_open_positions_refused_before_network(self):
        self.engine.ingest(trigger())
        self.store.set_meta('live_mode',True)
        self.engine.live=False
        with self.assertRaisesRegex(ValueError,'mode_change'):
            self.engine.startup()

    def test_close_each_policy_signal_reaches_reduce_http(self):
        scenarios=('max_age','funding_exit','adverse_spread','loss_limit','converged')
        for n,reason in enumerate(scenarios):
            with self.subTest(reason=reason):
                p=self.engine.ingest(trigger('exit-'+str(n)))
                self.assertIsNotNone(p)
                if reason=='max_age':
                    p['opened_at']=time.time()-7200
                elif reason=='funding_exit':
                    p['funding'][0]['next']=time.time()+299
                if reason=='converged':
                    books=[Book([['101','10']],[['102','10']],time.time()),Book([['99','10']],[['100','10']],time.time())]
                elif reason=='loss_limit':
                    books=[Book([['98','10']],[['99','10']],time.time()),Book([['99','10']],[['100','10']],time.time())]
                else:
                    # Positive spread > 50bp, but absolute loss below 1%.
                    books=[Book([['100','10']],[['100.1','10']],time.time()),Book([['100.5','10']],[['100.6','10']],time.time())]
                # Save modified entry timestamps before track/refresh reads the journal.
                self.store.pair(p)
                with patch.object(self.market,'books',return_value=books):
                    self.engine.track(p)
                self.assertEqual(p['state'],'CLOSED')
                self.assertEqual(p['exit_reason'],reason)

    def test_funding_overrun_not_reported_as_known_profit(self):
        p=self.engine.ingest(trigger())
        p['funding'][0]['next']=time.time()-1
        self.engine.close_pair(p,'funding_exit')
        self.assertIsNone(p['net_usd'])
        self.assertFalse(p['funding_known'])
        self.assertEqual(report(self.store)['incomplete_accounting'],1)


class MarketSafetyTests(unittest.TestCase):
    def rule(self):
        return dict(symbol='GATE_FUTURE_BTC_USDT',exchange_type='GATE',business_type='FUTURE',state='live',
                    delist_time='0',tag='',support_cross='true',lot_size='.0001',min_size='.0001',
                    min_notional=None,max_limit_size='1200',tick_size='.1',contract_size=None)

    def test_crossex_uses_base_quantity_not_native_contracts(self):
        m=Market('gate','future','BTC_USDT')
        s=Spec('BTC','USDT','.0001','.0001','.0001','0')
        i=mapped_instrument(m,s,'.1',[self.rule()])
        self.assertEqual(dec(i.spec.qty_step),dec('.0001'))
        self.assertEqual(i.symbol,'GATE_FUTURE_BTC_USDT')

    def test_ambiguous_or_inactive_mapping_rejected(self):
        m=Market('gate','future','BTC_USDT')
        s=Spec('BTC','USDT','.0001','.0001','.0001','0')
        for rules in ([self.rule(),self.rule()],[dict(self.rule(),tag='xyz')],[dict(self.rule(),state='suspend')]):
            with self.subTest(rules=rules),self.assertRaises(ValueError):
                mapped_instrument(m,s,'.1',rules)

    def test_funding_history_missing_window_or_gap_fails_closed(self):
        m=instrument().market
        now=200000
        s=dict(rate='.0001',interval=28800,next=now+10000,captured_at=now)
        def rows(times):
            return [dict(symbol='BTCUSDT',fundingTime=str(t*1000),fundingRate='.001') for t in times]
        good=[now-100000,now-71200,now-42400,now-13600]
        self.assertTrue(parse_history(m,rows(good),s,now)['history_ok'])
        self.assertFalse(parse_history(m,rows(good[1:]),s,now)['history_ok'])
        self.assertFalse(parse_history(m,rows([good[0],good[-1]]),s,now)['history_ok'])

    def test_bitget_uta_margin_ratio(self):
        d=dict(effEquity='1200',mmr='300',usdtEquity='1500')
        self.assertEqual(bitget_margin(d),4)
        self.assertEqual(bitget_margin(dict(d,mmr='0')),dec('1e30'))
        with self.assertRaises(ValueError):
            bitget_margin(dict(d,mmr='-1'))

    def test_gate_margin_ratio_checks_units(self):
        d=dict(position_mode='SINGLE',account_mode='CROSS_EXCHANGE',exchange_type='CROSSEX',margin_balance='1200',maintenance_margin='250',maintenance_margin_rate='4.8')
        self.assertEqual(gate_margin(d,'SINGLE'),dec('4.8'))
        with self.assertRaises(ValueError):
            gate_margin(dict(d,maintenance_margin_rate='480'),'SINGLE')

    def test_positions_preserve_side_and_full_symbol(self):
        gate=[dict(symbol='GATE_FUTURE_BTC_USDT',position_side='NONE',position_qty='-.1')]
        bg=[dict(symbol='BTCUSDT',posSide='long',total='.1',marginMode='crossed')]
        self.assertEqual(remote_positions(gate,bg),{'crossex|GATE_FUTURE_BTC_USDT|short':dec('.1'),'bitget|BTCUSDT|long':dec('.1')})

    def test_okx_turnover_is_lower_bound(self):
        m=Market('okx','future','BTC-USDT-SWAP')
        data={'code':'0','data':[dict(instId=m.raw_symbol,volCcy24h='100',low24h='40000')]}
        self.assertEqual(dec(turnover(m,data)),4000000)
