from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.config import Settings
from app.models.astro import AstroCardCreateRequest
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AstroCardSettings, LivePilotSettings, RiskSettings
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
        add_errors: dict[str, AstroClientError] | None = None,
    ):
        self.pairs = pairs or []
        self.error = error
        self.add_errors = add_errors or {}
        self.added: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.list_calls = 0

    async def list_pairs(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return self.pairs

    async def add_pair(self, pair: dict[str, Any]) -> dict[str, Any]:
        route = f"{pair.get('buyEx')}->{pair.get('sellEx')}"
        if error := self.add_errors.get(route):
            raise error
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
@pytest.mark.parametrize(
    "handler_name",
    [
        "handle_alert",
        "handle_live_pilot",
    ],
)
async def test_global_blacklist_blocks_automatic_astro_create_paths(handler_name: str) -> None:
    async def load_risk_settings() -> RiskSettings:
        return RiskSettings(excluded_symbols=["BTCUSDT"])

    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(
            astro_alert_auto_create=True,
            astro_manual_card_create=True,
            astro_dry_run_only=False,
        ),
        live_pilot_settings=LivePilotSettings(enabled=True),
        risk_settings_loader=load_risk_settings,
    )

    result = await getattr(service, handler_name)(opportunity())

    assert result.status == "skipped"
    assert result.action == "excluded_symbol"
    assert "BTCUSDT 已在全局黑名单" in result.message
    assert client.list_calls == 0
    assert not client.added


@pytest.mark.asyncio
async def test_global_blacklist_warns_but_does_not_block_manual_create() -> None:
    async def load_risk_settings() -> RiskSettings:
        return RiskSettings(excluded_symbols=["BTCUSDT"])

    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_manual_card_create=True, astro_dry_run_only=False),
        risk_settings_loader=load_risk_settings,
        add_restart_delay_seconds=0,
    )

    result = await service.handle_manual_create(opportunity())

    assert result.status == "created"
    assert client.added
    assert any("BTCUSDT 已在全局黑名单" in warning for warning in result.warnings)
    assert any("仅作风险提示，未拦截创建" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_risk_settings_failure_blocks_astro_write() -> None:
    async def fail_to_load_risk_settings() -> RiskSettings:
        raise RuntimeError("database unavailable")

    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        risk_settings_loader=fail_to_load_risk_settings,
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "failed"
    assert result.action == "risk_settings"
    assert "已阻止创建" in result.message
    assert client.list_calls == 0
    assert not client.added


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
async def test_manual_create_can_override_open_enabled() -> None:
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

    result = await service.handle_manual_create(
        opportunity(),
        AstroCardCreateRequest(open_enabled=True),
    )

    assert result.status == "created"
    assert client.added[0]["status"] is True
    assert client.added[0]["disableOpen"] is False


@pytest.mark.asyncio
async def test_manual_create_allows_reverse_sf_with_negative_open_spread() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_manual_card_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    reverse_sf = opportunity(
        OpportunityType.SF,
        MarketType.FUTURE,
        MarketType.SPOT,
    ).model_copy(
        update={
            "open_spread_pct": -0.8,
            "close_spread_pct": -0.35,
        }
    )

    result = await service.handle_manual_create(reverse_sf)

    assert result.status == "created"
    assert client.added[0]["type"] == "FS"
    assert client.added[0]["openPosition"] == "-0.008000"
    assert client.added[0]["closePosition"] == "-0.009000"
    assert any("Astro FS" in warning for warning in result.warnings)


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
                    "net_funding_next_pct": -1.6,
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
    assert "同步创建 gc-binance->gc-okx（共 2 张）" in result.message
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "okx"),
        ("gc-binance", "gc-okx"),
    ]
    assert client.added[0]["status"] is False
    assert client.added[0]["disableOpen"] is True
    assert client.added[0]["type"] == "FF"


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
async def test_lighter_creation_writes_only_gc_routes_even_with_existing_plain_card(manual: bool) -> None:
    client = FakeAstroClient(pairs=[{"name": "BTC", "type": "FF", "buyEx": "lighter", "sellEx": "okx"}])
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_manual_card_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    opp = opportunity().model_copy(update={"buy_exchange": "lighter"})
    result = await (service.handle_manual_create(opp) if manual else service.handle_alert(opp))
    assert result.status == "created"
    assert [(pair["buyEx"], pair["sellEx"]) for pair in client.added] == [
        ("gc-lighter", "gc-okx")
    ]
    assert client.updated == []


