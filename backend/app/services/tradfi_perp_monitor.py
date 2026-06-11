from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha1

from app.exchanges.binance import BinanceAdapter
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.models.market import MarketSnapshot, MarketType
from app.models.tradfi_perp_monitor import (
    TradfiPerpDirection,
    TradfiPerpLeg,
    TradfiPerpMonitorPreview,
    TradfiPerpMonitorRow,
    TradfiPerpUnmatchedAsset,
)


HYPERLIQUID_EXCHANGE = "hyperliquid"
BINANCE_EXCHANGE = "binance"

HL_TO_BINANCE_ALIASES: dict[str, str] = {
    "SKHX": "SKHYNIX",
    "SMSN": "SAMSUNG",
    "GOLD": "XAU",
    "GOLDJM": "XAU",
    "SILVER": "XAG",
    "SILVERJM": "XAG",
    "PLATINUM": "XPT",
    "PALLADIUM": "XPD",
    "BRENTOIL": "BZ",
    "OIL": "CL",
    "WTI": "CL",
}

BINANCE_TO_HL_ALIASES = {value: key for key, value in HL_TO_BINANCE_ALIASES.items()}

BINANCE_TRADFI_BASES_FALLBACK = {
    "AAOI",
    "AAPL",
    "AMD",
    "AMAT",
    "AMZN",
    "ANTHROPIC",
    "ARM",
    "ASTS",
    "AVGO",
    "AXTI",
    "BABA",
    "BBX",
    "BE",
    "BRKB",
    "BX",
    "CBRS",
    "CL",
    "COHR",
    "COIN",
    "COPPER",
    "COST",
    "CRCL",
    "CRDO",
    "CRM",
    "CRWD",
    "CRWV",
    "CSCO",
    "DELL",
    "DIS",
    "DRAM",
    "EBAY",
    "EWJ",
    "EWT",
    "EWY",
    "FLNC",
    "GOOGL",
    "HD",
    "HIMS",
    "HOOD",
    "HPE",
    "HYUNDAI",
    "IBM",
    "INTC",
    "IREN",
    "IWM",
    "JPM",
    "LITE",
    "LLY",
    "META",
    "MRVL",
    "MSFT",
    "MSTR",
    "MU",
    "NATGAS",
    "NBIS",
    "NFLX",
    "NOK",
    "NOW",
    "NVDA",
    "NVO",
    "ONDS",
    "OPENAI",
    "ORCL",
    "PAYP",
    "PLTR",
    "QCOM",
    "QNTX",
    "QQQ",
    "RKLB",
    "SAMSUNG",
    "SKHYNIX",
    "SNDK",
    "SOXL",
    "SPCX",
    "SPY",
    "TSLA",
    "TSM",
    "UBER",
    "URNM",
    "USAR",
    "V",
    "WDC",
    "WMT",
    "XAG",
    "XAU",
    "XPD",
    "XPT",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _asset_from_symbol(symbol: str) -> str:
    return symbol.upper().removesuffix("USDT")


def _hl_asset_from_market(market: MarketSnapshot) -> str:
    raw = market.raw_symbol.upper()
    if ":" in raw:
        return raw.split(":", 1)[1]
    return market.base.upper()


def _hl_dex_from_raw(raw_symbol: str) -> str | None:
    return raw_symbol.split(":", 1)[0] if ":" in raw_symbol else None


def _binance_base_for_hl_asset(asset: str) -> str:
    return HL_TO_BINANCE_ALIASES.get(asset.upper(), asset.upper())


def _hl_alias_for_binance_base(base: str) -> str:
    return BINANCE_TO_HL_ALIASES.get(base.upper(), base.upper())


def _mid(snapshot: MarketSnapshot) -> float:
    return (snapshot.bid + snapshot.ask) / 2


def _pick_price(snapshot: MarketSnapshot) -> float:
    return snapshot.mark_price or _mid(snapshot)


def _pct_diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return 2 * (left - right) / (left + right) * 100


def _open_edge_pct(long_ask: float | None, short_bid: float | None) -> float | None:
    if long_ask is None or short_bid is None or long_ask <= 0 or short_bid <= 0:
        return None
    return 2 * (short_bid - long_ask) / (short_bid + long_ask) * 100


def _funding_hourly_pct(snapshot: MarketSnapshot) -> float | None:
    if snapshot.funding_rate_pct is None:
        return None
    interval = snapshot.funding_interval_hours
    if interval is None or interval <= 0:
        return None
    return snapshot.funding_rate_pct / interval


def _funding_edge_hourly(long_leg: MarketSnapshot, short_leg: MarketSnapshot) -> float | None:
    long_hourly = _funding_hourly_pct(long_leg)
    short_hourly = _funding_hourly_pct(short_leg)
    if long_hourly is None or short_hourly is None:
        return None
    return short_hourly - long_hourly


def _min_known_volume(left: MarketSnapshot, right: MarketSnapshot) -> float | None:
    if left.volume_24h_usdt is None or right.volume_24h_usdt is None:
        return None
    return min(left.volume_24h_usdt, right.volume_24h_usdt)


def _leg(snapshot: MarketSnapshot, *, symbol: str | None = None, dex: str | None = None) -> TradfiPerpLeg:
    return TradfiPerpLeg(
        exchange=snapshot.exchange,
        symbol=symbol or snapshot.symbol,
        raw_symbol=snapshot.raw_symbol,
        base_asset=snapshot.base,
        dex=dex,
        bid=snapshot.bid,
        ask=snapshot.ask,
        mid=_mid(snapshot),
        mark_price=snapshot.mark_price,
        index_price=snapshot.index_price,
        funding_rate_pct=snapshot.funding_rate_pct,
        funding_rate_hourly_pct=_funding_hourly_pct(snapshot),
        funding_interval_hours=snapshot.funding_interval_hours,
        funding_next_time=snapshot.funding_next_time,
        volume_24h_usdt=snapshot.volume_24h_usdt,
        timestamp=snapshot.timestamp,
    )


def _row_id(hl: MarketSnapshot, binance: MarketSnapshot) -> str:
    raw = f"{hl.raw_symbol}:{binance.raw_symbol}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _best_direction(
    hl: MarketSnapshot,
    binance: MarketSnapshot,
) -> tuple[TradfiPerpDirection | None, float | None]:
    hl_long = _open_edge_pct(hl.ask, binance.bid)
    binance_long = _open_edge_pct(binance.ask, hl.bid)
    candidates: list[tuple[TradfiPerpDirection, float]] = []
    if hl_long is not None:
        candidates.append(("LONG_HL_SHORT_BINANCE", hl_long))
    if binance_long is not None:
        candidates.append(("LONG_BINANCE_SHORT_HL", binance_long))
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[1])


