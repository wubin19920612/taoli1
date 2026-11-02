"""Retirement HTTP and legacy database compatibility regressions."""
import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app import main
from app.core.config import RETIRED_FEATURES, Settings
from app.services.funding_formula_families import FORMULA_FAMILIES, exchange_formula_family
from test_feature_flags import startup  # noqa: F401 - shared offline lifespan fixture


# Method/path pairs frozen from the pre-removal routes.
RETIRED_ENDPOINTS = (
    ('GET', '/api/fs-monitor/report'),
    ('GET', '/api/fs-monitor/settings'),
    ('PUT', '/api/fs-monitor/settings'),
    ('POST', '/api/fs-monitor/refresh'),
    ('GET', '/api/fs-monitor/history'),
    ('GET', '/api/fs-monitor/events'),
    ('GET', '/api/funding-research/candidates'),
    ('GET', '/api/funding-research/candidate-snapshots'),
    ('GET', '/api/funding-research/paper-trades'),
    ('GET', '/api/funding-research/paper-trades/summary'),
    ('POST', '/api/funding-research/paper-trades/open/test'),
    ('POST', '/api/funding-research/paper-trades/test/close'),
    ('POST', '/api/funding-research/run'),
    ('GET', '/api/funding-research/legacy-backtest'),
    ('GET', '/api/negative-basis-monitor/exchanges'),
    ('GET', '/api/negative-basis-monitor/watchlist'),
    ('POST', '/api/negative-basis-monitor/watchlist'),
    ('DELETE', '/api/negative-basis-monitor/watchlist/test'),
    ('POST', '/api/negative-basis-monitor/watchlist/test/collect'),
    ('GET', '/api/negative-basis-monitor/status'),
    ('POST', '/api/negative-basis-monitor/auto-scan'),
    ('GET', '/api/negative-basis-monitor/auto-scan/settings'),
    ('PUT', '/api/negative-basis-monitor/auto-scan/settings'),
    ('POST', '/api/negative-basis-monitor/auto-scan/block-symbol'),
    ('DELETE', '/api/negative-basis-monitor/auto-scan/block-symbol'),
    ('POST', '/api/negative-basis-monitor/auto-scan/block-exchange'),
    ('DELETE', '/api/negative-basis-monitor/auto-scan/block-exchange'),
    ('POST', '/api/negative-basis-monitor/auto-scan/block-exchange-symbol'),
    ('DELETE', '/api/negative-basis-monitor/auto-scan/block-exchange-symbol'),
    ('GET', '/api/negative-basis-monitor/samples'),
    ('GET', '/api/negative-basis-monitor/events'),
    ('GET', '/api/negative-basis-monitor/query'),
    ('GET', '/api/oil-news'),
    ('POST', '/api/oil-news/refresh'),
    ('GET', '/api/opportunity-radar/settings'),
    ('PUT', '/api/opportunity-radar/settings'),
    ('POST', '/api/opportunity-radar/test-notification'),
    ('GET', '/api/opportunity-radar/preview'),
    ('GET', '/api/second-level-sampling/exchanges'),
    ('GET', '/api/second-level-sampling/config'),
    ('PUT', '/api/second-level-sampling/config'),
    ('POST', '/api/second-level-sampling/start'),
    ('POST', '/api/second-level-sampling/stop'),
    ('GET', '/api/second-level-sampling/status'),
    ('GET', '/api/second-level-sampling/samples'),
    ('GET', '/api/second-level-sampling/component-samples'),
    ('POST', '/api/second-level-sampling/fat-finger-backtest'),
    ('GET', '/api/spread-events'),
    ('GET', '/api/squeeze-arbitrage/status'),
    ('GET', '/api/squeeze-arbitrage/watchlist'),
    ('GET', '/api/squeeze-arbitrage/events'),
    ('GET', '/api/squeeze-arbitrage/routes'),
    ('GET', '/api/squeeze-arbitrage/route-events'),
    ('GET', '/api/squeeze-arbitrage/paper/status'),
    ('GET', '/api/squeeze-arbitrage/paper/positions'),
    ('GET', '/api/squeeze-arbitrage/paper/trades'),
    ('GET', '/api/squeeze-arbitrage/paper/report'),
    ('GET', '/api/tradfi-perp-monitor/preview'),
    ('POST', '/api/tradfi-perp-monitor/refresh'),
    ('GET', '/api/settings/live-pilot'),
    ('GET', '/api/settings/live-pilot/preview'),
    ('PUT', '/api/settings/live-pilot'),
    ('GET', '/api/settings/oil-news'),
    ('PUT', '/api/settings/oil-news'),
)


