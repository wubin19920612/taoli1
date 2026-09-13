from datetime import UTC, datetime
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from app.models.astro import AstroFieldAssumption, AstroPairPlan
from app.models.market import MarketType
from app.models.opportunity import Opportunity, OpportunityType
from app.models.settings import AstroCardSettings
from app.services.funding_edge import current_cycle_funding_edge_pct, next_cycle_funding_edge_pct
from app.services.market_labels import astro_exchange_id
from app.services.market_sessions import is_opportunity_tradable


SUPPORTED_ASTRO_TYPES = {OpportunityType.SF, OpportunityType.FF}


@dataclass(frozen=True)
class AstroPlannerConfig:
    default_max_trade_usdt: float = 10
    default_leverage: int = 1
    default_min_notional: float = 10
    default_max_notional: float = 10
    default_open_enabled: bool = False
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
            default_open_enabled=settings.open_enabled,
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


def _ratio_position(percent_value: float) -> str:
    spread = Decimal(str(percent_value)) / Decimal("100")
    value = ((Decimal("2") + spread) / (Decimal("2") - spread)).quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )
    return f"{value:.6f}"


def _raw_base_name(symbol: str | None) -> str | None:
    if not symbol:
        return None
    normalized = symbol.strip().upper().split(":", 1)[-1]
    for suffix in ("-SWAP", "_SWAP", "/SWAP", "-PERP", "_PERP", "/PERP", "_UMCBL"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    normalized = normalized.replace("-", "").replace("_", "").replace("/", "")
    return _base_name(normalized) or None


def _fr_pair_bases(opportunity: Opportunity) -> tuple[str, str] | None:
    if opportunity.type != OpportunityType.FF:
        return None
    buy_base = _raw_base_name(opportunity.buy_raw_symbol)
    sell_base = _raw_base_name(opportunity.sell_raw_symbol)
    if buy_base is None or sell_base is None or buy_base == sell_base:
        return None
    return buy_base, sell_base


def _hyperliquid_dex(raw_symbol: str | None) -> str | None:
    if not raw_symbol or ":" not in raw_symbol:
        return None
    dex, _ = raw_symbol.split(":", 1)
    return dex.strip().lower() or None


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


def _type_blockers(
    opportunity: Opportunity,
    now: datetime | None = None,
) -> list[str]:
    if not is_opportunity_tradable(opportunity, now or datetime.now(UTC)):
        return ["Bitget 股票现货当前处于休市时间，已阻止提交 Astro。"]
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

    def plan(
        self,
        opportunity: Opportunity,
        now: datetime | None = None,
    ) -> AstroPairPlan:
        blockers = _type_blockers(opportunity, now=now)
        if opportunity.open_spread_pct <= 0:
            blockers.append("Open spread must be positive before building an Astro pair.")
        if opportunity.close_spread_pct < 0:
            blockers.append("Close spread is negative; closePosition mapping needs manual review.")

        close_decision = _astro_close_decision(opportunity, self.config)
        fr_pair_bases = _fr_pair_bases(opportunity)
        pair_type = "FR" if fr_pair_bases is not None else str(opportunity.type)
        pair_name = (
            f"{fr_pair_bases[0]}-{fr_pair_bases[1]}"
            if fr_pair_bases is not None
            else _base_name(opportunity.symbol)
        )
        position_value = _ratio_position if fr_pair_bases is not None else _decimal_position

        buy_astro_exchange = astro_exchange_id(
            opportunity.buy_exchange,
            opportunity.buy_market_type,
            opportunity.buy_raw_symbol,
            opportunity.symbol,
        )
        sell_astro_exchange = astro_exchange_id(
            opportunity.sell_exchange,
            opportunity.sell_market_type,
            opportunity.sell_raw_symbol,
            opportunity.symbol,
        )

        assumptions = [
            AstroFieldAssumption(
                field="name",
                source=f"symbol={opportunity.symbol}",
                assumed_value=pair_name,
                note=(
                    "FR cards use the raw buy/sell base symbols; regular cards use the "
                    "canonical base symbol."
                    if fr_pair_bases is not None
                    else "SDK examples use base asset names such as ETH. Whether names can "
                    "include exchange/type is unverified."
                ),
            ),
            AstroFieldAssumption(
                field="openPosition",
                source=f"open_spread_pct={opportunity.open_spread_pct}",
                assumed_value=position_value(opportunity.open_spread_pct),
                note=(
                    "FR openPosition is the price ratio equivalent of the local executable spread."
                    if fr_pair_bases is not None
                    else "Local spread is percent points; SDK examples look like decimal "
                    "fractions. This uses percent / 100."
                ),
            ),
            AstroFieldAssumption(
                field="closePosition",
                source=f"close_spread_pct={opportunity.close_spread_pct}",
                assumed_value=position_value(close_decision.close_spread_pct),
                note=close_decision.note,
            ),
            AstroFieldAssumption(
                field="buyEx/sellEx",
                source=f"{opportunity.buy_exchange}->{opportunity.sell_exchange}",
                assumed_value=f"{buy_astro_exchange}->{sell_astro_exchange}",
                note=(
                    "Uses Astro exchange ids; Hyperliquid is mapped to hl and Bitget "
                    "RToken stock spot is mapped from bitget to bitgetr."
                ),
            ),
        ]

        warnings = [
            "Dry-run only: this plan does not call Astro add/update/delete and cannot open positions.",
            "Astro SDK add action restarts astro-core; existing pairs are skipped instead of updated.",
        ]
        if close_decision.adjusted_for_astro:
            warnings.append(
                "closePosition was adjusted below openPosition because the live close spread is not lower than the open spread."
            )
        if close_decision.source == "unknown":
            warnings.append(
                "Funding data was unavailable, so closePosition used the "
                "spread-disappearance floor."
            )

        if blockers:
            return AstroPairPlan(
                opportunity_id=opportunity.id,
                symbol=opportunity.symbol,
                source_open_spread_pct=opportunity.open_spread_pct,
                quoted_at=opportunity.last_seen_at,
                can_submit=False,
                blockers=blockers,
                warnings=warnings,
                assumptions=assumptions,
            )

        pair = {
            "name": pair_name,
            "status": self.config.default_open_enabled,
            "type": pair_type,
            "openPosition": position_value(opportunity.open_spread_pct),
            "disableOpen": not self.config.default_open_enabled,
            "closePosition": position_value(close_decision.close_spread_pct),
            "disableClose": False,
            "maxTradeUSDT": _compact_number(self.config.default_max_trade_usdt),
            "leverage": _compact_number(self.config.default_leverage),
            "buyEx": buy_astro_exchange,
            "sellEx": sell_astro_exchange,
            "startTime": "0",
            "minNotional": _compact_number(self.config.default_min_notional),
            "maxNotional": _compact_number(self.config.default_max_notional),
        }
        if fr_pair_bases is not None:
            pair.update({"regressionValue": "1", "rateMultiply": "1"})
            buy_hl_dex = _hyperliquid_dex(opportunity.buy_raw_symbol)
            sell_hl_dex = _hyperliquid_dex(opportunity.sell_raw_symbol)
            if buy_astro_exchange == "hl" and buy_hl_dex is not None:
                pair["aHlDex"] = buy_hl_dex
            if sell_astro_exchange == "hl" and sell_hl_dex is not None:
                pair["bHlDex"] = sell_hl_dex
        return AstroPairPlan(
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            source_open_spread_pct=opportunity.open_spread_pct,
            quoted_at=opportunity.last_seen_at,
            can_submit=True,
            pair=pair,
            sdk_payload={"action": "add", "pair": pair},
            blockers=[],
            warnings=warnings,
            assumptions=assumptions,
        )
