from datetime import UTC, datetime

import pytest

from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.trade_availability import (
    MarketTradeAvailability,
    SpotTransferAvailability,
    SpotTransferNetworkStatus,
    TradeActionStatus,
    TradeAvailabilityResult,
    TradeAvailabilityState,
    TradeEvidenceScope,
    TransferAvailabilityState,
)
from app.services.opportunity_trade_availability import (
    build_opportunity_trade_availability_report,
)

NOW = datetime(2026, 9, 21, 9, 45, tzinfo=UTC)


class FakeTradeAvailabilityService:
    def __init__(self, results: dict[str, TradeAvailabilityResult | BaseException]) -> None:
        self.results = results
        self.calls: list[tuple[str, str | None]] = []
        self.transfer_calls: list[tuple[str, str, str | None]] = []

    async def fetch_status(self, symbol: str, *, exchange: str | None = None, **kwargs):
        self.calls.append((symbol, exchange))
        result = self.results[exchange or ""]
        if isinstance(result, BaseException):
            raise result
        return result

    async def fetch_transfer_status(
        self,
        symbol: str,
        *,
        exchange: str,
        raw_symbol: str | None = None,
    ) -> SpotTransferAvailability:
        self.transfer_calls.append((symbol, exchange, raw_symbol))
        result = self.results[exchange]
        if isinstance(result, BaseException):
            raise result
        transfer = next(
            (market.spot_transfer for market in result.markets if market.spot_transfer is not None),
            None,
        )
        return transfer or SpotTransferAvailability(
            asset=symbol.removesuffix("USDT"),
            deposit_state=TransferAvailabilityState.UNKNOWN,
            withdraw_state=TransferAvailabilityState.UNKNOWN,
            all_enabled=None,
            publicly_queryable=False,
            source=f"{exchange} public asset API",
            note="未找到可匿名核验的公开充提状态",
        )


def _action(
    state: TradeAvailabilityState,
    reason_code: str,
    reason: str,
    *,
    scope: TradeEvidenceScope = TradeEvidenceScope.PUBLIC_MARKET,
) -> TradeActionStatus:
    return TradeActionStatus(
        state=state,
        reason_code=reason_code,
        reason=reason,
        scope=scope,
    )


AVAILABLE = _action(
    TradeAvailabilityState.AVAILABLE,
    "PUBLIC_MARKET_AVAILABLE",
    "公开市场状态允许且实时盘口有报价",
)
NOT_APPLICABLE = _action(
    TradeAvailabilityState.NOT_APPLICABLE,
    "NOT_APPLICABLE",
    "现货没有 Reduce Only 持仓语义",
    scope=TradeEvidenceScope.NONE,
)


def _market(
    exchange: str,
    market_type: MarketType,
    raw_symbol: str,
    *,
    dex: str | None = None,
    status: str = "TRADING",
    buy_open: TradeActionStatus = AVAILABLE,
    sell_open: TradeActionStatus = AVAILABLE,
    buy_reduce_only: TradeActionStatus = AVAILABLE,
    sell_reduce_only: TradeActionStatus = AVAILABLE,
    restrictions: list[str] | None = None,
    transfer: SpotTransferAvailability | None = None,
) -> MarketTradeAvailability:
    return MarketTradeAvailability(
        exchange=exchange,
        market_type=market_type,
        symbol="ZETAUSDT",
        raw_symbol=raw_symbol,
        dex=dex,
        coverage_tier="core" if exchange != "hyperliquid" else "existing",
        observed_at=NOW,
        market_data_updated_at=NOW,
        orderbook_source="test book",
        public_status_code=status,
        public_status_source="test status",
        public_restrictions=restrictions or [],
        spot_transfer=transfer,
        buy_open=buy_open,
        sell_open=sell_open,
        buy_reduce_only=buy_reduce_only,
        sell_reduce_only=sell_reduce_only,
    )


def _result(exchange: str, markets: list[MarketTradeAvailability]) -> TradeAvailabilityResult:
    return TradeAvailabilityResult(query="ZETAUSDT", observed_at=NOW, markets=markets)


