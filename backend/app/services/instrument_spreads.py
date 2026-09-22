from datetime import UTC, datetime
from itertools import combinations

from app.models.instrument import InstrumentSpreadComparison
from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import OpportunityType
from app.services.market_labels import astro_exchange_route_variants
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
    buy_market: MarketSnapshot,
    sell_market: MarketSnapshot,
) -> tuple[bool, str | None]:
    if "lighter" in {buy_market.exchange, sell_market.exchange} and not astro_exchange_route_variants(
        buy_market.exchange, sell_market.exchange
    ):
        return False, "gc-lighter 仅支持与已知 GC 或 Bitget 路由配对"
    if executable_spread_pct <= 0:
        return False, "当前买一卖一没有正向可成交价差"
    if opportunity_type in {OpportunityType.SF, OpportunityType.FF}:
        return True, None
    if opportunity_type == OpportunityType.SS:
        return False, "Astro 暂不支持现货对现货卡片"
    return False, "Astro 暂不支持买永续、卖现货的反向现永卡片"


def _comparison_id(buy_market: MarketSnapshot, sell_market: MarketSnapshot) -> str:
    def identity(market: MarketSnapshot) -> str:
        dex = market.dex or (
            market.raw_symbol.split(":", 1)[0]
            if market.exchange == "hyperliquid" and ":" in market.raw_symbol
            else "main" if market.exchange == "hyperliquid" else ""
        )
        value = (
            f"{market.exchange}:{market.market_type.value}:{dex}:"
            f"{market.raw_symbol}:{market.symbol_alias_price_multiplier:.12g}"
        )
        if market.contract_size_multiplier is not None:
            value = f"{value}:{market.contract_size_multiplier:.12g}"
        return value

    return f"{identity(buy_market)}->{identity(sell_market)}"


def _market_dex(market: MarketSnapshot) -> str | None:
    if market.dex:
        return market.dex
    if market.exchange != "hyperliquid" or market.market_type != MarketType.FUTURE:
        return None
    return market.raw_symbol.split(":", 1)[0] if ":" in market.raw_symbol else "main"


def instrument_market_age_seconds(
    market: MarketSnapshot,
    *,
    now: datetime | None = None,
) -> float:
    current = now or datetime.now(UTC)
    observed_at = market.upstream_timestamp or market.timestamp
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
            buy_market,
            sell_market,
        )
        comparisons.append(
            InstrumentSpreadComparison(
                id=_comparison_id(buy_market, sell_market),
                buy_exchange=buy_market.exchange,
                buy_market_type=buy_market.market_type,
                buy_raw_symbol=buy_market.raw_symbol,
                buy_dex=_market_dex(buy_market),
                buy_price_multiplier=buy_market.symbol_alias_price_multiplier,
                buy_contract_size_multiplier=buy_market.contract_size_multiplier,
                buy_ask=buy_market.ask,
                buy_volume_24h_usdt=buy_market.volume_24h_usdt,
                buy_funding_rate_pct=buy_market.funding_rate_pct,
                buy_funding_interval_hours=buy_market.funding_interval_hours,
                buy_timestamp=buy_market.upstream_timestamp or buy_market.timestamp,
                buy_data_source=buy_market.data_source,
                buy_is_estimated=buy_market.is_estimated,
                sell_exchange=sell_market.exchange,
                sell_market_type=sell_market.market_type,
                sell_raw_symbol=sell_market.raw_symbol,
                sell_dex=_market_dex(sell_market),
                sell_price_multiplier=sell_market.symbol_alias_price_multiplier,
                sell_contract_size_multiplier=sell_market.contract_size_multiplier,
                sell_bid=sell_market.bid,
                sell_volume_24h_usdt=sell_market.volume_24h_usdt,
                sell_funding_rate_pct=sell_market.funding_rate_pct,
                sell_funding_interval_hours=sell_market.funding_interval_hours,
                sell_timestamp=sell_market.upstream_timestamp or sell_market.timestamp,
                sell_data_source=sell_market.data_source,
                sell_is_estimated=sell_market.is_estimated,
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
