from dataclasses import dataclass
from typing import Protocol

from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.models.orderbook import DepthValidationResult, OrderBookLevel, OrderBookSnapshot
from app.models.settings import AstroCardSettings, RiskSettings
from app.services.alert_metrics import funding_edge_pct

EPSILON = 1e-9


class OrderBookAdapter(Protocol):
    name: str

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        ...


@dataclass(frozen=True)
class FillResult:
    depth_usdt: float
    filled_usdt: float
    base_size: float
    vwap: float | None


def _is_level_inside_band(
    level: OrderBookLevel,
    *,
    side: str,
    reference_price: float,
    band_pct: float,
) -> bool:
    if band_pct < 0:
        return False
    if side == "ask":
        return level.price <= reference_price * (1 + band_pct / 100) + EPSILON
    return level.price >= reference_price * (1 - band_pct / 100) - EPSILON


def _fill_quote_notional(
    levels: list[OrderBookLevel],
    target_notional: float,
    *,
    side: str,
    band_pct: float,
) -> FillResult:
    if not levels:
        return FillResult(depth_usdt=0.0, filled_usdt=0.0, base_size=0.0, vwap=None)
    reference_price = levels[0].price
    band_levels = [
        level
        for level in levels
        if _is_level_inside_band(
            level,
            side=side,
            reference_price=reference_price,
            band_pct=band_pct,
        )
    ]
    depth = sum(level.price * level.size for level in band_levels)
    remaining = target_notional
    filled = 0.0
    base_size = 0.0
    for level in band_levels:
        level_notional = level.price * level.size
        take_notional = min(level_notional, remaining)
        if take_notional <= 0:
            continue
        filled += take_notional
        base_size += take_notional / level.price
        remaining -= take_notional
        if remaining <= EPSILON:
            break
    if base_size <= 0:
        return FillResult(depth_usdt=depth, filled_usdt=filled, base_size=base_size, vwap=None)
    return FillResult(depth_usdt=depth, filled_usdt=filled, base_size=base_size, vwap=filled / base_size)


def _cost_pct(opportunity: Opportunity) -> float:
    return opportunity.open_spread_pct - opportunity.fee_adjusted_open_pct


def _target_notional(risk_settings: RiskSettings) -> float:
    return risk_settings.signal_validation_notional_usdt


def _required_depth_usdt(risk_settings: RiskSettings) -> float:
    return max(
        risk_settings.min_top_of_book_depth_usdt,
        _target_notional(risk_settings) * risk_settings.orderbook_depth_safety_multiple,
    )


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text else exc.__class__.__name__


