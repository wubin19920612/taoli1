"""Offline regression coverage for the PM's first decision round."""
from concurrent.futures import Future
from dataclasses import asdict
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from urllib.error import HTTPError

from tools.fill_sim.core import EXCHANGES, Market, dec, funding_pnl
from tools.fill_sim.engine import Engine
from tools.fill_sim.funding import funding_urls, parse_funding, fetch_funding
from tools.fill_sim.network import PublicClient, Limiter, RateLimited
from tools.fill_sim.reporting import summarize
from tools.fill_sim.storage import atomic_json
from tools.fill_sim.__main__ import guard, window, default_fees_json, unconfigured_fees
from tools.fill_sim.tests import test_fill_sim as base


def market(ex):
    return Market(ex, "future", "BTC_USDT" if ex == "gate" else
                  "BTC-USDT-SWAP" if ex == "okx" else "BTCUSDT")


class FundingParserTests(base.OfflineCase):
    def test_stale_server_timestamp_rejected(self):
        d = {"symbol":"BTCUSDT", "lastFundingRate":"0", "nextFundingTime":200000, "time":0}
        with self.assertRaisesRegex(ValueError,"stale_funding_response"):
            parse_funding(market("binance"),d,100,[{"symbol":"BTCUSDT","fundingIntervalHours":1}])

    def test_recorded_six_funding_snapshots(self):
        for ex in EXCHANGES:
            with self.subTest(exchange=ex):
                rows = json.loads((base.FIXTURES / "funding" / (ex+".json")).read_text())
                s = parse_funding(market(ex), rows[0]["response"], rows[0]["received_at"],
                                  rows[1]["response"] if len(rows) > 1 else None)
                self.assertGreater(s["next"], s["captured_at"])
                self.assertEqual(s["interval"], 28800)
                self.assertEqual(s["market_key"], market(ex).key)
                self.assertTrue(all(r["url"].startswith("https://") for r in rows))
                self.assertEqual([r["url"] for r in rows], funding_urls(market(ex)))

    def test_okx_uses_upcoming_funding_time_and_signed_rate(self):
        d = {"code":"0", "data":[{"instId":"BTC-USDT-SWAP", "fundingRate":"-0.001",
             "fundingTime":"100000", "nextFundingTime":"3700000"}]}
        s = parse_funding(market("okx"), d, 90)
        self.assertEqual((s["next"], s["interval"], s["rate"]), (100,3600,"-0.001"))
        self.assertAlmostEqual(funding_pnl([s,{"spot":True}],90,100,100,100)[0], .1)

    def test_missing_interval_never_defaults_to_eight_hours(self):
        d = {"symbol":"BTCUSDT", "lastFundingRate":"0", "nextFundingTime":100000}
        for ex in ("binance", "aster"):
            for info in (None, [], [{"symbol":"ETHUSDT","fundingIntervalHours":8}],
                         [{"symbol":"BTCUSDT","fundingIntervalHours":0}]):
                with self.subTest(exchange=ex, info=info), self.assertRaises((ValueError, KeyError)):
                    parse_funding(market(ex),d,90,info)

    def test_actual_one_hour_interval_not_normalized_to_eight(self):
        d = {"symbol":"BTCUSDT", "lastFundingRate":"0.001", "nextFundingTime":100000}
        for ex in ("binance", "aster"):
            s = parse_funding(market(ex),d,90,[{"symbol":"BTCUSDT","fundingIntervalHours":1}])
            self.assertEqual(s["interval"],3600)
            self.assertAlmostEqual(funding_pnl([s,{"spot":True}],90,3700,100,100)[0],-.2)

    def test_rejects_missing_duplicate_mismatched_and_invalid_fields(self):
        d = {"code":"00000", "data":[{"symbol":"BTCUSDT","fundingRate":"0.0001",
             "fundingRateInterval":"4","nextUpdate":"100000"}]}
        for field, value in (("fundingRate","NaN"),("fundingRate",""),
                             ("fundingRateInterval","0"),("fundingRateInterval","Infinity"),
                             ("nextUpdate","0"),("nextUpdate","100000000000"),
                             ("symbol","ETHUSDT")):
            bad = copy.deepcopy(d)
            bad["data"][0][field] = value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                parse_funding(market("bitget"),bad,90)
        d["data"] *= 2
        with self.assertRaises(ValueError):
            parse_funding(market("bitget"),d,90)

    def test_spot_has_zero_funding_and_no_requests(self):
        m = Market("gate","spot","BTC_USDT")
        client = PublicClient(Limiter(), lambda url: self.fail("spot funding requested"))
        self.assertEqual(funding_urls(m),[])
        self.assertEqual(fetch_funding(client,m,lambda:90),{"spot":True})

    def test_second_request_and_primary_share_rate_budget(self):
        c = base.Clock(90)
        urls = []
        def transport(url):
            urls.append(url)
            return ([{"symbol":"BTCUSDT","fundingIntervalHours":8}] if "fundingInfo" in url else
                    {"symbol":"BTCUSDT","lastFundingRate":"0.01","nextFundingTime":100000})
        client = PublicClient(Limiter(clock=c),transport)
        fetch_funding(client,market("binance"),c)
        self.assertEqual(len(urls),2)
        self.assertFalse(base.send(client.limiter, ["binance"]))
        with self.assertRaises(RateLimited):
            fetch_funding(client,market("binance"),c)

    def test_public_funding_rate_limits_pause_same_exchange(self):
        def transport(url):
            raise HTTPError(url,429,"rate limit",{},None)
        c = base.Clock(90)
        client = PublicClient(Limiter(clock=c),transport)
        with self.assertRaises(RateLimited):
            fetch_funding(client,market("gate"),c)
        self.assertEqual(client.limiter.stats["gate"]["paused_until"],150)
        self.assertFalse(base.send(client.limiter, ["gate"]))


