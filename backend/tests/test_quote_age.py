from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_opportunities
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity
from app.models.opportunity_radar import OpportunityRadarSettings
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadLegQuery,
    PairSpreadPoint,
    PairSpreadPriceField,
)
from app.models.settings import RiskSettings
from app.services import pair_spread_query
from app.services.opportunity_radar import build_opportunity_radar_preview
from app.services.pair_spread_query import PairSpreadQueryService
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_directional_opportunity

NOW = datetime(2026, 11, 1, 8, tzinfo=UTC)


def market(exchange: str, *, mark: float = 100, **updates) -> MarketSnapshot:
    values = {
        "symbol": "BTCUSDT", "base": "BTC", "exchange": exchange,
        "market_type": MarketType.FUTURE, "raw_symbol": "BTCUSDT",
        "bid": 100.1, "ask": 100.2, "bid_size": 200, "ask_size": 200,
        "mark_price": mark, "index_price": 100, "volume_24h_usdt": 5_000_000,
        "timestamp": NOW,
    }
    values.update(updates)
    return MarketSnapshot(**values)


def opportunity(**updates) -> Opportunity:
    result = build_directional_opportunity(
        market("binance", timestamp=NOW - timedelta(seconds=2)),
        market("okx", timestamp=NOW - timedelta(seconds=3)),
        mode="FF",
    )
    return result.model_copy(update=updates)


def opportunity_client(rows, monkeypatch, *, stale_after_seconds=30):
    app = FastAPI()
    app.include_router(routes_opportunities.router, prefix="/api")
    store = SnapshotStore()
    store.set_opportunities(rows)
    app.state.snapshot_store = store
    app.state.settings_repo = SimpleNamespace(
        get_risk_settings=AsyncMock(return_value=RiskSettings(
            stale_after_seconds=stale_after_seconds,
        )),
    )
    monkeypatch.setattr(routes_opportunities, "utc_now", lambda: NOW)
    return TestClient(app), store


def test_opportunities_expire_during_collector_stall_and_keep_default_array(monkeypatch):
    row = opportunity(buy_local_timestamp=NOW, sell_local_timestamp=NOW)
    client, store = opportunity_client([row], monkeypatch)
    first = client.get("/api/opportunities")
    assert first.status_code == 200
    assert isinstance(first.json(), list)
    assert first.json()[0]["buy_age_ms"] == 0
    assert first.headers["X-Excluded-Stale"] == "0"

    monkeypatch.setattr(routes_opportunities, "utc_now", lambda: NOW + timedelta(seconds=30))
    assert len(client.get("/api/opportunities").json()) == 1
    monkeypatch.setattr(routes_opportunities, "utc_now", lambda: NOW + timedelta(seconds=31))
    response = client.get("/api/opportunities?include_metadata=true")
    assert response.status_code == 200
    assert response.json() == {"opportunities": [], "excluded_stale": 1}
    assert response.headers["X-Excluded-Stale"] == "1"
    assert client.get("/api/opportunities").json() == []
    assert store.get_opportunities()[0] is row
    assert row.buy_age_ms is None  # No request writes derived values into the cache.


@pytest.mark.parametrize("stale_leg", ["buy", "sell"])
def test_opportunities_filter_each_leg_before_sort_and_limit(monkeypatch, stale_leg):
    stale = opportunity(id="stale", open_spread_pct=99, **{
        f"{stale_leg}_local_timestamp": NOW - timedelta(seconds=31),
    })
    fresh = opportunity(id="fresh", open_spread_pct=1)
    client, _ = opportunity_client([stale, fresh], monkeypatch)
    response = client.get(
        "/api/opportunities?sort_by=open_spread_pct&limit=1&include_metadata=true",
    )
    assert [row["id"] for row in response.json()["opportunities"]] == ["fresh"]
    assert response.json()["excluded_stale"] == 1


def test_opportunities_reuse_configured_stale_threshold(monkeypatch):
    client, _ = opportunity_client([
        opportunity(buy_local_timestamp=NOW - timedelta(seconds=31)),
    ], monkeypatch, stale_after_seconds=45)
    assert len(client.get("/api/opportunities").json()) == 1


