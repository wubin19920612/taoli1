from datetime import UTC, datetime
from itertools import combinations

from app.models.instrument import InstrumentSpreadComparison
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import OpportunityType
from app.services.spread_engine import midpoint_spread_pct


def _mid_price(market: MarketSnapshot) -> float:
    return (market.bid + market.ask) / 2


def _mid_spread_pct(buy_market: MarketSnapshot, sell_market: MarketSnapshot) -> float:
    buy_mid = _mid_price(buy_market)
    sell_mid = _mid_price(sell_market)
    return 2 * (sell_mid - buy_mid) / (buy_mid + sell_mid) * 100


def _opportunity_type(
    buy_market: MarketSnapshot,
    sell_market: MarketSnapshot,
) -> OpportunityType | None:
    if buy_market.market_type == MarketType.FUTURE and sell_market.market_type == MarketType.FUTURE:
        return OpportunityType.FF
    if buy_market.market_type == MarketType.SPOT and sell_market.market_type == MarketType.SPOT:
        return OpportunityType.SS
    if buy_market.market_type == MarketType.SPOT and sell_market.market_type == MarketType.FUTURE:
        return OpportunityType.SF
    return None


def _astro_support(
    opportunity_type: OpportunityType | None,
    executable_spread_pct: float,
) -> tuple[bool, str | None]:
    if executable_spread_pct <= 0:
        return False, "当前买一卖一没有正向可成交价差"
    if opportunity_type in {OpportunityType.SF, OpportunityType.FF}:
        return True, None
    if opportunity_type == OpportunityType.SS:
        return False, "Astro 暂不支持现货对现货卡片"
    return False, "Astro 暂不支持买永续、卖现货的反向现永卡片"


def _comparison_id(buy_market: MarketSnapshot, sell_market: MarketSnapshot) -> str:
    return (
        f"{buy_market.exchange}:{buy_market.market_type.value}->"
        f"{sell_market.exchange}:{sell_market.market_type.value}"
    )


def instrument_market_age_seconds(
    market: MarketSnapshot,
    *,
    now: datetime | None = None,
) -> float:
    current = now or datetime.now(UTC)
    observed_at = market.timestamp
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return max(0.0, (current - observed_at).total_seconds())


def instrument_market_is_fresh(
    market: MarketSnapshot,
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> bool:
    return instrument_market_age_seconds(market, now=now) <= stale_after_seconds


def build_instrument_spreads(
    markets: list[MarketSnapshot],
    *,
    stale_after_seconds: int = 30,
    now: datetime | None = None,
) -> list[InstrumentSpreadComparison]:
    current = now or datetime.now(UTC)
    fresh_markets = [
        market
        for market in markets
        if instrument_market_is_fresh(
            market,
            stale_after_seconds=stale_after_seconds,
            now=current,
        )
    ]
    comparisons: list[InstrumentSpreadComparison] = []
    for first, second in combinations(fresh_markets, 2):
        directions = ((first, second), (second, first))
        buy_market, sell_market = max(
            directions,
            key=lambda pair: midpoint_spread_pct(pair[0], pair[1])[0],
        )
        executable_spread_pct = midpoint_spread_pct(buy_market, sell_market)[0]
        opportunity_type = _opportunity_type(buy_market, sell_market)
        astro_supported, astro_blocker = _astro_support(
            opportunity_type,
            executable_spread_pct,
        )
        comparisons.append(
            InstrumentSpreadComparison(
                id=_comparison_id(buy_market, sell_market),
                buy_exchange=buy_market.exchange,
                buy_market_type=buy_market.market_type,
                buy_ask=buy_market.ask,
                sell_exchange=sell_market.exchange,
                sell_market_type=sell_market.market_type,
                sell_bid=sell_market.bid,
                price_difference=sell_market.bid - buy_market.ask,
                executable_spread_pct=executable_spread_pct,
                mid_spread_pct=_mid_spread_pct(buy_market, sell_market),
                opportunity_type=opportunity_type,
                astro_supported=astro_supported,
                astro_blocker=astro_blocker,
            )
        )
    return sorted(
        comparisons,
        key=lambda item: (-item.executable_spread_pct, item.id),
    )