class FundingEngineTests(base.OfflineCase):
    setUp = base.EngineTests.setUp

    def public_transport(self, url):
        if "order_book" in url or "depth" in url:
            return self.depth_transport(url)
        self.funding_calls.append(url)
        if "gateio" in url:
            return {"name":"BTC_USDT","funding_rate":"0.0001","funding_interval":3600,
                    "funding_next_apply":self.clock()+100}
        if "fundingInfo" in url:
            return [{"symbol":"BTCUSDT","fundingIntervalHours":4}]
        return {"symbol":"BTCUSDT","lastFundingRate":"0.0002","nextFundingTime":(self.clock()+100)*1000}

    def fresh_public_open(self):
        self.engine.funding_cache.clear()
        self.funding_calls = []
        self.client.transport = self.public_transport
        p = base.event(self.clock)
        # Upstream snapshots must be ignored, even if present and plausible.
        p["fill_sim_funding"] = [dict(rate=99, next=self.clock()+1000, interval=1)]*2
        self.engine.ingest(p)
        for _ in range(20):
            self.engine.tick()
            self.clock.advance(.2)

    def test_fetch_once_per_contract_shared_by_all_variants_without_payload(self):
        self.fresh_public_open()
        self.assertEqual(len(self.engine.positions),12)
        self.assertEqual(len(self.funding_calls),3)
        self.assertEqual(len(self.calls),4)
        for p in self.engine.positions.values():
            self.assertTrue(p["funding_known"])
            self.assertEqual([s["rate"] for s in p["funding"]],["0.0001","0.0002"])
            self.assertEqual([s["interval"] for s in p["funding"]],[3600,14400])
            self.assertLessEqual(max(s["captured_at"] for s in p["funding"]),p["opened_at"])

    def test_cache_ttl_exact_market_and_settlement_boundary(self):
        m = market("gate")
        self.assertIsNotNone(self.engine._cached_funding(m))
        self.assertIsNone(self.engine._cached_funding(Market("gate","future","ETH_USDT")))
        self.assertIsNone(self.engine._cached_funding(Market("bybit","future","BTCUSDT")))
        self.clock.advance(299)
        self.assertIsNotNone(self.engine._cached_funding(m))
        self.clock.advance(1)
        self.assertIsNone(self.engine._cached_funding(m))
        self.engine.funding_cache[m.key]["at"] = self.clock()
        self.engine.funding_cache[m.key]["snapshot"]["next"] = self.clock()
        self.assertIsNone(self.engine._cached_funding(m))

    def test_second_event_reuses_cache_without_funding_http(self):
        self.fresh_public_open()
        count = len(self.funding_calls)
        self.engine.ingest(base.event(self.clock,"e2","OTHER"))
        self.engine.tick()
        self.assertEqual(len(self.funding_calls),count)
        rows = [p for p in self.engine.positions.values() if p["event_id"]=="e2"]
        self.assertEqual(len(rows),4)
        self.assertTrue(all(p["funding_known"] for p in rows))

    def test_negative_cache_expires_and_does_not_become_zero_funding(self):
        m = market("gate")
        self.engine.funding_cache[m.key] = dict(at=self.clock(),snapshot=None,error="ValueError")
        self.assertIsNone(self.engine._cached_funding(m)["snapshot"])
        self.clock.advance(300)
        self.assertIsNone(self.engine._cached_funding(m))

    def test_delay_crossing_settlement_does_not_reuse_old_quote_as_known(self):
        for value in self.engine.funding_cache.values():
            value["snapshot"]["next"] = self.clock()+.5
        self.engine.ingest(base.event(self.clock))
        self.engine.tick()
        self.clock.advance(1)
        self.engine.tick()
        self.engine.tick()
        self.assertTrue(all(p["funding_known"] == (p["variant"]=="measured")
                            for p in self.engine.positions.values()))

    def test_network_failure_provisional_even_when_upstream_claims_known(self):
        self.engine.funding_cache.clear()
        self.engine.ingest(base.event(self.clock))
        for _ in range(20):
            self.engine.tick()
            self.clock.advance(.2)
        self.assertEqual(len(self.engine.positions),12)
        self.assertTrue(all(not p["funding_known"] for p in self.engine.positions.values()))
        for p in list(self.engine.positions.values()):
            self.engine._track_position(p,[base.book(101,102,self.clock()),base.book(100,101,self.clock())])
        self.store.db.commit()
        r = window(self.store,self.clock()-100,self.clock()+1)
        self.assertEqual(r["funding_unknown_ratio"],1)
        self.assertEqual(r["main"]["n"],0)
        self.assertEqual(r["provisional_price_only"]["300"]["n"],1)
        base.publish_daily(self.store,Path(self.temp.name)/"out",self.clock())
        daily = json.loads((Path(self.temp.name)/"out"/("daily-"+base.datetime.fromtimestamp(
            self.clock(),base.timezone.utc).date().isoformat()+".json")).read_text())
        self.assertEqual(daily["funding_unknown_ratio"],1)

    def test_fresh_public_snapshot_survives_sqlite_restart(self):
        self.fresh_public_open()
        other = Engine(self.store,self.client,self.engine.fees,self.clock,base.ImmediateExecutor())
        self.addCleanup(other.close)
        self.assertEqual(len(other.positions),12)
        self.assertTrue(all(p["funding_known"] for p in other.positions.values()))
        self.assertEqual(next(iter(other.positions.values()))["funding"],next(iter(self.engine.positions.values()))["funding"])

    def test_waiting_funding_recovered_without_replaying_event(self):
        self.engine.funding_cache.clear()
        self.engine.ingest(base.event(self.clock))
        other = Engine(self.store,self.client,self.engine.fees,self.clock,base.ImmediateExecutor())
        self.addCleanup(other.close)
        self.assertEqual(list(self.store.rows("events"))[0]["status"],"interrupted_restart")
        self.assertEqual(len(list(self.store.rows("fills"))),12)
        self.engine.pending.clear()

    def test_hanging_funding_is_bounded_and_opens_unknown(self):
        self.engine.funding_cache.clear()
        self.engine.funding_jobs = {market(ex).key:Future() for ex in ("gate","aster")}
        self.engine.ingest(base.event(self.clock))
        self.clock.advance(7)
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.engine.positions),4)
        self.assertTrue(all(not p["funding_known"] for p in self.engine.positions.values()))
        self.assertEqual(self.engine._inflight(),0)
        self.assertFalse(self.engine.funding_jobs)
        self.engine.funding_jobs.clear()

    def test_funding_requests_share_opening_queue_without_reservations(self):
        self.engine.funding_cache.clear()
        self.engine.ingest(base.event(self.clock))
        sent = []
        self.client.transport = lambda url: (sent.append(url) or {})
        self.engine.tick()
        self.assertEqual(len(self.engine.funding_jobs),2)
        self.assertEqual(len(sent),2)
        self.assertEqual(sum(s["requests"] for s in self.client.limiter.stats.values()),2)
        self.assertFalse(hasattr(self.client.limiter, "acquire"))

    def test_spot_sell_rejected_before_specs_and_in_coverage_denominator(self):
        self.engine.cache.clear()
        p = base.event(self.clock)
        p["sell_market_key"]["market_type"] = "spot"
        self.engine.ingest(p)
        e = list(self.store.rows("events"))[0]
        self.assertEqual(e["status"],"unsupported_spot_short")
        self.assertTrue(e["filter_passed"])
        self.assertFalse(self.engine.want_specs)
        self.assertFalse(self.engine.pending)
        self.assertFalse(self.engine.positions)
        self.assertFalse(self.calls)
        r = summarize([e],[],[])
        self.assertEqual(r["considered_events"],1)
        self.assertEqual(r["still_there_rate"],0)
        self.assertEqual(r["main"]["n"],0)

    def test_spot_buy_allowed_with_zero_funding(self):
        p = base.event(self.clock)
        p["buy_market_key"]["market_type"] = "spot"
        m = Market.from_payload(p["buy_market_key"])
        self.engine.cache[m.key] = dict(at=self.clock(),market=asdict(m),spec=asdict(base.spec()))
        self.engine.ingest(p)
        self.engine.tick()
        self.assertEqual(len(self.engine.positions),4)
        self.assertTrue(all(p["funding"][0]=={"spot":True} for p in self.engine.positions.values()))