def _best_funding_direction(
    hl: MarketSnapshot,
    binance: MarketSnapshot,
) -> tuple[TradfiPerpDirection | None, float | None]:
    hl_long = _funding_edge_hourly(hl, binance)
    binance_long = _funding_edge_hourly(binance, hl)
    candidates: list[tuple[TradfiPerpDirection, float]] = []
    if hl_long is not None:
        candidates.append(("LONG_HL_SHORT_BINANCE", hl_long))
    if binance_long is not None:
        candidates.append(("LONG_BINANCE_SHORT_HL", binance_long))
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[1])


def _mark_index_deviation_pct(snapshot: MarketSnapshot) -> float | None:
    return _pct_diff(snapshot.mark_price, snapshot.index_price)


def _risk_labels(
    hl: MarketSnapshot,
    binance: MarketSnapshot,
    *,
    min_volume_24h_usdt: float,
    max_mark_index_deviation_pct: float,
) -> list[str]:
    labels: list[str] = []
    min_volume = _min_known_volume(hl, binance)
    if min_volume is None:
        labels.append("UNKNOWN_VOLUME")
    elif min_volume < min_volume_24h_usdt:
        labels.append("LOW_VOLUME")
    if hl.funding_rate_pct is None or binance.funding_rate_pct is None:
        labels.append("MISSING_FUNDING")
    if hl.funding_interval_hours is None or binance.funding_interval_hours is None:
        labels.append("MISSING_FUNDING_INTERVAL")
    for prefix, snapshot in (("HL", hl), ("BINANCE", binance)):
        deviation = _mark_index_deviation_pct(snapshot)
        if deviation is not None and abs(deviation) >= max_mark_index_deviation_pct:
            labels.append(f"{prefix}_MARK_INDEX_DEVIATION")
    if ":" not in hl.raw_symbol:
        labels.append("HL_MAINNET_PERP")
    return labels


