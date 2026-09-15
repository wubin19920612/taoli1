from dataclasses import dataclass
from typing import Protocol

from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.services.funding_research.models import FundingResearchCandidate, FundingResearchDepthStats

EPSILON = 1e-9
BookCacheKey = tuple[str, str, str, str, int]


class FundingDepthAdapter(Protocol):
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
class DepthFill:
    depth_usdt: float
    filled_usdt: float
    base_size: float
    vwap: float | None


def _depth_usdt(levels: list[OrderBookLevel]) -> float:
    return sum(level.price * level.size for level in levels)


def _fill(levels: list[OrderBookLevel], target_notional_usdt: float) -> DepthFill:
    remaining = target_notional_usdt
    filled = 0.0
    base_size = 0.0
    for level in levels:
        level_notional = level.price * level.size
        take = min(level_notional, remaining)
        if take <= 0:
            continue
        filled += take
        base_size += take / level.price
        remaining -= take
        if remaining <= EPSILON:
            break
    return DepthFill(
        depth_usdt=_depth_usdt(levels),
        filled_usdt=filled,
        base_size=base_size,
        vwap=filled / base_size if base_size > 0 else None,
    )


def _stats_from_books(
    *,
    long_book: OrderBookSnapshot,
    short_book: OrderBookSnapshot,
    target_notional_usdt: float,
    levels: int,
) -> FundingResearchDepthStats:
    long_fill = _fill(long_book.asks[:levels], target_notional_usdt)
    short_fill = _fill(short_book.bids[:levels], target_notional_usdt)
    depths = [long_fill.depth_usdt, short_fill.depth_usdt]
    executable_basis = None
    slippage_loss = None
    if long_fill.vwap is not None and short_fill.vwap is not None:
        executable_basis = (short_fill.vwap - long_fill.vwap) / long_fill.vwap * 100
        quoted_basis = (
            (short_book.bids[0].price - long_book.asks[0].price) / long_book.asks[0].price * 100
            if long_book.asks and short_book.bids
            else None
        )
        slippage_loss = quoted_basis - executable_basis if quoted_basis is not None else None
    return FundingResearchDepthStats(
        source="orderbook",
        levels=levels,
        long_entry_depth_usdt=long_fill.depth_usdt,
        short_entry_depth_usdt=short_fill.depth_usdt,
        min_entry_depth_usdt=min(depths),
        target_notional_usdt=target_notional_usdt,
        long_entry_vwap=long_fill.vwap,
        short_entry_vwap=short_fill.vwap,
        executable_basis_diff_pct=executable_basis,
        slippage_loss_pct=slippage_loss,
    )


async def orderbook_depth_stats_for_candidate(
    candidate: FundingResearchCandidate,
    markets: list[MarketSnapshot],
    adapters: list[FundingDepthAdapter],
    *,
    target_notional_usdt: float,
    levels: int = 20,
    book_cache: dict[BookCacheKey, OrderBookSnapshot | None] | None = None,
) -> FundingResearchDepthStats | None:
    adapter_by_exchange = {adapter.name.lower(): adapter for adapter in adapters}
    market_by_exchange = {market.exchange.lower(): market for market in markets if market.symbol == candidate.symbol}
    long_market = market_by_exchange.get(candidate.long_exchange.lower())
    short_market = market_by_exchange.get(candidate.short_exchange.lower())
    long_adapter = adapter_by_exchange.get(candidate.long_exchange.lower())
    short_adapter = adapter_by_exchange.get(candidate.short_exchange.lower())
    if long_market is None or short_market is None or long_adapter is None or short_adapter is None:
        return None
    long_book = await _fetch_order_book(
        long_adapter,
        long_market,
        levels=levels,
        book_cache=book_cache,
    )
    short_book = await _fetch_order_book(
        short_adapter,
        short_market,
        levels=levels,
        book_cache=book_cache,
    )
    if long_book is None or short_book is None:
        return None
    return _stats_from_books(
        long_book=long_book,
        short_book=short_book,
        target_notional_usdt=target_notional_usdt,
        levels=levels,
    )


async def _fetch_order_book(
    adapter: FundingDepthAdapter,
    market: MarketSnapshot,
    *,
    levels: int,
    book_cache: dict[BookCacheKey, OrderBookSnapshot | None] | None,
) -> OrderBookSnapshot | None:
    key = (
        adapter.name.lower(),
        market.symbol,
        market.market_type.value,
        market.raw_symbol,
        levels,
    )
    if book_cache is not None and key in book_cache:
        return book_cache[key]
    book = await adapter.fetch_order_book(
        market.symbol,
        market.market_type,
        market.raw_symbol,
        limit=levels,
    )
    if book_cache is not None:
        book_cache[key] = book
    return book
