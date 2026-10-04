from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.models.astro_preadd import AstroPreaddRunRequest, AstroPreaddSettings
from app.models.market import MarketSnapshot, MarketType
from app.models.settings import AstroCardSettings, RiskSettings
from app.services.astro_alerts import AstroAlertService
from app.services.astro_preadd import AstroPreaddService, find_preadd_candidates
from app.services.snapshot_store import SnapshotStore


def market(
    exchange: str,
    *,
    symbol: str = "ETHUSDT",
    funding: float | None = None,
    predicted: float | None = None,
    interval: int = 8,
    mark: float = 100,
    index: float = 100,
    volume: float | None = 1_000_000,
    timestamp: datetime | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol, base=symbol.removesuffix("USDT"), exchange=exchange,
        market_type=MarketType.FUTURE, bid=99, ask=101,
        funding_rate_pct=funding, funding_next_rate_pct=predicted,
        funding_interval_hours=interval, mark_price=mark, index_price=index,
        volume_24h_usdt=volume,
        raw_symbol=symbol, timestamp=timestamp or datetime.now(UTC),
    )


class FakeRepo:
    def __init__(
        self,
        settings: AstroPreaddSettings | None = None,
        risk: RiskSettings | None = None,
        card_settings: AstroCardSettings | None = None,
    ):
        self.settings = settings or AstroPreaddSettings()
        self.risk = risk or RiskSettings()
        self.card_settings = card_settings or AstroCardSettings(open_enabled=True)

    async def get_astro_preadd_settings(self):
        return self.settings

    async def get_risk_settings(self):
        return self.risk

    async def get_astro_card_settings(self):
        return self.card_settings


class FakeClient:
    def __init__(self):
        self.pairs: list[dict] = []
        self.added: list[dict] = []
        self.list_calls = 0

    async def list_pairs(self):
        self.list_calls += 1
        return self.pairs

    async def add_pair(self, pair):
        self.added.append(pair)
        self.pairs.append(pair)
        return {"code": 0}

    async def update_pair(self, pair):
        raise AssertionError("预建不能更新已有卡片")


def test_preadd_settings_validate_supported_exchanges_and_positive_thresholds():
    assert AstroPreaddSettings().exchanges == ["bitget", "binance"]
    assert AstroPreaddSettings().min_volume_24h_usdt == 0
    assert AstroPreaddSettings(exchanges=["Bitget", "binance", "bitget"]).exchanges == ["bitget", "binance"]
    with pytest.raises(ValidationError):
        AstroPreaddSettings(exchanges=["bitget", "aster"])
    with pytest.raises(ValidationError):
        AstroPreaddSettings(funding_threshold_pct=0)
    with pytest.raises(ValidationError):
        AstroPreaddSettings(min_volume_24h_usdt=-1)
    with pytest.raises(ValidationError):
        AstroPreaddRunRequest(card_variant="invalid")


def test_preadd_funding_uses_prediction_then_current_with_explicit_cycle():
    rows = [market("bitget", funding=-0.1, predicted=0.8, interval=4), market("binance", funding=0.01)]
    [item] = find_preadd_candidates(rows, AstroPreaddSettings()).items
    assert item.signal_type == "funding"
    assert item.funding_source == "predicted"
    assert item.funding_interval_hours == 4
    assert (item.buy_exchange, item.sell_exchange) == ("binance", "bitget")
    assert item.buy_leg.exchange == "binance"
    assert item.buy_leg.funding_rate_pct == pytest.approx(0.01)
    assert item.buy_leg.funding_interval_hours == 8
    assert item.buy_leg.premium_index_pct == pytest.approx(0)
    assert item.buy_leg.volume_24h_usdt == pytest.approx(1_000_000)
    assert item.sell_leg.exchange == "bitget"
    assert item.sell_leg.funding_rate_pct == pytest.approx(-0.1)
    assert item.sell_leg.funding_interval_hours == 4
    assert item.entry_mode == "convergence"
    assert item.card_open_spread_pct == pytest.approx(0.9)
    rows[0] = market("bitget", funding=-0.75, interval=8)
    [item] = find_preadd_candidates(rows, AstroPreaddSettings()).items
    assert item.funding_source == "current"
    assert (item.buy_exchange, item.sell_exchange) == ("bitget", "binance")


