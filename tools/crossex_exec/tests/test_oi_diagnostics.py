"""Open-interest diagnostics use simulated venues and the real review journal."""
import json
import sqlite3
import sys
import unittest
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import Mock, patch

from tools.crossex_exec.admission import AdmissionError
from tools.crossex_exec.engine import bounded_oi_failures
from tools.crossex_exec.market import value_open_interests
from tools.crossex_exec.open_interest import oi_urls, parse_oi
from tools.crossex_exec.sizing import size_plan
from tools.fill_sim.network import Limiter
from baseline_fixtures import seed_baseline
from test_engine import oi_payloads
from test_exec import instrument, setUpModule, tearDownModule
import test_opening_book_latency as latency
import test_round7 as previous


def failure(leg, exchange, kind, *, stage='payload', raw_symbol='BTCUSDT', **details):
    return dict(leg=leg, exchange=exchange, raw_symbol=raw_symbol, stage=stage, kind=kind, **details)


def gate_fixture():
    gate = instrument('gate')
    gate.market = replace(gate.market, raw_symbol='BTC_USDT')
    gate.symbol = 'GATE_FUTURE_BTC_USDT'
    return gate, [dict(contract='BTC_USDT', total_size='1250', quanto_multiplier='1', mark_price='100')]


@contextmanager
def slow_batch_writes(market, now):
    """Charge two milliseconds for writes to the real batch error list on the test thread."""
    batch = market.batch
    sink = None

    def watch_errors(items, **options):
        nonlocal sink
        sink = options.get('errors')
        return batch(items, **options)

    def slow_writes(frame, event, method):
        if event == 'c_call' and sink is not None and method.__name__ in ('append', 'extend') and method.__self__ is sink:
            now[0] += .002

    with patch.object(market, 'batch', side_effect=watch_errors):
        previous_profile = sys.getprofile()
        try:
            sys.setprofile(slow_writes)
            yield
        finally:
            sys.setprofile(previous_profile)


