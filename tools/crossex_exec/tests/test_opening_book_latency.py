"""Opening depth and OI scheduling with simulated venues only."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tools.crossex_exec.admission import AdmissionError, OpeningLimits
from tools.crossex_exec.engine import book_timing_details
from tools.crossex_exec.market import MarketData, value_open_interests
from tools.crossex_exec.open_interest import oi_urls
from tools.crossex_exec.storage import Store
from tools.fill_sim.core import Book, dec
from tools.fill_sim.network import Limiter, PublicClient
from test_engine import oi_payloads
from test_exec import instrument, setUpModule, tearDownModule
import test_round7 as previous


def depth(exchange, at, bid='100', ask='100.02'):
    row=dict(bids=[[bid,'10']],asks=[[ask,'10']])
    if exchange=='binance':
        return dict(row,T=int(at*1000))
    return dict(code='00000',data=dict(row,ts=str(int(at*1000))))


class OpeningQueueTests(unittest.TestCase):
    def setUp(self):
        temp=self.enterContext(tempfile.TemporaryDirectory())
        self.store=self.enterContext(Store(Path(temp)/'db'))
        self.legs=[instrument(),instrument('bitget')]

    def market(self,limiter,transport):
        market=MarketData(self.store,clock=limiter.clock,client=PublicClient(limiter,transport))
        self.addCleanup(market.close)
        return market

    def test_first_pump_sends_both_books_while_four_background_requests_are_in_flight(self):
        busy=threading.Barrier(5,timeout=3)
        release=threading.Event()
        both_books=threading.Barrier(2,timeout=3)
        sent={}
        def transport(url):
            if url=='background':
                busy.wait()
                release.wait(5)
                return {}
            ex='bitget' if 'bitget' in url else 'binance'
            sent[ex]=time.monotonic()
            both_books.wait()
            return depth(ex,time.time())

        limiter=Limiter()
        market=self.market(limiter,transport)
        low=[market.queue.submit(ex,'background',priority=2) for ex in ('gate','okx','aster','bybit')]
        backlog=market.queue.submit('gate','queued',priority=2)
        market.queue.pump(market.pool)
        try:
            busy.wait()
            # Allow just one real opening pump. The four original sends stay blocked.
            real_pump=market.opening_queue.pump
            with (patch.object(market.opening_queue,'pump') as pump,
                  patch.object(limiter,'admit_send',wraps=limiter.admit_send) as admit,
                  patch.object(limiter,'available',wraps=limiter.available) as available):
                pump.side_effect=lambda pool: real_pump(pool) if pump.call_count==1 else None
                self.assertTrue(all(market.opening_books(self.legs)))
                self.assertCountEqual([call.args[0] for call in admit.call_args_list],['binance','bitget'])
                self.assertEqual({call.args[0] for call in available.call_args_list},{'binance','bitget'})
            self.assertLess(abs(sent['binance']-sent['bitget']),.2)
            self.assertTrue(all(not job.done() for job in low))
            self.assertIsNone(backlog.sent_at)
        finally:
            release.set()
            for job in low:
                job.result(timeout=3)

    def test_full_venue_quota_delays_only_that_leg_and_times_the_wait_and_wire(self):
        now=[previous.NOW]
        limiter=Limiter(clock=lambda:now[0])
        for _ in range(2):
            self.assertTrue(limiter.admit_send('binance'))
            limiter.finish_send('binance')
        peer_returned=threading.Event()
        blocked=threading.Event()
        real_available=limiter.available
        def available(ex,**options):
            allowed=real_available(ex,**options)
            if ex=='binance' and not allowed:
                blocked.set()
            return allowed
        def transport(url):
            if 'bitget' in url:
                peer_returned.set()
                return depth('bitget',now[0])
            now[0]+=.125
            return depth('binance',now[0])

        market=self.market(limiter,transport)
        timings=[]
        with patch.object(limiter,'available',side_effect=available), ThreadPoolExecutor(1) as caller:
            fetch=caller.submit(market.opening_books,self.legs,timings=timings)
            try:
                self.assertTrue(blocked.wait(2))
                self.assertTrue(peer_returned.wait(2))
                self.assertEqual(limiter.stats['binance']['requests'],2)
                self.assertFalse(fetch.done())
            finally:
                now[0]=previous.NOW+1
            books=fetch.result(timeout=3)
        self.assertTrue(all(books))
        legs=book_timing_details(self.legs,books,previous.NOW,now[0],now[0],0,'after_fetch',timings)['legs']
        self.assertEqual((legs[0]['queue_wait_ms'],legs[0]['wire_ms'],legs[0]['limiter_blocked']),
                         (1000,125,True))
        self.assertFalse(legs[1]['limiter_blocked'])
        self.assertEqual(limiter.stats['binance']['requests'],3)

    def test_paused_venue_has_no_send_or_wire_time(self):
        limiter=Limiter(clock=lambda:previous.NOW,opening_book_reserve=True)
        limiter.throttled('binance')
        market=self.market(limiter,lambda url:depth('bitget',previous.NOW))
        timings=[]
        books=market.opening_books(self.legs,timings=timings)
        self.assertIsNone(books[0])
        self.assertIsNotNone(books[1])
        self.assertEqual(limiter.stats['binance']['requests'],0)
        self.assertEqual(timings[0],dict(queue_wait_ms=None,wire_ms=None,limiter_blocked=True))

    def test_final_limiter_refusal_never_enters_transport(self):
        limiter=Limiter()
        market=self.market(limiter,lambda url:self.fail('unexpected send'))
        timings=[]
        with (patch.object(limiter,'admit_send',return_value=False) as admit,
              patch.object(market.client,'_perform',wraps=market.client._perform) as send):
            self.assertEqual(market.opening_books(self.legs,timings=timings),[None,None])
        self.assertEqual(admit.call_count,2)
        send.assert_not_called()
        self.assertEqual(timings,[dict(queue_wait_ms=None,wire_ms=None,limiter_blocked=True)]*2)

    def test_only_opening_books_can_use_the_two_extra_global_sends(self):
        market=MarketData(self.store,clock=lambda:previous.NOW)
        self.addCleanup(market.close)
        limiter=market.client.limiter
        limiter.clock=market.clock
        sent=[]
        def transport(url):
            sent.append(url)
            return depth('bitget' if 'bitget' in url else 'binance',previous.NOW)
        market.client.transport=transport
        for ex in ('binance','bitget','okx','gate'):
            for _ in range(2):
                self.assertTrue(limiter.admit_send(ex))
                limiter.finish_send(ex)
        tracking=market.queue.submit('binance','ordinary_depth',priority=0)
        oi=market.queue.submit('bybit','ordinary_oi',priority=1)
        market.queue._send_one()
        self.assertIsNone(tracking.sent_at)
        self.assertIsNone(oi.sent_at)
        self.assertEqual(sent,[])
        timings=[]
        self.assertTrue(all(market.opening_books(self.legs,timings=timings)))
        self.assertEqual(len(sent),2)
        self.assertEqual([leg['limiter_blocked'] for leg in timings],[False,False])
        self.assertFalse(limiter.admit_send('bybit',opening_book=True))

    def test_one_batch_sends_both_oi_legs_and_keeps_binance_payload_order(self):
        together=threading.Barrier(3,timeout=3)
        payloads=oi_payloads(self.legs,previous.NOW)
        by_url={url:row for i,leg in zip(self.legs,payloads) for url,row in zip(oi_urls(i.market),leg)}
        def transport(url):
            together.wait()
            return by_url[url]
        market=self.market(Limiter(clock=lambda:previous.NOW),transport)
        with patch.object(market,'batch',wraps=market.batch) as batch:
            # Aster has no verified OI URL and must not shift the following leg.
            rows=market.open_interest_payloads([self.legs[0],instrument('aster'),self.legs[1]])
        batch.assert_called_once()
        self.assertEqual(rows,[payloads[0],[],payloads[1]])
        self.legs[1].quantity_multiplier='1000'
        book=Book([['.100','10']],[['.102','10']],previous.NOW)
        partial=value_open_interests(self.legs,[book,book],[[payloads[0][0],None],payloads[1]],previous.NOW)
        self.assertIsNone(partial[0])
        self.assertEqual(dec(partial[1]['usd']),101*100000)

    def test_only_opening_oi_has_a_three_second_batch_deadline(self):
        market=self.market(Limiter(),None)
        elapsed=[0]
        def tick(_):
            elapsed[0]+=1
        with (patch('tools.crossex_exec.market.time') as timer,patch.object(market.queue,'pump'),
              patch.object(market.opening_queue,'pump')):
            timer.monotonic.side_effect=lambda:elapsed[0]
            timer.sleep.side_effect=tick
            self.assertEqual(market.open_interest_payloads(self.legs),[[],[]])
            self.assertEqual(elapsed[0],3)
            self.assertEqual(market.open_interests(self.legs,[None,None]),[None,None])
            self.assertEqual(elapsed[0],13)
            self.assertEqual(market.opening_books(self.legs),[None,None])
            self.assertEqual(elapsed[0],23)
            self.assertEqual(market.books(self.legs),[None,None])
            self.assertEqual(elapsed[0],33)


class OpeningReviewTests(unittest.TestCase):
    setUp=previous.EngineAdmissionTests.setUp
    snapshot=previous.EngineAdmissionTests.snapshot
    pair=previous.EngineAdmissionTests.pair
    trigger=previous.EngineAdmissionTests.trigger

    def oi_market(self,transport):
        clock=lambda:self.now[0]
        market=MarketData(self.store,clock=clock,client=PublicClient(Limiter(clock=clock),transport))
        self.addCleanup(market.close)
        return market

    def assert_oi_skip(self,identity):
        self.assertIsNone(self.engine.ingest(self.trigger(identity)))
        skips=[json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
        failures=skips[-1].pop('oi_failures')
        self.assertTrue(failures and all(row.get('kind') for row in failures))
        self.assertEqual(skips[-1],dict(id=identity,reason='oi_missing',stage='review'))
        self.assertEqual(self.store.all_orders(),[])
        self.assertFalse(any(r[0]=='place' for r in self.trace))

    def review_with_market(self, market):
        self.trigger()  # Publish the account snapshot and seed the in-memory funding observations.
        instruments=list(self.market.mapping.values())
        with patch.object(self.engine,'market',market):
            return self.engine.opening_review(instruments,['0','0'],self.market.funding(instruments),
                                              ['5000000']*2,dict(baseline_bps='0'))

    def test_consecutive_reviews_fetch_both_oi_legs_before_books(self):
        instruments=list(self.market.mapping.values())
        urls=[url for i in instruments for url in oi_urls(i.market)]
        responses={}
        market=self.oi_market(responses.__getitem__)
        batches_before_books=[]
        def current_books(legs):
            batches_before_books.append(fetch.call_count)
            return previous.books(self.now[0],prices=('100',price))
        with (patch.object(self.market,'open_interest_payloads',market.open_interest_payloads),
              patch.object(market,'batch',wraps=market.batch) as fetch,
              patch.object(self.market,'books',side_effect=current_books)):
            for quantity,price in (('100000','102'),('200000','103')):
                raw=oi_payloads(instruments,self.now[0],quantity)
                responses.update(zip(urls,(row for leg in raw for row in leg)))
                _,fill,_=self.review_with_market(self.market)
                self.assertEqual([dec(oi['usd']) for oi in fill['open_interests']],
                                 [dec(quantity)*100,dec(quantity)*dec(price)])
                self.now[0]+=1
        self.assertEqual(batches_before_books,[1,2])
        self.assertEqual([[url for _,url,_ in call.args[0]] for call in fetch.call_args_list],[urls,urls])

    def test_slow_oi_precedes_books_and_retry_reprices_the_same_payloads_locally(self):
        market=MarketData(self.store,clock=lambda:self.now[0])
        self.addCleanup(market.close)
        instruments=list(self.market.mapping.values())
        urls=[url for i in instruments for url in oi_urls(i.market)]
        trace=[]
        def batch(items,**options):
            if not options.get('opening_books'):
                self.assertEqual([url for _,url,_ in items],urls)
                trace.append('oi')
                self.now[0]+=1.25
                rows=[row for leg in oi_payloads(instruments,self.now[0]) for row in leg]
                return [receive(row) for (_,_,receive),row in zip(items,rows)]
            trace.append('books')
            bid,ask=('102','102.02') if trace.count('books')==1 else ('103','103.02')
            return [parse(depth(ex,self.now[0],bid,ask) if ex=='bitget' else depth(ex,self.now[0]))
                    for ex,_,parse in items]
        check,outlier=OpeningLimits.books,OpeningLimits.outlier
        def checked(limits,books,now):
            trace.append('check')
            return check(limits,books,now)
        def expire_first_pair(limits,*args):
            result=outlier(limits,*args)
            if trace.count('books')==1:
                self.now[0]+=1.125
            return result
        with (patch.object(market,'batch',side_effect=batch),patch.object(OpeningLimits,'books',checked),
              patch.object(OpeningLimits,'outlier',expire_first_pair)):
            _,fill,_=self.review_with_market(market)
        self.assertEqual(trace,['oi','books','check','check','check','books','check','check','check'])
        self.assertEqual(dec(fill['open_interests'][1]['usd']),dec('103.01')*100000)
        events=[json.loads(e['value']) for e in self.store.events() if e['kind']=='BOOK_RETRY']
        self.assertEqual(len(events),1)
        timing=events[0]['book_timing']
        self.assertEqual((timing['attempt'],timing['checkpoint'],timing['review_ms']),(0,'final',1125))
        self.assertEqual([leg['receipt_age_ms'] for leg in timing['legs']],[1125,1125])

    def test_review_records_actual_leg_timings_even_when_a_book_is_missing(self):
        limiter=Limiter(clock=lambda:self.now[0])
        limiter.throttled('binance')
        def transport(url):
            self.now[0]+=.125
            return depth('bitget',self.now[0])
        market=MarketData(self.store,clock=limiter.clock,client=PublicClient(limiter,transport))
        self.addCleanup(market.close)
        with (patch.object(market,'open_interest_payloads',side_effect=self.market.open_interest_payloads),
              self.assertRaises(AdmissionError) as failure):
            self.review_with_market(market)
        self.assertEqual(failure.exception.reason,'book_stale')
        timing=failure.exception.details
        self.assertEqual((timing['attempt'],timing['checkpoint']),(1,'after_fetch'))
        first,second=timing['legs']
        self.assertEqual((first['fetched'],first['queue_wait_ms'],first['wire_ms'],first['limiter_blocked']),
                         (False,None,None,True))
        self.assertEqual((second['fetched'],second['queue_wait_ms'],second['wire_ms'],second['limiter_blocked']),
                         (True,0,125,False))
        self.assertFalse(any(e['kind']=='BOOK_RETRY' for e in self.store.events()))

    def test_oi_batch_failure_still_rejects_as_oi_missing_with_fresh_books(self):
        market=MarketData(self.store,clock=lambda:self.now[0])
        self.addCleanup(market.close)
        def batch(items,**options):
            if not options.get('opening_books'):
                raise TimeoutError('oi unavailable')
            return [parse(depth(ex,self.now[0])) for ex,_,parse in items]
        with patch.object(market,'batch',side_effect=batch), self.assertRaisesRegex(ValueError,'^oi_missing$'):
            self.review_with_market(market)

    def test_one_oi_request_still_in_flight_rejects_pair_and_next_fetch_requests_both_legs(self):
        instruments=list(self.market.mapping.values())
        raw=oi_payloads(instruments,self.now[0])
        urls=[oi_urls(i.market) for i in instruments]
        by_url={url:row for group,leg in zip(urls,raw) for url,row in zip(group,leg)}
        slow_url=urls[1][0]
        entered,release=threading.Event(),threading.Event()
        sent=[]
        def transport(url):
            sent.append(url)
            if url==slow_url:
                entered.set()
                if not release.wait(15):
                    raise TimeoutError('fixture_release')
            return by_url[url]
        market=self.oi_market(transport)
        try:
            with patch.object(self.market,'open_interest_payloads',market.open_interest_payloads):
                self.assert_oi_skip('oi-in-flight')
            self.assertTrue(entered.is_set())
            self.assertCountEqual(sent,urls[0]+urls[1])
        finally:
            release.set()
            for worker in market.queue.workers:
                worker.result(timeout=3)
        self.now[0]+=1  # Advance the simulated venue rate-limit window.
        self.assertEqual(market.open_interest_payloads(instruments),raw)
        self.assertCountEqual(sent,(urls[0]+urls[1])*2)

    def test_failed_or_invalid_binance_fragment_is_rejected_and_fetched_again(self):
        instruments=list(self.market.mapping.values())
        raw=oi_payloads(instruments,self.now[0])
        urls=[oi_urls(i.market) for i in instruments]
        by_url={url:row for group,leg in zip(urls,raw) for url,row in zip(group,leg)}
        mark_url=urls[0][1]
        marks=iter((TimeoutError('wire timeout'),dict(raw[0][1],markPrice='NaN'),raw[0][1]))
        sent=[]
        def transport(url):
            sent.append(url)
            row=next(marks) if url==mark_url else by_url[url]
            if isinstance(row,Exception):
                raise row
            return row
        market=self.oi_market(transport)
        with patch.object(self.market,'open_interest_payloads',market.open_interest_payloads):
            for identity in ('oi-timeout','oi-malformed'):
                self.assert_oi_skip(identity)
                self.now[0]+=1
        self.assertEqual(market.open_interest_payloads(instruments),raw)
        self.assertCountEqual(sent,(urls[0]+urls[1])*3)

    def test_fresh_batch_still_rejects_one_expired_exchange_timestamp(self):
        instruments=list(self.market.mapping.values())
        raw=oi_payloads(instruments,self.now[0])
        raw[1]=oi_payloads(instruments,self.now[0]-300)[1]
        by_url={url:row for i,leg in zip(instruments,raw) for url,row in zip(oi_urls(i.market),leg)}
        market=self.oi_market(by_url.__getitem__)
        self.assertEqual(market.open_interest_payloads(instruments),raw)
        self.now[0]+=.001
        # Open the next venue window while keeping the exchange age at 300.001 s.
        with (patch.object(market.client.limiter,'clock',return_value=self.now[0]+1),
              patch.object(self.market,'open_interest_payloads',market.open_interest_payloads),
              patch.object(market,'batch',wraps=market.batch) as batch):
            self.assert_oi_skip('oi-expired')
        batch.assert_called_once()
        self.assertEqual([url for _,url,_ in batch.call_args.args[0]],list(by_url))

    def test_aster_without_oi_route_keeps_the_other_legs_sizing_constraint(self):
        self.trigger()
        legs=[instrument('aster'),instrument('bitget')]
        self.engine.baseline.record({ex:{'BTC':mid} for ex,mid in (('aster','100'),('bitget','102'),('bybit','100'))},('BTC',))
        raw=oi_payloads(legs,self.now[0],quantity='500')
        market=self.oi_market({oi_urls(legs[1].market)[0]:raw[1][0]}.__getitem__)
        with (patch.object(self.market,'open_interest_payloads',market.open_interest_payloads),
              patch.object(self.market,'books',return_value=previous.books(self.now[0]))):
            _,fill,_=self.engine.opening_review(legs,['0','0'],self.market.funding(legs),['5000000']*2,dict(baseline_bps='0'))
        self.assertEqual(oi_urls(legs[0].market),[])
        self.assertIsNone(fill['open_interests'][0])
        sizing=fill['sizing']
        self.assertEqual(sizing['oi_legs_used'],[1])
        self.assertEqual(dec(sizing['constraints']['oi']),500*102*dec(self.engine.settings.read()['values']['oi_fraction']))
        self.assertFalse(any(e['kind']=='BOOK_RETRY' for e in self.store.events()))