async def test_retired_endpoints_are_absent():
    app = main.create_app(settings=Settings(database_url="sqlite:///:memory:", disabled_features=RETIRED_FEATURES),
                          start_background_workers=False)
    paths = app.openapi()["paths"]
    assert len(RETIRED_ENDPOINTS) == 64
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            for method, path in RETIRED_ENDPOINTS:
                assert path not in paths
                response = await client.request(method, path, json={})
                assert response.status_code == 404, (method, path, response.text)
                assert response.json() == {"detail": "Not Found"}


def test_shared_funding_formula_families_and_unknown_fallback():
    expected = {
        "binance": "with_rate_clamp", "bitget": "with_rate_clamp",
        "okx": "with_rate_variable_interval", "bybit": "bybit_premium_interest_clamp",
        "gate": "gate_indicative", "hyperliquid": "hyperliquid_hourly", "htx": "htx", "aster": "aster",
    }
    assert FORMULA_FAMILIES == expected
    for exchange, family in expected.items():
        assert exchange_formula_family(f" {exchange.upper()} ") == family
    assert exchange_formula_family("not-a-venue") == "unknown"
    assert exchange_formula_family("") == "unknown"


def legacy_snapshot(path):
    with sqlite3.connect(path) as db:
        schema = dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        rows = {name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in schema}
    return schema, rows


