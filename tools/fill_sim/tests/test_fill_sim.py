import ast
from concurrent.futures import Future
from dataclasses import asdict, replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.fill_sim.core import (AMOUNTS, Book, Market, Spec, closing, close_reason,
                                 common_step, dec, funding_pnl, opening, track_interval, walk)
from tools.fill_sim.engine import Engine, trigger_reason
from tools.fill_sim.exchanges import endpoint, parse_book, parse_spec, check_response
from tools.fill_sim.network import Limiter, PublicClient, RateLimited, ShadowAPI, NoRedirect
from tools.fill_sim.reporting import summarize, gates, bootstrap_lower
from tools.fill_sim.storage import Store, atomic_json, run_lock
from tools.fill_sim.__main__ import guard, load_env, window, publish_daily, rss_mb, main

FIXTURES = Path(__file__).parent / "fixtures"


class OfflineCase(unittest.TestCase):
    def setUp(self):
        self.no_network = patch("socket.socket.connect", side_effect=AssertionError("unit tests must not access network"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)


class Clock:
    def __init__(self, now=1794206000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ImmediateExecutor:
    def submit(self, fn, *args):
        f = Future()
        try:
            f.set_result(fn(*args))
        except Exception as exc:
            f.set_exception(exc)
        return f

    def shutdown(self, wait=True):
        pass


def book(bid=99, ask=100, now=0, qty=10000):
    return Book([(str(bid), str(qty))], [(str(ask), str(qty))], now, now)


def spec(step="0.01", minimum="0.01", notional="0"):
    return Spec("BTC", "USDT", "1", step, minimum, notional)


def event(clock, identity="e1", symbol="BTCUSDT"):
    return dict(id=identity, symbol=symbol, opened_at=datetime.fromtimestamp(clock(), timezone.utc).isoformat(),
                closed_at=None, strategy_version="shadow-v2", variant="conv-half", fake_flag=False,
                spread_tier="0.5–1%", liquidity="gt50k",
                buy_market_key=dict(exchange="gate", market_type="future", raw_symbol="BTC_USDT", price_multiplier=1, contract_size_multiplier=1),
                sell_market_key=dict(exchange="aster", market_type="future", raw_symbol="BTCUSDT", price_multiplier=1, contract_size_multiplier=1),
                fill_sim_funding=[dict(next=clock()+1000, rate=0, interval=28800)]*2)


class ExchangeTests(OfflineCase):
    def test_fixture_provenance_and_all_twelve_markets(self):
        self.assertEqual(len(list(FIXTURES.glob("*.json"))), 12)

    def test_multiplier_mismatch_is_rejected(self):
        s = spec()
        for m in (Market("gate", "future", "BTC_USDT", "2", "1"), Market("gate", "future", "BTC_USDT", "1", "10")):
            with self.assertRaisesRegex(ValueError, "spec_mismatch"):
                s.validate(m)

    def test_null_contract_multiplier_allows_spec_source(self):
        spec().validate(Market("binance", "future", "BTCUSDT"))

    def test_gate_and_okx_convert_contract_quantity(self):
        for ex, raw, factor in (("gate", "BTC_USDT", "0.0001"), ("okx", "BTC-USDT-SWAP", "0.01")):
            f = json.loads((FIXTURES / (ex+"_future.json")).read_text())
            m = Market(ex, "future", raw)
            s = parse_spec(m, f["spec"]["response"])
            b = parse_book(m, s, f["depth"]["response"], f["depth"]["received_at"])
            raw_qty = f["depth"]["response"]["bids"][0]["s"] if ex == "gate" else f["depth"]["response"]["data"][0]["bids"][0][1]
            self.assertEqual(dec(b.bids[0][1]), dec(raw_qty)*dec(factor))

    def test_endpoints_are_exact_public_depth_and_spot(self):
        for ex in ("gate", "bitget", "binance", "bybit", "okx", "aster"):
            m = Market(ex, "spot", "BTC_USDT" if ex == "gate" else "BTCUSDT")
            self.assertIn(m.raw_symbol, endpoint(m, "depth"))
            self.assertNotIn("futures", endpoint(m, "depth"))
            self.assertNotIn("ticker", endpoint(m, "depth"))
            self.assertIn("50", endpoint(m, "depth"))

    def test_rate_error_codes_and_exchange_errors(self):
        for ex, value in (("bybit", {"retCode":10006}), ("okx", {"code":"50011"}), ("binance", {"code":-1003}), ("bitget", {"code":"429"}), ("gate", {"label":"TOO_MANY_REQUESTS_LIMIT"})):
            with self.assertRaisesRegex(ValueError, "rate_limited"):
                check_response(ex, value)
        with self.assertRaisesRegex(ValueError, "exchange_error"):
            check_response("okx", {"code":"51000"})

    def test_crossed_stale_nonfinite_empty_books(self):
        m = Market("binance", "future", "BTCUSDT")
        for raw in ({"bids":[["101","1"]],"asks":[["100","1"]]},
                    {"bids":[],"asks":[["100","1"]]},
                    {"bids":[["NaN","1"]],"asks":[["100","1"]]},
                    {"bids":[["99","1"]],"asks":[["100","1"]], "T":1000}):
            with self.assertRaises(ValueError):
                parse_book(m, spec(), raw, 100)

    def test_exact_identity_and_dex_rejected(self):
        for values in (dict(exchange="hyperliquid", market_type="future", raw_symbol="xyz:BTC"),
                       dict(exchange="gate", market_type="future", raw_symbol="BTC_USDT", dex="xyz"),
                       dict(exchange="gate", market_type="future", raw_symbol="../order")):
            with self.assertRaises(ValueError):
                Market.from_payload(values)


def fixture_test(ex, kind):
    def test(self):
        raw = "BTC_USDT" if ex == "gate" else ("BTC-USDT" + ("-SWAP" if kind == "future" else "") if ex == "okx" else "BTCUSDT")
        m = Market(ex, kind, raw)
        f = json.loads((FIXTURES / (ex+"_"+kind+".json")).read_text())
        s = parse_spec(m, f["spec"]["response"])
        b = parse_book(m, s, f["depth"]["response"], f["depth"]["received_at"])
        self.assertEqual(s.base, "BTC")
        self.assertEqual(s.quote, "USDT")
        self.assertGreater(dec(s.qty_step), 0)
        self.assertEqual((len(b.bids),len(b.asks)), (50,50))
        self.assertLess(dec(b.bids[0][0]), dec(b.asks[0][0]))
        self.assertTrue(f["spec"]["url"].startswith("https://"))
        if ex != "binance" or kind != "spot":
            self.assertIsNotNone(b.exchange_at)
    return test


for exchange in ("gate", "bitget", "binance", "bybit", "okx", "aster"):
    for market_type in ("future", "spot"):
        setattr(ExchangeTests, "test_recorded_"+exchange+"_"+market_type, fixture_test(exchange, market_type))


class MathTests(OfflineCase):
    def test_vwap_notional_eats_multiple_levels(self):
        result = walk([("100","1"),("110","2")], notional=210)
        self.assertEqual(dec(result["qty"]), 2)
        self.assertEqual(dec(result["vwap"]), 105)
        self.assertEqual(result["levels"], 2)
        self.assertEqual(dec(result["deepest"]), 110)
        self.assertEqual(dec(result["slippage_bps"]), 500)

    def test_vwap_quantity_and_insufficient_depth(self):
        self.assertEqual(dec(walk([("100","1"),("110","2")], quantity=2)["notional"]), 210)
        with self.assertRaisesRegex(ValueError, "too_thin"):
            walk([("100","1")], notional=101)

    def test_common_step_is_lcm_not_max(self):
        self.assertEqual(common_step("0.02", "0.03"), dec("0.06"))
        self.assertEqual(common_step("0.001", "10"), dec("10"))

    def test_invalid_number_is_controlled(self):
        for value in ("NaN", "Infinity", None, "n/a"):
            with self.assertRaises(ValueError):
                dec(value)

    def test_both_legs_same_quantized_quantity(self):
        f = opening(book(), book(102,103), spec("0.02"), spec("0.03"), 100, 0, 0)
        self.assertEqual(dec(f["quantity"]) % dec("0.06"), 0)
        self.assertEqual(f["buy"]["qty"], f["sell"]["qty"])
        self.assertLessEqual(dec(f["sell"]["notional"]),100)

    def test_below_min_quantity_and_notional(self):
        for s in (spec("2"), spec(notional="101")):
            self.assertEqual(opening(book(),book(102,103),s,s,100,0,0)["status"], "below_min")

    def test_too_thin_even_when_rounding_could_hide_shortfall(self):
        self.assertEqual(opening(book(qty=.5),book(102,103),spec(),spec(),100,0,0)["status"], "too_thin")

    def test_fee_threshold_gone_thin_opened(self):
        self.assertEqual(opening(book(),book(102,103),spec(),spec(),100,.0005,.0005)["status"],"opened")
        self.assertEqual(opening(book(),book(100.1,100.2),spec(),spec(),100,.0005,.0005)["status"],"gone")
        a = Book([("99","100")],[("100","0.01"),("102","100")],0)
        self.assertEqual(opening(a,book(102,103),spec(),spec(),100,.0005,.0005)["status"],"thin")

    def test_close_rules(self):
        self.assertEqual(close_reason(10,50,100), "converged")
        self.assertIsNone(close_reason(86400,51,100))
        self.assertEqual(close_reason(86400,50,100), "timeout_24h_converged")
        self.assertEqual(close_reason(172800,1000,100), "timeout_48h")
        self.assertEqual(close_reason(10,1000,100,True), "stale_exit")

    def test_tracking_schedule_boundaries(self):
        self.assertEqual([track_interval(x) for x in (0,300,301,3600,3601)], [3,3,15,15,60])

    def test_funding_settlement_ratio_and_leg_signs(self):
        schedules = [dict(next=10,interval=10,rate=.001),dict(next=20,interval=20,rate=.002)]
        self.assertEqual(funding_pnl(schedules,0,9,100,100),(0,True))
        self.assertAlmostEqual(funding_pnl(schedules,0,20,100,110)[0], .02)
        self.assertEqual(funding_pnl(None,0,100,100,100),(0,False))
        self.assertEqual(funding_pnl([dict(spot=True)]*2,0,100,100,100),(0,True))

    def test_closed_pnl_uses_original_quantity_and_four_actual_fees(self):
        f = opening(book(),book(102,103),spec(),spec(),300,.001,.002)
        p = dict(fill=f,opened_at=0,funding=[dict(spot=True)]*2)
        result = closing(p,book(101,102),book(100,101),10)
        q = dec(f["quantity"])
        expected = q*2 - q*(dec(100+101)*dec(".001")+dec(102+101)*dec(".002"))
        self.assertAlmostEqual(result["net_usd"],float(expected))
        self.assertEqual(result["close_buy"]["qty"],f["quantity"])
        self.assertTrue(result["main_eligible"])

    def test_stale_and_unknown_funding_excluded(self):
        f = opening(book(),book(102,103),spec(),spec(),300,0,0)
        for stale, schedules in ((True,[dict(spot=True)]*2),(False,None)):
            p = dict(fill=f,opened_at=0,funding=schedules)
            self.assertFalse(closing(p,book(101,102),book(100,101),10,stale)["main_eligible"])


class NetworkTests(OfflineCase):
    def test_per_exchange_and_global_limits(self):
        c, limiter = Clock(), None
        limiter = Limiter(clock=c)
        for ex in ("gate","binance","bybit","okx"):
            self.assertTrue(limiter.acquire([ex,ex]))
        self.assertFalse(limiter.acquire(["aster"]))
        c.advance(.5)
        self.assertFalse(limiter.acquire(["gate"]))
        c.advance(.5)
        self.assertTrue(limiter.acquire(["gate","gate"]))

    def test_atomic_pair_reservation_does_not_consume_other_bucket(self):
        limiter = Limiter(clock=Clock())
        self.assertTrue(limiter.acquire(["gate","gate"]))
        self.assertFalse(limiter.acquire(["gate","aster"]))
        self.assertTrue(limiter.acquire(["aster","aster"]))

    def test_429_pause_exponential_and_restart(self):
        c = Clock()
        limiter = Limiter(clock=c)
        limiter.throttled("gate")
        self.assertFalse(limiter.acquire(["gate"]))
        c.advance(60)
        self.assertTrue(limiter.acquire(["gate"]))
        limiter.throttled("gate")
        c.advance(119)
        self.assertFalse(limiter.acquire(["gate"]))
        recovered = Limiter(clock=c,state=limiter.stats)
        self.assertFalse(recovered.acquire(["gate"]))
        c.advance(1)
        self.assertTrue(recovered.acquire(["gate"]))
        self.assertEqual(recovered.stats["gate"]["rate_limits"],2)

    def test_http_418_429_and_body_code_are_accounted(self):
        for code in (418,429,10006):
            limiter = Limiter(clock=Clock())
            def transport(url):
                if code == 10006:
                    return {"retCode":10006}
                raise HTTPError(url,code,"limited",{},None)
            with self.assertRaises(RateLimited):
                PublicClient(limiter,transport).fetch("bybit","https://public.example/depth")
            self.assertEqual(limiter.stats["bybit"]["rate_limits"],1)

    def test_api_backoff_fixed_limit_and_no_status_filter(self):
        c, calls = Clock(), []
        def transport(url,headers,timeout):
            calls.append((url,headers,timeout))
            raise TimeoutError()
        api = ShadowAPI("http://localhost:8000","test-placeholder",transport,c)
        for wait in (2,4,8,16,30,30):
            api.poll()
            self.assertEqual(api.next_poll-c(),wait)
            n = len(calls)
            api.poll()
            self.assertEqual(len(calls),n)
            c.advance(wait)
        self.assertEqual(api.failures,6)
        self.assertTrue(calls[0][0].endswith("/api/shadow/trades?limit=50"))
        self.assertNotIn("status",calls[0][0])
        self.assertEqual(calls[0][2],3)
        api.transport = lambda *a,**k: []
        self.assertEqual(api.poll(),[])
        self.assertEqual(api.next_poll-c(),1)

    def test_invalid_api_response_and_no_redirect(self):
        api = ShadowAPI("http://localhost",transport=lambda *a,**k: {"rows":[]},clock=Clock())
        self.assertIsNone(api.poll())
        self.assertEqual(api.failures,1)
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,"",{},"https://other"))

    def test_cannot_raise_rate_ceiling(self):
        for a,b in ((3,8),(2,9),(0,8)):
            with self.assertRaises(ValueError):
                Limiter(a,b)

    def test_subsecond_budget_can_be_lowered_below_one(self):
        c = Clock()
        limiter = Limiter(.5, 8, clock=c)
        self.assertTrue(limiter.acquire(["gate"]))
        c.advance(1)
        self.assertFalse(limiter.acquire(["gate"]))
        c.advance(1)
        self.assertTrue(limiter.acquire(["gate"]))

    def test_non200_api_response_uses_same_backoff(self):
        def transport(*a,**k):
            raise HTTPError("http://localhost/api",503,"unavailable",{},None)
        c = Clock()
        api = ShadowAPI("http://localhost",transport=transport,clock=c)
        self.assertIsNone(api.poll())
        self.assertEqual(api.next_poll,c()+2)


