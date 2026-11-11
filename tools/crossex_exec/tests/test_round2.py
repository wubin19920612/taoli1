"""Round-two contracts; fake credentials and loopback-only network guards."""
import base64
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.fill_sim.core import dec
from tools.crossex_exec.accounts import Accounts, gate_margin
from tools.crossex_exec.adapters import Bitget, CrossEx
from tools.crossex_exec.execution import Executor
from tools.crossex_exec.storage import Store
from tools.crossex_exec.transport import bitget_sign
from test_exec import intent, instrument, setUpModule, tearDownModule


def cross_account():
    return dict(position_mode='DUAL', account_mode='CROSS_EXCHANGE',
                margin_balance='254.06', maintenance_margin='49.93', maintenance_margin_rate='5.0879',
                assets=[dict(coin='USDC',exchange_type='LIGHTER',liability='498.86')])


def settings():
    return dict(accountMode='unified', holdMode='hedge_mode',assetMode='multi_assets',
                symbolConfigList=[dict(category='USDT-FUTURES',symbol='BTCUSDT',marginMode='crossed',leverage='3')])


class UTAContracts(unittest.TestCase):
    def test_v3_signature_post_official_preimage(self):
        body='{"category":"SPOT","symbol":"BTCUSDT","side":"buy","orderType":"limit","qty":"0.1","price":"30000","timeInForce":"gtc"}'
        pre='16273667805456POST/api/v3/trade/place-order'+body
        expected=base64.b64encode(hmac.new(b'secret',pre.encode(),hashlib.sha256).digest()).decode()
        self.assertEqual(bitget_sign('secret','POST','/api/v3/trade/place-order','',body,'16273667805456'),expected)

    def test_defaults_and_v3_order_directions(self):
        self.assertEqual(CrossEx(Mock()).mode,'DUAL')
        http=Mock()
        bg=Bitget(http)
        self.assertEqual(bg.mode,'hedge_mode')
        for side,position,reduce in [('buy','long',False),('sell','short',False),('sell','long',True),('buy','short',True)]:
            i=dict(intent(ex='bitget',side=side,reduce=reduce),position_side=position)
            http.request.return_value={'clientOid':i['cid'],'orderId':'42'}
            bg.place(i)
            args,kw=http.request.call_args
            self.assertEqual(args,('POST','/api/v3/trade/place-order'))
            body=kw['body']
            self.assertEqual((body['side'],body['posSide']),(side,position))
            self.assertEqual((body['qty'],body['timeInForce'],body['category']),('1','ioc','USDT-FUTURES'))
            self.assertFalse({'force','size','tradeSide','productType','reduceOnly'} & body.keys())
        with self.assertRaises(ValueError):
            bg.place(intent(cid='x'*33,ex='bitget'))

    def test_read_routes_cancel_and_null_positions(self):
        http=Mock()
        bg=Bitget(http)
        http.request.return_value={'list':None}
        self.assertEqual(bg.positions(),[])
        self.assertEqual(http.request.call_args.args,('GET','/api/v3/position/current-position',{'category':'USDT-FUTURES'}))
        http.request.return_value={'list':[],'cursor':''}
        self.assertEqual(bg.open_orders(),[])
        self.assertEqual(http.request.call_args.args[1],'/api/v3/trade/unfilled-orders')
        bg.account()
        self.assertEqual(http.request.call_args.args,('GET','/api/v3/account/assets'))
        bg.settings()
        self.assertEqual(http.request.call_args.args,('GET','/api/v3/account/settings'))
        http.request.return_value={'takerFeeRate':'.0006'}
        self.assertEqual(bg.fee('BTCUSDT'),'.0006')
        self.assertEqual(http.request.call_args.args[1],'/api/v3/account/fee-rate')
        bg.cancel(intent(ex='bitget'))
        self.assertEqual(http.request.call_args.args,('POST','/api/v3/trade/cancel-order'))
        self.assertEqual(http.request.call_args.kwargs['body'],{'category':'USDT-FUTURES','clientOid':'ce-test'})
        http.request.return_value={}
        with self.assertRaises((KeyError,ValueError)):
            bg.positions()

    def test_v3_fills_cursor_and_positive_fee_cost(self):
        http=Mock()
        bg=Bitget(http)
        row=dict(execId='t1',orderId='42',symbol='BTCUSDT',execQty='1',execPrice='100',
                 feeDetail=[dict(feeCoin='USDT',fee='.06')],createdTime='1700000000000')
        http.request.side_effect=[{'list':[row]*100,'cursor':'next'},{'list':[],'cursor':''}]
        result=bg.fills(dict(intent(ex='bitget'),oid='42',filled='1'))
        self.assertEqual(dec(result['fee']),dec('.06'))
        self.assertEqual(http.request.call_args.args[2]['cursor'],'next')
        self.assertEqual(http.request.call_args.args[1],'/api/v3/trade/fills')

    def accounts(self):
        cross,bg=Mock(mode='DUAL'),Mock(mode='hedge_mode')
        cross.account.return_value=cross_account()
        cross.positions.return_value=[]
        cross.leverage.return_value={instrument().symbol:'3'}
        bg.settings.return_value=settings()
        bg.account.return_value=dict(usdtEquity='30000',accountEquity='31000',effEquity='1200',mmr='300')
        bg.positions.return_value=[]
        bg.open_orders.return_value=[]
        return Accounts(cross,bg)

    def test_startup_checks_actual_modes_when_empty(self):
        a=self.accounts()
        snap=a.validate_startup()
        self.assertEqual(snap['equity_by_venue'],{'crossex':'254.06','bitget':'30000'})
        self.assertEqual(snap['liabilities'][0]['liability'],'498.86')
        self.assertEqual(dec(snap['margins'][1]),4)
        for change in ({'accountMode':'hybrid'},{'holdMode':'one_way_mode'}):
            a.bitget.settings.return_value=dict(settings(),**change)
            with self.assertRaises(ValueError):
                a.validate_startup()
        a.bitget.settings.return_value=settings()
        a.cross.account.return_value=dict(cross_account(),position_mode='SINGLE')
        with self.assertRaises(ValueError):
            a.validate_startup()

    def test_fee_special_precedence_and_failed_refresh_clears_cache(self):
        a=self.accounts()
        x,y=instrument(),instrument('bitget')
        a.cross.fees.return_value=[dict(exchange_type='BINANCE',future_taker_fee='.00048',
                                       special_fee_list=[dict(symbol=x.symbol,taker_fee_rate='.0001')])]
        a.bitget.fee.return_value='.0006'
        self.assertEqual(a.fees([x,y]),{x.market.key:'.0001',y.market.key:'.0006'})
        a.bitget.fee.assert_called_once_with('BTCUSDT')
        a.cross.fees.return_value=[dict(exchange_type='BINANCE',future_taker_fee='.00048')]
        self.assertEqual(a.fees([x])[x.market.key],'.00048')
        a.cross.fees.side_effect=ValueError('unavailable')
        a.bitget.fee.side_effect=ValueError('unavailable')
        self.assertEqual(a.fees([x,y]),{})

    def test_crossex_zero_maintenance_and_reported_ratio(self):
        self.assertEqual(gate_margin(cross_account(),'DUAL'),dec('5.0879'))
        self.assertEqual(gate_margin(dict(cross_account(),maintenance_margin='0',maintenance_margin_rate='0'),'DUAL'),dec('1e30'))


