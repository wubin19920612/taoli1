"""Read-only funding filters, crash ownership, and additive public contracts."""
import unittest
from unittest.mock import Mock

from tools.fill_sim.core import dec
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.api_data import JournalView
from test_exec import intent, setUpModule, tearDownModule
from test_round20_positions import PositionFixture, HL, ASTER, unitree
import test_round21_program as r21


class PublicContractTests(PositionFixture,unittest.TestCase):
    def test_closed_journal_currencies_are_still_quoted(self):
        self.http.responses['/api/v4/crossex/positions']=[]
        order=dict(intent(),leg=0,state='TERMINAL',filled='0',fee='0',created_at=self.now)
        order['instrument']['symbol']=HL
        self.store.save_order(order)
        snapshot=self.engine.read_account_snapshot()
        self.assertIn('USDC',snapshot['external_fx'])

    def test_rebaseline_offset_uses_the_same_usd_unit_as_journal_risk(self):
        from tools.crossex_exec.equity import EquityAdmin
        from tools.crossex_exec.program_risk import journal_pnl
        from test_round12_fx import quotes
        order=dict(intent(),leg=0,state='TERMINAL',filled='1',fee='1',vwap='100',
                   fill_ms=int(self.now*1000),created_at=self.now)
        order['instrument']['symbol']=HL
        self.store.save_order(order)
        self.store.save_order(dict(order,cid='close',reduce_only=True,vwap='90',fee='1'))
        fx={'USDC':quotes(self.now,('.8','.8'))}
        admin=EquityAdmin(self.store,self.accounts,clock=lambda:self.now)
        for _ in range(2):
            self.store.set_meta('risk_pnl_baseline',admin.pnl_offset(fx=fx))
            self.assertEqual(journal_pnl(self.store,self.now,fx=fx),(dec(0),dec(0)))

    def test_summary_adds_program_risk_and_keeps_account_observations(self):
        self.engine.limits.initialize(self.accounts.snapshot())
        self.engine.reconcile()
        value=JournalView(self.path,{},clock=lambda:self.now).get('summary',{})
        self.assertEqual(value['program_risk']['accounting_basis'],'own_fills_funding_marks')
        self.assertTrue(value['program_risk']['verified'])
        self.assertEqual(value['program_risk']['total_usd'],0)
        self.assertEqual(value['program_risk']['daily_usd'],0)
        self.assertEqual(value['program_risk']['ownership_mode'],'local_order_history')
        self.assertIn('equity_usd',value['accounts']['crossex'])
        self.assertIn('today',value); self.assertIn('total',value)
        self.now+=11
        stale=JournalView(self.path,{},clock=lambda:self.now).get('summary',{})
        self.assertFalse(stale['program_risk']['verified'])
        self.assertIsNone(stale['program_risk']['total_usd'])

    def test_remote_effective_ownership_excludes_shadow_and_preserves_registration_field(self):
        self.engine.account_cache.publish(self.engine.read_account_snapshot(),self.now)
        order=dict(intent(),state='SHADOW',filled='0')
        order['instrument']['symbol']=HL
        self.store.save_order(order)
        value=JournalView(self.path,{},clock=lambda:self.now).get('remote-positions',{})['positions'][0]
        self.assertEqual(value['ownership'],'unregistered')
        self.assertEqual(value['effective_ownership'],'external')
        self.assertFalse(value['registration_required'])
        self.assertEqual(value['ownership_basis'],'local_order_history')


