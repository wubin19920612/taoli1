from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.market import MarketSnapshot, MarketType
from app.models.premium_index import PremiumIndexPoint
from app.models.settings import FeeSettings, RiskSettings
from app.services.multi_exchange_premium import (
    MultiExchangePremiumMonitor,
    build_premium_alert_message,
    eligible_groups,
    matching_points,
)
from app.services.premium_index_query import PremiumIndexQueryService
from app.services.snapshot_store import SnapshotStore


def market(exchange="binance", **updates):
    now = datetime.now(UTC)
    row = MarketSnapshot(
        symbol="BTCUSDT",
        base="BTC",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        raw_symbol="BTCUSDT",
        bid=102,
        ask=104,
        mark_price=103,
        index_price=100,
        timestamp=now,
        asset_id="bitcoin",
        identity_status="verified",
        unit_status="verified",
        binding_valid_until=now + timedelta(hours=1),
    )
    return row.model_copy(update=updates)


def point(premium=3, age=0, source="binance_premium_index", now=None):
    return PremiumIndexPoint(
        premium_pct=premium,
        bucket_at=(now or datetime.now(UTC)) - timedelta(seconds=age),
        source=source,
    )


@pytest.mark.parametrize(
    "values,expected",
    [
        ([3, -3], True),
        ([3, 3], True),
        ([-3, -3], True),
        ([2, 3], False),
        ([-2, -3], False),
        ([1, 3], False),
        ([float("nan"), 3], False),
        ([float("inf"), 3], False),
    ],
)
def test_strict_absolute_threshold(values, expected):
    now = datetime.now(UTC)
    rows = [
        (market(exchange), point(value, now=now))
        for exchange, value in zip(["binance", "gate"], values)
    ]
    assert bool(matching_points(rows, RiskSettings(), now)) is expected


def test_different_dexes_are_one_exchange_and_raw_market_is_preserved():
    now = datetime.now(UTC)
    rows = [
        (market("hyperliquid", raw_symbol=f"{dex}:BTC", dex=dex), point(now=now))
        for dex in ("xyz", "io")
    ]
    assert not matching_points(rows, RiskSettings(), now)
    rows.append((market("gate"), point(-3, now=now)))
    hits = matching_points(rows, RiskSettings(), now)
    message = build_premium_alert_message(hits, RiskSettings())
    assert "xyz:BTC" in message and "io:BTC" in message and "-3.00%" in message


def summary_rows(now):
    return [
        (
            market(
                "bitget",
                bid=99,
                ask=100,
                ask_size=20,
                timestamp=now,
                funding_rate_pct=-0.01,
                funding_interval_hours=8,
                volume_24h_usdt=12_300_000,
            ),
            point(-2.3, source="mark_index_fallback", now=now),
        ),
        (
            market(
                "gate",
                bid=101,
                ask=102,
                bid_size=10,
                timestamp=now,
                funding_next_rate_pct=-0.02,
                funding_interval_hours=4,
                volume_24h_usdt=450_000,
            ),
            point(2.4, now=now),
        ),
    ]


def test_compact_message_contains_executable_direction_funding_volume_and_estimated_cost():
    now = datetime.now(UTC)
    message = build_premium_alert_message(summary_rows(now), RiskSettings(), now=now)
    assert "多 bitget → 空 gate｜开 +0.995% / 平 +2.985%" in message
    assert "一档买 100 / 卖 101 USDT｜较薄档额 1.01K" in message
    assert "开仓双边费估 0.100%｜扣费差 +0.895%（未计滑点）" in message
    assert "资费-0.0100%/8h｜24h额 12.3M USDT" in message
    assert "预资费-0.0200%/4h｜24h额 450K USDT" in message
    assert "P-2.30%*" in message and "*回退估算" in message
    assert "bitcoin" not in message and "条件：" not in message
    assert len(message.splitlines()) == 7


@pytest.mark.parametrize(
    "updates",
    [
        {"timestamp": datetime(2020, 1, 1, tzinfo=UTC)},
        {"quote_kind": "reference"},
        {"estimated_fields": ["ask"]},
        {"original_quote_currency": "USDC"},
        {"identity_status": "pending"},
        {"symbol_alias_price_multiplier": 1, "unit_status": "pending"},
        {"bid": 105, "ask": 100},
    ],
)
def test_unusable_books_never_get_a_trade_spread(updates):
    now = datetime.now(UTC)
    rows = summary_rows(now)
    rows[0] = (rows[0][0].model_copy(update=updates), rows[0][1])
    message = build_premium_alert_message(rows, RiskSettings(), now=now)
    assert "价差暂缺" in message
    assert "扣费差" not in message and "一档买" not in message


