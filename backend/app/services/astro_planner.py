from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.models.astro import AstroFieldAssumption, AstroPairPlan
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AstroCardSettings
from app.services.funding_edge import current_cycle_funding_edge_pct, next_cycle_funding_edge_pct


SUPPORTED_ASTRO_TYPES = {OpportunityType.SF, OpportunityType.FF}


@dataclass(frozen=True)
class AstroPlannerConfig:
    default_max_trade_usdt: float = 10
    default_leverage: int = 1
    default_min_notional: float = 10
    default_max_notional: float = 10
    default_close_position_buffer_pct: float = 0.1
    default_unfavorable_funding_weight: float = 1
    default_close_position_floor_pct: float = 0

    @classmethod
    def from_card_settings(cls, settings: AstroCardSettings) -> "AstroPlannerConfig":
        return cls(
            default_max_trade_usdt=settings.max_trade_usdt,
            default_leverage=settings.leverage,
            default_min_notional=settings.min_notional,
            default_max_notional=settings.max_notional,
            default_close_position_buffer_pct=settings.close_position_buffer_pct,
            default_unfavorable_funding_weight=settings.unfavorable_funding_weight,
            default_close_position_floor_pct=settings.close_position_floor_pct,
        )


@dataclass(frozen=True)
class ClosePositionDecision:
    close_spread_pct: float
    source: str
    note: str
    adjusted_for_astro: bool = False


def _base_name(symbol: str) -> str:
    normalized = symbol.upper().strip()
    for suffix in ("USDT", "USDC", "USD"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _decimal_position(percent_value: float) -> str:
    value = (Decimal(str(percent_value)) / Decimal("100")).quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )
    return f"{value:.6f}"


def _compact_number(value: float | int) -> str:
    decimal = Decimal(str(value)).normalize()
    if decimal == decimal.to_integral():
        return str(decimal.quantize(Decimal("1")))
    return format(decimal, "f")


def _has_next_cycle_inputs(opportunity: Opportunity) -> bool:
    return any(
        value is not None
        for value in (
            opportunity.net_funding_next_pct,
            opportunity.funding_next_rate_buy_pct,
            opportunity.funding_next_rate_sell_pct,
            opportunity.mark_index_diff_buy_pct,
            opportunity.mark_index_diff_sell_pct,
        )
    )


def _funding_signal(opportunity: Opportunity) -> tuple[str, float | None, str]:
    next_cycle = next_cycle_funding_edge_pct(opportunity)
    if next_cycle is not None and _has_next_cycle_inputs(opportunity):
        return (
            "predicted",
            next_cycle,
            (
                f"predicted funding cycle={next_cycle:.6f}%, "
                f"buy interval={opportunity.buy_funding_interval_hours}h, "
                f"sell interval={opportunity.sell_funding_interval_hours}h"
            ),
        )

    current_cycle = current_cycle_funding_edge_pct(opportunity)
    if current_cycle is not None:
        return (
            "current",
            current_cycle,
            (
                f"current funding cycle={current_cycle:.6f}%, "
                f"buy interval={opportunity.buy_funding_interval_hours}h, "
                f"sell interval={opportunity.sell_funding_interval_hours}h"
            ),
        )

    return "unknown", None, "funding data is unavailable"


def _astro_close_decision(
    opportunity: Opportunity,
    config: AstroPlannerConfig,
) -> ClosePositionDecision:
    close_spread_pct = config.default_close_position_floor_pct
    source, net_cycle_pct, funding_note = _funding_signal(opportunity)
    if net_cycle_pct is None:
        note = f"Uses spread-disappearance floor because {funding_note}."
    elif net_cycle_pct >= 0:
        note = (
            f"Uses spread-disappearance floor because {source} funding is favorable or neutral; "
            f"{funding_note}."
        )
    else:
        funding_cost_pct = abs(net_cycle_pct)
        close_spread_pct += funding_cost_pct * config.default_unfavorable_funding_weight
        note = (
            f"Raised above spread-disappearance floor because unfavorable {source} funding "
            f"cost is estimated at {funding_cost_pct:.6f}%; {funding_note}."
        )

    adjusted_for_astro = False
    if close_spread_pct >= opportunity.open_spread_pct:
        close_spread_pct = max(
            opportunity.open_spread_pct - config.default_close_position_buffer_pct,
            0,
        )
        adjusted_for_astro = True
        note = (
            f"{note} Adjusted below openPosition to satisfy Astro's "
            "openPosition > closePosition rule."
        )

    return ClosePositionDecision(
        close_spread_pct=close_spread_pct,
        source=source,
        note=note,
        adjusted_for_astro=adjusted_for_astro,
    )


