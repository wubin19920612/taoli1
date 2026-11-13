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

    def test_mixed_contract_funding_does_not_contaminate_program_metrics(self):
        w=self.world();w.advance();w.open();w.advance()
        prior=w.store.meta('account_loss_current')
        w.venues[0].quantity+=3
        w.venues[0].settle('-90')
        w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        self.assertEqual(w.store.meta('account_loss_current'),prior)
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