class OrderBookDepthValidator:
    def __init__(self, adapters: list[OrderBookAdapter], limit: int = 20) -> None:
        self.adapters = {adapter.name.lower(): adapter for adapter in adapters}
        self.limit = limit

    async def validate(
        self,
        opportunity: Opportunity,
        risk_settings: RiskSettings,
        card_settings: AstroCardSettings | None = None,
        override_notional_usdt: float | None = None,
    ) -> DepthValidationResult:
        target = _target_notional(risk_settings)
        required_depth = _required_depth_usdt(risk_settings)
        band_pct = risk_settings.orderbook_depth_band_pct
        blockers: list[str] = []
        warnings: list[str] = []
        buy_adapter = self.adapters.get(opportunity.buy_exchange.lower())
        sell_adapter = self.adapters.get(opportunity.sell_exchange.lower())
        if buy_adapter is None:
            blockers.append(f"{opportunity.buy_exchange} 订单簿适配器不可用")
        if sell_adapter is None:
            blockers.append(f"{opportunity.sell_exchange} 订单簿适配器不可用")
        if blockers:
            return self._result(
                opportunity,
                target,
                required_depth,
                band_pct,
                blockers=blockers,
                warnings=warnings,
            )

        buy_book = None
        sell_book = None
        buy_raw_symbol = opportunity.buy_raw_symbol or opportunity.symbol
        sell_raw_symbol = opportunity.sell_raw_symbol or opportunity.symbol
        try:
            buy_book = await buy_adapter.fetch_order_book(
                opportunity.symbol,
                opportunity.buy_market_type,
                buy_raw_symbol,
                self.limit,
            )
        except Exception as exc:  # noqa: BLE001 - report validation blocker instead of aborting alert.
            blockers.append(
                f"买入侧订单簿请求失败："
                f"{opportunity.buy_exchange} {opportunity.buy_market_type}，{_exception_message(exc)}"
            )
        try:
            sell_book = await sell_adapter.fetch_order_book(
                opportunity.symbol,
                opportunity.sell_market_type,
                sell_raw_symbol,
                self.limit,
            )
        except Exception as exc:  # noqa: BLE001 - report validation blocker instead of aborting alert.
            blockers.append(
                f"卖出侧订单簿请求失败："
                f"{opportunity.sell_exchange} {opportunity.sell_market_type}，{_exception_message(exc)}"
            )
        if blockers:
            return self._result(
                opportunity,
                target,
                required_depth,
                band_pct,
                blockers=blockers,
                warnings=warnings,
            )

        if buy_book is None:
            blockers.append(f"买入侧订单簿不可用：{opportunity.buy_exchange} {opportunity.buy_market_type}")
        if sell_book is None:
            blockers.append(f"卖出侧订单簿不可用：{opportunity.sell_exchange} {opportunity.sell_market_type}")
        if blockers:
            return self._result(
                opportunity,
                target,
                required_depth,
                band_pct,
                blockers=blockers,
                warnings=warnings,
            )

        buy_fill = _fill_quote_notional(
            buy_book.asks,
            target,
            side="ask",
            band_pct=band_pct,
        )
        sell_fill = _fill_quote_notional(
            sell_book.bids,
            target,
            side="bid",
            band_pct=band_pct,
        )
        min_depth = min(buy_fill.depth_usdt, sell_fill.depth_usdt)
        if required_depth > 0 and buy_fill.depth_usdt + EPSILON < required_depth:
            blockers.append(
                f"买入侧 {band_pct:.3f}% 价格带深度不足："
                f"{buy_fill.depth_usdt:.2f}/{required_depth:.2f} USDT"
            )
        if required_depth > 0 and sell_fill.depth_usdt + EPSILON < required_depth:
            blockers.append(
                f"卖出侧 {band_pct:.3f}% 价格带深度不足："
                f"{sell_fill.depth_usdt:.2f}/{required_depth:.2f} USDT"
            )
        if buy_fill.filled_usdt + EPSILON < target:
            blockers.append(
                f"买入侧 {band_pct:.3f}% 价格带内可成交金额不足："
                f"{buy_fill.filled_usdt:.2f}/{target:.2f} USDT"
            )
        if sell_fill.filled_usdt + EPSILON < target:
            blockers.append(
                f"卖出侧 {band_pct:.3f}% 价格带内可成交金额不足："
                f"{sell_fill.filled_usdt:.2f}/{target:.2f} USDT"
            )

        executable_open = None
        effective_edge = None
        slippage_loss = None
        cost = None
        funding_edge = None
        slippage_buffer = None
        if buy_fill.vwap is not None and sell_fill.vwap is not None:
            executable_open = (sell_fill.vwap - buy_fill.vwap) / buy_fill.vwap * 100
            cost = _cost_pct(opportunity)
            funding_edge = funding_edge_pct(opportunity)
            slippage_buffer = risk_settings.signal_slippage_buffer_pct
            effective_edge = (
                executable_open
                - cost
                + funding_edge
                - slippage_buffer
            )
            slippage_loss = opportunity.open_spread_pct - executable_open
            if effective_edge + EPSILON < risk_settings.min_effective_open_pct:
                blockers.append(
                    f"实际可成交有效收益 {effective_edge:.3f}% "
                    f"低于最低要求 {risk_settings.min_effective_open_pct:.3f}%"
                )

        return DepthValidationResult(
            passed=not blockers,
            target_notional_usdt=target,
            required_depth_usdt=required_depth,
            price_band_pct=band_pct,
            buy_depth_usdt=buy_fill.depth_usdt,
            sell_depth_usdt=sell_fill.depth_usdt,
            min_depth_usdt=min_depth,
            buy_filled_usdt=buy_fill.filled_usdt,
            sell_filled_usdt=sell_fill.filled_usdt,
            buy_vwap=buy_fill.vwap,
            sell_vwap=sell_fill.vwap,
            quoted_open_pct=opportunity.open_spread_pct,
            executable_open_pct=executable_open,
            cost_pct=cost,
            funding_edge_pct=funding_edge,
            slippage_buffer_pct=slippage_buffer,
            effective_executable_edge_pct=effective_edge,
            slippage_loss_pct=slippage_loss,
            blockers=blockers,
            warnings=warnings,
        )

    def _result(
        self,
        opportunity: Opportunity,
        target: float,
        required_depth: float,
        band_pct: float,
        blockers: list[str],
        warnings: list[str],
    ) -> DepthValidationResult:
        return DepthValidationResult(
            passed=False,
            target_notional_usdt=target,
            required_depth_usdt=required_depth,
            price_band_pct=band_pct,
            buy_depth_usdt=None,
            sell_depth_usdt=None,
            min_depth_usdt=None,
            buy_filled_usdt=0,
            sell_filled_usdt=0,
            buy_vwap=None,
            sell_vwap=None,
            quoted_open_pct=opportunity.open_spread_pct,
            executable_open_pct=None,
            effective_executable_edge_pct=None,
            slippage_loss_pct=None,
            blockers=blockers,
            warnings=warnings,
        )