def _opportunity(
    *,
    buy_exchange: str = "hyperliquid",
    buy_market_type: MarketType = MarketType.FUTURE,
    buy_raw_symbol: str = "ZETA",
) -> Opportunity:
    return Opportunity(
        id="zeta-ff",
        type=OpportunityType.FF,
        symbol="ZETAUSDT",
        buy_exchange=buy_exchange,
        buy_market_type=buy_market_type,
        buy_raw_symbol=buy_raw_symbol,
        sell_exchange="binance",
        sell_market_type=MarketType.FUTURE,
        sell_raw_symbol="ZETAUSDT",
        open_spread_pct=3.188,
        close_spread_pct=3.217,
        fee_adjusted_open_pct=3.038,
        spread_width_pct=0.029,
        buy_bid=0.0648,
        buy_ask=0.0649,
        sell_bid=0.067,
        sell_ask=0.0671,
        buy_volume_24h_usdt=1_000_000,
        sell_volume_24h_usdt=2_000_000,
        risk_labels=[],
        last_seen_at=NOW,
    )


@pytest.mark.asyncio
async def test_report_includes_exact_hyperliquid_actions_and_future_leg_transfer_reference() -> (
    None
):
    blocked = _action(
        TradeAvailabilityState.BLOCKED,
        "OPEN_INTEREST_CAP",
        "官方未平仓量已达上限；普通订单不能新开或增加仓位",
    )
    hyperliquid = _market(
        "hyperliquid",
        MarketType.FUTURE,
        "ZETA",
        dex="main",
        status="OPEN_INTEREST_CAP",
        buy_open=blocked,
        sell_open=blocked,
        restrictions=["官方 OI 已达上限，普通增仓受限"],
    )
    binance_future = _market("binance", MarketType.FUTURE, "ZETAUSDT")
    binance_spot = _market(
        "binance",
        MarketType.SPOT,
        "ZETAUSDT",
        buy_reduce_only=NOT_APPLICABLE,
        sell_reduce_only=NOT_APPLICABLE,
        transfer=SpotTransferAvailability(
            asset="ZETA",
            deposit_state=TransferAvailabilityState.ENABLED,
            withdraw_state=TransferAvailabilityState.PARTIAL,
            all_enabled=False,
            source="Binance public asset service",
            observed_at=NOW,
            networks=[
                SpotTransferNetworkStatus(
                    network="ZETA",
                    deposit_enabled=True,
                    withdraw_enabled=True,
                ),
                SpotTransferNetworkStatus(
                    network="ETH",
                    deposit_enabled=True,
                    withdraw_enabled=False,
                ),
            ],
        ),
    )
    service = FakeTradeAvailabilityService(
        {
            "hyperliquid": _result("hyperliquid", [hyperliquid]),
            "binance": _result("binance", [binance_future, binance_spot]),
        }
    )

    report = await build_opportunity_trade_availability_report(service, _opportunity())

    assert report.opening_restricted is True
    assert "开仓路径：不可用（买入腿开多 公开受限；卖出腿开空 公开可用）" in report.text
    assert "平仓路径：公开可用（买入腿平多 公开可用；卖出腿平空 公开可用）" in report.text
    assert "买入腿：hyperliquid / future / DEX main / ZETA" in report.text
    assert (
        "买入方向：开多 公开受限[OPEN_INTEREST_CAP]；平空 公开可用"
        in report.text
    )
    assert (
        "卖出方向：开空 公开受限[OPEN_INTEREST_CAP]；平多 公开可用"
        in report.text
    )
    assert "- hyperliquid / asset ZETA：充币 未知；提币 未知；0 条链" in report.text
    assert "- binance / spot / ZETAUSDT：充币 全开；提币 部分开放；2 条链" in report.text
    assert service.calls == [("ZETAUSDT", "hyperliquid"), ("ZETAUSDT", "binance")]
    assert service.transfer_calls == [
        ("ZETAUSDT", "hyperliquid", "ZETA"),
        ("ZETAUSDT", "binance", "ZETAUSDT"),
    ]