class EngineTests(OfflineCase):
    def setUp(self):
        OfflineCase.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/"fill_sim.db")
        self.addCleanup(self.store.close)
        self.clock = Clock()
        self.client = PublicClient(Limiter(clock=self.clock), lambda url: {})
        self.engine = Engine(self.store,self.client,{ex:{"future":0,"spot":0} for ex in ("gate","aster")},self.clock,ImmediateExecutor())
        self.engine.want_specs.clear()
        self.addCleanup(self.engine.close)
        self.calls = []
        self.buy, self.sell = book(99,100,self.clock()), book(102,103,self.clock())
        def fetch(m,s):
            self.calls.append(m.exchange)
            source = self.buy if m.exchange == "gate" else self.sell
            if source is None:
                raise ValueError("outage")
            return replace(source,received_at=self.clock(),exchange_at=self.clock())
        self.engine._fetch_book = fetch
        for leg in ("buy_market_key","sell_market_key"):
            m = Market.from_payload(event(self.clock)[leg])
            self.engine.cache[m.key] = dict(at=self.clock(),market=asdict(m),spec=asdict(spec()))
            self.engine.funding_cache[m.key] = dict(at=self.clock(), snapshot=dict(
                rate="0", next=self.clock()+1000, interval=28800, captured_at=self.clock()), error=None)

    def open_three(self):
        self.engine.ingest(event(self.clock))
        self.engine.tick()
        self.clock.advance(1)
        self.engine.tick()
        self.engine.tick()

    def test_filters_closed_trigger_and_stale(self):
        p = event(self.clock)
        p["closed_at"] = p["opened_at"]
        self.assertIsNone(trigger_reason(p,self.clock()))
        self.assertEqual(trigger_reason(p,self.clock()+121),"skipped_stale_trigger")
        for field,value in (("strategy_version","other"),("variant","other"),("fake_flag",True),("fake_flag",0),("liquidity","lt500"),("spread_tier","<0.5%")):
            q = {**p,field:value}
            self.assertEqual(trigger_reason(q,self.clock()),"skipped_filter")

    def test_id_dedup_and_pair_dedup_reverse_direction(self):
        self.engine.ingest(event(self.clock))
        self.engine.ingest(event(self.clock))
        self.assertEqual(len(list(self.store.rows("events"))),1)
        p = event(self.clock,"e2")
        p["buy_market_key"],p["sell_market_key"] = p["sell_market_key"],p["buy_market_key"]
        self.engine.ingest(p)
        self.assertEqual(list(self.store.rows("events"))[-1]["status"],"skipped_duplicate_position")

    def test_all_variants_share_four_requests_and_independent_positions(self):
        self.open_three()
        self.assertEqual(len(self.calls),4)
        self.assertEqual(len(self.engine.positions),12)
        self.assertEqual(len(self.engine.active_events()),1)
        fills = list(self.store.rows("fills"))
        self.assertEqual(len(fills),12)
        rows = {f["variant"]:f for f in fills if f["amount"]==300}
        self.assertEqual(rows["leg_delay"]["tA"],rows["measured"]["tA"])
        self.assertEqual(rows["leg_delay"]["tB"],rows["plus_1s"]["tB"])
        self.assertGreaterEqual(rows["plus_1s"]["tA"]-rows["measured"]["tA"],1)

    def test_delay_changes_result_without_reusing_measured_price(self):
        self.engine.ingest(event(self.clock))
        self.engine.tick()
        self.sell = book(99,100,self.clock())
        self.clock.advance(1)
        self.engine.tick()
        self.engine.tick()
        rows = {f["variant"]:f for f in self.store.rows("fills") if f["amount"]==300}
        self.assertEqual(rows["measured"]["status"],"opened")
        self.assertEqual(rows["plus_1s"]["status"],"gone")
        self.assertEqual(rows["leg_delay"]["status"],"gone")

    def test_concurrency_cap_ten_events_not_twelve_variants(self):
        for n in range(10):
            self.engine.pending[str(n)] = dict(pair_key="other"+str(n))
        self.engine.ingest(event(self.clock))
        self.assertEqual(list(self.store.rows("events"))[0]["status"],"skipped_capacity")
        self.assertEqual(self.calls,[])
        self.engine.pending.clear()

    def test_positions_get_budget_before_new_openings(self):
        self.open_three()
        self.clock.advance(3)
        self.assertTrue(self.client.limiter.acquire(["gate","aster"]))
        self.engine.tick()
        self.engine.ingest(event(self.clock,"e2","OTHER"))
        self.assertEqual(list(self.store.rows("events"))[-1]["status"],"skipped_rate_limit")
        self.assertEqual(self.calls[-2:],["gate","aster"])
        self.assertEqual(len(self.engine.jobs),1)

    def test_restart_recovers_positions_and_ids_and_marks_partial_variants(self):
        self.engine.ingest(event(self.clock))
        self.engine.tick()
        other = Engine(self.store,self.client,self.engine.fees,self.clock,ImmediateExecutor())
        self.addCleanup(other.close)
        self.assertEqual(len(other.positions),4)
        other.ingest(event(self.clock))
        self.assertEqual(len(list(self.store.rows("events"))),1)
        fills = list(self.store.rows("fills"))
        self.assertEqual(sum(f["status"]=="interrupted_restart" for f in fills),8)
        self.engine.pending.clear()

    def test_stale_exit_after_one_leg_outage_excluded(self):
        self.open_three()
        self.buy = None
        self.clock.advance(3)
        self.engine.tick()
        self.engine.tick()
        self.clock.advance(60)
        self.engine.tick()
        self.assertFalse(self.engine.positions)
        positions = list(self.store.rows("positions"))
        self.assertTrue(all(p["exit_reason"]=="stale_exit" for p in positions))
        self.assertTrue(all(not p["main_eligible"] for p in positions))

    def test_healthy_sixty_second_tracking_not_false_stale(self):
        self.open_three()
        for p in self.engine.positions.values():
            p["opened_at"] = self.clock()-4000
            p["next_track"] = self.clock()+60
        self.clock.advance(60)
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.engine.positions),12)

    def test_independent_close_and_tracking_fetch_shared(self):
        self.open_three()
        self.buy,self.sell = book(101,102),book(100,101)
        self.clock.advance(3)
        self.engine.tick()
        self.engine.tick()
        self.assertFalse(self.engine.positions)
        self.assertEqual(len(self.calls),6)
        self.assertTrue(all(p["exit_reason"]=="converged" for p in self.store.rows("positions")))

    def test_missing_specs_and_mismatch_accounted(self):
        key = next(iter(self.engine.cache))
        self.engine.cache[key]["spec"]["contract_size"] = "10"
        self.engine.ingest(event(self.clock))
        self.assertEqual(list(self.store.rows("events"))[0]["status"],"spec_mismatch")
        self.engine.cache.clear()
        self.engine.ingest(event(self.clock,"e2"))
        self.assertEqual(list(self.store.rows("events"))[-1]["status"],"awaiting_specs")
        self.assertEqual(len(self.engine.want_specs),2)
        self.clock.advance(6)
        self.engine.tick()
        self.assertEqual(list(self.store.rows("events"))[-1]["status"],"spec_unavailable")

    def test_cold_symbol_fetches_specs_then_opens_without_losing_first_event(self):
        self.engine.cache.clear()
        self.engine.ingest(event(self.clock))
        with patch("tools.fill_sim.engine.parse_spec", return_value=spec()):
            for _ in range(4):
                self.engine.tick()
        self.assertEqual(len(self.engine.positions),4)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(len(self.store.meta("spec_cache")),2)

    def test_spec_ttl_refresh_and_cache_persisted(self):
        m = Market.from_payload(event(self.clock)["buy_market_key"])
        self.assertIsNotNone(self.engine.spec(m))
        self.clock.advance(21600)
        self.assertIsNone(self.engine.spec(m))
        self.assertIn(m.key,self.engine.want_specs)

    def test_shadow_outcome_updated_after_first_seen(self):
        self.open_three()
        p = event(self.clock)
        p.update(closed_at=p["opened_at"],net_bps=42)
        self.engine.ingest(p)
        self.assertTrue(all(p["shadow_net_bps"]==42 for p in self.engine.positions.values()))

    def test_delayed_variant_has_rate_limit_rows_instead_of_retry_queue(self):
        self.engine.ingest(event(self.clock))
        self.engine.tick()
        self.clock.advance(1)
        self.client.limiter.throttled("gate")
        self.engine.tick()
        self.assertEqual(sum(f["status"]=="skipped_rate_limit" for f in self.store.rows("fills")),8)
        self.assertFalse(self.engine.pending)
        self.assertEqual(len(self.engine.positions),4)

    def test_restart_after_long_gap_forces_stale_exit(self):
        self.open_three()
        self.clock.advance(120)
        recovered = Engine(self.store,self.client,self.engine.fees,self.clock,ImmediateExecutor())
        self.addCleanup(recovered.close)
        recovered.tick()
        self.assertFalse(recovered.positions)
        self.assertTrue(all(p["exit_reason"]=="stale_exit" for p in self.store.rows("positions")))
        self.engine.positions.clear()

    def test_live_engine_24h_converged_and_48h_unconditional(self):
        self.open_three()
        for p in self.engine.positions.values():
            p["opened_at"] = self.clock()-86400
        with self.store.transaction():
            for p in list(self.engine.positions.values()):
                self.engine._track_position(p,[book(101,102,self.clock()),book(100,101,self.clock())])
        self.assertFalse(self.engine.positions)
        self.assertTrue(all(p["exit_reason"]=="timeout_24h_converged" for p in self.store.rows("positions")))
        self.clock.advance(2)
        self.engine.ingest(event(self.clock,"new"))
        self.engine.tick()
        for p in self.engine.positions.values():
            p["opened_at"] = self.clock()-172800
        with self.store.transaction():
            for p in list(self.engine.positions.values()):
                self.engine._track_position(p,[book(90,91,self.clock()),book(110,111,self.clock())])
        rows = [p for p in self.store.rows("positions") if p["event_id"]=="new"]
        self.assertTrue(rows)
        self.assertTrue(all(p["exit_reason"]=="timeout_48h" for p in rows))


class StorageReportTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/"fill_sim.db")
        self.addCleanup(self.store.close)

    def test_wal_and_transaction_rollback(self):
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0],"wal")
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.event(dict(id="x",t0=1,t1=1,status="evaluated"))
                raise RuntimeError()
        self.assertFalse(self.store.seen("x"))

    def test_rotation_retains_open_families_ids_and_30_days(self):
        now = 1794206000
        with self.store.transaction():
            for identity in ("open","closed"):
                self.store.event(dict(id=identity,t0=now-86400,t1=now-86400,status="evaluated"))
                self.store.position(dict(id=identity,event_id=identity,opened_at=now-86400,closed_at=None if identity=="open" else now-80000))
        self.store.rotate(now)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events").fetchone()[0],1)
        self.assertEqual(len(list(self.store.rows("events"))),2)
        self.assertTrue(self.store.seen("closed"))
        self.assertEqual(len(self.store.open_positions()),1)
        self.store.rotate(now+32*86400)
        self.assertFalse(self.store.seen("closed"))
        self.assertEqual(len(list(self.store.rows("events"))),1)
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0],"wal")

    def sample(self):
        events,fills,positions = [],[],[]
        for n in range(350):
            e = dict(id=str(n),t1=100+n,status="evaluated",filter_passed=True)
            f = dict(event_id=str(n),variant="measured",amount=300,status="opened",symbol="coin"+str(n%7),pair="pair"+str(n%3),t1=e["t1"])
            p = dict(**f,id=str(n),opened_at=100,closed_at=(n%7)*86400+100,spread_tier="1–2%",main_eligible=True,
                     funding_known=True,net_bps=10+n%3,net_usd=.3,shadow_net_bps=20,exit_reason="converged")
            events.append(e)
            fills.append(f)
            positions.extend((p,{**p,"id":"delay"+str(n),"variant":"leg_delay"}))
        return events,fills,positions

    def test_daily_metrics_bootstrap_and_all_gates_pass_with_evidence(self):
        e,f,p = self.sample()
        summary = summarize(e,f,p)
        self.assertEqual(summary["still_there_rate"],1)
        self.assertEqual(summary["measured_300_open_events"],350)
        self.assertEqual(summary["without_top3"]["n"],200)
        self.assertGreater(summary["lower_90_bps"],0)
        results = gates(summary,7*86400,dict(taoli_stalls_not_worse=True,no_429_increase=True,max_rss_mb=50))
        self.assertTrue(all(results.values()))
        self.assertIsNone(gates(summary,7*86400)["resources"])

    def test_failure_gates_not_hidden_by_profitable_other_amounts(self):
        e,f,p = self.sample()
        for row in p:
            if row["variant"]=="leg_delay":
                row["net_bps"] = -1
        summary = summarize(e,f,p)
        self.assertFalse(gates(summary,6*86400)["observation_7_days"])
        self.assertFalse(gates(summary,7*86400)["leg_delay_positive"])
        self.assertFalse(gates(summary,7*86400,dict(taoli_stalls_not_worse=True,no_429_increase=False,max_rss_mb=50))["resources"])

    def test_stale_unknown_and_open_excluded_from_main(self):
        e,f,p = self.sample()
        p[0].update(main_eligible=False,exit_reason="stale_exit")
        p[2].update(main_eligible=False,funding_known=False)
        p[4].update(closed_at=None)
        summary = summarize(e,f,p)
        self.assertEqual(summary["main"]["n"],347)
        self.assertEqual(summary["stale_exits"]["n"],1)
        self.assertEqual(summary["funding_unknown"],1)
        self.assertEqual(summary["open_positions"],1)

    def test_bootstrap_resamples_days_not_individual_trades(self):
        rows = [dict(closed_at=100,net_bps=100)]*100 + [dict(closed_at=86500,net_bps=-100)]
        self.assertEqual(bootstrap_lower(rows),-100)
        self.assertIsNone(bootstrap_lower(rows[:100]))

    def test_empty_report_cannot_pass(self):
        summary = summarize([],[],[])
        self.assertIsNone(summary["still_there_rate"])
        self.assertFalse(gates(summary,0)["events_300_coins_5_pairs_3"])
        self.assertIsNone(summary["lower_90_bps"])

    def test_daily_json_and_late_closure_assigned_to_close_day(self):
        e,f,p = self.sample()
        now = 1794206000
        row = p[0]
        row.update(opened_at=now-3*86400,closed_at=now-1)
        with self.store.transaction():
            self.store.position(row)
        result = window(self.store,now-100,now+1)
        self.assertEqual(result["main"]["n"],1)
        publish_daily(self.store,Path(self.temp.name)/"out",now)
        self.assertEqual(len(list((Path(self.temp.name)/"out").glob("daily-*.json"))),2)

    def test_guard_stops_on_missing_stale_lag_and_accepts_fresh(self):
        p = Path(self.temp.name)/"guard.json"
        self.assertEqual(guard(None,100)[0],"guard_file_required")
        atomic_json(p,dict(timestamp=100,taoli_stalls_last_hour=5))
        self.assertEqual(guard(p,100)[0],"taoli_stalls")
        self.assertEqual(guard(p,200)[0],"guard_metrics_stale")
        atomic_json(p,dict(timestamp=100,taoli_stalls_last_hour=4))
        self.assertIsNone(guard(p,100)[0])

    def test_rss_measured_and_python310_ast(self):
        self.assertGreater(rss_mb(),0)
        for path in Path(__file__).parents[1].rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"),feature_version=(3,10))

    def test_env_whitelist_rejects_exchange_key_and_preserves_environment(self):
        path = Path(self.temp.name)/"test.env"
        path.write_text("EXCHANGE_SECRET=not-a-secret\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"unsupported_env_field"):
            load_env(path)
        path.write_text("FILL_SIM_DB='local.db'\n",encoding="utf-8")
        with patch.dict("os.environ",{},clear=True):
            load_env(path)
            import os
            self.assertEqual(os.environ["FILL_SIM_DB"],"local.db")

    def test_runtime_window_does_not_count_old_runs_or_hide_missing_evidence(self):
        with self.store.transaction():
            self.store.runtime(1,86400,50,dict(taoli_stalls_not_worse=True,no_429_increase=True),0)
            self.store.runtime(100,30,60,dict(taoli_stalls_not_worse=True,no_429_increase=True),0)
            self.store.runtime(130,30,55,{},0)
        observed, evidence = self.store.runtime_window(90,140)
        self.assertEqual(observed,60)
        self.assertEqual(evidence["max_rss_mb"],60)
        self.assertIsNone(evidence["no_429_increase"])
        self.assertIsNone(evidence["taoli_stalls_not_worse"])

    def test_runtime_own_429_blocks_resource_pass(self):
        with self.store.transaction():
            self.store.runtime(100,30,50,dict(taoli_stalls_not_worse=True,no_429_increase=True),1)
        _, evidence = self.store.runtime_window(0,101)
        self.assertFalse(evidence["no_429_increase"])

    def test_duplicate_service_process_lock_released_on_exit(self):
        path = Path(self.temp.name)/"locked.db"
        with run_lock(path):
            with self.assertRaises(OSError):
                with run_lock(path):
                    self.fail("second writer admitted")
        with run_lock(path):
            pass

    def test_real_disk_restart_and_report_cli(self):
        path = Path(self.temp.name)/"restart.db"
        store = Store(path)
        with store.transaction():
            store.event(dict(id="seen",t0=1,t1=1,status="skipped_filter"))
        store.close()
        reopened = Store(path)
        self.assertTrue(reopened.seen("seen"))
        reopened.close()
        with patch("sys.stdout",new_callable=io.StringIO) as stdout:
            code = main(["report","--db",str(path),"--days","7"])
        self.assertEqual(code,1)
        self.assertIn("FAIL observation_7_days",stdout.getvalue())
        self.assertIn("UNKNOWN resources",stdout.getvalue())

    def test_run_missing_guard_stops_before_any_network_or_token_use(self):
        path = Path(self.temp.name)/"run.db"
        with patch.dict("os.environ",{},clear=True), patch("sys.stdout",new_callable=io.StringIO) as stdout:
            code = main(["run","--db",str(path),"--out",str(Path(self.temp.name)/"out")])
        self.assertEqual(code,78)
        self.assertIn("STOP guard_file_required",stdout.getvalue())
        state = json.loads((Path(self.temp.name)/"out/status.json").read_text())
        self.assertEqual(state["api_failures"],0)
        self.assertIsNone(state["funding_unknown_ratio"])
        self.assertEqual(state["funding_observations"],0)
        self.assertEqual(state["unconfigured_fees"],[])
        self.assertTrue(all(s["requests"]==0 for s in state["exchanges"].values()))

    def test_production_code_has_no_backend_or_third_party_imports(self):
        import sys
        root = Path(__file__).parents[1]
        local = {p.stem for p in root.glob("*.py")}
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node,ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node,ast.ImportFrom) and not node.level:
                    names = [node.module.split(".")[0]]
                else:
                    continue
                self.assertTrue(all(n in sys.stdlib_module_names or n in local for n in names),(path,names))


if __name__ == "__main__":
    unittest.main()