def _build_row(
    hl: MarketSnapshot,
    binance: MarketSnapshot,
    *,
    min_volume_24h_usdt: float,
    max_mark_index_deviation_pct: float,
    observed_at: datetime,
) -> TradfiPerpMonitorRow:
    hl_asset = _hl_asset_from_market(hl)
    binance_base = _asset_from_symbol(binance.raw_symbol)
    hl_mid = _mid(hl)
    binance_mid = _mid(binance)
    best_price_direction, best_open_edge = _best_direction(hl, binance)
    hl_long_funding = _funding_edge_hourly(hl, binance)
    binance_long_funding = _funding_edge_hourly(binance, hl)
    best_funding_direction, best_funding_edge = _best_funding_direction(hl, binance)
    return TradfiPerpMonitorRow(
        id=_row_id(hl, binance),
        asset=hl_asset,
        binance_base_asset=binance_base,
        binance_symbol=binance.raw_symbol,
        hl_dex=_hl_dex_from_raw(hl.raw_symbol) or "main",
        hl_symbol=hl.symbol,
        hl_raw_symbol=hl.raw_symbol,
        hl=_leg(hl, symbol=hl_asset, dex=_hl_dex_from_raw(hl.raw_symbol)),
        binance=_leg(binance, symbol=binance.raw_symbol),
        mid_spread_pct=_pct_diff(hl_mid, binance_mid),
        mark_spread_pct=_pct_diff(_pick_price(hl), _pick_price(binance)),
        index_spread_pct=_pct_diff(hl.index_price, binance.index_price),
        open_long_hl_short_binance_pct=_open_edge_pct(hl.ask, binance.bid),
        open_long_binance_short_hl_pct=_open_edge_pct(binance.ask, hl.bid),
        best_price_direction=best_price_direction,
        best_open_edge_pct=best_open_edge,
        funding_edge_long_hl_short_binance_hourly_pct=hl_long_funding,
        funding_edge_long_binance_short_hl_hourly_pct=binance_long_funding,
        best_funding_direction=best_funding_direction,
        best_funding_edge_hourly_pct=best_funding_edge,
        best_funding_edge_daily_pct=best_funding_edge * 24 if best_funding_edge is not None else None,
        min_volume_24h_usdt=_min_known_volume(hl, binance),
        risk_labels=_risk_labels(
            hl,
            binance,
            min_volume_24h_usdt=min_volume_24h_usdt,
            max_mark_index_deviation_pct=max_mark_index_deviation_pct,
        ),
        observed_at=observed_at,
    )


def _is_hyperliquid_future(snapshot: MarketSnapshot) -> bool:
    return snapshot.exchange.lower() == HYPERLIQUID_EXCHANGE and snapshot.market_type == MarketType.FUTURE


def _is_binance_tradfi_future(
    snapshot: MarketSnapshot,
    tradfi_bases: set[str] | None,
) -> bool:
    if snapshot.exchange.lower() != BINANCE_EXCHANGE or snapshot.market_type != MarketType.FUTURE:
        return False
    base = _asset_from_symbol(snapshot.raw_symbol)
    return base in (tradfi_bases or BINANCE_TRADFI_BASES_FALLBACK)


def _hl_by_binance_base(markets: Iterable[MarketSnapshot]) -> dict[str, list[MarketSnapshot]]:
    rows: dict[str, list[MarketSnapshot]] = defaultdict(list)
    for item in markets:
        if not _is_hyperliquid_future(item):
            continue
        if ":" not in item.raw_symbol:
            continue
        hl_asset = _hl_asset_from_market(item)
        binance_base = _binance_base_for_hl_asset(hl_asset)
        rows[binance_base].append(item)
    return rows


def _binance_by_base(
    markets: Iterable[MarketSnapshot],
    tradfi_bases: set[str] | None,
) -> dict[str, MarketSnapshot]:
    rows: dict[str, MarketSnapshot] = {}
    for item in markets:
        if not _is_binance_tradfi_future(item, tradfi_bases):
            continue
        rows[_asset_from_symbol(item.raw_symbol)] = item
    return rows


def _unmatched_hyperliquid(
    hl_by_base: dict[str, list[MarketSnapshot]],
    binance_by_base: dict[str, MarketSnapshot],
) -> list[TradfiPerpUnmatchedAsset]:
    unmatched: list[TradfiPerpUnmatchedAsset] = []
    for binance_base, items in sorted(hl_by_base.items()):
        if binance_base in binance_by_base:
            continue
        for item in items:
            hl_asset = _hl_asset_from_market(item)
            unmatched.append(
                TradfiPerpUnmatchedAsset(
                    source="hyperliquid",
                    asset=hl_asset,
                    raw_symbol=item.raw_symbol,
                    dex=_hl_dex_from_raw(item.raw_symbol),
                    suggested_alias=binance_base if binance_base != hl_asset else None,
                )
            )
    return unmatched


def _unmatched_binance(
    hl_by_base: dict[str, list[MarketSnapshot]],
    binance_by_base: dict[str, MarketSnapshot],
) -> list[TradfiPerpUnmatchedAsset]:
    unmatched: list[TradfiPerpUnmatchedAsset] = []
    for binance_base, item in sorted(binance_by_base.items()):
        if binance_base in hl_by_base:
            continue
        unmatched.append(
            TradfiPerpUnmatchedAsset(
                source="binance",
                asset=binance_base,
                raw_symbol=item.raw_symbol,
                suggested_alias=_hl_alias_for_binance_base(binance_base),
            )
        )
    return unmatched