@pytest.mark.asyncio
async def test_report_keeps_spot_actions_free_of_short_and_reduce_only_labels() -> None:
    transfer = SpotTransferAvailability(
        asset="ZETA",
        deposit_state=TransferAvailabilityState.ENABLED,
        withdraw_state=TransferAvailabilityState.ENABLED,
        all_enabled=True,
        source="Binance public asset service",
        observed_at=NOW,
        networks=[
            SpotTransferNetworkStatus(
                network="ZETA",
                deposit_enabled=True,
                withdraw_enabled=True,
            )
        ],
    )
    binance_spot = _market(
        "binance",
        MarketType.SPOT,
        "ZETAUSDT",
        buy_reduce_only=NOT_APPLICABLE,
        sell_reduce_only=NOT_APPLICABLE,
        transfer=transfer,
    )
    service = FakeTradeAvailabilityService(
        {
            "binance": _result(
                "binance", [binance_spot, _market("binance", MarketType.FUTURE, "ZETAUSDT")]
            )
        }
    )
    opportunity = _opportunity(
        buy_exchange="binance",
        buy_market_type=MarketType.SPOT,
        buy_raw_symbol="ZETAUSDT",
    ).model_copy(
        update={
            "type": OpportunityType.SF,
            "sell_exchange": "binance",
        }
    )

    report = await build_opportunity_trade_availability_report(service, opportunity)

    buy_leg_block = report.text.split("买入腿：", 1)[1].split("卖出腿：", 1)[0]
    assert "现货交易：买入 公开可用；卖出 公开可用" in buy_leg_block
    assert "开多" not in buy_leg_block
    assert "平空" not in buy_leg_block
    assert "不适用" not in buy_leg_block


@pytest.mark.asyncio
async def test_report_uses_exact_spot_leg_for_transfer_when_raw_symbol_is_an_alias() -> None:
    transfer = SpotTransferAvailability(
        asset="RSOXL",
        deposit_state=TransferAvailabilityState.PARTIAL,
        withdraw_state=TransferAvailabilityState.DISABLED,
        all_enabled=False,
        source="Bitget spot public coins",
        observed_at=NOW,
        networks=[
            SpotTransferNetworkStatus(
                network="ETH",
                deposit_enabled=True,
                withdraw_enabled=False,
            )
        ],
    )
    bitget_spot = _market(
        "bitget",
        MarketType.SPOT,
        "RSOXLUSDT",
        buy_reduce_only=NOT_APPLICABLE,
        sell_reduce_only=NOT_APPLICABLE,
        transfer=transfer,
    ).model_copy(update={"symbol": "SOXLUSDT"})
    binance_future = _market("binance", MarketType.FUTURE, "SOXLUSDT").model_copy(
        update={"symbol": "SOXLUSDT"}
    )
    service = FakeTradeAvailabilityService(
        {
            "bitget": TradeAvailabilityResult(
                query="SOXLUSDT",
                observed_at=NOW,
                markets=[bitget_spot],
            ),
            "binance": TradeAvailabilityResult(
                query="SOXLUSDT",
                observed_at=NOW,
                markets=[binance_future],
            ),
        }
    )
    opportunity = _opportunity(
        buy_exchange="bitget",
        buy_market_type=MarketType.SPOT,
        buy_raw_symbol="RSOXLUSDT",
    ).model_copy(
        update={
            "symbol": "SOXLUSDT",
            "buy_raw_symbol": "RSOXLUSDT",
            "sell_raw_symbol": "SOXLUSDT",
            "type": OpportunityType.SF,
        }
    )

    report = await build_opportunity_trade_availability_report(service, opportunity)

    assert "- bitget / spot / RSOXLUSDT：充币 部分开放；提币 已关闭；1 条链" in report.text
    assert "- bitget：未知" not in report.text


@pytest.mark.asyncio
async def test_report_degrades_failed_exchange_to_unknown_without_dropping_other_leg() -> None:
    service = FakeTradeAvailabilityService(
        {
            "hyperliquid": TimeoutError("public status timed out"),
            "binance": _result(
                "binance",
                [_market("binance", MarketType.FUTURE, "ZETAUSDT")],
            ),
        }
    )

    report = await build_opportunity_trade_availability_report(service, _opportunity())

    assert report.opening_restricted is True
    assert "开仓路径：未知" in report.text
    assert "买入腿：hyperliquid / future / DEX main / ZETA" in report.text
    assert "交易状态：未知（诊断失败：TimeoutError: public status timed out）" in report.text
    assert "卖出腿：binance / future / ZETAUSDT" in report.text
