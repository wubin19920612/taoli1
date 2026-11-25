"""Request-local projection of the Astro floating panel and its instrument snapshots."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from math import isfinite
from time import monotonic

from app.api.routes_instruments import (
    _astro_pair_bases,
    _best_symbol,
    _candidate_symbols,
    _market_age_seconds,
    _market_identity,
    _matches_hyperliquid_dex,
    _risk_settings,
)
from app.models.instrument import INSTRUMENT_LOOKUP_EXCHANGES
from app.models.market import MarketSnapshot, MarketType
from app.models.mobile import MobileCard, MobileSummary
from app.models.pair_spread import normalize_pair_spread_symbol
from app.services.symbol_aliases import SymbolAliasResolver

logger = logging.getLogger(__name__)

EXCHANGE_LABELS = {
    "aster": "Aster",
    "binance": "Binance",
    "binance_alpha": "Binance Alpha",
    "bitget": "Bitget",
    "bybit": "Bybit",
    "gate": "Gate",
    "hyperliquid": "Hyperliquid",
    "lighter": "Lighter",
    "rh-lighter": "RH Lighter",
    "arcus": "Arcus",
    "okx": "OKX",
}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, float, int)):
        return None
    try:
        parsed = float(value)
    except (ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) else None


def malformed(value):
    # Astro uses "Infinity" as a "never close" sentinel; only unparsable values or NaN are malformed.
    if value in (None, "") or number(value) is not None:
        return False
    try:
        return not (isinstance(value, (str, float, int)) and not isinstance(value, bool)
                    and float(value) in (float("inf"), float("-inf")))
    except (ValueError, OverflowError):
        return True


def positive(value):
    parsed = number(value)
    return parsed if parsed is not None and parsed > 0 else None


def text_field(pair, key, default=""):
    value = pair.get(key)
    return value if isinstance(value, str) and value else default


def spread_pct(left, right):
    return (right - left) / ((left + right) / 2) * 100


def ratio_mode(pair):
    return str(pair.get("type") or "").upper().endswith("R")


def reference(pair):
    return positive(pair.get("regressionValue")) or 1.0


def condition(pair, field):
    value = number(pair.get(field))
    if ratio_mode(pair):
        return spread_pct(value, reference(pair)) if positive(value) else None
    return value * 100 if value is not None else None


@dataclass(frozen=True)
class Leg:
    exchange: str
    market_type: MarketType
    symbol: str
    dex: str


def legs_for(pair):
    bases = _astro_pair_bases(pair)
    if bases is None:
        return ()
    kind = text_field(pair, "type", "FF").upper()
    legs = []
    for index, (base, side, prefix) in enumerate(zip(bases, ("buyEx", "sellEx"), ("a", "b"))):
        venue = text_field(pair, side).strip().lower().removeprefix("gc-")
        exchange = {"hl": "hyperliquid", "bitgetr": "bitget"}.get(venue, venue)
        symbol = base.strip().upper().translate(str.maketrans("", "", "-_/"))
        if not symbol.endswith(("USDT", "USDC", "USD")):
            symbol += "USDT"
        dex = (
            (text_field(pair, prefix + "EffectiveHlDex") or text_field(pair, prefix + "HlDex"))
            .strip()
            .lower()
        )
        legs.append(
            Leg(
                exchange,
                MarketType.SPOT if kind[index : index + 1] == "S" else MarketType.FUTURE,
                symbol,
                dex if exchange == "hyperliquid" else "",
            )
        )
    return tuple(legs)


def market_lookup(request, settings):
    # Index the same collector publication once. The instrument page selects
    # its canonical candidate within the requested DEX, then the latest row per
    # exact raw identity. Avoid building unrelated spreads or fetching routes.
    by_symbol = defaultdict(list)
    for market in request.app.state.snapshot_store.get_all_markets():
        if market.exchange in INSTRUMENT_LOOKUP_EXCHANGES:
            by_symbol[market.symbol.upper()].append(market)
    resolver = SymbolAliasResolver(settings.symbol_aliases)
    validator = getattr(request.app.state, "market_validation", None)
    by_query = {}

    @cache
    def lookup(leg):
        # The instrument query resolves aliases when the card omits its DEX.
        # astroMarket then requires main within that query's returned markets.
        dex = leg.dex or None
        key = (leg.symbol, dex)
        if key not in by_query:
            requested = normalize_pair_spread_symbol(leg.symbol)
            scope = (
                dex
                or resolver.resolve(
                    exchange="hyperliquid", symbol=requested, market_type=MarketType.FUTURE
                ).dex
            )
            candidates = _candidate_symbols(requested, settings, scope)
            rows = [
                row
                for symbol in dict.fromkeys(candidates)
                for row in by_symbol[symbol]
                if _matches_hyperliquid_dex(row, scope)
            ]
            best = _best_symbol(candidates, rows)
            latest = {}
            for row in rows:
                if row.symbol.upper() == best:
                    identity = _market_identity(row)
                    if identity not in latest or row.timestamp > latest[identity].timestamp:
                        latest[identity] = row
            by_query[key] = sorted(latest.values(), key=lambda row: row.raw_symbol)
        requested_dex = (leg.dex or "main") if leg.exchange == "hyperliquid" else ""
        matches = []
        for row in by_query[key]:
            if row.exchange != leg.exchange or row.market_type != leg.market_type:
                continue
            row = validator.annotate(row) if validator else row
            market_dex = row.dex
            if not market_dex and leg.exchange == "hyperliquid":
                prefix, separator, _ = row.raw_symbol.partition(":")
                market_dex = (prefix if separator else "") or "main"
            if (market_dex or "").lower() == requested_dex:
                matches.append(row)
        return matches

    return lookup


def executable_price(market: MarketSnapshot, side: str):
    value = positive(getattr(market, side))
    if value is not None:
        return value
    if market.bid is not None and market.ask is not None:
        return positive((market.bid + market.ask) / 2)
    for fallback in (market.mark_price, market.bid, market.ask):
        if fallback is not None:
            return positive(fallback)
    return None


def difference(left, right):
    return left - right if left is not None and right is not None else None


def build_card(pair, legs, lookup, now, stale_after):
    markets, ages = [], []
    error = "" if legs else "无法从卡片名称识别两腿标的"
    for leg in legs:
        candidates = [(row, _market_age_seconds(row, now)) for row in lookup(leg)]
        # InstrumentMarketCandidate.data_status is live exactly at this cutoff.
        selected = next(((row, age) for row, age in candidates if age <= stale_after), None)
        markets.append(selected[0] if selected else None)
        ages.append(selected[1] if selected else min((age for _, age in candidates), default=None))
        if not selected and not error:
            venue = EXCHANGE_LABELS.get(leg.exchange, leg.exchange)
            market_type = "现货" if leg.market_type == MarketType.SPOT else "永续"
            error = f"未找到 {venue} {market_type}实时行情"

    a, b = markets or (None, None)
    qty_a, qty_b = (abs(number(pair.get(k)) or 0) for k in ("aExPosition", "bExPosition"))
    avg_a, avg_b = (positive(pair.get(k)) for k in ("avgOpenAExPrice", "avgOpenBExPrice"))
    holding = bool(qty_a or qty_b)
    leg_a_notional = qty_a * avg_a if avg_a is not None else None
    leg_b_notional = qty_b * avg_b if avg_b is not None else None
    notional = (leg_a_notional or 0) + (leg_b_notional or 0)
    leg_imbalance = holding and (
        not qty_a or not qty_b or (
            leg_a_notional is not None
            and leg_b_notional is not None
            and abs(leg_a_notional - leg_b_notional) > 0.2 * max(leg_a_notional, leg_b_notional)
        )
    )
    open_now = close_now = profit = None
    if a and b:
        buy_open, sell_open = executable_price(a, "ask"), executable_price(b, "bid")
        buy_close, sell_close = executable_price(a, "bid"), executable_price(b, "ask")
        if None in (buy_open, sell_open, buy_close, sell_close):
            error = "实时行情缺少有效买卖价"
        else:
            scale = 1.0
            if ratio_mode(pair):
                scale = (
                    reference(pair)
                    * (positive(a.symbol_alias_price_multiplier) or 1)
                    / (positive(b.symbol_alias_price_multiplier) or 1)
                )
            open_now = spread_pct(buy_open, sell_open * scale)
            close_now = spread_pct(buy_close, sell_close * scale)
            if holding and (not qty_a or avg_a is not None) and (not qty_b or avg_b is not None):
                fee_rate = (0.002, 0.0013, 0.0005)[
                    sum(leg.exchange == "hyperliquid" for leg in legs)
                ]
                profit = (
                    (buy_close - (avg_a or 0)) * qty_a
                    + ((avg_b or 0) - sell_close) * qty_b
                    - notional * fee_rate
                )

    enabled = bool(pair.get("status"))
    disable_open, disable_close = bool(pair.get("disableOpen")), bool(pair.get("disableClose"))
    state = "running"
    if not enabled:
        state = "paused"
    elif disable_close:
        state = "close_disabled"
    elif disable_open:
        state = "open_disabled"
    open_condition, close_condition = (
        condition(pair, k) for k in ("openPosition", "closePosition")
    )
    open_auto = open_condition is not None and abs(open_condition) < 100
    close_auto = close_condition is not None and abs(close_condition) < 100
    funding_a, funding_b = (row.funding_rate_pct if row else None for row in (a, b))
    # A spot leg has no funding payment. A missing futures rate is unknown.
    cost_a = 0 if a and a.market_type == MarketType.SPOT else funding_a
    income_b = 0 if b and b.market_type == MarketType.SPOT else funding_b
    return MobileCard(
        id=text_field(pair, "id", None),
        name=text_field(pair, "name"),
        type=text_field(pair, "type", "FF"),
        buy_ex=text_field(pair, "buyEx"),
        sell_ex=text_field(pair, "sellEx"),
        symbol_a=legs[0].symbol if legs else None,
        symbol_b=legs[1].symbol if legs else None,
        raw_symbol_a=a.raw_symbol if a else None,
        raw_symbol_b=b.raw_symbol if b else None,
        dex_a=(legs[0].dex or "main") if legs and legs[0].exchange == "hyperliquid" else None,
        dex_b=(legs[1].dex or "main") if legs and legs[1].exchange == "hyperliquid" else None,
        enabled=enabled,
        disable_open=disable_open,
        disable_close=disable_close,
        state=state,
        holding=holding,
        open_spread_now=open_now,
        close_spread_now=close_now,
        open_condition=open_condition,
        close_condition=close_condition,
        open_auto=open_auto,
        close_auto=close_auto,
        gap_to_open=difference(open_now, open_condition) if open_auto else None,
        gap_to_close=difference(close_condition, close_now) if close_auto else None,
        qty_a=qty_a,
        qty_b=qty_b,
        avg_open_a=avg_a,
        avg_open_b=avg_b,
        leg_a_notional=leg_a_notional,
        leg_b_notional=leg_b_notional,
        leg_imbalance=leg_imbalance,
        notional_usdt=notional,
        unrealized_usdt=profit,
        astro_profit=number(pair.get("profit")),
        realized_profit=number(pair.get("realizedProfit"))
        if positive(pair.get("avgCloseAExPrice")) and positive(pair.get("avgCloseBExPrice"))
        else None,
        funding_a=funding_a,
        funding_b=funding_b,
        funding_diff=difference(income_b, cost_a),
        funding_interval_h_a=a.funding_interval_hours if a else None,
        funding_interval_h_b=b.funding_interval_hours if b else None,
        next_funding_at_a=a.funding_next_time if a else None,
        next_funding_at_b=b.funding_next_time if b else None,
        metric_error=error,
        quote_age_s=max(ages) if ages and None not in ages else None,
    )


def _card_order(card):
    if card.holding:
        return (0 if card.leg_imbalance else 1, card.state == "running", 0)
    return (3 if card.state == "paused" else 2, card.gap_to_open is None, -(card.gap_to_open or 0))


async def summary(request, pairs):
    now = datetime.now(UTC)
    # Astro stores its cache timestamp on the monotonic clock.
    cached_at = getattr(request.app.state.astro_client, "_pairs_cached_at", None)
    age = max(0.0, monotonic() - cached_at) if cached_at is not None else None
    settings = await _risk_settings(request)
    lookup = market_lookup(request, settings)
    try:
        pins = await request.app.state.mobile_pins_repo.list()
    except Exception:
        logger.warning("Failed to read mobile card pins", exc_info=True)
        pins = {}
    cards = []
    for pair in pairs:
        try:
            malformed_text = not isinstance(pair.get("name"), str) or any(
                pair.get(key) is not None and not isinstance(pair[key], str)
                for key in ("type", "buyEx", "sellEx", "aEffectiveHlDex", "aHlDex",
                            "bEffectiveHlDex", "bHlDex")
            )
            legs = () if malformed_text else legs_for(pair)
            card = build_card(pair, legs, lookup, now, settings.stale_after_seconds)
            malformed_number = legs and any(
                malformed(pair.get(key))
                for key in ("openPosition", "closePosition", "regressionValue",
                            "aExPosition", "bExPosition", "avgOpenAExPrice", "avgOpenBExPrice",
                            "avgCloseAExPrice", "avgCloseBExPrice", "profit", "realizedProfit")
            )
            if malformed_text or malformed_number:
                card.metric_error = "卡片数据格式异常"
        except Exception:  # noqa: BLE001 - isolate malformed cards from the rest of the summary.
            card = build_card(pair if isinstance(pair, dict) else {}, (), lookup, now,
                              settings.stale_after_seconds)
            card.metric_error = "卡片数据格式异常"
        card.pinned_at = pins.get(card.id)
        cards.append(card)
    cards.sort(key=_card_order)
    stale = (
        age is None
        or age > 30
        or any(
            card.holding and (card.quote_age_s is None or card.quote_age_s > 30) for card in cards
        )
    )
    return MobileSummary(updated_at=now, astro_cache_age_s=age, stale=stale, cards=cards)