def test_missing_metrics_are_not_zero_and_configured_fees_and_multipliers_are_visible():
    now = datetime.now(UTC)
    rows = summary_rows(now)
    rows[0] = (
        rows[0][0].model_copy(
            update={
                "funding_rate_pct": None,
                "funding_interval_hours": None,
                "volume_24h_usdt": None,
                "symbol_alias_price_multiplier": 1000,
                "contract_size_multiplier": 10,
            }
        ),
        rows[0][1],
    )
    message = build_premium_alert_message(
        rows, RiskSettings(), now=now, fee_settings=FeeSettings(future_fee_pct=0.08)
    )
    assert "资费—/周期未知｜24h额 — USDT" in message
    assert "开仓双边费估 0.160%" in message
    assert "bitget价×1000" in message and "bitget合约面值×10" in message


def test_best_direction_can_be_negative_and_multiple_markets_are_bounded():
    now = datetime.now(UTC)
    rows = [
        (market(exchange, bid=99, ask=100, timestamp=now), point(3, now=now))
        for exchange in ("aster", "binance", "bitget", "bybit", "gate", "okx")
    ]
    message = build_premium_alert_message(rows, RiskSettings(), now=now)
    assert "开 -1.005%" in message
    assert "另 2 个市场命中" in message
    assert "回退估算" not in message


@pytest.mark.parametrize(
    "age,source", [(91, "official"), (31, "mark_index_fallback"), (-1, "official")]
)
def test_stale_or_future_data_is_excluded(age, source):
    now = datetime.now(UTC)
    assert not matching_points(
        [
            (market(), point(age=age, source=source, now=now)),
            (market("gate"), point(now=now)),
        ],
        RiskSettings(),
        now,
    )


def test_sample_skew_and_recent_window():
    now = datetime.now(UTC)
    rows = [(market(), point(age=80, now=now)), (market("gate"), point(now=now))]
    assert not matching_points(rows, RiskSettings(), now)
    rows.append((market("aster"), point(age=5, now=now)))
    assert len(matching_points(rows, RiskSettings(), now)) == 2


@pytest.mark.parametrize(
    "updates",
    [
        {"asset_id": "other-bitcoin"},
        {"identity_status": "pending"},
        {"unit_status": "pending"},
        {"binding_valid_until": datetime(2020, 1, 1, tzinfo=UTC)},
        {"market_type": MarketType.SPOT},
        {"exchange": "hyperliquid", "dex": "xyz", "raw_symbol": "io:BTC"},
    ],
)
def test_identity_and_contract_boundaries(updates):
    assert not eligible_groups(
        [market(), market(**{"exchange": "gate", **updates})], RiskSettings(), datetime.now(UTC)
    )


def test_blacklists_and_duplicate_markets():
    rows = [market(), market("gate")]
    now = datetime.now(UTC)
    assert not eligible_groups(rows, RiskSettings(ignored_exchanges=["gate"]), now)
    assert not eligible_groups(rows, RiskSettings(excluded_symbols=["BTCUSDT"]), now)
    assert not eligible_groups([rows[0], rows[0]], RiskSettings(), now)
    assert len(eligible_groups(rows + [rows[0]], RiskSettings(), now)) == 1


def test_risk_settings_persist_and_validate(tmp_path):
    url = "sqlite:///" + (tmp_path / "premium.db").as_posix()
    config = Settings(database_url=url, dashboard_password="test-secret")
    with TestClient(create_app(settings=config)) as client:
        baseline = client.get("/api/settings/risk").json()
        assert baseline["multi_exchange_premium_threshold_pct"] == 2
        payload = {**baseline, "multi_exchange_premium_threshold_pct": 3.5}
        assert client.put("/api/settings/risk", json=payload).status_code == 401
        # Match the existing dashboard authentication header.
        response = client.put(
            "/api/settings/risk", json=payload, headers={"X-Dashboard-Password": "test-secret"}
        )
        assert response.status_code == 200
        for invalid in (0, -1):
            assert (
                client.put(
                    "/api/settings/risk",
                    json={**payload, "multi_exchange_premium_threshold_pct": invalid},
                    headers={"X-Dashboard-Password": "test-secret"},
                ).status_code
                == 422
            )
    with TestClient(create_app(settings=config)) as client:
        assert (
            client.get("/api/settings/risk").json()["multi_exchange_premium_threshold_pct"] == 3.5
        )


