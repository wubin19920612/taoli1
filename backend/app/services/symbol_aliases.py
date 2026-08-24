from dataclasses import dataclass

from app.models.market import MarketSnapshot, MarketType
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadCurrentSnapshot,
    PairSpreadFundingHistoryResult,
    PairSpreadFundingPoint,
    PairSpreadPoint,
    PairSpreadQueryResult,
    PairSpreadValueStats,
    normalize_pair_spread_symbol,
)
from app.models.premium_index import (
    PremiumIndexCurrentSnapshot,
    PremiumIndexPoint,
    PremiumIndexQueryResult,
)
from app.models.settings import SymbolAlias


@dataclass(frozen=True)
class ResolvedSymbolAlias:
    exchange: str
    market_type: MarketType
    requested_symbol: str
    raw_symbol: str
    canonical_symbol: str
    price_multiplier: float


def _alias_key(alias: SymbolAlias) -> tuple[str, str, str | None]:
    return (
        alias.exchange.lower(),
        alias.symbol.upper(),
        alias.market_type.value if alias.market_type is not None else None,
    )


def _base_from_symbol(symbol: str) -> str:
    return symbol.removesuffix("USDT")


class SymbolAliasResolver:
    def __init__(self, aliases: list[SymbolAlias]) -> None:
        self._by_key = {_alias_key(alias): alias for alias in aliases}

    def alias_for(self, market: MarketSnapshot) -> SymbolAlias | None:
        exchange = market.exchange.lower()
        market_type = market.market_type.value
        symbol = market.symbol.upper()
        return self._by_key.get((exchange, symbol, market_type)) or self._by_key.get(
            (exchange, symbol, None)
        )

    def canonical_symbol_for(self, market: MarketSnapshot) -> str:
        alias = self.alias_for(market)
        return alias.canonical_symbol if alias is not None else market.symbol.upper()

    def resolve(
        self,
        *,
        exchange: str,
        symbol: str,
        market_type: MarketType,
    ) -> ResolvedSymbolAlias:
        normalized_exchange = exchange.strip().lower()
        requested_symbol = normalize_pair_spread_symbol(symbol)
        market_key = market_type.value
        direct = self._by_key.get((normalized_exchange, requested_symbol, market_key)) or self._by_key.get(
            (normalized_exchange, requested_symbol, None)
        )
        if direct is not None:
            return ResolvedSymbolAlias(
                exchange=normalized_exchange,
                market_type=market_type,
                requested_symbol=requested_symbol,
                raw_symbol=direct.symbol,
                canonical_symbol=direct.canonical_symbol,
                price_multiplier=direct.price_multiplier,
            )

        for alias in self._by_key.values():
            if alias.exchange != normalized_exchange or alias.canonical_symbol != requested_symbol:
                continue
            if alias.market_type is not None and alias.market_type != market_type:
                continue
            return ResolvedSymbolAlias(
                exchange=normalized_exchange,
                market_type=market_type,
                requested_symbol=requested_symbol,
                raw_symbol=alias.symbol,
                canonical_symbol=alias.canonical_symbol,
                price_multiplier=alias.price_multiplier,
            )

        return ResolvedSymbolAlias(
            exchange=normalized_exchange,
            market_type=market_type,
            requested_symbol=requested_symbol,
            raw_symbol=requested_symbol,
            canonical_symbol=requested_symbol,
            price_multiplier=1.0,
        )


def resolve_symbol_alias(
    aliases: list[SymbolAlias],
    *,
    exchange: str,
    symbol: str,
    market_type: MarketType,
) -> ResolvedSymbolAlias:
    return SymbolAliasResolver(aliases).resolve(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
    )


def _scaled(value: float | None, multiplier: float) -> float | None:
    return value * multiplier if value is not None else None


def _scaled_size(value: float | None, multiplier: float) -> float | None:
    return value / multiplier if value is not None else None


def _spread_pct(spread_abs: float, left_price: float, right_price: float) -> float:
    return spread_abs / ((left_price + right_price) / 2) * 100


def _stats(values: list[float]) -> PairSpreadValueStats:
    if not values:
        return PairSpreadValueStats()
    return PairSpreadValueStats(
        min=min(values),
        max=max(values),
        mean=sum(values) / len(values),
        current=values[-1],
    )


def _scale_current_leg(
    leg: PairSpreadCurrentLeg,
    alias: ResolvedSymbolAlias,
) -> PairSpreadCurrentLeg:
    multiplier = alias.price_multiplier

    def scale(value: float | None) -> float | None:
        return value * multiplier if value is not None else None

    return leg.model_copy(
        update={
            "symbol": alias.canonical_symbol,
            "price": leg.price * multiplier,
            "mark_price": scale(leg.mark_price),
            "index_price": scale(leg.index_price),
            "mid_price": scale(leg.mid_price),
            "last_price": scale(leg.last_price),
        }
    )