def test_bybit_funding_overrides_convergence_signal_and_uses_reverse_entry():
    config = AstroPreaddSettings(exchanges=["bybit", "binance"])

    [item] = find_preadd_candidates([
        market("bybit", funding=0.8, mark=98, index=100),
        market("binance", funding=0.01, mark=100, index=100),
    ], config).items

    assert item.signal_exchange == "bybit"
    assert item.signal_type == "funding"
    assert (item.buy_exchange, item.sell_exchange) == ("binance", "bybit")
    assert item.entry_mode == "bybit_funding_reverse"
    assert item.card_open_spread_pct == pytest.approx(-0.9)


def test_bybit_negative_funding_reverses_into_long_bybit():
    config = AstroPreaddSettings(exchanges=["bybit", "binance"])

    [item] = find_preadd_candidates([
        market("bybit", funding=-0.8, mark=102, index=100),
        market("binance", funding=0.01, mark=100, index=100),
    ], config).items

    assert item.signal_exchange == "bybit"
    assert item.signal_type == "funding"
    assert (item.buy_exchange, item.sell_exchange) == ("bybit", "binance")
    assert item.entry_mode == "bybit_funding_reverse"
    assert item.card_open_spread_pct == pytest.approx(-0.9)


def test_bybit_premium_only_candidate_keeps_convergence_mode():
    config = AstroPreaddSettings(exchanges=["bybit", "binance"])

    [item] = find_preadd_candidates([
        market("bybit", funding=0.1, mark=102, index=100),
        market("binance", funding=0.01, mark=100, index=100),
    ], config).items

    assert item.signal_exchange == "bybit"
    assert item.signal_type == "premium_proxy"
    assert (item.buy_exchange, item.sell_exchange) == ("binance", "bybit")
    assert item.entry_mode == "convergence"
    assert item.card_open_spread_pct == pytest.approx(0.9)


def test_preadd_proxy_and_conflicting_or_stale_signals_fail_closed():
    config = AstroPreaddSettings()
    [item] = find_preadd_candidates([
        market("bitget", mark=102, index=100), market("binance")
    ], config).items
    assert item.signal_type == "premium_proxy"
    assert item.signal_value_pct == pytest.approx(2)
    assert item.sell_exchange == "bitget"
    assert item.sell_leg.premium_index_pct == pytest.approx(2)
    conflicting = find_preadd_candidates([
        market("bitget", predicted=0.8), market("binance", predicted=0.9)
    ], config)
    assert conflicting.items == []
    assert "方向冲突" in conflicting.warnings[0]
    assert find_preadd_candidates([
        market("bitget", predicted=0.8),
        market("binance", timestamp=datetime.now(UTC) - timedelta(seconds=31))
    ], config).items == []
    assert find_preadd_candidates([
        market("bitget", predicted=0.8, interval=0), market("binance")
    ], config).items == []


def test_preadd_requires_both_legs_to_meet_24h_volume_threshold():
    config = AstroPreaddSettings(min_volume_24h_usdt=500_000)
    assert find_preadd_candidates([
        market("bitget", predicted=0.8, volume=600_000),
        market("binance", volume=499_999),
    ], config).items == []
    assert find_preadd_candidates([
        market("bitget", predicted=0.8, volume=600_000),
        market("binance", volume=None),
    ], config).items == []
    assert len(find_preadd_candidates([
        market("bitget", predicted=0.8, volume=600_000),
        market("binance", volume=500_000),
    ], config).items) == 1