async def test_official_source_precedes_mark_fallback_and_preserves_timestamp():
    now = datetime.now(UTC)
    service = PremiumIndexQueryService()
    official = point(-5, age=30, now=now)
    service._fetch_history = AsyncMock(return_value=[official])
    assert await service.monitor_point(market(mark_price=100), now=now) == official
    assert service._fetch_history.call_args.args[1] == "BTCUSDT"
    service._fetch_history.return_value = [point(-5, age=200, now=now)]
    fallback = await service.monitor_point(market(), now=now)
    assert fallback.source == "mark_index_fallback"
    assert fallback.premium_pct == pytest.approx(3)
    await service.aclose()


def make_monitor():
    app = FastAPI()
    settings = RiskSettings()
    app.state.settings_repo = SimpleNamespace(get_risk_settings=AsyncMock(return_value=settings))
    app.state.snapshot_store = SnapshotStore()
    app.state.snapshot_store.set_markets([market(), market("gate")])
    app.state.feishu_notifier = SimpleNamespace(send_text=AsyncMock())
    app.state.alert_event_repo = SimpleNamespace(create=AsyncMock())
    service = SimpleNamespace(
        monitor_point=AsyncMock(side_effect=lambda market, now: point(now=now)), aclose=AsyncMock()
    )
    return MultiExchangePremiumMonitor(app, service), app, settings


async def test_delivery_cooldown_and_changed_threshold_apply_without_restart():
    monitor, app, settings = make_monitor()
    await monitor.run_once()
    await monitor.run_once()
    assert app.state.feishu_notifier.send_text.await_count == 1
    assert app.state.alert_event_repo.create.call_args.args[0].status == "sent"
    monitor._next_allowed.clear()
    settings.multi_exchange_premium_threshold_pct = 4
    await monitor.run_once()
    assert app.state.feishu_notifier.send_text.await_count == 1
    settings.multi_exchange_premium_threshold_pct = 2
    await monitor.run_once()
    assert app.state.feishu_notifier.send_text.await_count == 2


async def test_failed_delivery_retries_after_backoff():
    monitor, app, _ = make_monitor()
    app.state.feishu_notifier.send_text.side_effect = RuntimeError("offline")
    await monitor.run_once()
    assert app.state.alert_event_repo.create.call_args.args[0].status == "failed"
    await monitor.run_once()
    assert app.state.feishu_notifier.send_text.await_count == 1
    monitor._next_allowed.clear()
    app.state.feishu_notifier.send_text.side_effect = None
    await monitor.run_once()
    assert app.state.alert_event_repo.create.call_args.args[0].status == "sent"


async def test_query_failure_and_setting_changes_during_query_do_not_alert():
    monitor, app, settings = make_monitor()
    monitor.service.monitor_point.side_effect = RuntimeError("rate limited")
    await monitor.run_once()
    assert monitor.status["last_error_count"] == 2
    app.state.feishu_notifier.send_text.assert_not_awaited()
    monitor.service.monitor_point.side_effect = lambda market, now: point(now=now)
    app.state.settings_repo.get_risk_settings.side_effect = [
        settings,
        settings.model_copy(update={"multi_exchange_premium_alert_enabled": False}),
    ]
    await monitor.run_once()
    app.state.feishu_notifier.send_text.assert_not_awaited()


async def test_rotation_covers_more_than_one_batch(monkeypatch):
    monkeypatch.setattr("app.services.multi_exchange_premium.BATCH_SIZE", 1)
    monitor, app, _ = make_monitor()
    app.state.snapshot_store.set_markets(
        [
            market(exchange, asset_id=asset, raw_symbol=f"{asset}USDT", symbol=f"{asset}USDT")
            for asset in ("AAA", "BBB", "CCC")
            for exchange in ("binance", "gate")
        ]
    )
    for _ in range(3):
        await monitor.run_once()
    assert app.state.feishu_notifier.send_text.await_count == 3
    assert monitor.status["eligible_symbols"] == 3