def seed_legacy_database(path):
    ddl = (Path(__file__).parent / "fixtures/retired_legacy_schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(path) as db:
        db.executescript(ddl)
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if table == "app_settings":
                for key in ("live_pilot", "oil_news", "opportunity_radar", "second_level_sampling", "negative_basis_auto_scan_settings"):
                    db.execute("INSERT INTO app_settings(key,payload) VALUES (?,?)", (key, '{"enabled":true,"legacy":true}'))
                continue
            # A sentinel in every old table detects accidental cleanup, not just table loss.
            columns = [row for row in db.execute(f'PRAGMA table_info("{table}")') if row[3] or row[5]]
            values = []
            for _, name, kind, *_ in columns:
                value = 1 if kind in {"INTEGER", "REAL"} else "legacy"
                if name.endswith("_at") or name in {"event_time", "bucket_at"}:
                    value = "2020-01-01T00:00:00+00:00"
                if "json" in name or name == "payload":
                    value = '{"enabled":true,"legacy":true}'
                if table == "astro_card_attempts":
                    value = {"source": "live_pilot", "state": "unknown"}.get(name, value)
                values.append(value)
            names = ",".join(f'"{row[1]}"' for row in columns)
            placeholders = ",".join("?" for _ in columns)
            db.execute(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', values)


@pytest.mark.parametrize("existing", [False, True])
async def test_new_and_legacy_copy_lifespan_restarts_preserve_data(existing, startup, monkeypatch, tmp_path):
    started, _ = startup
    db_path = tmp_path / "radar.db"
    route_path = tmp_path / "radar-squeeze-route.db"
    before = None
    if existing:
        original = tmp_path / "legacy-original.db"
        seed_legacy_database(original)
        with sqlite3.connect(original) as source, sqlite3.connect(db_path) as target:
            source.backup(target)
        before = legacy_snapshot(db_path)
        # A distinct old route DB must never be connected, copied, or cleaned at startup.
        with sqlite3.connect(route_path) as db:
            db.execute("CREATE TABLE squeeze_route_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO squeeze_route_meta VALUES ('route-only', 'untouched')")
        route_hash = hashlib.sha256(route_path.read_bytes()).hexdigest()
    stop_events = []

    async def capture_alert_loop(app, interval, stop):
        assert not stop.is_set()
        stop_events.append(stop)

    monkeypatch.setattr(main, "_run_alert_loop", capture_alert_loop)
    app = main.create_app(settings=Settings(database_url=f"sqlite:///{db_path}", disabled_features=RETIRED_FEATURES),
                          start_collector=True)
    supervisors = []
    for _ in range(2):
        closes = []
        async with app.router.lifespan_context(app):
            supervisors.append(app.state.task_supervisor)
            assert app.state.market_collector.history_recorder.repository is app.state.history_repo
            assert app.state.astro_preadd_service is not None
            assert app.state.account_connection_service is not None
            assert app.state.account_position_service is not None
            assert {"market-collector", "history-recorder", "alert-loop", "astro-preadd-loop"} <= started.keys()
            await started["alert-loop"]()
            for resource, method in ((app.state.market_collector, "close"),
                                     (app.state.account_connection_service, "aclose"),
                                     (app.state.db, "close"),
                                     (app.state.astro_alert_service.attempts.db, "close")):
                close = AsyncMock(wraps=getattr(resource, method))
                monkeypatch.setattr(resource, method, close)
                closes.append(close)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.get("/api/health")).status_code == 200
                if existing:
                    response = await client.get("/api/astro/card-attempts")
                    assert response.status_code == 200
                    assert len(response.json()) == 1
                    assert response.json()[0]["source"] == "live_pilot"
                    assert response.json()[0]["state"] == "unknown"
        for close in closes:
            close.assert_awaited_once()
        assert stop_events[-1].is_set()
        with pytest.raises(ValueError, match="no active connection"):
            await app.state.db.execute("SELECT 1")
        if before:
            schema, rows = legacy_snapshot(db_path)
            assert {name: schema[name] for name in before[0]} == before[0]
            assert {name: rows[name] for name in before[1]} == before[1]
            assert hashlib.sha256(route_path.read_bytes()).hexdigest() == route_hash
        else:
            assert not route_path.exists()
    assert supervisors[0] is not supervisors[1]
    assert stop_events[0] is not stop_events[1]


async def test_old_squeeze_columns_are_added_without_changing_rows(tmp_path):
    from app.db.database import connect_database
    from app.db.retired_squeeze_schema import initialize_squeeze_schema

    db = await connect_database(str(tmp_path / "older.db"))
    try:
        await db.executescript('''
            CREATE TABLE squeeze_positioning_samples (
                market_key TEXT NOT NULL, event_time TEXT NOT NULL, raw_open_interest REAL NOT NULL,
                raw_unit TEXT NOT NULL, open_interest_usdt REAL, account_ratio REAL,
                received_at TEXT NOT NULL, available_at TEXT NOT NULL, source TEXT NOT NULL,
                sampling_period_seconds INTEGER NOT NULL,
                PRIMARY KEY(market_key,source,event_time,sampling_period_seconds)
            );
            INSERT INTO squeeze_positioning_samples VALUES ('xyz:BTC','old',1,'contracts',2,3,'old','old','old',60);
            CREATE TABLE squeeze_route_worker_state (
                id INTEGER PRIMARY KEY CHECK(id=1), last_attempt_at TEXT,
                last_success_at TEXT, last_error TEXT, last_route_id TEXT
            );
            INSERT INTO squeeze_route_worker_state VALUES(1,'old','old',NULL,'legacy');
            CREATE TABLE squeeze_route_latest (
                route_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL, evaluation_json TEXT NOT NULL
            );
            INSERT INTO squeeze_route_latest VALUES('legacy','old','{}');
        ''')
        for _ in range(2):
            await initialize_squeeze_schema(db)
        async with db.execute("SELECT * FROM squeeze_positioning_samples") as cursor:
            row = dict(await cursor.fetchone())
        assert row["market_key"] == "xyz:BTC" and row["account_ratio"] == 3
        assert row["account_ratio_event_time"] is None
        async with db.execute("SELECT * FROM squeeze_route_worker_state") as cursor:
            row = dict(await cursor.fetchone())
        assert row["last_route_id"] == "legacy"
        assert row["storage_failure_count"] == 0 and row["dropped_scan_count"] == 0
        async with db.execute("SELECT * FROM squeeze_route_latest") as cursor:
            assert dict(await cursor.fetchone()) == {
                "route_id": "legacy", "evaluated_at": "old", "evaluation_json": "{}", "inputs_json": None,
            }
    finally:
        await db.close()