@pytest.mark.asyncio
async def test_lighter_unknown_counterparty_never_calls_astro() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client, Settings(astro_manual_card_create=True, astro_dry_run_only=False)
    )
    result = await service.handle_manual_create(
        opportunity().model_copy(update={"buy_exchange": "lighter", "sell_exchange": "aster"})
    )
    assert result.action == "unsupported"
    assert client.list_calls == 0
    assert client.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name",
    [
        "handle_alert",
        "handle_live_pilot",
        "handle_manual_create",
        "handle_preadd",
    ],
)
async def test_rh_lighter_is_read_only_across_all_astro_create_paths(
    handler_name: str,
) -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(
            astro_alert_auto_create=True,
            astro_manual_card_create=True,
            astro_dry_run_only=False,
        ),
        live_pilot_settings=LivePilotSettings(enabled=True),
    )
    rh_opportunity = opportunity().model_copy(
        update={"sell_exchange": "rh-lighter"}
    )

    if handler_name == "handle_preadd":
        result = await service.handle_preadd(rh_opportunity, AstroCardSettings())
    else:
        result = await getattr(service, handler_name)(rh_opportunity)

    assert result.status == "skipped"
    assert result.action == "unsupported"
    assert "公开只读行情" in result.message
    assert "未开放 Astro 建卡或交易执行" in result.message
    assert client.list_calls == 0
    assert client.added == []
    assert client.updated == []


@pytest.mark.asyncio
async def test_new_listing_risk_label_does_not_override_card_defaults() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(
        opportunity().model_copy(
            update={
                "symbol": "UNITREEUSDT",
                "risk_labels": ["NEW_LISTING"],
            }
        )
    )

    assert result.status == "created"
    assert "已创建暂停卡片 UNITREE FF binance->okx，禁开=true" in result.message
    assert client.added[0]["status"] is False
    assert client.added[0]["disableOpen"] is True


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
            open_enabled=True,
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
    assert client.added[0]["status"] is True
    assert client.added[0]["disableOpen"] is False


@pytest.mark.asyncio
async def test_live_pilot_experiment_create_uses_pilot_notional_and_enabled_card() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        card_settings=AstroCardSettings(
            max_trade_usdt=10,
            leverage=1,
            min_notional=10,
            max_notional=10,
            close_position_buffer_pct=0.1,
            unfavorable_funding_weight=1,
            close_position_floor_pct=0,
        ),
        live_pilot_settings=LivePilotSettings(
            enabled=True,
            notional_per_symbol_usdt=100,
            create_cards_enabled=True,
        ),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_live_pilot(opportunity())

    assert result.status == "created"
    assert "已创建开启卡片 BTC FF binance->okx，禁开=false" in result.message
    assert client.added[0]["status"] is True
    assert client.added[0]["disableOpen"] is False
    assert client.added[0]["maxTradeUSDT"] == "100"
    assert client.added[0]["maxNotional"] == "100"


@pytest.mark.asyncio
async def test_live_pilot_experiment_does_not_change_regular_alert_card_defaults() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        card_settings=AstroCardSettings(
            max_trade_usdt=10,
            leverage=1,
            min_notional=10,
            max_notional=10,
            open_enabled=False,
        ),
        live_pilot_settings=LivePilotSettings(
            enabled=True,
            notional_per_symbol_usdt=100,
            create_cards_enabled=True,
        ),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "created"
    assert client.added[0]["status"] is False
    assert client.added[0]["disableOpen"] is True
    assert client.added[0]["maxTradeUSDT"] == "10"
    assert client.added[0]["maxNotional"] == "10"


@pytest.mark.asyncio
async def test_existing_same_route_pair_is_skipped_without_update() -> None:
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
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("gc-binance", "gc-okx")
    ]
    assert not client.updated
    assert result.status == "created"
    assert result.action == "add"
    assert "已存在 binance->okx" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_dex,expected_action", [("para", "existing"), ("main", "conflict")])
async def test_existing_hl_card_must_match_dex_even_when_route_variants_allowed(
    existing_dex: str, expected_action: str
) -> None:
    existing = [
        {
            "name": "TTWO", "type": "FF", "buyEx": "binance", "sellEx": "hl",
            "bEffectiveHlDex": existing_dex,
        },
        {
            "name": "TTWO", "type": "FF", "buyEx": "gc-binance", "sellEx": "gc-hl",
            "bEffectiveHlDex": existing_dex,
        },
    ]
    client = FakeAstroClient(existing)
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    service.allow_same_name_variants = True
    pair = opportunity().model_copy(
        update={
            "symbol": "TTWOUSDT", "sell_exchange": "hyperliquid",
            "sell_raw_symbol": "para:TTWO",
        }
    )

    result = await service.handle_alert(pair)

    assert result.status == "skipped"
    assert result.action == expected_action
    assert not client.added
    assert "hl(para)" in result.message
    if expected_action == "conflict":
        assert "hl(main)" in result.message


@pytest.mark.asyncio
async def test_manual_hl_card_without_market_does_not_use_astro_default() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_manual_card_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    pair = opportunity().model_copy(update={"sell_exchange": "hyperliquid"})

    result = await service.handle_manual_create(pair)

    assert result.status == "skipped"
    assert "HL 市场未确认" in result.message
    assert not client.added


