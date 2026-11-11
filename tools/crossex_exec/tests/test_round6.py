"""Round-six contracts. All HTTP/DNS are guarded; public payloads are fixtures."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.fill_sim.core import Book, dec
from tools.crossex_exec.storage import Store
from tools.crossex_exec.policy import review, send_review, exit_reason
from tools.crossex_exec.market import MarketData
from tools.fill_sim.network import PublicClient, Limiter
from tools.crossex_exec.api_data import JournalView, position, trade
from test_exec import instrument, setUpModule, tearDownModule
from baseline_fixtures import seed_baseline
import test_round3 as previous


NOW = 1794355200.0


class BaselineTests(unittest.TestCase):
    def setUp(self):
        from tools.crossex_exec.baseline import Baseline
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'baseline.db'
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.now = NOW
        self.baseline = Baseline(self.store, lambda: self.now)

    def test_median_mad_and_exact_inverse_direction(self):
        rows = [('BTC|binance|bitget', int((NOW-n*30)//30), NOW-n*30, str((20, 40, 60)[n%3]))
                for n in range(363)]
        self.store.add_baseline_samples(rows, NOW)
        state = self.baseline.require('BTC', 'binance', 'bitget')
        self.assertEqual(dec(state['baseline_bps']), 40)
        self.assertEqual(dec(state['mad_bps']), 20)
        reverse = self.baseline.require('BTC', 'bitget', 'binance')
        self.assertEqual(dec(reverse['baseline_bps']), (1/dec('1.004')-1)*10000)
        self.assertEqual(reverse['key'], state['key'])

    def test_warmup_requires_both_duration_and_count(self):
        for count, spacing in ((299, 60), (300, 30)):
            with self.subTest(count=count):
                self.store.db.execute('DELETE FROM baseline_samples')
                seed_baseline(self.store, NOW, count=count, spacing=spacing)
                with self.assertRaisesRegex(ValueError, 'baseline_warming'):
                    self.baseline.require('BTC', 'binance', 'bitget')
        seed_baseline(self.store, NOW)
        self.assertTrue(self.baseline.require('BTC', 'binance', 'bitget')['ready'])

    def test_stale_and_outside_six_hour_samples_do_not_warm(self):
        seed_baseline(self.store, NOW-21601)
        seed_baseline(self.store, NOW, count=1)
        with self.assertRaisesRegex(ValueError, 'baseline_warming'):
            self.baseline.require('BTC', 'binance', 'bitget')
        seed_baseline(self.store, NOW)
        self.now += 91
        with self.assertRaisesRegex(ValueError, 'baseline_warming'):
            self.baseline.require('BTC', 'binance', 'bitget')

    def test_structural_thresholds_recheck_after_ten_minutes_and_recover(self):
        for value in ('80', '-80'):
            with self.subTest(bps=value):
                self.store.db.execute('DELETE FROM baseline_samples')
                self.store.set_meta('baseline_status', {})
                seed_baseline(self.store, self.now, bps=value)
                with self.assertRaisesRegex(ValueError, 'structural_spread'):
                    self.baseline.require('BTC', 'binance', 'bitget')
                # Change the underlying six-hour window, keeping the persisted latch.
                self.store.db.execute('DELETE FROM baseline_samples')
                seed_baseline(self.store, self.now, bps='0')
                self.now += 599
                seed_baseline(self.store, self.now, count=1)
                with self.assertRaisesRegex(ValueError, 'structural_spread'):
                    self.baseline.require('BTC', 'bitget', 'binance')
                self.now += 1
                self.assertFalse(self.baseline.require('BTC', 'binance', 'bitget')['blocked'])

    def test_mad_equal_forty_is_blacklisted(self):
        rows = [('BTC|binance|bitget', int((NOW-n*30)//30), NOW-n*30, str((-40, 40)[n%2]))
                for n in range(362)]
        self.store.add_baseline_samples(rows, NOW)
        with self.assertRaisesRegex(ValueError, 'structural_spread'):
            self.baseline.require('BTC', 'binance', 'bitget')

    def test_one_point_per_bucket_retention_and_restart(self):
        seed_baseline(self.store, NOW, bps='40')
        seed_baseline(self.store, NOW+1, bps='79', count=1)
        seed_baseline(self.store, NOW-86401, count=1)
        self.baseline.record({}, ('BTC',))
        rows = self.store.baseline_samples('BTC|binance|bitget', 0, NOW+1)
        self.assertEqual(len(rows), 361)
        self.store.close()
        self.store = Store(self.path)
        from tools.crossex_exec.baseline import Baseline
        self.baseline = Baseline(self.store, lambda: self.now)
        self.assertEqual(dec(self.baseline.require('BTC', 'binance', 'bitget')['baseline_bps']), 40)

    def test_record_uses_mid_ratio_whitelist_and_unordered_pair(self):
        mids = {'binance': {'BTC':'100', 'ETH':'1'}, 'bitget': {'BTC':'100.4', 'ETH':'9'}}
        self.baseline.record(mids, ('BTC',))
        rows = self.store.baseline_samples('BTC|binance|bitget', 0, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(dec(rows[0][1]), 40)
        self.assertFalse(self.store.baseline_samples('ETH|binance|bitget', 0, NOW))

    def closed(self, reason='loss_limit', net='-1', **changes):
        pair = dict(id='loss', base='BTC', state='CLOSED', closed_at=NOW, net_usd=net,
                    accounting_pending=False, funding_known=True, exit_reason=reason,
                    instruments=[instrument().dump(), instrument('bitget').dump()])
        pair.update(changes)
        self.store.pair(pair)
        return pair

    def test_loss_cooldown_both_directions_restart_and_exact_expiry(self):
        pair = self.closed()
        self.now += 10
        self.store.pair(pair)  # A reread cannot move the expiry forward.
        self.store.close()
        self.store = Store(self.path)
        from tools.crossex_exec.baseline import Baseline
        self.baseline = Baseline(self.store, lambda: self.now)
        self.now = NOW+86399
        seed_baseline(self.store, self.now)
        for a,b in (('binance','bitget'), ('bitget','binance')):
            with self.assertRaisesRegex(ValueError, 'pair_cooldown'):
                self.baseline.require('BTC', a, b)
        self.now += 1
        self.assertTrue(self.baseline.require('BTC', 'binance', 'bitget')['ready'])

    def test_cooldown_reasons_profit_zero_unknown_and_other_pair(self):
        aliases = ('loss_limit','adverse_spread','max_age','funding_exit','funding_unknown',
                   'stale_exit','one_leg','repair_failed','unknown_order',
                   'stop_loss','max_hold','pre_funding','stale')
        for reason in aliases:
            self.store.set_meta('pair_cooldowns', {})
            self.closed(reason)
            self.assertEqual(self.store.meta('pair_cooldowns')['BTC|binance|bitget'], NOW+86400)
        for reason, net, changes in (('converged','-1',{}), ('halt','-1',{}), ('loss_limit','0',{}),
                                    ('max_age','1',{}), ('max_age',None,{}),
                                    ('max_age','-1',{'funding_known':False}),
                                    ('max_age','-1',{'accounting_pending':True})):
            self.store.set_meta('pair_cooldowns', {})
            self.closed(reason, net, **changes)
            self.assertFalse(self.store.meta('pair_cooldowns'))
        self.closed()
        seed_baseline(self.store, NOW, venues=('binance','gate'))
        self.assertTrue(self.baseline.require('BTC', 'binance', 'gate')['ready'])

    def test_late_known_loss_starts_at_original_close_and_is_atomic(self):
        pair = self.closed(net=None)
        self.assertFalse(self.store.meta('pair_cooldowns', {}))
        pair['net_usd'] = '-1'
        original = self.store.set_meta
        def fail(key, value):
            if key == 'pair_cooldowns':
                raise RuntimeError('injected cooldown commit failure')
            return original(key, value)
        with patch.object(self.store, 'set_meta', side_effect=fail), self.assertRaises(RuntimeError):
            self.store.pair(pair)
        self.assertIsNone(self.store.pairs()[0]['net_usd'])
        self.now += 60
        self.store.pair(pair)
        self.assertEqual(self.store.meta('pair_cooldowns')['BTC|binance|bitget'], NOW+86400)

    def test_summary_counts_ready_blacklisted_cooling_and_expiry(self):
        seed_baseline(self.store, NOW)
        seed_baseline(self.store, NOW, '80', venues=('binance','gate'))
        self.baseline.record({}, ('BTC',))
        self.closed()
        view = JournalView(self.path, {}, clock=lambda:self.now)
        summary = view.get('summary', {})
        self.assertEqual([summary[k] for k in ('baseline_ready_pairs','structural_spread_pairs','cooldown_pairs')], [2,1,1])
        self.now += 86400
        summary = view.get('summary', {})
        self.assertEqual(summary['baseline_ready_pairs'], 0)
        self.assertEqual(summary['cooldown_pairs'], 0)


class PricingTests(unittest.TestCase):
    def inputs(self, spread=60):
        return ([instrument(), instrument('bitget')],
                [Book([['100','10']], [['100','10']], NOW),
                 Book([[str(100*(1+dec(spread)/10000)),'10']], [['101','10']], NOW)],
                ['0','0'], [dict(rate='0', interval=28800, captured_at=NOW, next=NOW+10000,
                                 history_ok=True, max_abs_24h='0') for _ in range(2)], ['5000000']*2, NOW)

    def test_sixty_minus_forty_is_twenty_and_all_four_fees_are_deducted(self):
        args = list(self.inputs())
        result = review(*args, baseline_bps='40')
        self.assertEqual(dec(result['excess_bps']), 20)
        self.assertEqual(dec(result['net_bps']), 20)
        args[2] = ['.0001', '.00015']  # Five bp round trip: exact threshold.
        self.assertEqual(dec(review(*args, baseline_bps='40')['net_bps']), 15)
        args[2][1] = '.000151'
        with self.assertRaises(ValueError):
            review(*args, baseline_bps='40')

    def test_missing_baseline_has_no_implicit_zero_fallback(self):
        with self.assertRaisesRegex(ValueError,'baseline_warming'):
            review(*self.inputs())

    def test_depth_vwap_is_used_instead_of_best_quote(self):
        args = list(self.inputs())
        args[1][1].bids = [['100.6','.1'], ['100.1','10']]
        with self.assertRaises(ValueError):
            review(*args, baseline_bps='40')

    def test_hourly_two_hours_net_receipts_zero_and_missing_rejected(self):
        args = list(self.inputs(200))
        for rates, intervals, expected in ((('.001','.0004'), (3600,14400),18),
                                            (('.001','.0004'), (28800,14400),'.5'),
                                            (('-.001','.0004'), (3600,14400),0),
                                            (('.0001','.001'), (3600,14400),0),
                                            (('-.0001','-.001'), (3600,14400),3)):
            for row, rate, interval in zip(args[3], rates, intervals):
                row.update(rate=rate, interval=interval)
            self.assertEqual(dec(review(*args, baseline_bps=0)['funding_cost_bps']), dec(expected))
        for leg in (0,1):
            for field, value in (('rate',None), ('rate','NaN'), ('interval',None), ('interval',0), ('interval','Infinity')):
                bad = copy.deepcopy(args)
                bad[3][leg][field] = value
                with self.subTest(leg=leg,field=field,value=value), self.assertRaises(ValueError):
                    review(*bad, baseline_bps=0)

    def test_send_review_repeats_excess_and_hourly_cost_threshold(self):
        args = list(self.inputs())
        pair = dict(last_books=[b.to_dict() for b in args[1]], funding=args[3], baseline_at_open='40', excess_at_open='20')
        orders = [dict(quantity='1', price='101'), dict(quantity='1', price='100')]
        send_review(pair, orders, ['0','0'], NOW)
        pair['funding'][0].update(rate='.0003', interval=3600)
        with self.assertRaises(ValueError):
            send_review(pair, orders, ['0','0'], NOW)
        pair['funding'][0]['rate'] = '0'
        with self.assertRaises(ValueError):
            send_review(pair, orders, ['.0002','.0002'], NOW)

    def test_exit_half_excess_and_unchanged_stop_rules(self):
        args = dict(age=1, spread=50, entry_spread=60, baseline_at_open=40, excess_at_open=20,
                    pnl=0, notional=100, next_funding=1000, stale_age=0)
        self.assertEqual(exit_reason(**args), 'converged')
        self.assertIsNone(exit_reason(**dict(args,spread='50.001')))
        for fields, result in ((dict(spread=110),'adverse_spread'), (dict(pnl=-1),'loss_limit'),
                               (dict(age=7200),'max_age'), (dict(next_funding=299),'funding_exit'),
                               (dict(stale_age=60),'stale_exit')):
            self.assertEqual(exit_reason(**dict(args,**fields)), result)

    def test_api_observation_fields_and_unknown_legacy_values(self):
        p = dict(id='p', baseline_at_open='40', excess_at_open='20', fill={'funding_cost_bps':'2'})
        for row in (position(p, [], NOW), trade(p, [])):
            self.assertEqual([row[k] for k in ('baseline_bps','excess_bps','funding_cost_bps')], [40,20,2])
        for row in (position({}, [], NOW), trade({}, [])):
            self.assertTrue(all(row[k] is None for k in ('baseline_bps','excess_bps','funding_cost_bps')))


class PublicBatchTests(unittest.TestCase):
    def payload(self, ex, bad=None):
        row = ({'contract':'BTC_USDT','highest_bid':'99','lowest_ask':'101'} if ex=='gate' else
               {'instId':'BTC-USDT-SWAP','instType':'SWAP','bidPx':'99','askPx':'101','ts':str(int(NOW*1000))} if ex=='okx' else
               {'symbol':'BTCUSDT','bidPrice':'99','askPrice':'101','time':int(NOW*1000)} if ex in ('binance','aster') else
               {'symbol':'BTCUSDT','bid1Price':'99','ask1Price':'101', **({'category':'USDT-FUTURES'} if ex=='bitget' else {})})
        row.update(bad or {})
        return ([row] if ex in ('gate','binance','aster') else
                {'retCode':0,'time':int(NOW*1000),'result':{'category':'linear','list':[row]}} if ex=='bybit' else
                {'code':'00000' if ex=='bitget' else '0','requestTime':int(NOW*1000),'data':[row]})

    def test_all_six_official_shapes_and_bad_quotes(self):
        from tools.crossex_exec.baseline import parse_tickers, BATCH_URLS
        self.assertEqual(len(BATCH_URLS),6)
        for ex in BATCH_URLS:
            with self.subTest(ex=ex):
                self.assertEqual(parse_tickers(ex,self.payload(ex),('BTC',),NOW), {'BTC':dec(100)})
                self.assertEqual(parse_tickers(ex,self.payload(ex),('ETH',),NOW), {})
                field = 'highest_bid' if ex=='gate' else 'bidPx' if ex=='okx' else 'bidPrice' if ex in ('binance','aster') else 'bid1Price'
                for value in ('0','102','NaN','Infinity',''):
                    self.assertEqual(parse_tickers(ex,self.payload(ex,{field:value}),('BTC',),NOW), {})

    def test_duplicate_alias_wrong_category_and_stale_do_not_enter_history(self):
        from tools.crossex_exec.baseline import parse_tickers
        data = self.payload('binance')*2
        self.assertEqual(parse_tickers('binance',data,('BTC',),NOW), {})
        for ex, bad in (('binance',{'symbol':'1000BTCUSDT'}), ('binance',{'time':int((NOW-91)*1000)}),
                         ('okx',{'instType':'FUTURES'}), ('bitget',{'category':'SPOT'})):
            self.assertEqual(parse_tickers(ex,self.payload(ex,bad),('BTC',),NOW), {})

    def test_collector_one_request_per_venue_uses_existing_queue_and_partial_results(self):
        from tools.crossex_exec.baseline import BATCH_URLS
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            market = MarketData(store, clock=lambda:NOW)
            def batch(items, priority=1, partial=False):
                self.assertEqual(len(items),6)
                self.assertTrue(partial)
                self.assertEqual(priority,2)
                self.assertEqual({ex:url for ex,url,_ in items},BATCH_URLS)
                return [None if ex=='gate' else convert(self.payload(ex)) for ex,_,convert in items]
            with patch.object(market,'batch',side_effect=batch) as call:
                result = market.baseline_quotes(('BTC',))
                self.assertEqual(set(result), set(BATCH_URLS)-{'gate'})
                market.baseline_quotes(())
                self.assertEqual(call.call_count,1)
            market.close()

    def test_batch_sends_are_counted_by_shared_limiter(self):
        from tools.crossex_exec.baseline import BATCH_URLS
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            calls=[]
            def transport(url):
                calls.append(url)
                ex=next(k for k,v in BATCH_URLS.items() if v==url)
                return self.payload(ex)
            limiter=Limiter()
            market=MarketData(store,clock=lambda:NOW,client=PublicClient(limiter,transport=transport))
            try:
                self.assertEqual(len(market.baseline_quotes(('BTC',))),6)
                self.assertEqual(sorted(calls),sorted(BATCH_URLS.values()))
                self.assertTrue(all(limiter.stats[ex]['requests']==1 for ex in BATCH_URLS))
            finally:
                market.close()


class EngineBaselineTests(unittest.TestCase):
    snapshot = previous.Round3Tests.snapshot
    pair = previous.Round3Tests.pair

    def setUp(self):
        previous.Round3Tests.setUp(self)

    def trigger(self, identity='r6'):
        from test_engine import trigger
        self.engine.account_cache.publish(self.snapshot())
        self.store.set_meta('mapping',dict(at=self.now[0]))
        self.market.funding = lambda instruments:[dict(rate='0',interval=28800,next=self.now[0]+10000,
            captured_at=self.now[0],history_ok=True,max_abs_24h='0') for _ in instruments]
        payload = trigger(identity)
        payload['opened_at'] = self.now[0]
        return payload

    def test_ingest_rejects_warmup_with_explicit_reason_before_market_calls(self):
        self.store.db.execute('DELETE FROM baseline_samples')
        payload = self.trigger()
        with patch.object(self.market,'books') as books:
            self.assertIsNone(self.engine.ingest(payload))
            books.assert_not_called()
        self.assertEqual(json.loads(self.store.events()[-1]['value'])['reason'],'baseline_warming')

    def test_open_snapshots_persist_and_track_uses_frozen_half_excess(self):
        self.store.db.execute('DELETE FROM baseline_samples')
        seed_baseline(self.store,self.now[0],'40')
        p = self.engine.ingest(self.trigger())
        self.assertIsNotNone(p)
        self.assertEqual(dec(p['baseline_at_open']),40)
        self.assertEqual(dec(p['excess_at_open']),160)
        # New live baseline is irrelevant to this position's frozen threshold (120bp).
        self.store.db.execute('DELETE FROM baseline_samples')
        seed_baseline(self.store,self.now[0],'-40')
        self.market.books = lambda instruments:[Book([['100' if i.market.exchange=='binance' else '101.2','10']],
            [['100' if i.market.exchange=='binance' else '101.2','10']],self.now[0]) for i in instruments]
        self.engine.track(p)
        self.assertEqual(p['state'],'CLOSED')
        self.assertEqual(p['exit_reason'],'converged')

    def test_cooldown_is_rechecked_at_shared_send_gate(self):
        p = self.pair(('0','0'))
        p.update(state='OPENING',baseline_at_open='0',excess_at_open='200')
        self.store.pair(p)
        orders = [self.engine.new_intent(p,n,'1','100.03' if n==0 else '101.96',False) for n in (0,1)]
        self.store.set_meta('pair_cooldowns',{'BTC|binance|bitget':self.now[0]+86400})
        with self.assertRaisesRegex(ValueError,'pair_cooldown'):
            self.engine.check_pair(orders)

    def test_realized_losing_close_blocks_next_trigger(self):
        p=self.engine.ingest(self.trigger())
        self.assertIsNotNone(p)
        for venue in self.venues:
            original=venue.fills
            venue.fills=lambda order,original=original:dict(original(order),fee='.1')
        self.engine.close_pair(p,'loss_limit')
        self.assertEqual(p['state'],'CLOSED')
        self.assertLess(dec(p['net_usd']),0)
        self.assertIsNone(self.engine.ingest(self.trigger('after-loss')))
        self.assertEqual(json.loads(self.store.events()[-1]['value'])['reason'],'pair_cooldown')

    def test_structural_ingest_is_explicit_and_never_reads_books(self):
        self.store.db.execute('DELETE FROM baseline_samples')
        seed_baseline(self.store,self.now[0],'80')
        payload=self.trigger()
        with patch.object(self.market,'books') as books:
            self.assertIsNone(self.engine.ingest(payload))
            books.assert_not_called()
        self.assertEqual(json.loads(self.store.events()[-1]['value'])['reason'],'structural_spread')

    def test_missing_baseline_at_send_fails_closed(self):
        p=self.pair(('0','0'))
        p.update(state='OPENING')
        p.pop('baseline_at_open')
        self.store.pair(p)
        orders=[self.engine.new_intent(p,n,'1','100.03' if n==0 else '101.96',False) for n in (0,1)]
        with self.assertRaises((ValueError,KeyError)):
            self.engine.check_pair(orders)

    def test_send_book_age_includes_time_spent_reading_baseline(self):
        p=self.pair(('0','0'))
        p.update(state='OPENING')
        self.store.pair(p)
        orders=[self.engine.new_intent(p,n,'1','100.03' if n==0 else '101.96',False) for n in (0,1)]
        require=self.engine.baseline.require
        def slow(*args):
            result=require(*args)
            self.now[0]+=2.01
            return result
        with patch.object(self.engine.baseline,'require',side_effect=slow), self.assertRaisesRegex(ValueError,'send_book_stale'):
            self.engine.check_pair(orders)

    def test_baseline_background_worker_is_thirty_seconds_and_separate(self):
        calls=[]
        with patch('tools.crossex_exec.engine.PeriodicWorker',side_effect=lambda cb,interval,name,on_error:calls.append((cb,interval,name)) or unittest.mock.Mock()):
            self.engine.start_background(('BTC',))
        self.assertIn((self.engine.refresh_baselines,30,'exec-baseline'),calls)
        with patch.object(self.market,'baseline_quotes',return_value={'binance':{'BTC':'100'},'bitget':{'BTC':'100.4'}}) as quotes:
            self.engine.refresh_baselines()
            quotes.assert_called_once_with(('BTC',))
