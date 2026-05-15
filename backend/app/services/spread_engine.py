from collections import defaultdict
from hashlib import sha1
from typing import Literal

from app.models.market import MarketSnapshot, MarketType
from app.models.opportunity import Opportunity, OpportunityType

Mode = Literal["SF", "FF", "SS"]


def midpoint_spread_pct(buy_leg: MarketSnapshot, sell_leg: MarketSnapshot) -> tuple[float, float]:
    open_spread = 2 * (sell_leg.bid - buy_leg.ask) / (buy_leg.ask + sell_leg.bid) * 100
    close_spread = 2 * (sell_leg.ask - buy_leg.bid) / (buy_leg.bid + sell_leg.ask) * 100
    return open_spread, close_spread


def mark_index_diff_pct(snapshot: MarketSnapshot) -> float | None:
    if not snapshot.mark_price or not snapshot.index_price or snapshot.index_price <= 0:
        return None
    return (snapshot.mark_price - snapshot.index_price) / snapshot.index_price * 100


def opportunity_id(mode: Mode, symbol: str, buy_leg: MarketSnapshot, sell_leg: MarketSnapshot) -> str:
    value = (
        f"{mode}:{symbol}:{buy_leg.exchange}:{buy_leg.market_type}:"
        f"{sell_leg.exchange}:{sell_leg.market_type}"
    )
    return sha1(value.encode("utf-8")).hexdigest()[:16]


def pair_allowed(mode: Mode, first: MarketSnapshot, second: MarketSnapshot) -> bool:
    if mode == "SF":
        return first.market_type == MarketType.SPOT and second.market_type == MarketType.FUTURE
    if mode == "FF":
        return first.market_type == MarketType.FUTURE and second.market_type == MarketType.FUTURE
    if mode == "SS":
        return first.market_type == MarketType.SPOT and second.market_type == MarketType.SPOT
    return False


def orient_pair(mode: Mode, first: MarketSnapshot, second: MarketSnapshot) -> tuple[MarketSnapshot, MarketSnapshot] | None:
    if pair_allowed(mode, first, second):
        return first, second
    if mode in {"FF", "SS"} and pair_allowed(mode, second, first):
        return second, first
    return None


def build_opportunities(
    snapshots: list[MarketSnapshot],
    mode: Mode,
    buy_fee_pct: float = 0.1,
    sell_fee_pct: float = 0.1,
    safety_slippage_pct: float = 0.05,
) -> list[Opportunity]:
    by_symbol: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        by_symbol[snapshot.symbol].append(snapshot)

    opportunities: list[Opportunity] = []
    seen: set[tuple[str, str, str]] = set()
    for symbol, legs in by_symbol.items():
        if len(legs) < 2:
            continue
        for first in legs:
            for second in legs:
                if first == second:
                    continue
                oriented = orient_pair(mode, first, second)
                if oriented is None:
                    continue
                buy_leg, sell_leg = oriented
                pair_key = tuple(
                    sorted(
                        (
                            buy_leg.exchange + buy_leg.market_type,
                            sell_leg.exchange + sell_leg.market_type,
                        )
                    )
                )
                dedupe_key = (mode, symbol, "|".join(pair_key))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                open_spread_pct, close_spread_pct = midpoint_spread_pct(buy_leg, sell_leg)
                if mode in {"FF", "SS"} and open_spread_pct < 0 and close_spread_pct < 0:
                    buy_leg, sell_leg = sell_leg, buy_leg
                    open_spread_pct, close_spread_pct = midpoint_spread_pct(buy_leg, sell_leg)
                if open_spread_pct <= 0:
                    continue

                fee_adjusted = open_spread_pct - buy_fee_pct - sell_fee_pct - safety_slippage_pct
                net_funding = None
                if buy_leg.funding_rate_pct is not None and sell_leg.funding_rate_pct is not None:
                    net_funding = sell_leg.funding_rate_pct - buy_leg.funding_rate_pct

                opportunities.append(
                    Opportunity(
                        id=opportunity_id(mode, symbol, buy_leg, sell_leg),
                        type=OpportunityType(mode),
                        symbol=symbol,
                        buy_exchange=buy_leg.exchange,
                        buy_market_type=buy_leg.market_type,
                        sell_exchange=sell_leg.exchange,
                        sell_market_type=sell_leg.market_type,
                        open_spread_pct=open_spread_pct,
                        close_spread_pct=close_spread_pct,
                        fee_adjusted_open_pct=fee_adjusted,
                        spread_width_pct=abs(close_spread_pct - open_spread_pct),
                        buy_bid=buy_leg.bid,
                        buy_ask=buy_leg.ask,
                        sell_bid=sell_leg.bid,
                        sell_ask=sell_leg.ask,
                        buy_volume_24h_usdt=buy_leg.volume_24h_usdt,
                        sell_volume_24h_usdt=sell_leg.volume_24h_usdt,
                        funding_rate_buy_pct=buy_leg.funding_rate_pct,
                        funding_rate_sell_pct=sell_leg.funding_rate_pct,
                        net_funding_pct=net_funding,
                        mark_index_diff_buy_pct=mark_index_diff_pct(buy_leg),
                        mark_index_diff_sell_pct=mark_index_diff_pct(sell_leg),
                        risk_labels=[],
                        last_seen_at=max(buy_leg.timestamp, sell_leg.timestamp),
                    )
                )
    return sorted(opportunities, key=lambda item: item.open_spread_pct, reverse=True)