def apply_pair_spread_symbol_aliases(
    result: PairSpreadQueryResult,
    *,
    leg1_alias: ResolvedSymbolAlias,
    leg2_alias: ResolvedSymbolAlias,
) -> PairSpreadQueryResult:
    scales_prices = (
        abs(leg1_alias.price_multiplier - 1.0) > 1e-12
        or abs(leg2_alias.price_multiplier - 1.0) > 1e-12
    )
    points = result.points
    if scales_prices:
        points = []
        for point in result.points:
            leg1_close = point.leg1_close * leg1_alias.price_multiplier
            leg2_close = point.leg2_close * leg2_alias.price_multiplier
            spread_abs = leg2_close - leg1_close
            points.append(
                point.model_copy(
                    update={
                        "leg1_close": leg1_close,
                        "leg2_close": leg2_close,
                        "spread_abs": spread_abs,
                        "spread_pct": _spread_pct(spread_abs, leg1_close, leg2_close),
                    }
                )
            )

    current: PairSpreadCurrentSnapshot | None = None
    if result.current is not None:
        leg1 = _scale_current_leg(result.current.leg1, leg1_alias)
        leg2 = _scale_current_leg(result.current.leg2, leg2_alias)
        if scales_prices:
            spread_abs = leg2.price - leg1.price
            current = result.current.model_copy(
                update={
                    "leg1": leg1,
                    "leg2": leg2,
                    "spread_abs": spread_abs,
                    "spread_pct": _spread_pct(spread_abs, leg1.price, leg2.price),
                }
            )
        else:
            current = result.current.model_copy(update={"leg1": leg1, "leg2": leg2})

    funding_history = [
        point.model_copy(update={"symbol": _funding_symbol(point, leg1_alias, leg2_alias)})
        for point in result.funding_history
    ]
    return result.model_copy(
        update={
            "leg1": result.leg1.model_copy(update={"symbol": leg1_alias.canonical_symbol}),
            "leg2": result.leg2.model_copy(update={"symbol": leg2_alias.canonical_symbol}),
            "points": points,
            "spread_abs": _stats([point.spread_abs for point in points])
            if scales_prices
            else result.spread_abs,
            "spread_pct": _stats([point.spread_pct for point in points])
            if scales_prices
            else result.spread_pct,
            "current": current,
            "funding_history": funding_history,
        }
    )


def _funding_symbol(
    point: PairSpreadFundingPoint,
    leg1_alias: ResolvedSymbolAlias,
    leg2_alias: ResolvedSymbolAlias,
) -> str:
    normalized_symbol = normalize_pair_spread_symbol(point.symbol)
    for alias in (leg1_alias, leg2_alias):
        if point.exchange == alias.exchange and normalized_symbol == alias.raw_symbol:
            return alias.canonical_symbol
    return point.symbol


def apply_pair_spread_funding_aliases(
    result: PairSpreadFundingHistoryResult,
    *,
    leg1_alias: ResolvedSymbolAlias,
    leg2_alias: ResolvedSymbolAlias,
) -> PairSpreadFundingHistoryResult:
    return result.model_copy(
        update={
            "leg1": result.leg1.model_copy(update={"symbol": leg1_alias.canonical_symbol}),
            "leg2": result.leg2.model_copy(update={"symbol": leg2_alias.canonical_symbol}),
            "funding_history": [
                point.model_copy(update={"symbol": _funding_symbol(point, leg1_alias, leg2_alias)})
                for point in result.funding_history
            ],
        }
    )


def apply_premium_index_symbol_alias(
    result: PremiumIndexQueryResult,
    *,
    alias: ResolvedSymbolAlias,
) -> PremiumIndexQueryResult:
    multiplier = alias.price_multiplier

    def scale_point(point: PremiumIndexPoint) -> PremiumIndexPoint:
        return point.model_copy(
            update={
                "mark_price": _scaled(point.mark_price, multiplier),
                "index_price": _scaled(point.index_price, multiplier),
            }
        )

    current: PremiumIndexCurrentSnapshot | None = None
    if result.current is not None:
        current = result.current.model_copy(
            update={
                "symbol": alias.canonical_symbol,
                "mark_price": _scaled(result.current.mark_price, multiplier),
                "index_price": _scaled(result.current.index_price, multiplier),
                "mid_price": _scaled(result.current.mid_price, multiplier),
                "last_price": _scaled(result.current.last_price, multiplier),
            }
        )
    return result.model_copy(
        update={
            "symbol": alias.canonical_symbol,
            "current": current,
            "points": [scale_point(point) for point in result.points],
        }
    )


def apply_premium_index_current_alias(
    current: PremiumIndexCurrentSnapshot,
    *,
    alias: ResolvedSymbolAlias,
) -> PremiumIndexCurrentSnapshot:
    multiplier = alias.price_multiplier
    return current.model_copy(
        update={
            "symbol": alias.canonical_symbol,
            "mark_price": _scaled(current.mark_price, multiplier),
            "index_price": _scaled(current.index_price, multiplier),
            "mid_price": _scaled(current.mid_price, multiplier),
            "last_price": _scaled(current.last_price, multiplier),
        }
    )


def apply_symbol_aliases(
    markets: list[MarketSnapshot],
    aliases: list[SymbolAlias],
) -> list[MarketSnapshot]:
    if not aliases:
        return markets

    resolver = SymbolAliasResolver(aliases)
    normalized: list[MarketSnapshot] = []
    for market in markets:
        alias = resolver.alias_for(market)
        if alias is None:
            normalized.append(market)
            continue
        multiplier = alias.price_multiplier
        canonical = alias.canonical_symbol
        if canonical == market.symbol and multiplier == 1:
            normalized.append(market)
            continue
        normalized.append(
            market.model_copy(
                update={
                    "symbol": canonical,
                    "base": _base_from_symbol(canonical),
                    "bid": market.bid * multiplier,
                    "ask": market.ask * multiplier,
                    "bid_size": _scaled_size(market.bid_size, multiplier),
                    "ask_size": _scaled_size(market.ask_size, multiplier),
                    "mark_price": _scaled(market.mark_price, multiplier),
                    "index_price": _scaled(market.index_price, multiplier),
                    "symbol_alias_original_symbol": market.symbol,
                    "symbol_alias_price_multiplier": multiplier,
                }
            )
        )
    return normalized