def _type_blockers(opportunity: Opportunity) -> list[str]:
    if opportunity.type == OpportunityType.SS:
        return ["Astro SDK document does not list SS as a supported pair type."]
    if opportunity.type not in SUPPORTED_ASTRO_TYPES:
        return [f"Astro SDK support for {opportunity.type} is not documented."]
    if opportunity.type == OpportunityType.SF:
        if (
            opportunity.buy_market_type != MarketType.SPOT
            or opportunity.sell_market_type != MarketType.FUTURE
        ):
            return ["SF must map to spot buy leg and future sell leg before submitting to Astro."]
    if opportunity.type == OpportunityType.FF:
        if (
            opportunity.buy_market_type != MarketType.FUTURE
            or opportunity.sell_market_type != MarketType.FUTURE
        ):
            return ["FF must map to future buy leg and future sell leg before submitting to Astro."]
    return []


class AstroPairPlanner:
    def __init__(self, config: AstroPlannerConfig | None = None):
        self.config = config or AstroPlannerConfig()

    def plan(self, opportunity: Opportunity) -> AstroPairPlan:
        blockers = _type_blockers(opportunity)
        if opportunity.open_spread_pct <= 0:
            blockers.append("Open spread must be positive before building an Astro pair.")
        if opportunity.close_spread_pct < 0:
            blockers.append("Close spread is negative; closePosition mapping needs manual review.")

        close_decision = _astro_close_decision(opportunity, self.config)

        assumptions = [
            AstroFieldAssumption(
                field="name",
                source=f"symbol={opportunity.symbol}",
                assumed_value=_base_name(opportunity.symbol),
                note="SDK examples use base asset names such as ETH. Whether names can include exchange/type is unverified.",
            ),
            AstroFieldAssumption(
                field="openPosition",
                source=f"open_spread_pct={opportunity.open_spread_pct}",
                assumed_value=_decimal_position(opportunity.open_spread_pct),
                note="Local spread is percent points; SDK examples look like decimal fractions. This uses percent / 100.",
            ),
            AstroFieldAssumption(
                field="closePosition",
                source=f"close_spread_pct={opportunity.close_spread_pct}",
                assumed_value=_decimal_position(close_decision.close_spread_pct),
                note=close_decision.note,
            ),
            AstroFieldAssumption(
                field="buyEx/sellEx",
                source=f"{opportunity.buy_exchange}->{opportunity.sell_exchange}",
                assumed_value=f"{opportunity.buy_exchange}->{opportunity.sell_exchange}",
                note="Uses local exchange ids directly. Astro exchange id coverage must be verified on your instance.",
            ),
        ]

        warnings = [
            "Dry-run only: this plan does not call Astro add/update/delete and cannot open positions.",
            "Astro SDK add action restarts astro-core; use update or manual pre-created pairs after verification.",
        ]
        if close_decision.adjusted_for_astro:
            warnings.append(
                "closePosition was adjusted below openPosition because the live close spread is not lower than the open spread."
            )
        if close_decision.source == "unknown":
            warnings.append("Funding data was unavailable, so closePosition used the spread-disappearance floor.")

        if blockers:
            return AstroPairPlan(
                opportunity_id=opportunity.id,
                symbol=opportunity.symbol,
                can_submit=False,
                blockers=blockers,
                warnings=warnings,
                assumptions=assumptions,
            )

        pair = {
            "name": _base_name(opportunity.symbol),
            "status": False,
            "type": str(opportunity.type),
            "openPosition": _decimal_position(opportunity.open_spread_pct),
            "disableOpen": True,
            "closePosition": _decimal_position(close_decision.close_spread_pct),
            "disableClose": False,
            "maxTradeUSDT": _compact_number(self.config.default_max_trade_usdt),
            "leverage": _compact_number(self.config.default_leverage),
            "buyEx": opportunity.buy_exchange,
            "sellEx": opportunity.sell_exchange,
            "startTime": "0",
            "minNotional": _compact_number(self.config.default_min_notional),
            "maxNotional": _compact_number(self.config.default_max_notional),
        }
        return AstroPairPlan(
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            can_submit=True,
            pair=pair,
            sdk_payload={"action": "add", "pair": pair},
            blockers=[],
            warnings=warnings,
            assumptions=assumptions,
        )
