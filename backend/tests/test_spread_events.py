import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.spread_events import SpreadEventsParams
from app.services import spread_events
from app.services.spread_events import query_spread_events

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


def aggregate_spread_events(rows, query):
    # Production supplies a single-pass stream in (observed_at, id) order.
    field = "fee_adjusted_open_scaled" if query.metric == "fee_adjusted" else "open_spread_scaled"
    return spread_events.aggregate_spread_events(
        ((r["opportunity_id"], r["observed_at"], r[field])
         for r in sorted(rows, key=lambda r: (r["observed_at"], r["id"]))), query,
    )


def params(**kwargs):
    return SpreadEventsParams(end_at=BASE + timedelta(hours=24), **kwargs)


@pytest.fixture(autouse=True)
def configured_history_cadence(monkeypatch):
    monkeypatch.setattr(spread_events, "get_settings", lambda: Settings(history_sample_seconds=120))


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


def test_gap_floor_is_120_and_comparison_is_strict(monkeypatch):
    monkeypatch.setattr(spread_events, "get_settings", lambda: Settings(history_sample_seconds=10))
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


def test_minimum_duration_single_sample_and_raw_metric():
    rows = [point(0, 0.2, raw="xyz:BTC"), point(60, 0.2, raw="xyz:BTC")]
    assert aggregate_spread_events(rows, params())[0] == []
    event, = aggregate_spread_events(rows, params(metric="raw", min_duration_s=60))[0]
    assert event.peak == 0.4
    assert aggregate_spread_events(rows, params(metric="raw", min_duration_s=61))[0] == []
    single, = aggregate_spread_events([point(0, 0.5)], params())[0]
    assert single.duration_s == 0
    assert single.end_reason == "ongoing"


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "history.db"
    db = await connect_database(str(path))
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
    await db.close()
    yield path


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
    app = create_app(settings=Settings(dashboard_password="test-password",
                                     database_url=f"sqlite:///{db}"), start_collector=False)
    # The endpoint must not touch this shared connection.
    app.state.history_repo = SimpleNamespace(db=object())
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


def test_streaming_gap_bootstrap_uses_default_cadence():
    events, _ = aggregate_spread_events([point(0, 0.5), point(361, 0.5)], params())
    assert {e.end_reason for e in events} == {"gap", "ongoing"}
    assert all(e.samples == 1 for e in events)


def test_interval_estimate_freezes_after_first_31_positive_intervals():
    early = [point(t, 0.5) for t in range(0, 32 * 300, 300)]
    later = [point(9300 + i * 100, 0.5) for i in range(1, 41)]
    events, _ = aggregate_spread_events(
        early + later + [point(13800, 0.5), point(14701, 0.5)], params(),
    )
    assert len(events) == 2
    assert sum(e.samples for e in events) == 74
    assert next(e for e in events if e.end_reason == "gap").duration_s == 13800


def test_unstable_interval_estimate_does_not_split_continuous_event():
    times = [0, 60, *range(360, 9061, 300)]
    event, = aggregate_spread_events([point(t, 0.5) for t in times], params())[0]
    assert event.end_reason == "ongoing"
    assert event.duration_s == 9060
    assert event.samples == len(times)


@pytest.mark.parametrize("last_interval,expected_reasons", [
    (900, ["ongoing"]), (901, ["ongoing", "gap"]),
])
def test_stable_median_extends_gap_limit_but_real_gap_still_closes(last_interval, expected_reasons):
    times = [0, 60, *range(360, 9061, 300), 9060 + last_interval]
    events, _ = aggregate_spread_events([point(t, 0.5) for t in times], params(sort="start"))
    assert [e.end_reason for e in events] == expected_reasons
    assert sum(e.samples for e in events) == len(times)
    if last_interval > 900:
        assert events[1].end_at == BASE + timedelta(seconds=9060)


