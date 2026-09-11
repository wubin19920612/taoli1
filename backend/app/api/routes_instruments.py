from collections.abc import Iterable

from fastapi import APIRouter, HTTPException, Request

from app.models.instrument import (
    INSTRUMENT_LOOKUP_EXCHANGES,
    InstrumentExchangeSnapshot,
    InstrumentLookupResult,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.settings import RiskSettings
from app.services.symbol_aliases import SymbolAliasResolver


router = APIRouter(prefix="/instruments")


async def _risk_settings(request: Request) -> RiskSettings:
    repo = getattr(request.app.state, "settings_repo", None)
    if repo is None:
        return RiskSettings()
    return await repo.get_risk_settings()


def _exchange_error(errors: dict[str, str], exchange: str) -> str | None:
    messages = [
        message
        for key, message in errors.items()
        if key.split(":", 1)[0].strip().lower() == exchange
    ]
    return "; ".join(dict.fromkeys(messages)) or None


def _candidate_symbols(
    requested_symbol: str,
    settings: RiskSettings,
) -> list[str]:
    resolver = SymbolAliasResolver(settings.symbol_aliases)
    candidates = [requested_symbol]
    for exchange in INSTRUMENT_LOOKUP_EXCHANGES:
        for market_type in MarketType:
            resolved = resolver.resolve(
                exchange=exchange,
                symbol=requested_symbol,
                market_type=market_type,
            )
            if resolved.canonical_symbol not in candidates:
                candidates.append(resolved.canonical_symbol)
    return candidates


def _best_symbol(candidates: list[str], markets: Iterable[MarketSnapshot]) -> str:
    market_symbols = [market.symbol.upper() for market in markets]
    return max(candidates, key=lambda candidate: market_symbols.count(candidate))


@router.get("/{symbol}", response_model=InstrumentLookupResult)
async def lookup_instrument(symbol: str, request: Request) -> InstrumentLookupResult:
    try:
        requested_symbol = normalize_pair_spread_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="请输入有效标的，例如 BTC 或 BTCUSDT") from exc

    store = request.app.state.snapshot_store
    all_markets = store.get_all_markets()
    settings = await _risk_settings(request)
    canonical_symbol = _best_symbol(
        _candidate_symbols(requested_symbol, settings),
        all_markets,
    )
    matching = [market for market in all_markets if market.symbol.upper() == canonical_symbol]
    latest_by_market: dict[tuple[str, MarketType], MarketSnapshot] = {}
    for market in matching:
        key = (market.exchange.lower(), market.market_type)
        previous = latest_by_market.get(key)
        if previous is None or market.timestamp > previous.timestamp:
            latest_by_market[key] = market

    errors = store.get_exchange_errors()
    exchanges = [
        InstrumentExchangeSnapshot(
            exchange=exchange,
            spot=latest_by_market.get((exchange, MarketType.SPOT)),
            future=latest_by_market.get((exchange, MarketType.FUTURE)),
            error=_exchange_error(errors, exchange),
        )
        for exchange in INSTRUMENT_LOOKUP_EXCHANGES
    ]
    observed_at = max((market.timestamp for market in latest_by_market.values()), default=None)
    base = canonical_symbol.removesuffix("USDT")
    return InstrumentLookupResult(
        query=symbol,
        symbol=canonical_symbol,
        base=base,
        observed_at=observed_at,
        exchange_count=sum(1 for item in exchanges if item.spot is not None or item.future is not None),
        market_count=len(latest_by_market),
        exchanges=exchanges,
    )
