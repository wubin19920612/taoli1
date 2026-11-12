"""Round seven: exact Decimal boundaries, real shared admission, offline only."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.fill_sim.core import Book, dec
from tools.fill_sim.network import Limiter, PublicClient
from tools.crossex_exec.api_data import JournalView, position, trade
from tools.crossex_exec.baseline import Baseline
from tools.crossex_exec.market import MarketData
from tools.crossex_exec.storage import Store
from test_exec import instrument, setUpModule, tearDownModule
from baseline_fixtures import seed_baseline
import test_round6 as previous


NOW = 1794355200.0
REASONS = ('book_skew', 'book_stale', 'funding_diff', 'baseline_not_flat',
           'not_single_venue_outlier', 'too_few_venues')


def books(at=NOW, skew='0', age='0', prices=('100', '102')):
    stamps = [dec(at)-dec(age), dec(at)-dec(age)-dec(skew)/1000]
    return [Book([[p, '10']], [[p, '10']], at, float(t)) for p,t in zip(prices,stamps)]


def quotes(at=NOW, **changes):
    mids = dict(binance='100', bitget='102', bybit='100')
    mids.update(changes)
    return dict(at=at, mids=mids)


class TimingTests(unittest.TestCase):
    def guard(self, config=None):
        from tools.crossex_exec.admission import OpeningLimits
        return OpeningLimits(config)

    def test_skew_300_passes_301_fails_using_exchange_not_receive_time(self):
        guard = self.guard()
        self.assertEqual(guard.books(books(skew=300), NOW), dec(300))
        with self.assertRaisesRegex(ValueError, 'book_skew'):
            guard.books(books(skew=301), NOW)
        # Simultaneous exchange snapshots are valid despite differing receipts.
        pair = books()
        pair[0].received_at -= .5
        self.assertEqual(guard.books(pair, NOW), 0)

    def test_age_exact_one_second_passes_1_01_fails_on_either_leg(self):
        self.assertEqual(self.guard().books(books(age=1), NOW), 0)
        for leg in (0,1):
            pair = books()
            pair[leg].exchange_at = NOW-1.01
            with self.subTest(leg=leg), self.assertRaisesRegex(ValueError, 'book_stale'):
                self.guard().books(pair, NOW)

    def test_receipt_fallback_and_old_receipt_cannot_be_relabelled_fresh(self):
        pair = books()
        for b in pair:
            b.exchange_at = None
        pair[0].received_at -= .3
        self.assertEqual(self.guard().books(pair, NOW), 300)
        pair[0].received_at -= .001
        with self.assertRaisesRegex(ValueError, 'book_skew'):
            self.guard().books(pair, NOW)
        pair = books()
        pair[0].received_at -= 1.01
        with self.assertRaisesRegex(ValueError, 'book_stale'):
            self.guard().books(pair, NOW)

    def test_missing_nonfinite_and_future_timestamps_fail_closed(self):
        for value in (float('nan'), float('inf'), NOW+.001, 'bad'):
            for field in ('exchange_at','received_at'):
                pair = books()
                setattr(pair[0], field, value)
                with self.subTest(field=field,value=value), self.assertRaisesRegex(ValueError, 'book_stale'):
                    self.guard().books(pair, NOW)
        for pair in ([None,None], [books()[0]], []):
            with self.assertRaisesRegex(ValueError, 'book_stale'):
                self.guard().books(pair, NOW)

    def test_stale_has_deterministic_priority_over_skew(self):
        with self.assertRaisesRegex(ValueError, 'book_stale'):
            self.guard().books(books(skew=301, age=1.01), NOW)

    def test_every_upper_bound_rejects_relaxation_and_invalid_config(self):
        caps = dict(CROSSEX_MAX_BOOK_SKEW_MS='300', CROSSEX_MAX_BOOK_AGE_SECONDS='1',
                    CROSSEX_MAX_FUNDING_DIFF_BPS_H='0.5', CROSSEX_MAX_FUNDING_RATE_8H='0.0005',
                    CROSSEX_MAX_BASELINE_BPS='10', CROSSEX_MAX_BASELINE_MAD_BPS='10',
                    CROSSEX_MAX_NORMAL_DEVIATION_BPS='10', CROSSEX_MAX_REFERENCE_AGE_SECONDS='90')
        for name,cap in caps.items():
            with self.subTest(name=name):
                self.guard({name:cap})
                self.guard({name:str(dec(cap)/2)})
                for value in (str(dec(cap)+dec('.000001')), '-1', 'NaN', 'Infinity', '', None):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        self.guard({name:value})

    def test_minimum_bounds_only_increase_and_thresholds_are_enforced(self):
        for name, bad in (('CROSSEX_MIN_OUTLIER_SHARE','0.6999'), ('CROSSEX_MIN_VENUES','2'),
                          ('CROSSEX_MIN_VENUES','3.5'), ('CROSSEX_MIN_VENUES','7'),
                          ('CROSSEX_MIN_OUTLIER_SHARE','1.01')):
            with self.subTest(name=name,bad=bad), self.assertRaises(ValueError):
                self.guard({name:bad})
        self.guard({'CROSSEX_MIN_OUTLIER_SHARE':'.8','CROSSEX_MIN_VENUES':'4'})
        with self.assertRaisesRegex(ValueError,'book_skew'):
            self.guard({'CROSSEX_MAX_BOOK_SKEW_MS':'100'}).books(books(skew=101),NOW)
        with self.assertRaisesRegex(ValueError,'book_stale'):
            self.guard({'CROSSEX_MAX_BOOK_AGE_SECONDS':'.5'}).books(books(age='.501'),NOW)


class FatFingerTests(unittest.TestCase):
    def guard(self, config=None):
        from tools.crossex_exec.admission import OpeningLimits
        return OpeningLimits(config)

    def funding(self, rates=('0','0'), intervals=(28800,28800)):
        return [dict(rate=r,interval=i) for r,i in zip(rates,intervals)]

    def test_hourly_difference_half_bp_inclusive_both_signs_and_unequal_periods(self):
        for sign in (1,-1):
            for intervals in ((3600,28800), (14400,3600)):
                rate = dec(sign)*dec('.00005')*intervals[0]/3600
                self.guard().funding(self.funding((str(rate),'0'),intervals))
                with self.subTest(sign=sign,intervals=intervals), self.assertRaisesRegex(ValueError,'funding_diff'):
                    self.guard().funding(self.funding((str(rate*dec('1.0001')),'0'),intervals))
        self.guard().funding(self.funding(('.0005','.0000625'), (28800,3600)))

    def test_each_leg_absolute_eight_hour_cap_inclusive_for_positive_and_negative(self):
        for sign in (1,-1):
            for interval in (3600,14400,28800):
                value = dec(sign)*dec('.0005')*interval/28800
                self.guard().funding(self.funding((str(value),str(value)),(interval,interval)))
                for leg in (0,1):
                    rows = self.funding((str(value),str(value)),(interval,interval))
                    rows[leg]['rate'] = str(value*dec('1.0001'))
                    with self.subTest(sign=sign,interval=interval,leg=leg), self.assertRaisesRegex(ValueError,'funding_diff'):
                        self.guard().funding(rows)

    def test_unknown_or_nonfinite_funding_is_never_zero(self):
        for leg in (0,1):
            for field, value in (('rate',None), ('rate','NaN'), ('rate','Infinity'),
                                  ('interval',None), ('interval',0), ('interval',-1), ('interval','Infinity')):
                rows = self.funding()
                rows[leg][field] = value
                with self.subTest(leg=leg,field=field,value=value), self.assertRaisesRegex(ValueError,'funding_diff'):
                    self.guard().funding(rows)
        for rows in ([],[None,None],[{}],None):
            with self.assertRaisesRegex(ValueError,'funding_diff'):
                self.guard().funding(rows)

    def test_flat_baseline_and_mad_ten_inclusive_both_directions(self):
        for center in ('10','-10','0'):
            self.guard().flat(dict(baseline_bps=center,mad_bps='10'))
        for center,mad in (('10.0001','0'),('-10.0001','0'),('0','10.0001'),
                           (None,'0'),('NaN','0'),('0',None),('0','-1')):
            with self.subTest(center=center,mad=mad), self.assertRaisesRegex(ValueError,'baseline_not_flat'):
                self.guard().flat(dict(baseline_bps=center,mad_bps=mad))

    def outlier(self, prices=('100','102'), snapshot=None, excess='200', guard=None):
        return (guard or self.guard()).outlier(('binance','bitget'), books(prices=prices),
                                               quotes() if snapshot is None else snapshot, excess, NOW)

    def test_buy_smashed_low_or_sell_pulled_high_and_reject_reversed_direction(self):
        self.assertEqual(self.outlier()['outlier_exchange'], 'bitget')
        self.assertEqual(self.outlier(('98','100'),quotes(binance='98',bitget='100'),excess='204.0816')['outlier_exchange'], 'binance')
        for prices in (('102','100'),('100','98')):
            with self.subTest(prices=prices), self.assertRaisesRegex(ValueError,'not_single_venue_outlier'):
                self.outlier(prices,quotes(binance=prices[0],bitget=prices[1]))

    def test_outlier_seventy_percent_inclusive_and_below_rejected(self):
        self.outlier(('100','100.7'), quotes(bitget='100.7'), excess=100)
        with self.assertRaisesRegex(ValueError,'not_single_venue_outlier'):
            self.outlier(('100','100.6999'),quotes(bitget='100.6999'),excess=100)
        with self.assertRaisesRegex(ValueError,'not_single_venue_outlier'):
            self.outlier(('100','100.7'),quotes(bitget='100.7'),excess=100,
                         guard=self.guard({'CROSSEX_MIN_OUTLIER_SHARE':'.71'}))

    def test_normal_leg_ten_bp_inclusive_and_above_rejected(self):
        snapshot = quotes(okx='100',gate='100',binance='100.1')
        self.outlier(('100.1','102'),snapshot,excess=180)
        with self.assertRaisesRegex(ValueError,'not_single_venue_outlier'):
            self.outlier(('100.10001','102'),snapshot,excess=180)

    def test_two_leg_anomaly_and_reference_disagreement_are_rejected(self):
        for prices,snapshot in ((('98','102'),quotes(binance='98')),
                                (('100','102'),quotes(bybit='103',okx='100',gate='100')),
                                (('101','103'),quotes())):
            with self.subTest(prices=prices,snapshot=snapshot), self.assertRaisesRegex(ValueError,'not_single_venue_outlier'):
                self.outlier(prices,snapshot)

    def test_three_unique_whitelisted_venues_including_both_legs_required(self):
        self.outlier()
        for mids in (dict(binance='100',bitget='102'), dict(binance='100',bybit='100',okx='100'),
                     dict(binance='100',bitget='102',unknown='100'),
                     dict(binance='100',bitget='102',bybit='NaN'),
                     dict(binance='100',bitget='102',bybit='0')):
            with self.subTest(mids=mids), self.assertRaisesRegex(ValueError,'too_few_venues'):
                self.outlier(snapshot=dict(at=NOW,mids=mids))
        with self.assertRaisesRegex(ValueError,'too_few_venues'):
            self.outlier(guard=self.guard({'CROSSEX_MIN_VENUES':'4'}))

    def test_reference_age_boundary_future_and_absent_round_fail_closed(self):
        self.outlier(snapshot=quotes(at=NOW-90))
        for snapshot in ({}, quotes(at=NOW-90.001), quotes(at=NOW+.001), quotes(at='NaN')):
            with self.subTest(snapshot=snapshot), self.assertRaisesRegex(ValueError,'too_few_venues'):
                self.outlier(snapshot=snapshot)

    def test_stricter_funding_flatness_and_normal_leg_limits_take_effect(self):
        with self.assertRaisesRegex(ValueError,'funding_diff'):
            self.guard({'CROSSEX_MAX_FUNDING_DIFF_BPS_H':'.1'}).funding(self.funding(('.0002','0')))
        with self.assertRaisesRegex(ValueError,'funding_diff'):
            self.guard({'CROSSEX_MAX_FUNDING_RATE_8H':'.0001'}).funding(self.funding(('.0002','.0002')))
        for name in ('CROSSEX_MAX_BASELINE_BPS','CROSSEX_MAX_BASELINE_MAD_BPS'):
            with self.assertRaisesRegex(ValueError,'baseline_not_flat'):
                self.guard({name:'5'}).flat(dict(baseline_bps='10',mad_bps='10'))
        with self.assertRaisesRegex(ValueError,'not_single_venue_outlier'):
            self.outlier(('100.1','102'),quotes(okx='100',gate='100'),excess=180,
                         guard=self.guard({'CROSSEX_MAX_NORMAL_DEVIATION_BPS':'5'}))


class EngineAdmissionTests(unittest.TestCase):
    snapshot = previous.EngineBaselineTests.snapshot
    pair = previous.EngineBaselineTests.pair
    trigger = previous.EngineBaselineTests.trigger

    def setUp(self):
        previous.EngineBaselineTests.setUp(self)
        self.engine.baseline.record({'binance':{'BTC':'100'},'bitget':{'BTC':'102'},'bybit':{'BTC':'100'}},('BTC',))

    def skips(self):
        return [json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']

    def test_initial_bad_pair_refetches_both_once_then_passes(self):
        for field in ('skew','age'):
            # Shadow avoids residual positions across the independent subcases.
            self.engine.live = self.engine.executor.live = False
            initial = books(self.now[0], **{field:301 if field=='skew' else '1.01'})
            with self.subTest(field=field), patch.object(self.market,'books',side_effect=[initial,books(self.now[0],skew=100)]) as fetch:
                pair = self.engine.ingest(self.trigger(field))
                self.assertIsNotNone(pair)
                self.assertEqual(fetch.call_count,2)
                self.assertTrue(all(len(call.args[0])==2 for call in fetch.call_args_list))
                self.assertEqual(pair['open_book_skew_ms'],100)
                self.assertEqual(self.skips(),[])

    def test_initial_two_bad_pairs_skip_once_and_never_send(self):
        for reason, fields in (('book_skew',dict(skew=301)),('book_stale',dict(age='1.01'))):
            with self.subTest(reason=reason), patch.object(self.market,'books',return_value=books(self.now[0],**fields)) as fetch:
                self.assertIsNone(self.engine.ingest(self.trigger(reason)))
                self.assertEqual(fetch.call_count,2)
                self.assertEqual(self.skips()[-1]['reason'],reason)
        self.assertFalse(self.store.all_orders())
        self.assertFalse(any(r[0]=='place' for r in self.trace))
        view = JournalView(Path(self.tmp.name)/'exec.db',{},clock=lambda:self.now[0])
        self.assertEqual(view.get('summary',{})['opening_skip_counts']['book_skew'],1)
        self.assertEqual(view.get('summary',{})['opening_skip_counts']['book_stale'],1)

    def test_failed_refresh_does_not_reuse_old_valid_leg(self):
        with patch.object(self.market,'books',side_effect=[books(skew=301),[books()[0],None]]) as fetch:
            self.assertIsNone(self.engine.ingest(self.trigger()))
            self.assertEqual(fetch.call_count,2)
        self.assertEqual(self.skips()[-1]['reason'],'book_stale')
        self.assertFalse(any(r[0]=='place' for r in self.trace))

    def test_three_hundred_ms_live_boundary_no_unnecessary_refresh(self):
        with patch.object(self.market,'books',return_value=books(skew=300)) as fetch:
            pair=self.engine.ingest(self.trigger())
        self.assertEqual(pair['state'],'OPEN')
        self.assertEqual(pair['open_book_skew_ms'],300)
        self.assertEqual(fetch.call_count,1)

    def test_nonzero_flat_baseline_and_final_excess_are_saved(self):
        self.store.db.execute('DELETE FROM baseline_samples')
        seed_baseline(self.store,self.now[0],'5')
        pair=self.engine.ingest(self.trigger())
        self.assertEqual(pair['state'],'OPEN')
        self.assertEqual(dec(pair['baseline_at_open']),5)
        self.assertEqual(dec(pair['excess_at_open']),195)
        self.assertEqual(dec(self.store.pairs()[0]['excess_at_open']),195)

    def change_before_gate(self, callback):
        original = self.venues[0].place
        def place(order, before_send):
            callback()
            return original(order,before_send)
        self.venues[0].place = place

    def test_shared_gate_rechecks_age_refetches_once_and_freezes_final_skew(self):
        self.change_before_gate(lambda:self.now.__setitem__(0,self.now[0]+1.01))
        with patch.object(self.market,'books',side_effect=lambda instruments:books(self.now[0],skew=200)) as fetch:
            pair = self.engine.ingest(self.trigger())
            self.assertEqual(fetch.call_count,2)
        self.assertEqual(pair['state'],'OPEN')
        self.assertEqual(pair['open_book_skew_ms'],200)
        persisted = self.store.pairs()[0]
        self.assertEqual(persisted['last_books'][0]['received_at'],self.now[0])
        self.assertEqual(persisted['open_book_skew_ms'],200)
        self.assertEqual(len([r for r in self.trace if r[0]=='place']),2)

    def test_shared_gate_two_invalid_observations_retire_both_intents_without_send(self):
        self.change_before_gate(lambda:self.now.__setitem__(0,self.now[0]+1.01))
        with patch.object(self.market,'books',side_effect=[books(),books(age='1.01')]) as fetch:
            self.engine.ingest(self.trigger())
            self.assertEqual(fetch.call_count,2)
        self.assertFalse(any(r[0]=='place' for r in self.trace))
        self.assertEqual([o['state'] for o in self.store.all_orders()],['UNSENT','UNSENT'])
        self.assertEqual([s['reason'] for s in self.skips()],['book_stale'])

    def test_refreshed_prices_still_must_fit_original_limits(self):
        self.change_before_gate(lambda:self.now.__setitem__(0,self.now[0]+1.01))
        with patch.object(self.market,'books',side_effect=lambda instruments:books(self.now[0],prices=('101','104')) if self.now[0]>NOW else books()):
            self.engine.ingest(self.trigger())
        self.assertFalse(any(r[0]=='place' for r in self.trace))
        self.assertTrue(all(o['state']=='UNSENT' for o in self.store.all_orders()))

    def test_shared_gate_skew_retries_both_and_counts_once_without_sending(self):
        def skew_cache():
            pair=self.store.pairs(active=True)[0]
            pair['last_books'][1]['exchange_at']=self.now[0]-.301
            self.store.pair(pair)
        self.change_before_gate(skew_cache)
        with patch.object(self.market,'books',side_effect=[books(),books(skew=301)]) as fetch:
            self.engine.ingest(self.trigger())
        self.assertEqual(fetch.call_count,2)
        self.assertEqual([s['reason'] for s in self.skips()],['book_skew'])
        self.assertEqual([o['state'] for o in self.store.all_orders()],['UNSENT','UNSENT'])
        self.assertFalse(any(r[0]=='place' for r in self.trace))

    def test_all_v1_shared_gate_rejections_are_real_zero_send_batches(self):
        original=self.venues[0].place
        for reason in REASONS[2:]:
            self.venues[0].place=original
            self.store.db.execute('DELETE FROM baseline_samples')
            seed_baseline(self.store,self.now[0])
            self.engine.baseline.record({'binance':{'BTC':'100'},'bitget':{'BTC':'102'},'bybit':{'BTC':'100'}},('BTC',))
            def change(reason=reason):
                if reason=='funding_diff':
                    pair=self.store.pairs(active=True)[0]
                    pair['funding'][0]['rate']='.000401'
                    self.store.pair(pair)
                elif reason=='baseline_not_flat':
                    self.store.db.execute('DELETE FROM baseline_samples')
                    seed_baseline(self.store,self.now[0],'10.01')
                else:
                    mids={'binance':{'BTC':'100'},'bitget':{'BTC':'102'}}
                    if reason=='not_single_venue_outlier':
                        mids['bybit']={'BTC':'101'}
                    self.engine.baseline.record(mids,('BTC',))
            self.change_before_gate(change)
            with self.subTest(reason=reason):
                self.engine.ingest(self.trigger(reason))
                self.assertEqual(self.skips()[-1]['reason'],reason)
                self.assertFalse(any(r[0]=='place' for r in self.trace))
                self.assertTrue(all(o['state']=='UNSENT' for o in self.store.all_orders()))

    def test_retry_rechecks_equity_and_never_bypasses_original_hard_limit(self):
        self.change_before_gate(lambda:self.now.__setitem__(0,self.now[0]+1.01))
        calls=[]
        def fetch(instruments):
            calls.append(1)
            if len(calls)==2:
                self.data['equity_by_venue']['bitget']='29800'
                self.engine.account_cache.publish(self.snapshot())
            return books(self.now[0])
        with patch.object(self.market,'books',side_effect=fetch):
            self.engine.ingest(self.trigger())
        self.assertEqual(len(calls),2)
        self.assertFalse(any(r[0]=='place' for r in self.trace))
        self.assertIn(self.store.meta('state'),('HALTING','HALTED'))

    def test_initial_pricing_delay_counts_toward_age_and_retry_budget(self):
        from tools.crossex_exec.policy import review
        def slow(*args,**kwargs):
            result=review(*args,**kwargs)
            self.now[0]+=1.01
            return result
        self.engine.live=self.engine.executor.live=False
        with patch('tools.crossex_exec.engine.review',side_effect=slow), patch.object(
                self.market,'books',side_effect=lambda instruments:books(self.now[0])) as fetch:
            self.assertIsNone(self.engine.ingest(self.trigger()))
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(self.skips()[-1]['reason'],'book_stale')

    def test_ingest_records_each_v1_reason_and_never_sends(self):
        for reason in REASONS[2:]:
            self.store.db.execute('DELETE FROM baseline_samples')
            seed_baseline(self.store,self.now[0], '10.01' if reason=='baseline_not_flat' else '0')
            mids = {'binance':{'BTC':'100'},'bitget':{'BTC':'102'}}
            if reason!='too_few_venues':
                mids['bybit']={'BTC':'101' if reason=='not_single_venue_outlier' else '100'}
            self.engine.baseline.record(mids,('BTC',))
            payload = self.trigger(reason)
            if reason=='funding_diff':
                original=self.market.funding
                self.market.funding=lambda instruments: [dict(row,rate='.000401' if n==0 else '0')
                    for n,row in enumerate(original(instruments))]
            with self.subTest(reason=reason):
                self.assertIsNone(self.engine.ingest(payload))
                self.assertEqual(self.skips()[-1]['reason'],reason)
        self.assertFalse(any(r[0]=='place' for r in self.trace))

    def test_shared_gate_rechecks_all_four_v1_reasons(self):
        for reason in REASONS[2:]:
            with self.subTest(reason=reason):
                # Direct shared gate call isolates each last-moment rejection.
                p=self.pair(('0','0'))
                p.update(state='OPENING')
                self.store.pair(p)
                self.store.db.execute('DELETE FROM baseline_samples')
                seed_baseline(self.store,self.now[0],'10.01' if reason=='baseline_not_flat' else '0')
                mids={'binance':{'BTC':'100'},'bitget':{'BTC':'102'}}
                if reason!='too_few_venues':
                    mids['bybit']={'BTC':'101' if reason=='not_single_venue_outlier' else '100'}
                self.engine.baseline.record(mids,('BTC',))
                if reason=='funding_diff':
                    p['funding'][0]['rate']='.000401'
                    self.store.pair(p)
                orders=[self.engine.new_intent(p,n,'1','100.03' if n==0 else '101.96',False) for n in (0,1)]
                with self.assertRaisesRegex(ValueError,reason):
                    self.engine.check_pair(orders)

    def test_closing_ignores_age_skew_and_v1_rules_but_records_skew(self):
        p=self.pair()
        p['open_book_skew_ms']=100
        self.store.pair(p)
        self.engine.baseline.record({},('BTC',))
        with patch.object(self.market,'books',side_effect=lambda instruments:[
                books(self.now[0],skew=2300,age=60)[0 if i.market.exchange=='binance' else 1] for i in instruments]):
            self.engine.close_pair(p,'stale_exit')
        self.assertEqual(p['state'],'CLOSED')
        self.assertEqual(p['close_book_skew_ms'],2300)
        self.assertEqual(p['open_book_skew_ms'],100)
        self.assertTrue(all(v.quantity==0 for v in self.venues))


class ReferenceAndAPITests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'exec.db'
        self.store=Store(self.path)
        self.addCleanup(self.store.close)
        self.baseline=Baseline(self.store,lambda:NOW)

    def test_latest_round_is_durable_and_failure_replaces_rather_than_merges(self):
        mids={'binance':{'BTC':'100'},'bitget':{'BTC':'102'},'bybit':{'BTC':'100'},'foreign':{'BTC':'100'}}
        self.baseline.record(mids,('BTC',))
        self.assertEqual(self.baseline.latest('BTC'),quotes())
        with Store(self.path) as reopened:
            self.assertEqual(Baseline(reopened,lambda:NOW).latest('BTC'),quotes())
        self.baseline.record({'binance':{'BTC':'100'}},('BTC',),at=NOW+30)
        self.assertEqual(self.baseline.latest('BTC'),dict(at=NOW+30,mids={'binance':'100'}))
        self.baseline.record({},('BTC',),at=NOW+60)
        self.assertEqual(self.baseline.latest('BTC'),dict(at=NOW+60,mids={}))

    def test_all_six_counters_are_zero_then_count_once_per_trigger_across_restart(self):
        view=JournalView(self.path,{},clock=lambda:NOW)
        self.assertEqual(view.get('summary',{})['opening_skip_counts'],dict.fromkeys(REASONS,0))
        for reason in REASONS:
            self.store.claim(reason,{'received_at':NOW})
            self.store.skip(reason,reason)
            self.store.skip(reason,reason)
        with Store(self.path) as reopened:
            for reason in REASONS:
                reopened.skip(reason,reason)
        self.assertEqual(view.get('summary',{})['opening_skip_counts'],dict.fromkeys(REASONS,1))
        self.assertEqual(len([e for e in self.store.events() if e['kind']=='SKIP']),6)

    def test_skip_counter_trigger_marker_and_event_commit_atomically(self):
        original=self.store.set_meta
        def fail(key,value):
            if key=='opening_skip_counts':
                raise ValueError('injected_skip_commit_failure')
            return original(key,value)
        self.store.claim('r7',{'received_at':NOW})
        with patch.object(self.store,'set_meta',side_effect=fail), self.assertRaises(ValueError):
            self.store.skip('r7','book_skew')
        self.assertFalse(self.store.events())
        self.store.skip('r7','book_skew')
        self.assertEqual(self.store.meta('opening_skip_counts'),{'book_skew':1})

    def test_background_round_exception_invalidates_reference_set(self):
        from tools.crossex_exec.engine import Engine
        from types import SimpleNamespace
        engine=Engine(self.store,SimpleNamespace(),SimpleNamespace(cross=None,bitget=None),clock=lambda:NOW)
        self.addCleanup(engine.close)
        engine.bases=('BTC',)
        engine.market.baseline_quotes=lambda bases: {'binance':{'BTC':'100'},'bitget':{'BTC':'102'},'bybit':{'BTC':'100'}}
        engine.refresh_baselines()
        with patch.object(engine.market,'baseline_quotes',side_effect=ValueError('injected_failure')):
            with self.assertRaises(ValueError):
                engine.refresh_baselines()
        self.assertEqual(engine.baseline.latest('BTC')['mids'],{})

    def test_api_skew_fields_keep_null_for_legacy_and_numeric_for_new_records(self):
        for render in (lambda p:position(p,[],NOW),lambda p:trade(p,[])):
            self.assertIsNone(render({})['open_book_skew_ms'])
            self.assertIsNone(render({})['close_book_skew_ms'])
            row=render(dict(open_book_skew_ms='300',close_book_skew_ms='2300'))
            self.assertEqual(row['open_book_skew_ms'],300)
            self.assertEqual(row['close_book_skew_ms'],2300)


class PublicBookTests(unittest.TestCase):
    def payload(self,ex,stamp=True):
        row=dict(bids=[['99','1']],asks=[['101','1']])
        if ex in ('binance','aster'):
            return dict(row,**({'T':int(NOW*1000),'E':int(NOW*1000)+5} if stamp else {}))
        if ex=='bybit':
            return dict(retCode=0,time=int(NOW*1000),result=dict(s='BTCUSDT',b=row['bids'],a=row['asks'],
                        **({'cts':int(NOW*1000),'ts':int(NOW*1000)+5} if stamp else {})))
        if ex=='gate':
            return dict(row,**({'update':NOW,'current':NOW+.005} if stamp else {}))
        row.update({'ts':str(int(NOW*1000))} if stamp else {})
        return dict(code='0' if ex=='okx' else '00000',data=[row] if ex=='okx' else row)

    def test_six_official_book_timestamps_units_and_priority(self):
        from tools.crossex_exec.market import parse_execution_book
        for ex in ('binance','bybit','okx','gate','bitget','aster'):
            i=instrument(ex)
            with self.subTest(ex=ex):
                book=parse_execution_book(i.market,i.spec,self.payload(ex),NOW+.1)
                self.assertEqual(book.exchange_at,NOW)
                self.assertEqual(book.received_at,NOW+.1)
                with self.assertRaises(ValueError):
                    parse_execution_book(i.market,i.spec,self.payload(ex,False),NOW+.1)

    def test_market_books_dispatches_two_requests_concurrently_on_shared_limiter(self):
        rendezvous=threading.Barrier(2,timeout=2)
        calls=[]
        def transport(url):
            calls.append(url)
            rendezvous.wait()
            return self.payload('bitget' if 'bitget' in url else 'binance')
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            limiter=Limiter()
            market=MarketData(store,clock=lambda:NOW,client=PublicClient(limiter,transport=transport))
            try:
                result=market.opening_books([instrument(),instrument('bitget')])
                self.assertTrue(all(result))
                self.assertEqual(len(calls),2)
                self.assertEqual(limiter.stats['binance']['requests'],1)
                self.assertEqual(limiter.stats['bitget']['requests'],1)
            finally:
                market.close()

    def test_documented_clock_missing_blocks_only_opening_not_close_quotes(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'db') as store:
            market=MarketData(store,clock=lambda:NOW,client=PublicClient(Limiter(),
                transport=lambda url:self.payload('bitget' if 'bitget' in url else 'binance',False)))
            try:
                instruments=[instrument(),instrument('bitget')]
                closing=market.books(instruments)
                self.assertTrue(all(closing))
                self.assertTrue(all(b.exchange_at is None for b in closing))
                self.assertEqual(market.opening_books(instruments),[None,None])
            finally:
                market.close()