@pytest.mark.parametrize("warmup_samples", [1, 32])
def test_configured_recorder_cadence_is_always_a_floor(monkeypatch, warmup_samples):
    from app.services.history import OpportunityHistoryRecorder

    settings = Settings(history_sample_seconds=240)
    recorder = OpportunityHistoryRecorder(None, settings.history_settings)
    monkeypatch.setattr(spread_events, "get_settings", lambda: settings)
    boundary = warmup_samples - 1 + 3 * recorder.settings.sample_seconds
    times = [*range(warmup_samples), boundary, boundary + 3 * recorder.settings.sample_seconds + 1]
    events, _ = aggregate_spread_events([point(t, 0.5) for t in times], params(sort="start"))
    assert [e.end_reason for e in events] == ["ongoing", "gap"]
    assert events[1].end_at == BASE + timedelta(seconds=boundary)
    assert events[1].samples == warmup_samples + 1


def test_unavailable_recorder_configuration_falls_back_to_120_seconds(monkeypatch):
    monkeypatch.setattr(spread_events, "get_settings", lambda: SimpleNamespace())
    events, _ = aggregate_spread_events(
        [point(0, 0.5), point(360, 0.5), point(721, 0.5)], params(sort="start"),
    )
    assert [e.end_reason for e in events] == ["ongoing", "gap"]
    assert events[1].samples == 2


@pytest.mark.parametrize("batch_size", [1, 2])
@pytest.mark.parametrize("row_limit", [1, 2, 3, 500_000])
async def test_sql_equal_timestamps_follow_insertion_order(db, monkeypatch, batch_size, row_limit):
    samples = [point(0, 0.5), point(60, 0.5), point(60, 0.1)]
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM opportunity_history")
        connection.executemany(
            """INSERT INTO opportunity_history (
                observed_at, opportunity_id, type, symbol,
                buy_exchange, buy_market_type, sell_exchange, sell_market_type,
                open_spread_scaled, close_spread_scaled, fee_adjusted_open_scaled, spread_width_scaled
            ) VALUES (?, ?, 'FF', ?, ?, ?, ?, ?, ?, 0, ?, 0)""",
            [[row[k] for k in ["observed_at", "opportunity_id", "symbol", "buy_exchange",
                               "buy_market_type", "sell_exchange", "sell_market_type",
                               "open_spread_scaled", "fee_adjusted_open_scaled"]] for row in samples],
        )
    monkeypatch.setattr(spread_events, "FETCH_BATCH_SIZE", batch_size)
    monkeypatch.setattr(spread_events, "MAX_SCANNED_ROWS", row_limit)
    result = await query_spread_events(db, params(threshold=0.3))
    event, = result.events
    assert event.end_reason == ("below" if row_limit >= 3 else "ongoing")
    assert event.samples == min(row_limit, 2)
    assert event.duration_s == (0 if row_limit == 1 else 60)
    assert result.scanned_rows == min(row_limit, 3)
    assert result.truncated == (row_limit < 3)