class GuardAndReportTests(base.OfflineCase):
    def test_default_fees_match_env_and_pm_verified_binance(self):
        fees = json.loads(default_fees_json())
        example = Path(__file__).parents[1]/"deploy/fill-sim.env.example"
        raw = next(s.split("=",1)[1] for s in example.read_text().splitlines() if s.startswith("FILL_SIM_FEES_JSON="))
        self.assertEqual(json.loads(raw.strip("'")),fees)
        self.assertEqual(unconfigured_fees(fees),[])
        self.assertEqual(fees["binance"]["future"], .0005)
        self.assertEqual(unconfigured_fees({**fees, "binance": {"spot": .001, "future": None}}), ["binance:future"])
        self.assertEqual(fees["aster"]["future"],.0004)
        self.assertTrue(all(fees[ex]["spot"] is not None for ex in EXCHANGES))
        with tempfile.TemporaryDirectory() as temp:
            store = base.Store(Path(temp)/"fees.db")
            try:
                self.assertEqual(window(store,0,1)["unconfigured_fees"],[])
            finally:
                store.close()

    def test_unknown_ratio_includes_open_and_closed_zero_denominator_is_null(self):
        self.assertIsNone(summarize([],[],[])["funding_unknown_ratio"])
        rows = [dict(closed_at=None,funding_known=known) for known in (True,False)]
        result = summarize([],[],rows)
        self.assertEqual((result["funding_unknown"],result["funding_observations"],result["funding_unknown_ratio"]),(1,2,.5))

    def test_guard_field_types_missing_and_expiry_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/"guard.json"
            good = dict(timestamp=100,taoli_stalls_last_hour=0)
            atomic_json(p,good)
            self.assertIsNone(guard(p,190)[0])
            self.assertEqual(guard(p,190.001)[0],"guard_metrics_stale")
            self.assertIsNone(guard(p,95)[0])
            self.assertEqual(guard(p,94.999)[0],"guard_metrics_stale")
            for field,value in (("timestamp",True),("timestamp","100"),("timestamp",None),
                                ("taoli_stalls_last_hour",True),("taoli_stalls_last_hour",1.5),
                                ("taoli_stalls_last_hour",-1),("no_429_increase",1),
                                ("taoli_stalls_not_worse","false")):
                atomic_json(p,{**good,field:value})
                with self.subTest(field=field,value=value):
                    self.assertEqual(guard(p,100)[0],"guard_metrics_unavailable")
            for value in ([],None,{},dict(timestamp=100),dict(taoli_stalls_last_hour=0)):
                atomic_json(p,value)
                self.assertEqual(guard(p,100)[0],"guard_metrics_unavailable")
            for value in ('NaN','Infinity','-Infinity'):
                p.write_text('{"timestamp":'+value+',"taoli_stalls_last_hour":0}')
                self.assertEqual(guard(p,100)[0],"guard_metrics_unavailable")

    def test_comparison_flags_false_are_valid_evidence_not_immediate_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/"guard.json"
            atomic_json(p,dict(timestamp=100,taoli_stalls_last_hour=4,
                               taoli_stalls_not_worse=False,no_429_increase=False))
            self.assertIsNone(guard(p,100)[0])