class OIMarketDiagnosticsTests(unittest.TestCase):
    setUp = latency.OpeningQueueTests.setUp
    market = latency.OpeningQueueTests.market

    def test_partial_batch_keeps_job_errors_aligned_with_binance_fragments(self):
        history,mark=oi_urls(self.legs[0].market)
        def transport(url):
            if url==history:
                return oi_payloads(self.legs,previous.NOW)[0][0]
            if url==mark:
                raise OSError('disconnected')
            raise TimeoutError('wire timeout')

        market=self.market(Limiter(clock=lambda:previous.NOW),transport)
        failures=[]
        with patch.object(market,'batch',wraps=market.batch) as batch:
            self.assertEqual(market.open_interest_payloads(self.legs,failures=failures),[[],[]])
        batch.assert_called_once()
        self.assertEqual(batch.call_args.kwargs['errors'],[None,'request_failed','timeout'])
        self.assertEqual(failures,[failure(0,'binance','request_failed'),failure(1,'bitget','timeout')])

    def test_whole_batch_exception_reports_only_routed_legs(self):
        market=self.market(Limiter(),None)
        failures=[]
        legs=[instrument('aster'),self.legs[1]]
        with patch.object(market,'batch',side_effect=TimeoutError('whole batch')) as batch:
            self.assertEqual(market.open_interest_payloads(legs,failures=failures),[[],[]])
        batch.assert_called_once()
        self.assertEqual(failures,[failure(1,'bitget','request_failed')])

    def test_gate_receipt_age_preserves_payload_and_value_results_without_a_collector(self):
        gate,raw=gate_fixture()
        legs=[instrument('aster'),gate]
        received_at=1943
        market=self.market(Limiter(),None)
        def batch(items, **_):
            (_, _, receive), = items
            with patch.object(market,'clock',return_value=received_at):
                return [receive(raw)]
        for age,stale in ((240,False),(300,False),(301.26,True),(-1.37,True)):
            now=received_at+age
            with (self.subTest(age=age),patch.object(market,'batch',side_effect=batch),
                  patch.object(market,'clock',return_value=now)):
                failures=[]
                payloads=[[],[(raw,received_at)]]
                plain=market.open_interest_payloads(legs)
                self.assertEqual(plain,[[],[]] if stale else payloads)
                self.assertEqual(market.open_interest_payloads(legs,failures=failures),plain)
                books=previous.books(now,prices=('102','100'))
                values=value_open_interests(legs,books,payloads,now)
                self.assertEqual(value_open_interests(legs,books,payloads,now,failures=failures),values)
                if stale:
                    self.assertEqual(values,[None,None])
                    self.assertEqual(failures,[failure(1,'gate','stale',stage=stage,raw_symbol='BTC_USDT',
                                                       age_s=round(age,1)) for stage in ('payload','value')])
                    with self.assertRaisesRegex(ValueError,'^oi_missing$'):
                        parse_oi(gate.market,[raw],None,now,contract_size=gate.spec.contract_size,received_at=received_at)
                else:
                    self.assertEqual(values[1]['usd'],'125000')
                    self.assertEqual(values[1]['at'],received_at)
                    self.assertEqual(failures,[])

    def test_binance_expired_mark_keeps_its_original_parse_error(self):
        raw=oi_payloads(self.legs,previous.NOW)
        raw[0][1]['time']-=302340
        with self.assertRaisesRegex(ValueError,'^oi_unit_unverified$'):
            parse_oi(self.legs[0].market,raw[0],100,previous.NOW)
        failures=[]
        valued=value_open_interests(self.legs,previous.books(),raw,previous.NOW,failures=failures)
        self.assertIsNone(valued[0])
        self.assertIsNotNone(valued[1])
        self.assertEqual(failures,[failure(0,'binance','stale',stage='value',age_s=302.3)])

    def test_each_gate_response_keeps_its_receipt_across_a_five_minute_boundary(self):
        btc, ticker = gate_fixture()
        eth = replace(btc, market=replace(btc.market, raw_symbol='ETH_USDT'), spec=replace(btc.spec, base='ETH'))
        legs = [btc, eth]
        now = [2099.75]
        market = self.market(Limiter(clock=lambda: now[0]), None)

        def batch(items, **_):
            eth_response = items[1][2]([dict(ticker[0], contract='ETH_USDT')])
            now[0] = 2100.25
            btc_response = items[0][2](ticker)
            now[0] = 2102
            return [btc_response, eth_response]

        with patch.object(market, 'batch', side_effect=batch):
            payloads = market.open_interest_payloads(iter(legs))
        rows = value_open_interests(legs, previous.books(2120, prices=('100', '100')), payloads, 2120)
        self.assertEqual([row['at'] for row in rows], [2100.25, 2099.75])
        self.assertEqual([row['usd'] for row in rows], ['125000', '125000'])

    def test_gate_catalog_and_midpoint_fail_at_the_stage_that_can_verify_them(self):
        gate, ticker = gate_fixture()
        now = previous.NOW
        market = self.market(Limiter(clock=lambda: now), {oi_urls(gate.market)[0]: ticker}.__getitem__)
        for spec in (None, replace(gate.spec, contract_size='10')):
            with self.subTest(spec=spec):
                failures = []
                self.assertEqual(market.open_interest_payloads([replace(gate, spec=spec)], failures=failures), [[]])
                self.assertEqual(failures, [failure(0, 'gate', 'parse_invalid', raw_symbol='BTC_USDT',
                                                    error='oi_unit_unverified')])
            now += 1  # Each independent fetch gets its own limiter window.
        failures = []
        payloads = market.open_interest_payloads([gate], failures=failures)
        self.assertEqual(payloads, [[(ticker, now)]])
        self.assertEqual(failures, [])
        book = previous.books(now, prices=('102', '100'))[0]
        self.assertEqual(value_open_interests([gate], [book], payloads, now, failures=failures), [None])
        self.assertEqual(failures, [failure(0, 'gate', 'parse_invalid', stage='value', raw_symbol='BTC_USDT',
                                            error='oi_unit_unverified')])

    def test_value_parse_error_is_bounded_and_collector_errors_do_not_escape(self):
        class BrokenCollector(list):
            def extend(self, rows):
                raise RuntimeError('collector unavailable')

        raw=oi_payloads(self.legs,previous.NOW)
        failures=[]
        with patch('tools.crossex_exec.market.parse_oi',side_effect=ValueError('x'*120)) as parse:
            for options in ({},{'failures':failures},{'failures':BrokenCollector()}):
                self.assertEqual(value_open_interests(self.legs,previous.books(),raw,previous.NOW,**options),[None,None])
        self.assertEqual(parse.call_count,6)
        self.assertEqual(failures,[failure(n,i.market.exchange,'parse_invalid',stage='value',error='x'*80)
                                   for n,i in enumerate(self.legs)])

    def test_slow_error_collector_cannot_complete_the_next_job_before_its_deadline_check(self):
        market = self.market(Limiter(), None)
        items = [('gate', url, None) for url in ('first', 'late', 'last')]
        for partial in (True, False):
            first, late, last = Future(), Future(), Future()
            first.set_result('first')
            late.set_running_or_notify_cancel()
            last.set_result('last')

            class Collector(list):
                def extend(self, kinds):
                    # Model a request completing while the caller is recording diagnostics.
                    if not late.done():
                        late.set_result('late')
                    super().extend(kinds)

            errors = Collector()
            with (self.subTest(partial=partial), patch.object(market.queue, 'submit', side_effect=[first, late, last]),
                  patch.object(last, 'result', wraps=last.result) as last_result):
                if partial:
                    self.assertEqual(market.batch(items, partial=True, deadline=0, errors=errors), ['first', None, 'last'])
                    self.assertEqual(errors, [None, 'timeout', None])
                    last_result.assert_called_once()
                else:
                    with self.assertRaisesRegex(ValueError, '^public_data_unavailable$') as caught:
                        market.batch(items, partial=False, deadline=0, errors=errors)
                    self.assertIsInstance(caught.exception.__context__, TimeoutError)
                    self.assertEqual(errors, [None, 'timeout'])
                    last_result.assert_not_called()
            self.assertEqual(late.result(timeout=0), 'late')

    def test_wrapped_batch_receives_diagnostics_once_for_valid_payloads(self):
        raw = oi_payloads(self.legs, previous.NOW)
        market = self.market(Limiter(clock=lambda: previous.NOW), None)

        def batch(items, *, priority, partial, deadline, **_):
            return [row for leg in raw for row in leg]

        failures = []
        with patch.object(market, 'batch', side_effect=batch) as fetch:
            self.assertEqual(market.open_interest_payloads(self.legs, failures=failures), raw)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.kwargs['errors'], [])
        self.assertEqual(failures, [])

    def test_payload_parses_the_300_second_leg_before_publishing_the_first_failure(self):
        now = [previous.NOW]
        raw = oi_payloads(self.legs, now[0] - 300)
        raw[0][1]['markPrice'] = 'NaN'
        by_url = {url: row for leg, data in zip(self.legs, raw) for url, row in zip(oi_urls(leg.market), data)}
        market = self.market(Limiter(clock=lambda: now[0]), by_url.__getitem__)
        failures = Mock()

        def slow_write(_):
            now[0] += .002
            raise RuntimeError('collector unavailable')

        failures.append.side_effect = failures.extend.side_effect = slow_write
        with patch('tools.crossex_exec.market.parse_oi', wraps=parse_oi) as parse:
            self.assertEqual(market.open_interest_payloads(self.legs, failures=failures), [[], raw[1]])
        self.assertEqual([call.args[3] for call in parse.call_args_list], [previous.NOW, previous.NOW])
        failures.extend.assert_called_once_with([failure(0, 'binance', 'parse_invalid', error='nonfinite_number')])
        failures.append.assert_not_called()

    def test_partial_batch_error_publication_cannot_age_the_other_leg(self):
        now = [previous.NOW]
        raw = oi_payloads(self.legs, now[0] - 300)
        by_url = {url: row for leg, data in zip(self.legs, raw) for url, row in zip(oi_urls(leg.market), data)}
        mark = oi_urls(self.legs[0].market)[1]

        def transport(url):
            if url == mark:
                raise TimeoutError()
            return by_url[url]

        market = self.market(Limiter(clock=lambda: now[0]), transport)
        failures = []
        with slow_batch_writes(market, now):
            self.assertEqual(market.open_interest_payloads(self.legs, failures=failures), [[], raw[1]])
        self.assertEqual(failures, [failure(0, 'binance', 'timeout')])
        self.assertEqual(now[0], previous.NOW + .002)

    def test_payload_clock_is_sampled_again_after_the_previous_parse(self):
        now = [previous.NOW]
        raw = oi_payloads(self.legs, now[0] - 300)
        market = self.market(Limiter(clock=lambda: now[0]), None)
        failures = []

        def parse(market_key, data, price, at):
            value = parse_oi(market_key, data, price, at)
            now[0] += .002
            return value

        with (patch.object(market, 'batch', return_value=raw[0] + raw[1]),
              patch('tools.crossex_exec.market.parse_oi', side_effect=parse)):
            self.assertEqual(market.open_interest_payloads(self.legs, failures=failures), [raw[0], []])
        self.assertEqual(failures, [failure(1, 'bitget', 'stale', age_s=300.0)])

    def test_value_finishes_both_parses_before_one_failing_extend(self):
        raw = oi_payloads(self.legs, previous.NOW)
        failures = Mock()
        trace = []

        def invalid(market, *args):
            trace.append(market.exchange)
            raise ValueError('bad_payload')

        def unavailable(rows):
            trace.append('publish')
            raise RuntimeError('collector unavailable')

        failures.extend.side_effect = unavailable
        with patch('tools.crossex_exec.market.parse_oi', side_effect=invalid):
            self.assertEqual(value_open_interests(self.legs, previous.books(), raw, previous.NOW, failures=failures),
                             [None, None])
        self.assertEqual(trace, ['binance', 'bitget', 'publish'])
        failures.extend.assert_called_once_with([failure(n, leg.market.exchange, 'parse_invalid', stage='value',
                                                         error='bad_payload') for n, leg in enumerate(self.legs)])

    def test_batch_extends_errors_once_even_if_the_collector_rejects_them(self):
        market = self.market(Limiter(), None)
        items = [('gate', url, None) for url in ('ok', 'timeout', 'last')]
        jobs = [Future() for _ in items]
        jobs[0].set_result('ok')
        jobs[1].set_exception(TimeoutError())
        jobs[2].set_result('last')
        for partial in (False, True):
            errors = Mock()
            errors.extend.side_effect = RuntimeError('collector unavailable')
            with self.subTest(partial=partial), patch.object(market.queue, 'submit', side_effect=jobs):
                if partial:
                    self.assertEqual(market.batch(items, partial=True, errors=errors), ['ok', None, 'last'])
                    errors.extend.assert_called_once_with([None, 'timeout', None])
                else:
                    with self.assertRaisesRegex(ValueError, '^public_data_unavailable$') as caught:
                        market.batch(items, errors=errors)
                    self.assertIs(caught.exception.__context__, jobs[1].exception())
                    errors.extend.assert_called_once_with([None, 'timeout'])
                errors.append.assert_not_called()


