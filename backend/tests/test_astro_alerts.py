from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.config import Settings
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AstroCardSettings
from app.services.astro_alerts import AstroAlertService
from app.services.astro_client import AstroClientError


def opportunity(
    opportunity_type: OpportunityType = OpportunityType.FF,
    buy_market_type: MarketType = MarketType.FUTURE,
    sell_market_type: MarketType = MarketType.FUTURE,
) -> Opportunity:
    return Opportunity(
        id="opp-1",
        type=opportunity_type,
        symbol="BTCUSDT",
        buy_exchange="binance",
        buy_market_type=buy_market_type,
        sell_exchange="okx",
        sell_market_type=sell_market_type,
        open_spread_pct=0.8,
        close_spread_pct=0.35,
        fee_adjusted_open_pct=0.55,
        spread_width_pct=0.45,
        buy_bid=99,
        buy_ask=100,
        sell_bid=100.8,
        sell_ask=101,
        buy_volume_24h_usdt=10_000_000,
        sell_volume_24h_usdt=12_000_000,
        funding_rate_buy_pct=0.01,
        funding_rate_sell_pct=0.02,
        funding_next_rate_buy_pct=0.01,
        funding_next_rate_sell_pct=0.03,
        funding_next_time_buy=datetime(2026, 5, 20, 8, tzinfo=UTC),
        funding_next_time_sell=datetime(2026, 5, 20, 8, tzinfo=UTC),
        net_funding_pct=0.01,
        net_funding_next_pct=0.02,
        buy_funding_interval_hours=8,
        sell_funding_interval_hours=8,
        net_funding_hourly_pct=0.00125,
        net_funding_daily_pct=0.03,
        net_funding_next_hourly_pct=0.0025,
        net_funding_next_daily_pct=0.06,
        mark_index_diff_buy_pct=0.01,
        mark_index_diff_sell_pct=0.01,
        risk_labels=[],
        last_seen_at=datetime(2026, 5, 20, 1, tzinfo=UTC),
    )


class FakeAstroClient:
    def __init__(
        self,
        pairs: list[dict[str, Any]] | None = None,
        error: AstroClientError | None = None,
    ):
        self.pairs = pairs or []
        self.error = error
        self.added: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.list_calls = 0

    async def list_pairs(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return self.pairs

    async def add_pair(self, pair: dict[str, Any]) -> dict[str, Any]:
        self.added.append(pair)
        return {"code": 0}

    async def update_pair(self, pair: dict[str, Any]) -> dict[str, Any]:
        self.updated.append(pair)
        return {"code": 0}


@pytest.mark.asyncio
async def test_auto_create_disabled_does_not_call_astro() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(client, Settings(astro_alert_auto_create=False))

    result = await service.handle_alert(opportunity())

    assert result.status == "disabled"
    assert result.action == "none"
    assert "未开启" in result.message
    assert result.format_message() == f"Astro: {result.message}"
    assert client.list_calls == 0


@pytest.mark.asyncio
async def test_dry_run_mode_skips_astro_writes() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=True),
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "skipped"
    assert result.action == "dry_run"
    assert "dry-run" in result.message
    assert client.list_calls == 0


@pytest.mark.asyncio
async def test_manual_create_disabled_does_not_call_astro() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(
            astro_alert_auto_create=False,
            astro_manual_card_create=False,
            astro_dry_run_only=False,
        ),
    )

    result = await service.handle_manual_create(opportunity())

    assert result.status == "disabled"
    assert result.action == "none"
    assert client.list_calls == 0


@pytest.mark.asyncio
async def test_manual_create_uses_manual_switch_instead_of_alert_switch() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(
            astro_alert_auto_create=False,
            astro_manual_card_create=True,
            astro_dry_run_only=False,
        ),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_manual_create(opportunity())

    assert result.status == "created"
    assert result.action == "add"
    assert client.added[0]["status"] is False
    assert client.added[0]["disableOpen"] is True


@pytest.mark.asyncio
async def test_unsupported_type_is_skipped() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
    )

    result = await service.handle_alert(
        opportunity(OpportunityType.SS, MarketType.SPOT, MarketType.SPOT)
    )

    assert result.status == "skipped"
    assert result.action == "unsupported"
    assert "SS" in result.message
    assert client.list_calls == 0


@pytest.mark.asyncio
async def test_invalid_open_close_position_order_is_adjusted_before_create() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(
        opportunity().model_copy(
            update={
                "open_spread_pct": 0.88,
                "close_spread_pct": 0.94,
                "net_funding_next_hourly_pct": -0.2,
                "net_funding_next_daily_pct": -4.8,
            }
        )
    )

    assert result.status == "created"
    assert result.action == "add"
    assert client.list_calls == 1
    assert client.added[0]["openPosition"] == "0.008800"
    assert client.added[0]["closePosition"] == "0.007800"


@pytest.mark.asyncio
async def test_missing_pair_creates_paused_disable_open_card() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "created"
    assert result.action == "add"
    assert result.pair_name == "BTC"
    assert result.pair_type == "FF"
    assert "已创建暂停卡片 BTC FF binance->okx" in result.message
    assert client.added[0]["status"] is False
    assert client.added[0]["disableOpen"] is True
    assert client.added[0]["type"] == "FF"


@pytest.mark.asyncio
async def test_alert_create_uses_supplied_astro_card_settings() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        card_settings=AstroCardSettings(
            max_trade_usdt=66,
            leverage=2,
            min_notional=12,
            max_notional=66,
            close_position_buffer_pct=0.2,
            unfavorable_funding_weight=1,
            close_position_floor_pct=0,
        ),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "created"
    assert client.added[0]["maxTradeUSDT"] == "66"
    assert client.added[0]["leverage"] == "2"
    assert client.added[0]["minNotional"] == "12"
    assert client.added[0]["maxNotional"] == "66"


@pytest.mark.asyncio
async def test_existing_same_route_pair_updates_paused_disable_open_card() -> None:
    client = FakeAstroClient(
        [
            {
                "id": "Ab12Cd34Ef",
                "name": "BTC",
                "type": "FF",
                "buyEx": "binance",
                "sellEx": "okx",
                "status": True,
                "disableOpen": False,
            }
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "updated"
    assert result.action == "update"
    assert client.updated[0]["id"] == "Ab12Cd34Ef"
    assert client.updated[0]["status"] is False
    assert client.updated[0]["disableOpen"] is True
    assert client.updated[0]["openPosition"] == "0.008000"
    assert not client.added


@pytest.mark.asyncio
async def test_existing_same_name_different_route_is_not_overwritten() -> None:
    client = FakeAstroClient(
        [
            {
                "name": "BTC",
                "type": "SF",
                "buyEx": "binance",
                "sellEx": "okx",
            }
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "skipped"
    assert result.action == "conflict"
    assert "同名 BTC" in result.message
    assert not client.added
    assert not client.updated


@pytest.mark.asyncio
async def test_sdk_failure_returns_failed_result_without_raising() -> None:
    client = FakeAstroClient(error=AstroClientError("Astro HTTP 429: rate limit", 429))
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "failed"
    assert result.action == "list"
    assert "Astro HTTP 429" in result.message
    assert not client.added
    assert not client.updated
