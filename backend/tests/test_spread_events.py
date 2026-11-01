import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.spread_events import SpreadEventsParams
from app.services.spread_events import aggregate_spread_events, query_spread_events

BASE = datetime(2026, 11, 1, tzinfo=UTC)


def point(seconds, value, key="a", raw=None):
    return {
        "id": seconds, "observed_at": (BASE + timedelta(seconds=seconds)).isoformat(),
        "opportunity_id": key, "symbol": "BTCUSDT",
        "buy_exchange": "hyperliquid" if raw else "binance", "buy_market_type": "future",
        "sell_exchange": "okx", "sell_market_type": "future",
        "open_spread_scaled": round((value + 0.2) * 10_000),
        "fee_adjusted_open_scaled": round(value * 10_000),
        "market_identity_json": json.dumps({"buy_market": {
            "raw_symbol": raw, "dex": "xyz",
        }}) if raw else None,
    }


def params(**kwargs):
    return SpreadEventsParams(end_at=BASE + timedelta(hours=24), **kwargs)


def test_below_threshold_peak_mean_and_inclusive_start():
    events, count = aggregate_spread_events(
        [point(180, 0.1), point(60, 0.9), point(0, 0.3), point(120, 0.6)], params(),
    )
    event, = events
    assert count == 1
    assert event.start_at == BASE
    assert event.end_at == BASE + timedelta(seconds=180)
    assert event.duration_s == 180
    assert event.end_reason == "below"
    assert event.samples == 3  # The terminating below-threshold point is not a sample.
    assert event.peak == 0.9
    assert event.peak_at == BASE + timedelta(seconds=60)
    assert event.mean == pytest.approx(0.6)


def test_gap_closes_at_last_sample_and_restarts_as_ongoing():
    rows = [point(t, 0.5) for t in [0, 60, 120, 600, 660]]
    events, _ = aggregate_spread_events(rows, params(sort="start"))
    assert [e.end_reason for e in events] == ["ongoing", "gap"]
    assert [e.duration_s for e in events] == [60, 120]
    assert events[1].end_at == BASE + timedelta(seconds=120)


def test_gap_floor_is_120_and_comparison_is_strict():
    rows = [point(t, 0.5) for t in [0, 10, 20, 140, 261, 271, 281]]
    events, _ = aggregate_spread_events(rows, params(sort="start"))
    assert [e.duration_s for e in events] == [20, 140]
    assert events[1].end_reason == "gap"


def test_median_is_per_series_and_gap_precedes_below():
    rows = [point(t, v) for t, v in [(0, 0.5), (60, 0.5), (120, 0.5), (600, 0.1)]]
    rows += [point(t, 0.7, key="b") for t in [0, 300, 600, 900]]
    events, count = aggregate_spread_events(rows, params())
    assert count == 2
    assert [(e.series_key, e.end_reason, e.duration_s) for e in events] == [
        ("b", "ongoing", 900), ("a", "gap", 120),
    ]


@pytest.mark.parametrize("sort,expected", [("peak", "a"), ("duration", "b"), ("start", "c")])
def test_sort_and_limit(sort, expected):
    rows = [point(0, 1, "a"), point(60, 1, "a"),
            point(0, 0.5, "b"), point(120, 0.5, "b"), point(180, 0.5, "c")]
    events, count = aggregate_spread_events(rows, params(sort=sort, limit=1))
    assert count == 3
    assert events[0].series_key == expected


def test_minimum_duration_single_sample_raw_metric_and_exact_identity():
    rows = [point(0, 0.2, raw="xyz:BTC"), point(60, 0.2, raw="xyz:BTC")]
    assert aggregate_spread_events(rows, params())[0] == []
    event, = aggregate_spread_events(rows, params(metric="raw", min_duration_s=60))[0]
    assert event.buy_leg.raw_symbol == "xyz:BTC"
    assert event.buy_leg.dex == "xyz"
    assert event.peak == 0.4
    assert aggregate_spread_events(rows, params(metric="raw", min_duration_s=61))[0] == []
    single, = aggregate_spread_events([point(0, 0.5)], params())[0]
    assert single.duration_s == 0
    assert single.end_reason == "ongoing"