def test_opportunity_age_uses_local_clock_and_keeps_source_timestamp(monkeypatch):
    buy = market(
        "binance", timestamp=(NOW - timedelta(milliseconds=1250)).replace(tzinfo=None),
        upstream_timestamp=NOW - timedelta(seconds=60),
    )
    sell = market("okx", timestamp=NOW - timedelta(milliseconds=2500))
    row = build_directional_opportunity(buy, sell, mode="FF")
    client, _ = opportunity_client([row], monkeypatch)
    payload = client.get("/api/opportunities").json()[0]
    assert payload["buy_age_ms"] == 1250
    assert payload["buy_source_age_ms"] == 60_000
    assert payload["buy_time_source"] == "upstream"
    assert payload["sell_age_ms"] == 2500
    assert payload["sell_source_age_ms"] is None
    assert payload["sell_time_source"] == "received"
    assert row.buy_timestamp == buy.upstream_timestamp
    assert row.last_seen_at == buy.upstream_timestamp
    monkeypatch.setattr(routes_opportunities, "utc_now", lambda: NOW + timedelta(seconds=1))
    assert client.get("/api/opportunities").json()[0]["buy_age_ms"] == 2250


def test_legacy_opportunity_without_new_timestamps_still_expires(monkeypatch):
    data = opportunity().model_dump()
    for key in list(data):
        if key.endswith(("local_timestamp", "source_timestamp", "age_ms", "time_source")):
            data.pop(key)
    row = Opportunity.model_validate(data)
    client, _ = opportunity_client([row], monkeypatch)
    assert len(client.get("/api/opportunities").json()) == 1
    monkeypatch.setattr(routes_opportunities, "utc_now", lambda: NOW + timedelta(seconds=31))
    assert client.get("/api/opportunities").json() == []


def test_old_peer_is_removed_before_median_and_scoring():
    preview = build_opportunity_radar_preview([
        market("bybit", mark=98),
        market("binance", mark=100),
        # Including this peer in the median would suppress the fresh candidate.
        market("okx", mark=90, timestamp=NOW - timedelta(seconds=121)),
    ], OpportunityRadarSettings(min_depth_multiple=1, max_candidates=1), now=NOW)
    assert preview.excluded_stale == 1
    assert preview.displayed_candidates == 1
    assert preview.candidates[0].peer_exchange == "binance"
    assert preview.candidates[0].peer_median_premium_pct == 0
    assert preview.total_pairs_evaluated == 1


@pytest.mark.parametrize("stale_exchange", ["bybit", "binance"])
def test_radar_leg_age_threshold_and_request_time(stale_exchange):
    markets = [market("bybit", mark=98), market("binance")]
    for row in markets:
        if row.exchange == stale_exchange:
            row.timestamp = NOW - timedelta(seconds=120)
    settings = OpportunityRadarSettings(min_depth_multiple=1)
    assert len(build_opportunity_radar_preview(markets, settings, now=NOW).candidates) == 1
    later = build_opportunity_radar_preview(markets, settings, now=NOW + timedelta(milliseconds=1))
    assert later.candidates == []
    assert later.excluded_stale == 1


@pytest.mark.parametrize("anchor_mark", [98, 102])
def test_radar_directional_leg_ages_and_book_source(anchor_mark):
    anchor = market(
        "bybit", mark=anchor_mark, timestamp=NOW - timedelta(seconds=2),
        upstream_timestamp=NOW - timedelta(seconds=10),
        book_timestamp=NOW - timedelta(seconds=4), quote_time_source="exchange",
    )
    peer = market("binance", timestamp=NOW - timedelta(seconds=1))
    preview = build_opportunity_radar_preview([anchor, peer], now=NOW)
    candidate = preview.candidates[0]
    anchor_side = "long" if anchor_mark < 100 else "short"
    peer_side = "short" if anchor_mark < 100 else "long"
    assert getattr(candidate, f"{anchor_side}_age_ms") == 2000
    assert getattr(candidate, f"{anchor_side}_source_age_ms") == 4000
    assert getattr(candidate, f"{anchor_side}_time_source") == "exchange"
    assert getattr(candidate, f"{peer_side}_age_ms") == 1000
    assert getattr(candidate, f"{peer_side}_source_age_ms") is None
    assert candidate.data_age_seconds == 2