class OIReviewDiagnosticsTests(unittest.TestCase):
    setUp = previous.EngineAdmissionTests.setUp
    snapshot = previous.EngineAdmissionTests.snapshot
    pair = previous.EngineAdmissionTests.pair
    trigger = previous.EngineAdmissionTests.trigger
    oi_market = latency.OpeningReviewTests.oi_market
    review_with_market = latency.OpeningReviewTests.review_with_market

    def assert_oi_skip(self,identity):
        self.assertIsNone(self.engine.ingest(self.trigger(identity)))
        skips=[json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
        self.last_oi_failures=skips[-1].pop('oi_failures')
        self.assertEqual(skips[-1],dict(id=identity,reason='oi_missing',stage='review'))
        self.assertEqual(self.store.all_orders(),[])
        self.assertFalse(any(r[0]=='place' for r in self.trace))

    def test_provider_exception_and_payload_count_mismatch_skip_before_books(self):
        raw=oi_payloads(list(self.market.mapping.values()),self.now[0])
        both_failed=[failure(0,'binance','request_failed'),failure(1,'bitget','request_failed')]
        extras=[failure(n,ex,'parse_invalid',error='payload_count_mismatch') for n,ex in enumerate(('binance','bitget'))]
        for identity,result,expected in (('oi-provider',TimeoutError(),both_failed),
                                         ('oi-short',raw[:1],both_failed[1:]),('oi-extra',raw+[[]],extras)):
            with (self.subTest(identity=identity),patch.object(self.market,'open_interest_payloads',side_effect=[result]) as fetch,
                  patch.object(self.market,'books') as books):
                self.assert_oi_skip(identity)
                self.assertEqual(self.last_oi_failures,expected)
                fetch.assert_called_once()
                books.assert_not_called()

    def test_review_at_300_seconds_keeps_three_oi_and_two_book_requests_with_slow_batch_writes(self):
        legs = list(self.market.mapping.values())
        raw = oi_payloads(legs, self.now[0] - 300)
        by_url = {url: row for leg, data in zip(legs, raw) for url, row in zip(oi_urls(leg.market), data)}
        requests = []

        def transport(url):
            requests.append(url)
            if url in by_url:
                return by_url[url]
            if 'bitget' in url:
                return latency.depth('bitget', self.now[0], '102', '102.02')
            return latency.depth('binance', self.now[0])

        market = self.oi_market(transport)
        market.client.limiter.opening_book_reserve = True
        with slow_batch_writes(market, self.now):
            books, fill, _ = self.review_with_market(market)
        self.assertCountEqual(requests[:3], by_url.keys())
        self.assertEqual(len(requests), 5)
        self.assertEqual(len(books), 2)
        self.assertEqual(fill['sizing']['oi_legs_used'], [0, 1])

    def test_gate_receipt_1e28_preserves_stale_in_the_review_and_value_diagnostics(self):
        gate, ticker = gate_fixture()
        legs = [gate, instrument('bitget')]
        bitget = oi_payloads(legs, self.now[0])[1]
        received_at = 1e28
        payloads = [[(ticker, received_at)], bitget]
        by_url = {oi_urls(gate.market)[0]: ticker, oi_urls(legs[1].market)[0]: bitget[0]}
        market = self.oi_market(by_url.__getitem__)
        batch = market.batch
        def future_receipt(items, **options):
            with patch.object(market, 'clock', return_value=received_at):
                return batch(items, **options)
        with (patch.object(self.engine, 'market', market), patch.object(market, 'batch', side_effect=future_receipt),
              patch.object(market, 'opening_books') as books,
              self.assertRaisesRegex(AdmissionError, '^oi_missing$') as caught):
            self.engine.opening_review(legs, [], [], [], dict(baseline_bps='0'))
        books.assert_not_called()
        self.assertEqual(caught.exception.details['oi_failures'], [failure(0, 'gate', 'stale', raw_symbol='BTC_USDT')])
        failures = []
        values = value_open_interests(legs, previous.books(), payloads, self.now[0], failures=failures)
        self.assertIsNone(values[0])
        self.assertIsNotNone(values[1])
        self.assertEqual(failures, [failure(0, 'gate', 'stale', stage='value', raw_symbol='BTC_USDT')])

    def test_provider_exception_preserves_an_already_reported_stale_leg(self):
        stale = failure(0, 'binance', 'stale')

        def interrupted(legs, *, failures):
            failures.extend([stale])
            raise OSError('batch interrupted')

        with patch.object(self.market, 'open_interest_payloads', interrupted), patch.object(self.market, 'books') as books:
            self.assert_oi_skip('oi-interrupted')
        books.assert_not_called()
        self.assertEqual(self.last_oi_failures, [stale, failure(1, 'bitget', 'request_failed')])

    def assert_gate_expiry_blocks_orders(self, target, check, stage):
        gate, ticker = gate_fixture()
        legs = [gate, instrument('bitget')]
        trigger = dict(self.trigger('gate-expiry-'+stage), buy_market_key=gate.dump()['market'])
        self.market.mapping = {i.market.key: i for i in legs}
        self.accounts.fee_snapshots[gate.market.key] = self.accounts.fee_snapshots[instrument().market.key]
        seed_baseline(self.store, self.now[0], venues=('gate', 'bitget'))
        self.engine.baseline.record({ex: {'BTC': p} for ex, p in (('gate', '100'), ('bitget', '102'), ('bybit', '100'))}, ('BTC',))

        received_at = self.now[0] - 300
        market = self.oi_market({oi_urls(gate.market)[0]: ticker}.__getitem__)
        with patch.object(market, 'clock', return_value=received_at):
            gate_payload, = market.open_interest_payloads([gate])
        payloads = [gate_payload, oi_payloads(legs, self.now[0])[1]]
        before_send = getattr(target, check)

        def advance(*args):
            self.now[0] += .25
            return before_send(*args)

        with (patch.object(self.market, 'open_interest_payloads', return_value=payloads),
              patch.object(self.market, 'books', return_value=previous.books(self.now[0])),
              patch.object(target, check, side_effect=advance) as barrier):
            pair = self.engine.ingest(trigger)

        barrier.assert_called_once()
        self.assertEqual([row['at'] for row in pair['open_interests']], [received_at, received_at+300])
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
        self.assertEqual(skips, [dict(id=trigger['id'], reason='oi_missing', stage=stage, pair_id=pair['id'],
            oi_failures=[failure(0, 'gate', 'stale', stage=stage, raw_symbol='BTC_USDT', age_s=300.2)])])
        self.assertFalse(any(row[0]=='place' for row in self.trace))

    def test_expired_gate_receipt_blocks_send_with_a_fresh_peer(self):
        self.assert_gate_expiry_blocks_orders(self.engine.executor, 'check_pair', 'send')

    def test_expired_gate_receipt_blocks_final_release_with_a_fresh_peer(self):
        self.assert_gate_expiry_blocks_orders(self.engine, 'check_cached_volumes', 'release')

    def test_oi_expiring_after_payload_validation_records_value_stage_without_refetch(self):
        instruments=list(self.market.mapping.values())
        raw=oi_payloads(instruments,self.now[0])
        raw[1]=oi_payloads(instruments,self.now[0]-300)[1]
        responses={url:row for i,leg in zip(instruments,raw) for url,row in zip(oi_urls(i.market),leg)}
        market=self.oi_market(responses.__getitem__)
        def books(legs):
            self.now[0]+=.26
            return previous.books(self.now[0])
        with (patch.object(self.market,'open_interest_payloads',market.open_interest_payloads),
              patch.object(market,'batch',wraps=market.batch) as batch,patch.object(self.market,'books',side_effect=books) as fetch,
              patch('tools.crossex_exec.market.parse_oi',wraps=parse_oi) as parse,patch('tools.crossex_exec.engine.size_plan') as size):
            self.assert_oi_skip('oi-value-expired')
        self.assertEqual(self.last_oi_failures,[failure(1,'bitget','stale',stage='value',age_s=300.3)])
        batch.assert_called_once()
        fetch.assert_called_once()
        self.assertEqual(parse.call_count,4)
        size.assert_not_called()

    def test_sizing_omission_reports_only_the_unused_leg(self):
        with patch('tools.crossex_exec.engine.size_plan',return_value=dict(oi_legs_used=[1])) as size:
            self.assert_oi_skip('oi-unused')
        size.assert_called_once()
        self.assertEqual(self.last_oi_failures,[failure(0,'binance','not_used_by_sizing',stage='sizing')])

    def test_all_legs_expiring_at_sizing_keep_the_original_exception_and_reason(self):
        instruments=list(self.market.mapping.values())
        raw=oi_payloads(instruments,self.now[0]-300)
        errors=[]
        def size_after_expiry(legs,books,interests,config,available,now):
            try:
                return size_plan(legs,books,interests,config,available,now+.001)
            except ValueError as error:
                errors.append(error)
                raise
        with (patch.object(self.market,'open_interest_payloads',return_value=raw) as fetch,
              patch('tools.crossex_exec.engine.size_plan',side_effect=size_after_expiry) as size):
            self.assert_oi_skip('oi-all-unused')
        fetch.assert_called_once()
        size.assert_called_once()
        self.assertEqual(self.last_oi_failures,[failure(n,i.market.exchange,'not_used_by_sizing',stage='sizing')
                                               for n,i in enumerate(instruments)])
        self.assertIs(type(errors[0]),ValueError)
        self.assertEqual(errors[0].details,{'oi_failures':self.last_oi_failures})

    def test_diagnostic_construction_failure_preserves_skip_reason_and_count(self):
        with (patch.object(self.market,'open_interest_payloads',side_effect=RuntimeError('provider')) as fetch,
              patch('tools.crossex_exec.engine.record_oi_failure',side_effect=RuntimeError('diagnostics')),
              patch.object(self.market,'books') as books):
            self.assertIsNone(self.engine.ingest(self.trigger('oi-broken-diagnostics')))
        fetch.assert_called_once()
        books.assert_not_called()
        skips=[json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
        self.assertEqual(skips,[dict(id='oi-broken-diagnostics',reason='oi_missing',stage='review')])
        self.assertEqual(self.store.meta('opening_skip_counts')['oi_missing'],1)
        self.assertEqual(self.store.all_orders(),[])

    def test_unserializable_provider_diagnostic_preserves_single_skip_write(self):
        def payloads(legs, *, failures, **_):
            failures.append(failure(0,'binance','parse_invalid',error=object()))
            return [[],oi_payloads(legs,self.now[0])[1]]
        with (patch.object(self.market,'open_interest_payloads',payloads),
              patch.object(self.store,'skip',wraps=self.store.skip) as skip):
            self.assertIsNone(self.engine.ingest(self.trigger('oi-bad-detail')))
        skip.assert_called_once()
        skips=[json.loads(e['value']) for e in self.store.events() if e['kind']=='SKIP']
        self.assertEqual(skips,[dict(id='oi-bad-detail',reason='oi_missing',stage='review')])
        self.assertEqual(self.store.meta('opening_skip_counts')['oi_missing'],1)

    def test_payload_skip_records_the_failed_fragment_and_kind_before_books(self):
        legs = list(self.market.mapping.values())
        raw = oi_payloads(legs, self.now[0])
        urls = [oi_urls(leg.market) for leg in legs]
        expired = oi_payloads(legs, self.now[0] - 300.001)[1][0]
        cases = (
            ('oi-wire-timeout', urls[1][0], TimeoutError(), failure(1, 'bitget', 'timeout')),
            ('oi-mark-timeout', urls[0][1], TimeoutError(), failure(0, 'binance', 'timeout')),
            ('oi-mark-invalid', urls[0][1], dict(raw[0][1], markPrice='NaN'),
             failure(0, 'binance', 'parse_invalid', error='nonfinite_number')),
            ('oi-bucket-expired', urls[1][0], expired, failure(1, 'bitget', 'stale', age_s=300.0)),
        )
        for identity, url, response, expected in cases:
            by_url = {endpoint: row for group, leg in zip(urls, raw) for endpoint, row in zip(group, leg)}
            by_url[url] = response

            def transport(endpoint):
                row = by_url[endpoint]
                if isinstance(row, Exception):
                    raise row
                return row

            market = self.oi_market(transport)
            with (self.subTest(identity=identity), patch.object(self.market, 'open_interest_payloads', market.open_interest_payloads),
                  patch.object(self.market, 'books') as books):
                self.assert_oi_skip(identity)
                self.assertEqual(self.last_oi_failures, [expected])
                books.assert_not_called()

    def test_unrouted_leg_diagnostics_reuse_the_review_routes(self):
        self.trigger()
        legs = [instrument('aster'), instrument('bitget')]
        self.engine.baseline.record({ex: {'BTC': mid} for ex, mid in (('aster','100'),('bitget','102'),('bybit','100'))}, ('BTC',))
        raw = oi_payloads(legs, self.now[0], quantity='500')
        market = self.oi_market({oi_urls(legs[1].market)[0]: raw[1][0]}.__getitem__)
        with (patch.object(self.market, 'open_interest_payloads', market.open_interest_payloads),
              patch('tools.crossex_exec.market.oi_urls', wraps=oi_urls) as routes,
              patch.object(self.market, 'books', return_value=previous.books(self.now[0]))):
            _, fill, _ = self.engine.opening_review(legs, ['0','0'], self.market.funding(legs), ['5000000']*2, dict(baseline_bps='0'))
        self.assertEqual([call.args[0] for call in routes.call_args_list], [leg.market for leg in legs])
        self.assertEqual(fill['sizing']['oi_legs_used'], [1])

    def test_sizing_uses_the_original_reason_even_if_its_argument_cannot_be_compared(self):
        class Reason:
            def __eq__(self, other):
                raise RuntimeError('comparison is unavailable')

            def __str__(self):
                return 'size_too_small'

        error = ValueError(Reason())
        with patch('tools.crossex_exec.engine.size_plan', side_effect=error):
            self.assertIsNone(self.engine.ingest(self.trigger('oi-sizing-reason')))
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'SKIP']
        self.assertEqual(skips, [dict(id='oi-sizing-reason', reason='size_too_small', stage='review')])
        self.assertEqual(self.store.meta('opening_skip_counts')['size_too_small'], 1)

    def test_sizing_reraises_the_same_exception_if_diagnostics_fail(self):
        class Unprintable(ValueError):
            def __str__(self):
                raise RuntimeError('formatting is unavailable')

        for error in (Unprintable(), ValueError('oi_missing')):
            with (self.subTest(error_type=type(error)), patch('tools.crossex_exec.engine.size_plan', side_effect=error),
                  patch('tools.crossex_exec.engine.oi_missing_error', side_effect=RuntimeError('diagnostics')),
                  self.assertRaises(ValueError) as raised):
                self.review_with_market(self.market)
            self.assertIs(raised.exception, error)

    def test_skip_projects_four_bounded_rows_before_the_single_sqlite_write(self):
        trigger = self.trigger('oi-bounded')
        limit = self.store.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8192)
        self.addCleanup(self.store.db.setlimit, sqlite3.SQLITE_LIMIT_LENGTH, limit)
        error = AdmissionError('oi_missing')
        long = 'x' * 16384
        row = dict(leg=0, exchange=long, raw_symbol=long, stage=long, kind=long, extra=long)
        error.details = {'oi_failures': [dict(row, error=long, age_s=23), row, row, row, None]}
        with (patch.object(self.engine, 'opening_review', side_effect=error),
              patch.object(self.store, 'skip', wraps=self.store.skip) as skip):
            self.assertIsNone(self.engine.ingest(trigger))
        skip.assert_called_once()
        bounded = dict(leg=0, exchange='x'*16, raw_symbol='x'*40, stage='x'*16, kind='x'*24)
        expected = [dict(bounded, error='x'*80, age_s=23.0), bounded, bounded, bounded]
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'SKIP']
        self.assertEqual(skips, [dict(id='oi-bounded', reason='oi_missing', stage='review', oi_failures=expected)])
        self.assertIs(type(skips[0]['oi_failures'][0]['age_s']), float)
        self.assertEqual(self.store.meta('opening_skip_counts')['oi_missing'], 1)

    def test_invalid_row_omits_all_diagnostics_and_keeps_each_skip(self):
        valid = failure(0, 'gate', 'stale', age_s=301.2)
        invalid_rows = [dict(valid, age_s=float('nan')), dict(valid, leg=10**5000), dict(valid, error=object()), {}]
        expected = []
        for n, invalid in enumerate(invalid_rows):
            identity = f'oi-invalid-row-{n}'
            error = AdmissionError('oi_missing')
            error.details = {'oi_failures': [valid, invalid]}
            with (self.subTest(identity=identity), patch.object(self.engine, 'opening_review', side_effect=error),
                  patch.object(self.store, 'skip', wraps=self.store.skip) as skip):
                self.assertIsNone(self.engine.ingest(self.trigger(identity)))
                skip.assert_called_once()
            expected.append(dict(id=identity, reason='oi_missing', stage='review'))
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'SKIP']
        self.assertCountEqual(skips, expected)
        self.assertEqual(self.store.meta('opening_skip_counts')['oi_missing'], len(expected))

    def test_emoji_and_json_escapes_keep_the_single_skip_under_the_8k_sqlite_limit(self):
        limit = self.store.db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8192)
        self.addCleanup(self.store.db.setlimit, sqlite3.SQLITE_LIMIT_LENGTH, limit)
        for identity, glyph in (('oi-emoji', '\U0001f600'), ('oi-control', '\x00')):
            text = glyph * 16384
            error = AdmissionError('oi_missing')
            error.details = {'oi_failures': [dict(leg=n, exchange=text, raw_symbol=text, stage=text, kind=text,
                                                  error=text) for n in range(4)]}
            with self.subTest(identity=identity), patch.object(self.engine, 'opening_review', side_effect=error):
                self.assertIsNone(self.engine.ingest(self.trigger(identity)))
        skips = [json.loads(event['value']) for event in self.store.events() if event['kind'] == 'SKIP']
        projected = [dict(leg=n, exchange='?'*16, raw_symbol='?'*40, stage='?'*16, kind='?'*24, error='?'*80)
                     for n in range(4)]
        self.assertGreater(len(json.dumps(projected).encode('utf-8')), 1024)
        self.assertLessEqual(len(json.dumps(projected[:3]).encode('utf-8')), 1024)
        self.assertCountEqual(skips, [dict(id='oi-emoji', reason='oi_missing', stage='review', oi_failures=projected[:3]),
                                      dict(id='oi-control', reason='oi_missing', stage='review')])
        self.assertEqual(self.store.meta('opening_skip_counts')['oi_missing'], 2)

    def test_json_budget_includes_a_fourth_row_at_exactly_1024_bytes(self):
        rows = [dict(leg=n, exchange='?'*16, raw_symbol='?'*40, stage='?'*16, kind='?'*24, error='?'*76)
                for n in range(4)]
        self.assertEqual(len(json.dumps(rows).encode('utf-8')), 1024)
        self.assertEqual(bounded_oi_failures(rows), rows)