@pytest.mark.asyncio
async def test_preadd_creates_only_paused_bgbn_cards_and_never_updates_existing():
    store = SnapshotStore()
    store.set_all_markets([
        market("bitget", funding=0.08, predicted=0.8, volume=2_000_000),
        market("binance", funding=0.01, volume=3_000_000),
    ])
    repo = FakeRepo()
    client = FakeClient()
    service = AstroAlertService(
        client, Settings(astro_alert_auto_create=False, astro_dry_run_only=False),
        add_restart_delay_seconds=0, risk_settings_loader=repo.get_risk_settings,
    )
    messages: list[str] = []

    async def send_message(message: str) -> None:
        messages.append(message)

    preparer = AstroPreaddService(store, repo, service, alert_sender=send_message)
    first = await preparer.run()
    assert first.created == 1 and first.failed == 0
    assert [(pair["buyEx"], pair["sellEx"]) for pair in client.added] == [
        ("binance", "bitget"), ("gc-binance", "bitget")
    ]
    assert all(pair["status"] is False and pair["disableOpen"] is True for pair in client.added)
    assert all(pair["openPosition"] == "0.009000" for pair in client.added)
    assert len(messages) == 1
    assert "[Astro 预建] 已创建暂停卡片" in messages[0]
    assert "方向：做多 binance → 做空 bitget" in messages[0]
    assert "策略：价差收敛" in messages[0]
    assert "做多侧 binance：溢价 +0.000%，当前资金费 +0.0100% / 8h，24h 3.00M USDT" in messages[0]
    assert "做空侧 bitget：溢价 +0.000%，当前资金费 +0.0800% / 8h，24h 2.00M USDT" in messages[0]
    assert "卡片开仓价差阈值：+0.900%" in messages[0]
    assert "卡片状态：暂停、禁开" in messages[0]
    second = await preparer.run()
    assert second.created == 0 and second.skipped == 1
    assert len(client.added) == 2
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_preadd_creates_bybit_funding_card_with_negative_open_threshold():
    store = SnapshotStore()
    store.set_all_markets([
        market("bybit", funding=0.8, mark=98, index=100),
        market("binance", funding=0.01, mark=100, index=100),
    ])
    repo = FakeRepo(AstroPreaddSettings(exchanges=["bybit", "binance"]))
    client = FakeClient()
    service = AstroAlertService(
        client, Settings(astro_alert_auto_create=False, astro_dry_run_only=False),
        add_restart_delay_seconds=0, risk_settings_loader=repo.get_risk_settings,
    )
    messages: list[str] = []

    async def send_message(message: str) -> None:
        messages.append(message)

    result = await AstroPreaddService(store, repo, service, alert_sender=send_message).run()

    assert result.created == 1 and result.failed == 0
    assert [(pair["buyEx"], pair["sellEx"]) for pair in client.added] == [
        ("binance", "bybit"), ("gc-binance", "gc-bybit")
    ]
    assert all(pair["openPosition"] == "-0.009000" for pair in client.added)
    assert all(pair["closePosition"] == "-0.010000" for pair in client.added)
    assert all(pair["status"] is False and pair["disableOpen"] is True for pair in client.added)
    assert len(messages) == 1
    assert "策略：Bybit 资金费优先，反向开仓（不做价差收敛）" in messages[0]
    assert "卡片开仓价差阈值：-0.900%" in messages[0]
    assert "[Astro 预建] 已创建暂停卡片" in messages[0]
    assert "方向：做多 binance → 做空 bybit" in messages[0]
    assert "做多侧 binance：溢价 +0.000%，当前资金费 +0.0100% / 8h，24h 1.00M USDT" in messages[0]
    assert "做空侧 bybit：溢价 -2.000%，当前资金费 +0.8000% / 8h，24h 1.00M USDT" in messages[0]
    assert "卡片状态：暂停、禁开" in messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("default_variant, override, expected", [
    ("non_gc", None, ("binance", "bitget")),
    ("gc", None, ("gc-binance", "bitget")),
    ("non_gc", "gc", ("gc-binance", "bitget")),
])
async def test_preadd_uses_global_or_one_run_card_variant(
    default_variant: str, override: str | None, expected: tuple[str, str],
) -> None:
    store = SnapshotStore()
    store.set_all_markets([
        market("bitget", predicted=0.8), market("binance"),
    ])
    repo = FakeRepo(card_settings=AstroCardSettings(card_variant=default_variant))
    client = FakeClient()
    service = AstroAlertService(
        client, Settings(astro_dry_run_only=False), add_restart_delay_seconds=0,
    )

    result = await AstroPreaddService(store, repo, service).run(card_variant=override)

    assert result.created == 1
    assert [(pair["buyEx"], pair["sellEx"]) for pair in client.added] == [expected]


