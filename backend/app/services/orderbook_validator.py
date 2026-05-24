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
    filled_usdt: float
    base_size: float
    vwap: float | None


def _fill_quote_notional(levels: list[OrderBookLevel], target_notional: float) -> FillResult:
    remaining = target_notional
    filled = 0.0
    base_size = 0.0
    for level in levels:
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
        return FillResult(filled_usdt=filled, base_size=base_size, vwap=None)
    return FillResult(filled_usdt=filled, base_size=base_size, vwap=filled / base_size)


def _cost_pct(opportunity: Opportunity) -> float:
    return opportunity.open_spread_pct - opportunity.fee_adjusted_open_pct


def _target_notional(
    risk_settings: RiskSettings,
    card_settings: AstroCardSettings | None,
    override_notional_usdt: float | None,
) -> float:
    candidates = [risk_settings.signal_validation_notional_usdt]
    if card_settings is not None:
        candidates.append(card_settings.max_trade_usdt)
    if override_notional_usdt is not None:
        candidates.append(override_notional_usdt)
    return max(candidates)


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
        target = _target_notional(risk_settings, card_settings, override_notional_usdt)
        blockers: list[str] = []
        warnings: list[str] = []
        buy_adapter = self.adapters.get(opportunity.buy_exchange.lower())
        sell_adapter = self.adapters.get(opportunity.sell_exchange.lower())
        if buy_adapter is None:
            blockers.append(f"order book adapter unavailable for {opportunity.buy_exchange}")
        if sell_adapter is None:
            blockers.append(f"order book adapter unavailable for {opportunity.sell_exchange}")
        if blockers:
            return self._result(opportunity, target, blockers=blockers, warnings=warnings)

        buy_book = await buy_adapter.fetch_order_book(
            opportunity.symbol,
            opportunity.buy_market_type,
            opportunity.symbol,
            self.limit,
        )
        sell_book = await sell_adapter.fetch_order_book(
            opportunity.symbol,
            opportunity.sell_market_type,
            opportunity.symbol,
            self.limit,
        )
        if buy_book is None:
            blockers.append(f"order book unavailable for {opportunity.buy_exchange} {opportunity.buy_market_type}")
        if sell_book is None:
            blockers.append(f"order book unavailable for {opportunity.sell_exchange} {opportunity.sell_market_type}")
        if blockers:
            return self._result(opportunity, target, blockers=blockers, warnings=warnings)

        buy_fill = _fill_quote_notional(buy_book.asks, target)
        sell_fill = _fill_quote_notional(sell_book.bids, target)
        if buy_fill.filled_usdt + EPSILON < target:
            blockers.append(f"buy side depth filled {buy_fill.filled_usdt:.2f}/{target:.2f} USDT")
        if sell_fill.filled_usdt + EPSILON < target:
            blockers.append(f"sell side depth filled {sell_fill.filled_usdt:.2f}/{target:.2f} USDT")

        executable_open = None
        effective_edge = None
        slippage_loss = None
        if buy_fill.vwap is not None and sell_fill.vwap is not None:
            executable_open = (sell_fill.vwap - buy_fill.vwap) / buy_fill.vwap * 100
            effective_edge = (
                executable_open
                - _cost_pct(opportunity)
                + funding_edge_pct(opportunity)
                - risk_settings.signal_slippage_buffer_pct
            )
            slippage_loss = opportunity.open_spread_pct - executable_open
            if effective_edge + EPSILON < risk_settings.min_effective_open_pct:
                blockers.append(
                    f"effective executable edge {effective_edge:.3f}% is below "
                    f"{risk_settings.min_effective_open_pct:.3f}%"
                )

        return DepthValidationResult(
            passed=not blockers,
            target_notional_usdt=target,
            buy_filled_usdt=buy_fill.filled_usdt,
            sell_filled_usdt=sell_fill.filled_usdt,
            buy_vwap=buy_fill.vwap,
            sell_vwap=sell_fill.vwap,
            quoted_open_pct=opportunity.open_spread_pct,
            executable_open_pct=executable_open,
            effective_executable_edge_pct=effective_edge,
            slippage_loss_pct=slippage_loss,
            blockers=blockers,
            warnings=warnings,
        )

    def _result(
        self,
        opportunity: Opportunity,
        target: float,
        blockers: list[str],
        warnings: list[str],
    ) -> DepthValidationResult:
        return DepthValidationResult(
            passed=False,
            target_notional_usdt=target,
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