@pytest.fixture
async def db():
    db = await connect_database(":memory:")
    await initialize_schema(db)
    # Use real schema/scaled values, including points outside both query bounds.
    for t in [-60, 0, 60, 120, 180]:
        row = point(t, 0.5)
        await db.execute(
            """INSERT INTO opportunity_history (
                observed_at, opportunity_id, type, symbol,
                buy_exchange, buy_market_type, sell_exchange, sell_market_type,
                open_spread_scaled, close_spread_scaled, fee_adjusted_open_scaled, spread_width_scaled
            ) VALUES (?, ?, 'FF', ?, ?, ?, ?, ?, ?, 0, ?, 0)""",
            [row[k] for k in ["observed_at", "opportunity_id", "symbol", "buy_exchange",
                              "buy_market_type", "sell_exchange", "sell_market_type",
                              "open_spread_scaled", "fee_adjusted_open_scaled"]],
        )
    await db.commit()
    yield db
    await db.close()


async def test_sql_both_bounds_symbol_and_truncation(db, monkeypatch):
    window = SpreadEventsParams(start_at=BASE, end_at=BASE + timedelta(seconds=120))
    result = await query_spread_events(db, window)
    assert result.scanned_rows == 3
    assert not result.truncated
    assert result.events[0].duration_s == 120
    monkeypatch.setattr("app.services.spread_events.MAX_SCANNED_ROWS", 2)
    result = await query_spread_events(db, window)
    assert result.scanned_rows == 2
    assert result.truncated
    window.end_at = BASE + timedelta(seconds=60)
    assert not (await query_spread_events(db, window)).truncated
    window.symbol = "ETHUSDT"
    result = await query_spread_events(db, window)
    assert result.scanned_rows == result.series_count == 0
    assert result.events == []


@pytest.fixture
async def client(db):
    app = create_app(settings=Settings(dashboard_password="test-password"), start_collector=False)
    app.state.history_repo = SimpleNamespace(db=db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_route_uses_existing_auth_and_defaults(client):
    assert (await client.get("/api/spread-events")).status_code == 401
    assert (await client.get("/api/spread-events", headers={"X-Dashboard-Password": "bad"})).status_code == 401
    response = await client.get("/api/spread-events", headers={"X-Dashboard-Password": "test-password"})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"events", "scanned_rows", "series_count", "truncated", "params"}
    query = SpreadEventsParams.model_validate(data["params"])
    assert query.end_at - query.start_at == timedelta(hours=24)
    assert (query.threshold, query.metric, query.limit) == (0.3, "fee_adjusted", 100)


@pytest.mark.parametrize("query", [
    {"start_at": "2026-10-24T00:00:00Z", "end_at": "2026-11-01T00:00:00Z"},
    {"start_at": "2026-11-02", "end_at": "2026-11-01"},
    {"start_at": "2026-11-01", "end_at": "2026-11-01"},
    {"start_at": "bad"}, {"threshold": -1}, {"threshold": "nan"},
    {"threshold": "inf"}, {"metric": "bad"}, {"min_duration_s": -1},
    {"min_duration_s": "nan"}, {"min_duration_s": "inf"}, {"sort": "bad"},
    {"limit": 0}, {"limit": 501}, {"limit": 1.5}, {"symbol": " "},
])
async def test_route_invalid_parameters_return_422(client, query):
    response = await client.get("/api/spread-events", params=query,
                                headers={"X-Dashboard-Password": "test-password"})
    assert response.status_code == 422


async def test_route_seven_days_and_timezone_normalization(client):
    response = await client.get("/api/spread-events", params={
        "start_at": "2026-10-25T08:00:00+08:00", "end_at": "2026-11-01T08:00:00+08:00",
        "symbol": " btc-usdt ",
    }, headers={"X-Dashboard-Password": "test-password"})
    assert response.status_code == 200
    assert response.json()["scanned_rows"] == 2
    assert response.json()["params"]["symbol"] == "BTCUSDT"