class HardLimitTests(unittest.TestCase):
    def setUp(self):
        from tools.crossex_exec.limits import HardLimits
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.db')
        self.addCleanup(self.store.close)
        self.snapshot=dict(equity_by_venue={'crossex':'1000','bitget':'30000'},notionals={},margins=['4','4'],liabilities=[])
        self.provider=Mock(side_effect=lambda:self.snapshot)
        self.limits=HardLimits(self.store,self.provider)
        self.limits.initialize(self.snapshot)
        self.adapter=Mock()
        self.adapter.place.return_value='42'
        self.adapter.detail.return_value=dict(oid='42',venue_state='FILLED',filled='0',rejected=False,terminal=True)
        self.executor=Executor(self.store,{'crossex':self.adapter,'bitget':self.adapter},live=True,check_order=self.limits.check_order)

    def assert_blocked(self,i):
        result=self.executor.execute(i)
        self.assertEqual(result['state'],'UNSENT')
        self.adapter.place.assert_not_called()

    def test_leg_limit_rejects_before_adapter(self):
        self.assert_blocked(intent(quantity='1.50001'))

    def test_total_limit_includes_remote_positions(self):
        self.snapshot['notionals']={'external': '500.01'}
        self.assert_blocked(intent())

    def test_pending_pair_and_unknown_reserve_capacity(self):
        self.snapshot['notionals']={'external':'450'}
        results=self.executor.execute_pair([intent('first'),intent('second',ex='bitget')])
        # Both intents reserve capacity before threads run; at most one can pass.
        self.assertLessEqual(sum(r['state']=='TERMINAL' for r in results),1)
        pending=dict(intent('pending',quantity='5.01'),state='UNKNOWN',filled='0',oid=None)
        self.store.save_order(pending)
        self.adapter.reset_mock()
        self.snapshot['notionals']={}
        self.assert_blocked(intent('new'))

    def test_each_equity_drop_cannot_be_offset_by_other_account(self):
        for venue,other in [('crossex','bitget'),('bitget','crossex')]:
            with self.subTest(venue=venue):
                baseline=self.store.meta('account_equity_baseline')
                self.snapshot['equity_by_venue']=dict(baseline)
                self.snapshot['equity_by_venue'][venue]=str(dec(baseline[venue])-150)
                self.snapshot['equity_by_venue'][other]=str(dec(baseline[other])+200)
                self.assert_blocked(intent('drop-'+venue))
                self.assertEqual(self.store.meta('state'),'HALTING')
                self.store.set_meta('state','RUNNING')

    def test_boundary_and_reduce_exemption(self):
        self.snapshot['notionals']={'external':'450'}
        self.assertEqual(self.executor.execute(intent(quantity='1.5'))['state'],'TERMINAL')
        self.snapshot['notionals']={'external':'9000'}
        self.assertEqual(self.executor.execute(intent('close',reduce=True,side='sell',quantity='99'))['state'],'TERMINAL')

    def test_config_can_only_lower_and_missing_equity_blocks(self):
        from tools.crossex_exec.limits import HardLimits
        for config in ({'CROSSEX_MAX_LEG_USD':'151'},{'CROSSEX_MAX_TOTAL_USD':'601'},
                       {'CROSSEX_MAX_EQUITY_DROP_USD':'151'},{'CROSSEX_MAX_LEG_USD':'NaN'},
                       {'CROSSEX_MAX_LEG_USD':'0'}):
            with self.subTest(config=config),self.assertRaises(ValueError):
                HardLimits(self.store,self.provider,config)
        lower=HardLimits(self.store,self.provider,{'CROSSEX_MAX_LEG_USD':'50'})
        with self.assertRaises(ValueError):
            lower.check_order(intent())
        self.snapshot['equity_by_venue']={}
        self.assert_blocked(intent('missing'))

    def test_before_send_rechecks_after_admission(self):
        def place(i,before_send):
            self.snapshot['equity_by_venue']['bitget']='29850'
            before_send()
            self.fail('wire send must not happen')
        self.adapter.place.side_effect=place
        self.assertEqual(self.executor.execute(intent())['state'],'UNSENT')
        self.assertEqual(self.store.meta('state'),'HALTING')

    def test_baselines_survive_restart(self):
        self.snapshot['equity_by_venue']['bitget']='29900'
        self.limits.initialize(self.snapshot)
        self.assertEqual(self.store.meta('account_equity_baseline')['bitget'],'30000')