def build_tradfi_perp_monitor_preview(
    markets: list[MarketSnapshot],
    *,
    tradfi_bases: set[str] | None = None,
    min_volume_24h_usdt: float = 1_000_000,
    max_mark_index_deviation_pct: float = 2.0,
    max_rows: int = 500,
    now: datetime | None = None,
) -> TradfiPerpMonitorPreview:
    observed_at = now or _now()
    hl_by_base = _hl_by_binance_base(markets)
    binance_by_base = _binance_by_base(markets, tradfi_bases)
    rows: list[TradfiPerpMonitorRow] = []
    for base, hl_rows in hl_by_base.items():
        binance = binance_by_base.get(base)
        if binance is None:
            continue
        for hl in hl_rows:
            rows.append(
                _build_row(
                    hl,
                    binance,
                    min_volume_24h_usdt=min_volume_24h_usdt,
                    max_mark_index_deviation_pct=max_mark_index_deviation_pct,
                    observed_at=observed_at,
                )
            )
    rows.sort(
        key=lambda item: (
            abs(item.best_funding_edge_hourly_pct or 0),
            abs(item.mark_spread_pct or 0),
            item.min_volume_24h_usdt or 0,
        ),
        reverse=True,
    )
    return TradfiPerpMonitorPreview(
        observed_at=observed_at,
        matched_count=len(rows),
        hyperliquid_asset_count=sum(len(items) for items in hl_by_base.values()),
        binance_symbol_count=len(binance_by_base),
        rows=rows[:max_rows],
        unmatched_hyperliquid=_unmatched_hyperliquid(hl_by_base, binance_by_base),
        unmatched_binance=_unmatched_binance(hl_by_base, binance_by_base),
    )


class TradfiPerpLiveFetcher:
    def __init__(
        self,
        binance: BinanceAdapter | None = None,
        hyperliquid: HyperliquidAdapter | None = None,
    ) -> None:
        self.binance = binance or BinanceAdapter()
        self.hyperliquid = hyperliquid or HyperliquidAdapter()

    async def fetch_tradfi_bases(self) -> set[str]:
        payload = await self.binance.get_json(f"{self.binance.futures_base_url}/fapi/v1/exchangeInfo")
        bases: set[str] = set()
        for item in payload.get("symbols", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            if item.get("contractType") != "TRADIFI_PERPETUAL":
                continue
            if item.get("status") != "TRADING":
                continue
            base = str(item.get("baseAsset", "")).strip().upper()
            if base:
                bases.add(base)
        return bases

    async def _fetch_binance_24h_volume_by_symbol(self) -> dict[str, float]:
        payload = await self.binance.get_json(f"{self.binance.futures_base_url}/fapi/v1/ticker/24hr")
        volumes: dict[str, float] = {}
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip().upper()
            if not symbol:
                continue
            try:
                volumes[symbol] = float(item.get("quoteVolume"))
            except (TypeError, ValueError):
                continue
        return volumes

    async def _fetch_binance_futures(self) -> list[MarketSnapshot]:
        futures, volumes = await asyncio.gather(
            self.binance.fetch_future_tickers(),
            self._fetch_binance_24h_volume_by_symbol(),
        )
        enriched: list[MarketSnapshot] = []
        for item in futures:
            volume = volumes.get(item.raw_symbol.upper())
            enriched.append(item.model_copy(update={"volume_24h_usdt": volume}))
        return enriched

    async def _fetch_hyperliquid_hip3_futures(self) -> list[MarketSnapshot]:
        dex_names = await self.hyperliquid._fetch_perp_dex_names()
        if not dex_names:
            return []
        rows = await asyncio.gather(
            *(self.hyperliquid._fetch_perp_dex_rows(name) for name in dex_names),
            return_exceptions=True,
        )
        markets: list[MarketSnapshot] = []
        for result in rows:
            if isinstance(result, Exception):
                continue
            markets.extend(result)
        return [item for item in markets if ":" in item.raw_symbol]

    async def fetch_markets(self) -> tuple[list[MarketSnapshot], set[str]]:
        tradfi_bases = await self.fetch_tradfi_bases()
        binance_futures, hyperliquid_futures = await asyncio.gather(
            self._fetch_binance_futures(),
            self._fetch_hyperliquid_hip3_futures(),
        )
        return [*binance_futures, *hyperliquid_futures], tradfi_bases

    async def aclose(self) -> None:
        await self.binance.client.aclose()
        await self.hyperliquid.client.aclose()


def group_rows_by_asset(rows: Iterable[TradfiPerpMonitorRow]) -> dict[str, list[TradfiPerpMonitorRow]]:
    grouped: dict[str, list[TradfiPerpMonitorRow]] = defaultdict(list)
    for row in rows:
        grouped[row.asset].append(row)
    return dict(grouped)
