"""PM currency bounds: offline prices, real accounting, unchanged hard guards."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.fill_sim.core import dec, day
from tools.crossex_exec.market import MarketData
from tools.crossex_exec.storage import Store
from test_exec import setUpModule, tearDownModule, intent
from test_round11_external import ExternalFixture, SYMBOL, position


def quotes(now, prices=('1', '1'), coin='USDC', age=0):
    return [dict(venue=venue, base=coin, quote='USDT', market_type='spot',
                 symbol=coin+'-USDT' if venue=='okx' else coin+'USDT',
                 bid=price, ask=price, at=now-age, received_at=now)
            for venue,price in zip(('okx','bitget','bybit'),prices)]


def payload(venue, now, coin='USDC', bid='.9999', ask='1.0001'):
    if venue=='okx':
        return dict(code='0',data=[dict(instType='SPOT',instId=coin+'-USDT',
            bidPx=bid,askPx=ask,ts=str(int(now*1000)))])
    if venue=='bitget':
        return dict(code='00000',data=[dict(symbol=coin+'USDT',bidPr=bid,askPr=ask,ts=str(int(now*1000)))])
    return dict(retCode=0,time=int(now*1000),result=dict(category='spot',
        list=[dict(symbol=coin+'USDT',bid1Price=bid,ask1Price=ask)]))


class FXMathTests(unittest.TestCase):
    def band(self, rows, now=1000, coin='USDC'):
        from tools.crossex_exec.external_fx import quote_band
        return quote_band(coin,rows,now)

    def test_median_two_and_three_venues_and_uncertainty(self):
        for prices,mid in ((('1','1.002'),'1.001'),(('1','1.001','1.002'),'1.001')):
            with self.subTest(prices=prices):
                result=self.band(quotes(1000,prices))
                self.assertEqual(dec(result['median']),dec(mid))
                self.assertEqual(dec(result['lower']),dec(mid)*dec('.998'))
                self.assertEqual(dec(result['upper']),dec(mid)*dec('1.002'))
                self.assertEqual(len(result['sources']),len(prices))
                self.assertEqual(result['expires_at'],1060)

    def test_age_boundary_exchange_clock_receipt_and_duplicate_venues(self):
        self.assertEqual(self.band(quotes(1000,age=60))['expires_at'],1000)
        for rows in (quotes(1000,age=60.001),quotes(1000,age=-.001),quotes(939.999),
                     quotes(1000)[:1],[],[quotes(1000)[0]]*2):
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                self.band(rows)

    def test_extreme_divergence_is_not_silently_trimmed_or_clamped_to_one(self):
        for prices in (('1','1.5'),('1','1','4'),('.8','1')):
            with self.subTest(prices=prices),self.assertRaises(ValueError):
                self.band(quotes(1000,prices))
        self.assertEqual(dec(self.band(quotes(1000,('.8','.8')))['median']),dec('.8'))
        self.assertEqual(dec(self.band(quotes(1000,('64000','64000'),coin='BTC'),coin='BTC')['median']),64000)

    def test_bad_identity_nonfinite_zero_crossed_and_wide_quotes_fail(self):
        changes=({'quote':'USDC'},{'base':'DAI'},{'market_type':'future'},
                 {'symbol':'USDTUSDC'},{'venue':'unknown'},{'bid':'NaN'},
                 {'ask':'Infinity'},{'bid':'0'},{'ask':'-1'},{'bid':'2'},
                 {'bid':'.5','ask':'1.5'},{'received_at':1001})
        for change in changes:
            rows=quotes(1000); rows[0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.band(rows)

    def test_upper_contribution_bounds_every_price_inside_uncertainty_interval(self):
        from tools.crossex_exec.external_fx import upper_contribution
        band=self.band(quotes(1000,('.8','.8')))
        for native in map(dec,('-10000','-50','-.0001','0','.0001','50','10000')):
            upper=upper_contribution(native,band)
            for rate in map(dec,('.7984','.7992','.8','.8008','.8016')):
                with self.subTest(native=native,rate=rate):
                    self.assertGreaterEqual(upper,native*rate)
                    self.assertLessEqual(dec('-20')-upper,dec('-20')-native*rate)

    def test_parsers_exact_spot_pair_and_exchange_timestamp(self):
        from tools.crossex_exec.external_fx import parse_spot_quote
        for venue in ('okx','bitget','bybit'):
            data=payload(venue,1000)
            parsed=parse_spot_quote(venue,'USDC',data,1000.5)
            self.assertEqual(parsed['at'],1000)
            self.assertEqual(parsed['received_at'],1000.5)
            self.assertEqual(parsed['base'],'USDC')
            self.assertEqual(parsed['quote'],'USDT')
            bad=payload(venue,1000,coin='DAI')
            with self.assertRaises(ValueError): parse_spot_quote(venue,'USDC',bad,1000)
            if venue=='okx': data['data'][0]['instType']='SWAP'
            elif venue=='bybit': data['result']['category']='linear'
            else: data['data'][0].pop('ts')
            with self.assertRaises((ValueError,KeyError)): parse_spot_quote(venue,'USDC',data,1000)


class FXQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'exec.db'); self.addCleanup(self.store.close)
        self.now=1000
        self.market=MarketData(self.store,clock=lambda:self.now); self.addCleanup(self.market.close)

    def test_prices_use_existing_queue_limiter_cache_and_public_only_client(self):
        sends=[]
        def perform(venue,url):
            sends.append((venue,url))
            return payload(venue,self.now)
        with patch.object(self.market.client,'_perform',side_effect=perform):
            result=self.market.spot_fx(('USDC',))
            self.assertEqual(len(result['USDC']),3)
            self.assertEqual(self.market.queue.send_count,3)
            self.assertEqual({v for v,u in sends},{'okx','bitget','bybit'})
            self.assertTrue(all('spot' in u or 'instId=USDC-USDT' in u for v,u in sends))
            self.assertTrue(all(u.startswith('https://') for v,u in sends))
            result['USDC'].clear()
            self.now+=10
            self.assertEqual(len(self.market.spot_fx(('USDC',))['USDC']),3)
            self.assertEqual(len(sends),3)
            self.now+=21
            self.assertEqual(len(self.market.spot_fx(('USDC',))['USDC']),3)
            self.assertEqual(len(sends),6)

    def test_failed_refresh_never_renews_old_price_or_substitutes_one(self):
        with patch.object(self.market.client,'_perform',side_effect=lambda v,u:payload(v,self.now)):
            self.market.spot_fx(('USDC',))
        self.now+=61
        with patch.object(self.market.client,'_perform',side_effect=ValueError('offline')):
            self.assertEqual(self.market.spot_fx(('USDC',)),{'USDC':[]})

    def test_one_failed_source_leaves_two_and_native_usd_needs_no_send(self):
        def perform(venue,url):
            if venue=='bybit': raise ValueError('unavailable')
            return payload(venue,self.now)
        with patch.object(self.market.client,'_perform',side_effect=perform):
            self.assertEqual(len(self.market.spot_fx(('USDC',))['USDC']),2)
            before=self.market.queue.send_count
            self.assertEqual(self.market.spot_fx(('USD','USDT')), {})
            self.assertEqual(self.market.queue.send_count,before)

    def test_late_queued_converters_keep_original_currency_after_next_batch(self):
        queued=[]
        def batch(items,**kwargs):
            queued.extend(items)
            return [None]*len(items)
        with patch.object(self.market,'batch',side_effect=batch):
            self.market.spot_fx(('DAI','USDC'))
        # Simulate a timed-out worker returning only after the next coin batch.
        for index,coin in enumerate(('DAI','USDC')):
            for venue,url,convert in queued[index*3:index*3+3]:
                self.assertEqual(convert(payload(venue,self.now,coin))['base'],coin)

    def test_cache_cannot_extend_an_already_59_second_old_quote(self):
        with patch.object(self.market.client,'_perform',side_effect=lambda v,u:payload(v,self.now-59)) as send:
            self.market.spot_fx(('USDC',))
            self.assertEqual(send.call_count,3)
            self.now+=2
            self.market.spot_fx(('USDC',))
            self.assertEqual(send.call_count,6)


class FXAccountingTests(ExternalFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fx_prices=('1','1')
        self.fx_age=0
        self.cash='1000'
        self.market.spot_fx=lambda coins:{c:quotes(self.now,self.fx_prices,c,self.fx_age) for c in coins}
        self.change()
        self.rows=[position()]
        self.anchor()

    def snapshot(self):
        result=super().snapshot()
        if hasattr(self,'cash'):
            result['raw_accounts']['crossex']['assets']=[dict(exchange_type='HYPERLIQUID',coin='USDC',balance=self.cash)]
        return result

    def reconcile(self):
        self.now+=1
        self.engine.reconcile()
        return self.store.meta('external_income',{})

    def test_external_profit_upper_and_loss_lower_never_hide_own_loss(self):
        for pnl,equity,contribution,daily in (('50','1030','50.100','-20.100'),
                                             ('-50','930','-49.900','-20.100')):
            with self.subTest(pnl=pnl):
                self.rows=[position(upnl=pnl)]; self.equity=equity
                value=self.reconcile()
                self.assertTrue(value['verified'])
                self.assertIsNone(value['external_contribution'])
                self.assertIsNone(value['equity_daily'])
                # R14 keeps the old conservative FX observation, but a whole
                # account valuation gap is no longer attributed to the program.
                self.assertEqual(dec(self.store.meta('account_loss_current')['daily']),0)
                self.assertNotIn('daily_loss',self.store.meta('stop_locks'))
                self.assertTrue(self.engine.can_open())
                self.assertEqual(self.store.all_orders(),[])

    def test_baseline_difference_is_native_units_then_converted(self):
        self.rows=[position(upnl='-10')]; self.equity='990'; self.anchor()
        self.fx_prices=('1.001','1.001')
        self.rows=[position(upnl='10')]; self.equity='1010'
        value=self.reconcile()
        self.assertTrue(value['verified'])
        self.assertIsNone(value['external_contribution'])
        self.assertIsNone(value['equity_daily'])

    def test_expired_single_and_extreme_quotes_invalidate_prior_success_and_keep_hard_exit(self):
        self.rows=[position(upnl='-10')]; self.equity='990'
        self.assertTrue(self.reconcile()['verified'])
        for prices,age in ((('1','1'),60.001),(('1',),0),(('1','9'),0)):
            with self.subTest(prices=prices,age=age):
                self.fx_prices=prices; self.fx_age=age
                self.assertTrue(self.reconcile()['verified'])
                self.assertTrue(self.engine.can_open())
                self.assertNotIn('external_income_unverifiable',self.store.meta('stop_locks'))
        self.margin='1.5'
        with patch.object(self.engine,'flatten_all') as flatten:
            self.reconcile()
        flatten.assert_called()
        self.assertEqual(self.store.all_orders(),[])

    def test_certificate_rechecks_fx_expiry_before_account_ten_second_expiry(self):
        self.fx_age=59
        self.rows=[position(upnl='1')]; self.equity='1001'
        self.assertTrue(self.reconcile()['verified'])
        self.engine.require_external_income()
        self.now+=1.001
        self.engine.require_external_income()
        self.assertTrue(self.engine.can_open())
        self.engine.check_order(intent(ex='bitget'))

    def test_non_usdt_zero_net_still_requires_prices_for_nonzero_components(self):
        self.rows=[position(upnl='10')]; self.equity='1010'; self.anchor()
        self.fx_prices=()
        self.assertTrue(self.reconcile()['verified'])
        self.assertTrue(self.engine.can_open())

    def test_external_funding_fee_realized_and_close_count_once_in_native_cash(self):
        at=str(int((self.now+.5)*1000))
        self.trades=[dict(transaction_id='close',order_id='close',symbol=SYMBOL,exchange_type='HYPERLIQUID',
            business_type='FUTURE',fee_coin='USDC',side='SELL',position_mode='DUAL',position_side='LONG',
            qty='1',price='120',rpnl='20',fee='1',create_time=at)]
        self.book=[dict(id=kind,business_id='close',statement_type=kind,change=value,coin='USDC',
            symbol=SYMBOL,exchange_type='HYPERLIQUID',create_time=at)
            for kind,value in (('TRANSACTION','20'),('TRADING_FEE','-1'),('FUNDING_FEE','-2'))]
        self.cash='1017'; self.equity='1017'; self.rows=[]
        value=self.reconcile()
        self.assertTrue(value['verified'])
        self.assertIsNone(value['external_cash'])
        self.assertIsNone(value['external_contribution'])
        self.assertIsNone(value['equity_daily'])
        self.book.pop()
        self.assertTrue(self.reconcile()['verified'])

    def test_fx_success_never_clears_daily_lock_or_exempts_liability(self):
        self.store.set_meta('daily_loss_day',day(self.now)); self.engine.stops.add('daily_loss')
        self.rows=[position(upnl='-30')]; self.equity='970'
        self.assertTrue(self.reconcile()['verified'])
        self.assertIn('daily_loss',self.store.meta('stop_locks'))
        self.anchor()
        self.assertIn('daily_loss',self.store.meta('stop_locks'))
        self.liabilities=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='.00001')]
        self.reconcile()
        self.assertIn('account_liability',self.store.meta('stop_locks'))
        self.assertFalse(self.engine.can_open())
        with self.assertRaisesRegex(ValueError,'account_liability'): self.anchor()

    def test_mixed_currency_cancellation_is_converted_separately(self):
        usdt='BINANCE_FUTURE_BTC_USDT'
        self.change(symbol=usdt)
        self.rows=[position(),position(usdt)]; self.anchor()
        self.rows=[position(upnl='-50'),position(usdt,upnl='50')]
        self.fx_prices=('.8','.8'); self.equity='1010'
        value=self.reconcile()
        self.assertTrue(value['verified'])
        self.assertIsNone(value['external_contribution'])
        self.assertIsNone(value['equity_daily'])

    def test_missing_public_provider_never_blocks_full_account_protection(self):
        self.market.spot_fx=lambda coins:(_ for _ in ()).throw(ValueError('offline'))
        self.rows=[position(upnl='-250')]; self.equity='750'
        self.reconcile()
        self.assertTrue(self.engine.can_open())
        # Unknown income stops opening; external equity alone cannot halt.
        self.assertNotIn(self.store.meta('state'),('HALTING','HALTED'))
        self.margin='1.5'
        with patch.object(self.engine,'flatten_all') as flatten:
            self.reconcile()
        flatten.assert_called_once()
        self.assertEqual(self.store.all_orders(),[])

    def test_utc_rollover_verifies_carried_fx_then_restart_keeps_native_anchor(self):
        self.now+=86398; self.catalog()
        self.rows=[position(upnl='-10')]; self.equity='990'
        self.assertTrue(self.reconcile()['verified'])
        self.now+=2
        self.assertTrue(self.reconcile()['verified'])
        self.assertIsNone(self.store.meta('external_equity_anchor'))
        from tools.crossex_exec.engine import Engine
        with Store(self.path) as reopened:
            engine=Engine(reopened,self.market,self.accounts,clock=lambda:self.now)
            try:
                engine.reconcile()
                self.assertTrue(reopened.meta('external_income')['verified'])
                self.assertEqual(engine.totals(),(dec(0),dec(0)))
            finally:
                engine.close()

    def test_halted_mismatch_needs_three_snapshots_before_release_preserving_daily_lock(self):
        from tools.crossex_exec.equity import EquityAdmin
        self.store.set_meta('state','HALTED')
        self.store.set_meta('halt_reason','account_equity_drop')
        self.store.set_meta('position_mismatch',True)
        self.store.set_meta('daily_loss_day',day(self.now))
        for reason in ('position_mismatch','daily_loss','account_equity_drop'):
            self.engine.stops.add(reason)
        admin=EquityAdmin(self.store,self.accounts,engine=self.engine,clock=lambda:self.now)
        with self.assertRaisesRegex(ValueError,'position_or_order_lock'):
            admin.state_check('account_equity_drop',True)
        # The stopped-service runbook invokes only this existing reconciler;
        # it never calls startup/tick/reconcile/flatten or edits a lock directly.
        for n in range(3):
            self.now+=1
            snapshot=self.engine.read_account_snapshot()
            self.assertTrue(snapshot['activity']['complete'])
            self.assertTrue(self.engine.reconcile_positions(snapshot))
            self.assertEqual(self.store.meta('position_match_streak'),n+1)
            self.engine.reconcile_positions(snapshot)
            self.assertEqual(self.store.meta('position_match_streak'),n+1)
            self.assertEqual(self.store.meta('position_mismatch'),n<2)
            self.assertEqual(self.store.meta('state'),'HALTED')
        self.engine.rebaseline(confirm='account_equity_drop',reason='reviewed_external_epoch',
                               source='cli',release_halt=True)
        self.assertEqual(self.store.meta('state'),'RUNNING')
        self.assertNotIn('position_mismatch',self.store.meta('stop_locks'))
        self.assertNotIn('account_equity_drop',self.store.meta('stop_locks'))
        self.assertIn('daily_loss',self.store.meta('stop_locks'))
        self.assertEqual(self.store.meta('daily_loss_day'),day(self.now))
        self.assertFalse(self.engine.can_open())
        self.assertEqual(self.store.all_orders(),[])

    def test_failed_fx_at_midnight_cannot_roll_anchor_or_clear_old_daily_lock(self):
        self.rows=[position(upnl='-10')]; self.equity='990'
        self.assertTrue(self.reconcile()['verified'])
        self.store.save_order(dict(intent('own',ex='bitget'),state='TERMINAL',filled='0',fee='0',accounting_pending=False))
        self.activity_complete=False
        old_anchor=copy.deepcopy(self.store.meta('external_equity_anchor'))
        old_day=day(self.now)
        self.store.set_meta('daily_loss_day',old_day); self.engine.stops.add('daily_loss')
        self.now+=86400; self.catalog(); self.fx_prices=('1',)
        self.assertFalse(self.reconcile()['verified'])
        self.assertEqual(self.store.meta('external_equity_anchor'),old_anchor)
        self.assertEqual(self.store.meta('daily_loss_day'),old_day)
        self.assertIn('daily_loss',self.store.meta('stop_locks'))


if __name__=='__main__': unittest.main()
