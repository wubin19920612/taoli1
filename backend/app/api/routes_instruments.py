import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.models.instrument import (
    INSTRUMENT_LOOKUP_EXCHANGES,
    InstrumentExchangeSnapshot,
    InstrumentLookupResult,
    InstrumentMarketCandidate,
    InstrumentRouteStatus,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.pair_spread import normalize_hyperliquid_dex, normalize_pair_spread_symbol
from app.models.settings import RiskSettings
from app.services.data_filters import ignored_exchange_set
from app.services.instrument_spreads import build_instrument_spreads
from app.services.symbol_aliases import SymbolAliasResolver, canonical_query_symbol

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


def _market_error(errors: dict[str, str], market: MarketSnapshot) -> str | None:
    exchange = market.exchange.lower()
    exact_key = f"{exchange}:{market.market_type.value}:{market.raw_symbol}"
    exact = errors.get(exact_key)
    if exact:
        return exact
    general = [
        message
        for key, message in errors.items()
        if key in {exchange, f"{exchange}:{market.market_type.value}"}
    ]
    return "; ".join(dict.fromkeys(general)) or None


def _candidate_symbols(
    requested_symbol: str,
    settings: RiskSettings,
    hyperliquid_dex: str | None = None,
) -> list[str]:
    resolver = SymbolAliasResolver(settings.symbol_aliases)
    candidates = [requested_symbol, canonical_query_symbol(requested_symbol)]
    for exchange in INSTRUMENT_LOOKUP_EXCHANGES:
        for market_type in MarketType:
            resolved = resolver.resolve(
                exchange=exchange,
                symbol=requested_symbol,
                market_type=market_type,
                dex=hyperliquid_dex if exchange == "hyperliquid" else None,
            )
            if resolved.canonical_symbol not in candidates:
                candidates.append(resolved.canonical_symbol)
    return candidates


def _best_symbol(candidates: list[str], markets: Iterable[MarketSnapshot]) -> str:
    market_symbols = [market.symbol.upper() for market in markets]
    return max(candidates, key=lambda candidate: market_symbols.count(candidate))


def _market_hyperliquid_dex(market: MarketSnapshot) -> str:
    if market.dex:
        return market.dex
    if ":" not in market.raw_symbol:
        return "main"
    return market.raw_symbol.split(":", 1)[0].strip().lower() or "main"


def _matches_hyperliquid_dex(market: MarketSnapshot, dex: str | None) -> bool:
    return (
        dex is None
        or market.exchange.lower() != "hyperliquid"
        or _market_hyperliquid_dex(market) == dex
    )


def _market_identity(market: MarketSnapshot) -> tuple[str, MarketType, str, str]:
    return (
        market.exchange.lower(),
        market.market_type,
        market.raw_symbol,
        _market_hyperliquid_dex(market) if market.exchange.lower() == "hyperliquid" else "",
    )


def _market_age_seconds(market: MarketSnapshot, now: datetime) -> float:
    observed_at = market.upstream_timestamp or market.timestamp
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return max(0.0, (now - observed_at).total_seconds())


def _astro_pair_bases(pair: dict[str, Any]) -> tuple[str, str] | None:
    name = str(pair.get("name") or "").strip()
    if not name:
        return None
    pair_type = str(pair.get("type") or "FF").upper()
    if not pair_type.endswith("R"):
        return name, name
    left, separator, right = name.partition("-")
    if not separator or not left.strip() or not right.strip():
        return None
    return left.strip(), right.strip()


def _astro_route_exchange(route: str) -> str | None:
    normalized = route.strip().lower()
    if normalized == "rh-lighter":
        return None
    if normalized.startswith("gc-"):
        normalized = normalized.removeprefix("gc-")
    return {"hl": "hyperliquid", "hyper": "hyperliquid", "bitgetr": "bitget"}.get(
        normalized,
        normalized,
    )


def _route_matches_market(
    market: MarketSnapshot,
    *,
    exchange: str,
    market_type: MarketType,
    canonical_symbol: str,
    dex: str | None,
) -> bool:
    return (
        market.exchange.lower() == exchange
        and market.market_type == market_type
        and market.symbol.upper() == canonical_symbol
        and _matches_hyperliquid_dex(market, dex)
    )


async def _astro_route_statuses(
    request: Request,
    canonical_symbol: str,
    markets: list[MarketSnapshot],
) -> tuple[list[InstrumentRouteStatus], dict[str, str]]:
    client = getattr(request.app.state, "astro_client", None)
    if client is None:
        return [], {"astro": "Astro SDK 未配置，无法读取路由证据"}
    config = getattr(client, "config", None)
    if config is not None and not getattr(config, "configured", False):
        return [], {"astro": "Astro SDK 未配置，无法读取路由证据"}
    now = datetime.now(UTC)
    cached = getattr(request.app.state, "instrument_astro_route_cache", None)
    payload: Any = None
    if (
        isinstance(cached, tuple)
        and len(cached) == 2
        and isinstance(cached[0], datetime)
        and (now - cached[0]).total_seconds() <= 10
    ):
        payload = cached[1]
    try:
        if payload is None:
            payload = await asyncio.wait_for(client.list_pairs(), timeout=5)
            request.app.state.instrument_astro_route_cache = (now, payload)
    except Exception as exc:  # noqa: BLE001 - route evidence must not block market lookup.
        return [], {"astro": f"Astro 路由读取失败: {exc}"}
    pairs = payload if isinstance(payload, list) else []
    statuses: list[InstrumentRouteStatus] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        bases = _astro_pair_bases(pair)
        if bases is None:
            continue
        pair_type = str(pair.get("type") or "FF").upper()
        routes = (str(pair.get("buyEx") or ""), str(pair.get("sellEx") or ""))
        dexes = (
            str(pair.get("aEffectiveHlDex") or pair.get("aHlDex") or "").strip().lower()
            or None,
            str(pair.get("bEffectiveHlDex") or pair.get("bHlDex") or "").strip().lower()
            or None,
        )
        for index, side in enumerate(("buy", "sell")):
            astro_raw_symbol = normalize_pair_spread_symbol(bases[index])
            leg_symbol = canonical_query_symbol(astro_raw_symbol)
            if leg_symbol != canonical_symbol:
                continue
            route = routes[index].strip().lower()
            if not route:
                continue
            market_type = (
                MarketType.SPOT
                if len(pair_type) > index and pair_type[index] == "S"
                else MarketType.FUTURE
            )
            exchange = _astro_route_exchange(route)
            dex = dexes[index] if exchange == "hyperliquid" else None
            match = next(
                (
                    market
                    for market in markets
                    if exchange is not None
                    and _route_matches_market(
                        market,
                        exchange=exchange,
                        market_type=market_type,
                        canonical_symbol=canonical_symbol,
                        dex=dex,
                    )
                ),
                None,
            )
            if exchange is None:
                status = "route_only"
                reason = (
                    "Astro 返回了 RH-Lighter 路由，但 Lighter 公开市场与资金接口没有独立 "
                    "rh-lighter 行情源；不复制 Lighter 行情。"
                )
            elif match is None:
                status = "market_missing"
                reason = "Astro 路由已发现，但统一行情快照中没有匹配的精确实时市场。"
            else:
                status = "live_market"
                reason = "已匹配真实公开行情；Astro 卡片本身不作为价格来源。"
            dedupe_key = (
                str(pair.get("id") or ""),
                side,
                route,
                astro_raw_symbol,
                dex or "",
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            statuses.append(
                InstrumentRouteStatus(
                    card_id=str(pair.get("id")) if pair.get("id") is not None else None,
                    card_name=str(pair.get("name") or ""),
                    side=side,
                    route=route,
                    exchange=exchange,
                    market_type=market_type,
                    astro_raw_symbol=astro_raw_symbol,
                    canonical_symbol=canonical_symbol,
                    dex=dex,
                    counterparty_route=routes[1 - index].strip().lower(),
                    status=status,
                    live_data_supported=match is not None,
                    matched_raw_symbol=match.raw_symbol if match is not None else None,
                    reason=reason,
                )
            )
    return statuses, {}


@router.get("/{symbol}", response_model=InstrumentLookupResult)
async def lookup_instrument(
    symbol: str,
    request: Request,
    dex: str | None = None,
) -> InstrumentLookupResult:
    try:
        requested_symbol = normalize_pair_spread_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="请输入有效标的，例如 BTC 或 BTCUSDT") from exc

    settings = await _risk_settings(request)
    requested_hyperliquid_dex = normalize_hyperliquid_dex(dex)
    resolved_hyperliquid = SymbolAliasResolver(settings.symbol_aliases).resolve(
        exchange="hyperliquid",
        symbol=requested_symbol,
        market_type=MarketType.FUTURE,
        dex=requested_hyperliquid_dex,
    )
    hyperliquid_dex = requested_hyperliquid_dex or resolved_hyperliquid.dex
    store = request.app.state.snapshot_store
    allowed_exchanges = set(INSTRUMENT_LOOKUP_EXCHANGES)
    all_markets = [
        market
        for market in store.get_all_markets()
        if market.exchange.lower() in allowed_exchanges
        and _matches_hyperliquid_dex(market, hyperliquid_dex)
    ]
    canonical_symbol = _best_symbol(
        _candidate_symbols(requested_symbol, settings, hyperliquid_dex),
        all_markets,
    )
    matching = [market for market in all_markets if market.symbol.upper() == canonical_symbol]
    latest_by_identity: dict[tuple[str, MarketType, str, str], MarketSnapshot] = {}
    for market in matching:
        key = _market_identity(market)
        previous = latest_by_identity.get(key)
        if previous is None or market.timestamp > previous.timestamp:
            latest_by_identity[key] = market
    exact_markets = list(latest_by_identity.values())
    latest_by_market: dict[tuple[str, MarketType], MarketSnapshot] = {}
    for market in exact_markets:
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
    observed_at = max((market.timestamp for market in exact_markets), default=None)
    ignored_exchanges = ignored_exchange_set(settings)
    spread_markets = [
        market
        for market in exact_markets
        if market.exchange.lower() not in ignored_exchanges
    ]
    now = datetime.now(UTC)
    instrument_markets = [
        InstrumentMarketCandidate(
            **market.model_dump(),
            data_status=(
                "live"
                if _market_age_seconds(market, now) <= settings.stale_after_seconds
                else "stale"
            ),
            age_seconds=_market_age_seconds(market, now),
            stale_after_seconds=settings.stale_after_seconds,
            error=_market_error(errors, market),
        )
        for market in sorted(
            exact_markets,
            key=lambda item: (
                item.exchange.lower(),
                item.market_type.value,
                _market_hyperliquid_dex(item)
                if item.exchange.lower() == "hyperliquid"
                else "",
                item.raw_symbol,
            ),
        )
    ]
    astro_routes, route_errors = await _astro_route_statuses(
        request,
        canonical_symbol,
        exact_markets,
    )
    base = canonical_symbol.removesuffix("USDT")
    return InstrumentLookupResult(
        query=symbol,
        symbol=canonical_symbol,
        base=base,
        observed_at=observed_at,
        exchange_count=sum(1 for item in exchanges if item.spot is not None or item.future is not None),
        market_count=len(exact_markets),
        markets=instrument_markets,
        astro_routes=astro_routes,
        route_errors=route_errors,
        exchanges=exchanges,
        spreads=build_instrument_spreads(
            spread_markets,
            stale_after_seconds=settings.stale_after_seconds,
        ),
    )