class FundingReadTests(unittest.TestCase):
    def test_funding_only_reads_filter_exact_symbols_and_preserve_filters_on_pages(self):
        cross,bg=Mock(),Mock()
        own='BINANCE_FUTURE_BTC_USDT'
        def cross_response(method,path,params,**kwargs):
            self.assertEqual(method,'GET')
            if path.endswith('account_book'):
                self.assertEqual(params['statement_type'],'FUNDING_FEE')
                if params['page']==1:
                    return [dict(id=str(n),symbol=own if n==0 else ASTER) for n in range(1000)]
                return []
            self.assertTrue(path.endswith('history_positions'))
            self.assertEqual(params['symbol'],own)
            return []
        def bg_response(method,path,params,**kwargs):
            self.assertEqual(method,'GET')
            if path.endswith('financial-records'):
                kind=params['type']
                self.assertIn(kind,('CONTRACT_MAIN_SETTLE_FEE_USER_IN','CONTRACT_MAIN_SETTLE_FEE_USER_OUT'))
                self.assertEqual(params['category'],'USDT-FUTURES')
                if 'cursor' not in params:
                    return dict(list=[dict(id=kind,symbol='BTCUSDT'),dict(id=kind+'ext',symbol='ETHUSDT')],cursor=kind)
                return dict(list=[],cursor=None)
            self.assertEqual(params['symbol'],'BTCUSDT')
            return dict(list=[],cursor=None)
        cross.request.side_effect=cross_response; bg.request.side_effect=bg_response
        accounts=Accounts(CrossEx(cross),Bitget(bg))
        activity=accounts.program_activity({('crossex',own),('bitget','BTCUSDT')},100,200)
        self.assertTrue(activity['complete'])
        self.assertEqual([r['symbol'] for r in activity['crossex_book']],[own])
        self.assertEqual([r['symbol'] for r in activity['bitget_book']],['BTCUSDT','BTCUSDT'])
        self.assertEqual(activity['crossex_trades'],[])
        self.assertEqual(cross.request.call_count,3)
        self.assertEqual(bg.request.call_count,5)


class AdditionalSafetyTests(unittest.TestCase):
    world=r21.ProgramAccountingTests.world

    def test_user_funding_before_first_program_order_is_excluded(self):
        w=self.world(); w.advance(120)
        for venue in w.venues:
            venue.history.append(dict(id='outside-before',created=int((w.now-20)*1000),
                closed=int((w.now-10)*1000),funding='-90'))
            venue.book.append(dict(venue.cash_row('outside-before','-90',funding=True),
                **({'create_time':str(int((w.now-10)*1000))} if venue.leg==0
                   else {'ts':str(int((w.now-10)*1000))})))
        w.open(); w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        self.assertEqual(w.engine.totals(),(dec(0),dec(0)))
        w.venues[0].settle('-2');w.advance()
        self.assertEqual(w.engine.totals(),(dec(-2),dec(-2)))

    def test_mixed_contract_funding_does_not_contaminate_program_metrics(self):
        w=self.world();w.advance();w.open();w.advance()
        w.venues[0].quantity+=3
        w.venues[0].settle('-90')
        w.advance()
        # Mixed inventory no longer refuses global proof; the whole expense
        # counts against the unchanged daily cap, with no external profit credit.
        self.assertTrue(w.store.meta('external_income')['verified'])
        self.assertEqual(w.engine.totals(), (dec(-90), dec(-90)))
        self.assertIn('daily_loss', w.store.meta('stop_locks'))
        self.assertFalse(w.store.meta('position_mismatch'))
        self.assertFalse(w.engine.can_open())

    def test_own_float_uses_journal_entry_and_mark_not_remote_pnl(self):
        w=self.world();w.advance();w.open();w.advance()
        order=next(r for r in w.store.iter_orders() if r['leg']==0)
        w.prices[0]=dec(99)
        original=w.accounts.snapshot
        def wrong_pnl():
            result=original()
            key='crossex|'+w.instruments[0].symbol+'|long'
            result['upnl_by_position'][key]='999999'
            result['upnl']='999999'
            return result
        w.accounts.snapshot=wrong_pnl
        w.advance()
        expected=dec(order['filled'])*(dec(99)-dec(order['vwap']))
        self.assertEqual(w.engine.totals(),(expected,expected))


if __name__=='__main__':
    unittest.main()