@pytest.mark.asyncio
async def test_ff_hl_card_submits_para_market_on_base_and_gc_routes() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    pair = opportunity().model_copy(
        update={
            "symbol": "TTWOUSDT", "sell_exchange": "hyperliquid",
            "sell_raw_symbol": "para:TTWO",
        }
    )

    result = await service.handle_alert(pair)

    assert result.status == "created"
    assert [(card["sellEx"], card["bHlDex"]) for card in client.added] == [
        ("hl", "para"), ("gc-hl", "para")
    ]


@pytest.mark.asyncio
async def test_existing_base_and_gc_routes_are_both_skipped_when_variants_allowed() -> None:
    client = FakeAstroClient(
        [
            {"name": "BTC", "type": "FF", "buyEx": "binance", "sellEx": "okx"},
            {"name": "BTC", "type": "FF", "buyEx": "gc-binance", "sellEx": "gc-okx"},
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
    )
    service.allow_same_name_variants = True

    result = await service.handle_alert(opportunity())

    assert not client.added
    assert result.status == "skipped"
    assert result.action == "existing"
    assert "binance->okx、gc-binance->gc-okx" in result.message


@pytest.mark.asyncio
async def test_existing_gc_route_only_backfills_base_route() -> None:
    client = FakeAstroClient(
        [
            {"name": "BTC", "type": "FF", "buyEx": "gc-binance", "sellEx": "gc-okx"},
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "okx")
    ]
    assert result.status == "created"
    assert "已存在 gc-binance->gc-okx" in result.message


@pytest.mark.asyncio
async def test_bitget_route_creates_plain_and_other_leg_gc_versions() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    bitget_opportunity = opportunity().model_copy(update={"buy_exchange": "bitget"})

    result = await service.handle_alert(bitget_opportunity)

    assert result.status == "created"
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("bitget", "okx"),
        ("bitget", "gc-okx"),
    ]


@pytest.mark.asyncio
async def test_reverse_bitget_route_only_adds_gc_to_other_leg() -> None:
    client = FakeAstroClient()
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    bitget_opportunity = opportunity().model_copy(update={"sell_exchange": "bitget"})

    result = await service.handle_alert(bitget_opportunity)

    assert result.status == "created"
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "bitget"),
        ("gc-binance", "bitget"),
    ]


@pytest.mark.asyncio
async def test_partial_gc_create_failure_is_reported_after_base_success() -> None:
    client = FakeAstroClient(
        add_errors={
            "gc-binance->gc-okx": AstroClientError("Astro HTTP 503: restarting", 503),
        }
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "failed"
    assert result.action == "add_partial"
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "okx")
    ]
    assert "已创建 binance->okx" in result.message
    assert "gc-binance->gc-okx 创建失败" in result.message


@pytest.mark.asyncio
async def test_existing_same_name_different_type_is_not_overwritten() -> None:
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
async def test_manual_create_warns_for_same_name_conflict_and_adds_new_route() -> None:
    client = FakeAstroClient(
        [
            {
                "name": "BTC",
                "type": "SF",
                "buyEx": "gate",
                "sellEx": "bybit",
            }
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_manual_card_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_manual_create(opportunity())

    assert result.status == "created"
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "okx"),
        ("gc-binance", "gc-okx"),
    ]
    assert any("Astro 已存在同名 BTC" in warning for warning in result.warnings)
    assert any("仅作风险提示，未拦截创建" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_existing_same_name_different_type_can_be_created_when_variants_allowed() -> None:
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
        add_restart_delay_seconds=0,
    )
    service.allow_same_name_variants = True

    result = await service.handle_alert(opportunity())

    assert result.status == "created"
    assert result.action == "add"
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "okx"),
        ("gc-binance", "gc-okx"),
    ]


@pytest.mark.asyncio
async def test_existing_same_name_same_type_different_route_is_blocked_by_default() -> None:
    client = FakeAstroClient(
        [
            {
                "name": "BTC",
                "type": "FF",
                "buyEx": "gate",
                "sellEx": "bybit",
            }
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )

    result = await service.handle_alert(opportunity())

    assert result.status == "skipped"
    assert result.action == "conflict"
    assert not client.added


@pytest.mark.asyncio
async def test_existing_same_name_same_type_different_route_can_be_created_when_variants_allowed() -> None:
    client = FakeAstroClient(
        [
            {
                "name": "BTC",
                "type": "FF",
                "buyEx": "gate",
                "sellEx": "bybit",
            }
        ]
    )
    service = AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_dry_run_only=False),
        add_restart_delay_seconds=0,
    )
    service.allow_same_name_variants = True

    result = await service.handle_alert(opportunity())

    assert result.status == "created"
    assert result.action == "add"
    assert [(item["buyEx"], item["sellEx"]) for item in client.added] == [
        ("binance", "okx"),
        ("gc-binance", "gc-okx"),
    ]


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