@pytest.mark.parametrize("row_limit", [3, 500_000])
@pytest.mark.parametrize("symbol", [None, "BTCUSDT"])
async def test_worker_uses_readonly_file_batches_index_and_selected_metadata(db, monkeypatch,
                                                                          row_limit, symbol):
    real_connect = sqlite3.connect
    calls, batches, threads, closed = [], [], [], []

    class Cursor(sqlite3.Cursor):
        def fetchall(self):
            raise AssertionError("stream must never fetchall")

        def fetchmany(self, size=1):
            batches.append(size)
            return super().fetchmany(size)

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            calls.append((sql, parameters))
            return self.cursor(factory=Cursor).execute(sql, parameters)

        def close(self):
            closed.append(threading.get_ident())
            super().close()

    def connect(db_uri, **kwargs):
        assert db_uri.endswith("?mode=ro") and kwargs["uri"] is True
        threads.append(threading.get_ident())
        connection = real_connect(db_uri, factory=Connection, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM opportunity_history")
        connection.rollback()
        return connection

    monkeypatch.setattr(spread_events.sqlite3, "connect", connect)
    monkeypatch.setattr(spread_events, "FETCH_BATCH_SIZE", 2)
    monkeypatch.setattr(spread_events, "MAX_SCANNED_ROWS", row_limit)
    result = await query_spread_events(db, params(symbol=symbol))
    assert result.scanned_rows == min(row_limit, 4)
    assert len(batches) >= 3 and set(batches) == {2}
    assert threads == closed and threads[0] != threading.get_ident()
    scans = [(sql, values) for sql, values in calls if sql.startswith("SELECT opportunity_id")]
    assert len(scans) == (2 if row_limit == 3 else 1)
    assert all("market_identity_json" not in sql for sql, _ in scans)
    assert "ORDER BY observed_at ASC, id DESC" in scans[0][0]
    assert sum(sql.startswith("SELECT symbol,") for sql, _ in calls) == 1
    with real_connect(db) as check:
        for sql, values in scans:
            plan = list(check.execute("EXPLAIN QUERY PLAN " + sql, values))
            assert any("idx_opportunity_history_" in row[3] and "observed_at" in row[3]
                       for row in plan)
            assert not any("TEMP B-TREE" in row[3] for row in plan)


@pytest.mark.parametrize("revision,unit_status,expected", [
    (7, "verified", 1000), (8, "verified", None), (7, "pending", None),
])
async def test_multipliers_require_exact_historical_binding_revision(db, revision, unit_status, expected):
    from app.models.market_identity import MarketKey

    market = {"exchange": "hyperliquid", "market_type": "future", "raw_symbol": "xyz:BTC", "dex": "xyz"}
    identity = {"buy_market": market, "buy_revision": 7}
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE opportunity_history SET buy_exchange='hyperliquid', market_identity_json=?",
                           (json.dumps(identity),))
        connection.execute("INSERT INTO market_identity_bindings (market_key, revision, identity_status, payload) VALUES (?, ?, 'verified', ?)", (
            MarketKey.model_validate(market).key, revision,
            json.dumps({"unit_status": unit_status, "price_multiplier": 1000, "contract_size": 0.01}),
        ))
        connection.execute("UPDATE opportunity_history SET market_identity_json='{}' WHERE observed_at > ?",
                           ((BASE + timedelta(seconds=120)).isoformat(),))
    result = await query_spread_events(db, SpreadEventsParams(start_at=BASE, end_at=BASE + timedelta(seconds=120)))
    leg = result.events[0].buy_leg
    assert leg.raw_symbol == "xyz:BTC" and leg.dex == "xyz"
    assert leg.price_multiplier == expected
    assert leg.contract_size_multiplier == (0.01 if expected else None)
    assert result.events[0].sell_leg.price_multiplier is None


async def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        await query_spread_events(path, params())
    assert not path.exists()


async def test_route_queue_timeout_is_429_with_chinese_detail(client, monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(spread_events, "_QUERY_SEMAPHORE", semaphore)
    monkeypatch.setattr(spread_events, "QUERY_WAIT_SECONDS", 0.01)
    await semaphore.acquire()
    try:
        response = await client.get("/api/spread-events", headers={"X-Dashboard-Password": "test-password"})
        assert response.status_code == 429
        assert "5" in response.json()["detail"]
        assert any(ord(c) > 127 for c in response.json()["detail"])
    finally:
        semaphore.release()
    assert (await client.get("/api/spread-events", headers={"X-Dashboard-Password": "test-password"})).status_code == 200


async def test_cancellation_keeps_global_permit_until_worker_exits(db, monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(spread_events, "_QUERY_SEMAPHORE", semaphore)
    monkeypatch.setattr(spread_events, "QUERY_WAIT_SECONDS", 0.01)
    started, release = threading.Event(), threading.Event()
    real_query = spread_events._query_spread_events

    def blocked(*args):
        started.set()
        assert release.wait(5)
        return real_query(*args)

    monkeypatch.setattr(spread_events, "_query_spread_events", blocked)
    first = asyncio.create_task(query_spread_events(db, params()))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert semaphore.locked()
        with pytest.raises(spread_events.SpreadEventsBusyError):
            await query_spread_events(db, params())
    finally:
        release.set()
        await asyncio.wait_for(semaphore.acquire(), 3)
        semaphore.release()
    assert (await query_spread_events(db, params())).scanned_rows == 4