def current_leg(exchange, **updates):
    values = {
        "exchange": exchange, "symbol": "BTCUSDT", "raw_symbol": "BTCUSDT",
        "price": 100, "price_field": PairSpreadPriceField.MID_PRICE,
        "mid_price": 100, "timestamp": NOW - timedelta(seconds=2),
    }
    values.update(updates)
    return PairSpreadCurrentLeg(**values)


@pytest.mark.asyncio
async def test_pair_current_ages_recomputed_without_mutating_reused_legs(monkeypatch):
    left = current_leg("binance", upstream_timestamp=NOW + timedelta(seconds=1), age_ms=999)
    right = current_leg("okx")
    service = PairSpreadQueryService()
    monkeypatch.setattr(service, "_fetch_current_leg", AsyncMock(side_effect=[left, right, left, right]))
    try:
        legs = (PairSpreadLegQuery(exchange="binance", symbol="btc"),
                PairSpreadLegQuery(exchange="okx", symbol="btc"))
        first = await service.query(*legs, hours=1, interval_seconds=5, now=NOW)
        second = await service.query(*legs, hours=1, interval_seconds=5, now=NOW + timedelta(seconds=3))
        assert first.current.leg1.age_ms == 2000
        assert first.current.leg1.source_age_ms == -1000  # Preserve upstream clock skew.
        assert first.current.leg1.time_source == "upstream"
        assert first.current.leg2.source_age_ms is None
        assert second.current.leg1.age_ms == 5000
        assert left.age_ms == 999
    finally:
        await service.aclose()
        pair_spread_query._REALTIME_PAIR_SPREAD_CACHE.clear()


@pytest.mark.parametrize("missing,expected_complete,expected_gap", [
    (set(), True, 5000),
    (set(range(10, 46, 10)), True, 10000),
    (set(range(10, 361, 10)), True, 10000),  # Exactly 95% of 720 samples.
    (set(range(10, 371, 10)), False, 10000),
    ({10, 11}, True, 15000),  # Exactly three intervals is allowed.
    ({10, 11, 12}, False, 20000),
    ({0, 1, 2, 3}, False, 20000),  # Missing leading edge counts as a gap.
    ({717, 718, 719}, False, 20000),  # As does a stalled tail.
    (set(range(720)), False, 3_600_000),
])
@pytest.mark.asyncio
async def test_realtime_window_completeness_boundaries(
    monkeypatch, missing, expected_complete, expected_gap,
):
    service = PairSpreadQueryService()
    start = NOW - timedelta(hours=1)
    points = [PairSpreadPoint(
        bucket_at=start + timedelta(seconds=index * 5),
        leg1_close=100, leg2_close=101, spread_abs=1, spread_pct=1,
    ) for index in range(720) if index not in missing]
    leg1 = PairSpreadLegQuery(exchange="binance", symbol="btc")
    leg2 = PairSpreadLegQuery(exchange="okx", symbol="btc")
    key = pair_spread_query._realtime_cache_key(
        leg1, leg2, leg2_multiplier=1, interval_seconds=5,
    )
    pair_spread_query._REALTIME_PAIR_SPREAD_CACHE[key] = points
    # Without a midpoint, this request must not fabricate another sample.
    monkeypatch.setattr(service, "_fetch_current_leg", AsyncMock(side_effect=[
        current_leg("binance", mid_price=None), current_leg("okx", mid_price=None),
    ]))
    try:
        result = await service.query(leg1, leg2, hours=1, interval_seconds=5, now=NOW)
        assert result.expected_samples == 720
        assert result.actual_samples == result.point_count == 720 - len(missing)
        assert result.max_gap_ms == expected_gap
        assert result.complete is expected_complete
    finally:
        await service.aclose()
        pair_spread_query._REALTIME_PAIR_SPREAD_CACHE.clear()