@pytest.mark.asyncio
async def test_preadd_uses_fresh_entry_mode_when_signal_changes_before_creation():
    store = SnapshotStore()
    initial_markets = [
        market("bybit", funding=0.8, mark=102, index=100),
        market("binance", funding=0.01, mark=100, index=100),
    ]
    fresh_markets = [
        market("bybit", funding=0.1, mark=102, index=100),
        market("binance", funding=0.01, mark=100, index=100),
    ]
    store.set_all_markets(initial_markets)
    repo = FakeRepo(AstroPreaddSettings(exchanges=["bybit", "binance"]))
    client = FakeClient()
    service = AstroAlertService(
        client, Settings(astro_alert_auto_create=False, astro_dry_run_only=False),
        add_restart_delay_seconds=0, risk_settings_loader=repo.get_risk_settings,
    )
    preparer = AstroPreaddService(store, repo, service)
    original_preview = preparer.preview
    preview_calls = 0

    async def changing_preview(settings=None):
        nonlocal preview_calls
        preview_calls += 1
        if preview_calls == 2:
            store.set_all_markets(fresh_markets)
        return await original_preview(settings)

    preparer.preview = changing_preview
    result = await preparer.run()

    assert result.created == 1 and result.failed == 0
    assert all(pair["openPosition"] == "0.009000" for pair in client.added)


@pytest.mark.asyncio
async def test_preadd_respects_ignored_exchanges_and_dry_run():
    store = SnapshotStore()
    store.set_all_markets([market("bitget", predicted=0.8), market("binance")])
    repo = FakeRepo(risk=RiskSettings(ignored_exchanges=["bitget"]))
    client = FakeClient()
    service = AstroAlertService(client, Settings(astro_dry_run_only=False))
    preparer = AstroPreaddService(store, repo, service)
    assert (await preparer.run()).attempted == 0
    assert client.list_calls == 0
    repo.risk = RiskSettings()
    service.settings = Settings(astro_dry_run_only=True)
    result = await preparer.run()
    assert result.attempted == 0
    assert "dry-run" in result.warnings[0]
    assert client.list_calls == 0


@pytest.mark.asyncio
async def test_preadd_notification_failure_does_not_change_created_result():
    store = SnapshotStore()
    store.set_all_markets([market("bitget", predicted=0.8), market("binance")])
    repo = FakeRepo()
    client = FakeClient()
    service = AstroAlertService(
        client, Settings(astro_dry_run_only=False), add_restart_delay_seconds=0,
    )

    async def fail_notification(_: str) -> None:
        raise RuntimeError("webhook unavailable")

    result = await AstroPreaddService(
        store, repo, service, alert_sender=fail_notification,
    ).run()
    assert result.created == 1
    assert result.failed == 0
    assert "卡片已创建，但飞书通知失败" in result.warnings[-1]


def test_preadd_api_saves_rule_with_auth_and_previews_without_writing_astro():
    store = SnapshotStore()
    store.set_all_markets([market("bitget", predicted=0.8), market("binance")])
    app = create_app(
        snapshot_store=store,
        settings=Settings(database_url="sqlite:///:memory:", dashboard_password="test-pass"),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/astro/preadd/settings").json()["enabled"] is False
        assert client.get("/api/astro/preadd/preview").json()["total_matches"] == 1
        assert client.put("/api/astro/preadd/settings", json=AstroPreaddSettings().model_dump()).status_code == 401
        saved = client.put(
            "/api/astro/preadd/settings",
            json=AstroPreaddSettings(
                funding_threshold_pct=0.7,
                min_volume_24h_usdt=500_000,
            ).model_dump(),
            headers={"x-dashboard-password": "test-pass"},
        )
        assert saved.status_code == 200
        assert client.get("/api/astro/preadd/settings").json()["funding_threshold_pct"] == 0.7
        assert client.get("/api/astro/preadd/settings").json()["min_volume_24h_usdt"] == 500_000
        result = client.post("/api/astro/preadd/run", headers={"x-dashboard-password": "test-pass"})
        assert result.status_code == 200
        assert result.json()["created"] == 0
        assert "dry-run" in result.json()["warnings"][0]
