from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from inspect import isawaitable

from app.db.repositories import IndexComponentRepository
from app.exchanges.base import (
    ExchangeAdapter,
    normalize_usdt_symbol,
    parse_datetime_ms,
    parse_datetime_seconds,
    parse_float,
    utc_now,
)
from app.models.index_component import IndexComponent, IndexComponentChange, IndexComponentSnapshot
from app.models.market import MarketSnapshot, MarketType

AlertSender = Callable[[str], None | Awaitable[None]]
logger = logging.getLogger(__name__)


def _component_map(components: list[IndexComponent]) -> dict[str, IndexComponent]:
    return {item.identity(): item for item in components}


def diff_components(
    baseline: IndexComponentSnapshot,
    current: IndexComponentSnapshot,
) -> tuple[list[IndexComponent], list[IndexComponent], list[IndexComponent]]:
    old = _component_map(baseline.components)
    new = _component_map(current.components)

    added = [new[key] for key in sorted(new.keys() - old.keys())]
    removed = [old[key] for key in sorted(old.keys() - new.keys())]
    changed = [
        new[key]
        for key in sorted(old.keys() & new.keys())
        if old[key].model_dump(mode="json", exclude={"price"}, exclude_none=True)
        != new[key].model_dump(mode="json", exclude={"price"}, exclude_none=True)
    ]
    return added, removed, changed


def _component_label(component: IndexComponent) -> str:
    label = component.identity()
    if component.weight is None:
        return label
    return f"{label} {_format_weight(component.weight)}"


def _format_weight(weight: float | None) -> str:
    if weight is None:
        return "-"
    percent = weight * 100
    text = f"{percent:.2f}"
    return f"{text}%"


def _display_source(source: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^a-zA-Z0-9]+", source) if part)


def _display_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(tzinfo=None).replace(microsecond=0) + timedelta(hours=8)


def _change_arrow(old_weight: float | None, new_weight: float | None) -> str:
    old_value = old_weight if old_weight is not None else 0
    new_value = new_weight if new_weight is not None else 0
    if new_value > old_value:
        return "↑→"
    if new_value < old_value:
        return "↓→"
    return "→"


def _component_change_lines(change: IndexComponentChange) -> list[str]:
    old = _component_map(change.old_components)
    new = _component_map(change.new_components)
    lines: list[str] = []
    for identity in sorted(old.keys() | new.keys()):
        old_component = old.get(identity)
        new_component = new.get(identity)
        component = new_component or old_component
        if component is None:
            continue
        old_weight = old_component.weight if old_component else 0
        new_weight = new_component.weight if new_component else 0
        if old_weight == new_weight:
            continue
        lines.append(
            f"• {_display_source(component.source)} ({component.symbol}): "
            f"权重 {_format_weight(old_weight)} {_change_arrow(old_weight, new_weight)} {_format_weight(new_weight)}"
        )
    return lines


def build_index_component_alert_message(change: IndexComponentChange) -> str:
    changed_lines = _component_change_lines(change)
    if not changed_lines:
        changed_lines = ["• 无权重变化"]
    lines = [
        f"⚠️ [{change.exchange.upper()}] {change.symbol} 指数成分变更",
        f"🕘 {_display_time(change.created_at).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "🔁 成分变更:",
        *changed_lines,
    ]
    return "\n".join(lines)


class IndexComponentMonitor:
    def __init__(
        self,
        repository: IndexComponentRepository,
        alert_sender: AlertSender | None = None,
    ) -> None:
        self.repository = repository
        self.alert_sender = alert_sender

    async def process_snapshots(
        self,
        snapshots: list[IndexComponentSnapshot],
    ) -> list[IndexComponentChange]:
        changes: list[IndexComponentChange] = []
        for snapshot in snapshots:
            baseline = await self.repository.get_snapshot(snapshot.exchange, snapshot.symbol)
            if baseline is None:
                await self.repository.upsert_snapshot(snapshot)
                continue

            if baseline.component_hash == snapshot.component_hash:
                await self.repository.upsert_snapshot(snapshot)
                continue

            added, removed, changed = diff_components(baseline, snapshot)
            is_watched = await self._is_symbol_watched(snapshot.symbol)
            change = await self.repository.create_change(
                baseline=baseline,
                current=snapshot,
                added_components=added,
                removed_components=removed,
                changed_components=changed,
                alert_status="pending" if is_watched else "muted",
            )
            if is_watched:
                alert_status = await self._send_alert(change)
                if alert_status != change.alert_status:
                    change = change.model_copy(update={"alert_status": alert_status})
                    await self.repository.update_change_alert_status(change.id, alert_status)
            await self.repository.upsert_snapshot(snapshot)
            changes.append(change)
        return changes

    async def _is_symbol_watched(self, symbol: str) -> bool:
        is_symbol_watched = getattr(self.repository, "is_symbol_watched", None)
        if is_symbol_watched is None:
            return True
        return bool(await is_symbol_watched(symbol))

    async def watched_symbols(self) -> set[str]:
        list_watch_items = getattr(self.repository, "list_watch_items", None)
        if list_watch_items is None:
            return set()
        symbols = {item.symbol for item in await list_watch_items()}
        return {symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()}

    async def _send_alert(self, change: IndexComponentChange) -> str:
        if self.alert_sender is None:
            return "skipped"
        try:
            result = self.alert_sender(build_index_component_alert_message(change))
            if isawaitable(result):
                await result
        except Exception:
            return "failed"
        return "sent"


class EmptyIndexComponentProvider:
    async def fetch_components(
        self,
        markets: list[MarketSnapshot],
    ) -> list[IndexComponentSnapshot]:
        return []


class MultiIndexComponentProvider:
    def __init__(
        self,
        providers: list[object],
    ) -> None:
        self.providers = providers

    async def fetch_components(
        self,
        markets: list[MarketSnapshot],
    ) -> list[IndexComponentSnapshot]:
        snapshots: list[IndexComponentSnapshot] = []
        for provider in self.providers:
            fetch_components = getattr(provider, "fetch_components", None)
            if fetch_components is None:
                continue
            try:
                snapshots.extend(await fetch_components(markets))
            except Exception:
                logger.exception("index component provider failed: %s", provider.__class__.__name__)
        return snapshots


class ExchangeIndexComponentProvider:
    exchange: str
    source: str

    def __init__(
        self,
        client: ExchangeAdapter,
        *,
        max_symbols_per_run: int = 20,
        refresh_interval_seconds: int = 3600,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.max_symbols_per_run = max(1, max_symbols_per_run)
        self.refresh_interval_seconds = max(1, refresh_interval_seconds)
        self._now_fn = now_fn or utc_now
        self._last_attempt_by_symbol: dict[str, datetime] = {}

    async def fetch_components(
        self,
        markets: list[MarketSnapshot],
    ) -> list[IndexComponentSnapshot]:
        snapshots: list[IndexComponentSnapshot] = []
        now = self._now_fn()
        for market in self._due_markets(self._supported_markets(markets), now):
            request_symbol = self._request_symbol(market)
            self._last_attempt_by_symbol[request_symbol] = now
            try:
                payload = await self.client.get_json(self._url(market))
            except Exception as exc:  # noqa: BLE001 - isolate optional component source.
                logger.debug(
                    "%s index components fetch failed for %s: %s",
                    self.exchange,
                    request_symbol,
                    exc,
                )
                continue
            snapshot = self._snapshot_from_payload(market, payload)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def _request_symbol(self, market: MarketSnapshot) -> str:
        return market.raw_symbol

    def _url(self, market: MarketSnapshot) -> str:
        raise NotImplementedError

    def _due_markets(
        self,
        markets: list[MarketSnapshot],
        now: datetime,
    ) -> list[MarketSnapshot]:
        due = [
            market
            for market in markets
            if self._is_due(self._request_symbol(market), now)
        ]
        due.sort(
            key=lambda market: (
                self._last_attempt_by_symbol.get(self._request_symbol(market)) is not None,
                self._last_attempt_by_symbol.get(self._request_symbol(market))
                or datetime.min.replace(tzinfo=now.tzinfo),
                self._request_symbol(market),
            )
        )
        return due[: self.max_symbols_per_run]

    def _is_due(self, raw_symbol: str, now: datetime) -> bool:
        last_attempt = self._last_attempt_by_symbol.get(raw_symbol)
        if last_attempt is None:
            return True
        return (now - last_attempt).total_seconds() >= self.refresh_interval_seconds

    def _supported_markets(
        self,
        markets: list[MarketSnapshot],
    ) -> list[MarketSnapshot]:
        unique: dict[str, MarketSnapshot] = {}
        for market in markets:
            if market.exchange.lower() != self.exchange:
                continue
            if market.market_type != MarketType.FUTURE:
                continue
            if market.index_price is None or market.mark_price is None:
                continue
            unique.setdefault(self._request_symbol(market), market)
        return list(unique.values())

    def _snapshot_from_payload(
        self,
        market: MarketSnapshot,
        payload: object,
    ) -> IndexComponentSnapshot | None:
        raise NotImplementedError


class BinanceIndexComponentProvider(ExchangeIndexComponentProvider):
    # Binance USDⓈ-M Futures public REST:
    # GET /fapi/v1/constituents returns the exchange weights behind an index price.
    exchange = "binance"
    source = "binance-fapi-constituents"
    base_url = "https://fapi.binance.com/fapi/v1/constituents"

    def _url(self, market: MarketSnapshot) -> str:
        return f"{self.base_url}?symbol={self._request_symbol(market)}"

    def _snapshot_from_payload(
        self,
        market: MarketSnapshot,
        payload: object,
    ) -> IndexComponentSnapshot | None:
        if not isinstance(payload, dict):
            return None
        rows = payload.get("constituents")
        if not isinstance(rows, list):
            return None
        components: list[IndexComponent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            source = row.get("exchange")
            symbol = row.get("symbol")
            if not source or not symbol:
                continue
            components.append(
                IndexComponent(
                    source=str(source),
                    symbol=str(symbol),
                    weight=parse_float(row.get("weight")),
                    price=parse_float(row.get("price")),
                    extra=self._extra(row),
                )
            )
        if not components:
            return None
        observed_at = parse_datetime_ms(payload.get("time")) or utc_now()
        return IndexComponentSnapshot.from_components(
            exchange="binance",
            symbol=str(payload.get("symbol") or market.symbol),
            components=components,
            source=self.source,
            observed_at=observed_at,
        )

    def _extra(self, row: dict) -> dict[str, object]:
        known = {"exchange", "symbol", "weight", "price"}
        return {str(key): value for key, value in row.items() if key not in known}


def _okx_index_symbol(market: MarketSnapshot) -> str:
    _, base, quote = normalize_usdt_symbol(market.symbol)
    return f"{base}-{quote}"


def _gate_index_symbol(market: MarketSnapshot) -> str:
    _, base, quote = normalize_usdt_symbol(market.symbol)
    return f"{base}_{quote}"


def _first_row(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    result = payload.get("result")
    if isinstance(result, dict):
        rows = result.get("list")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
    return None


def _component_extra(row: dict, known: set[str]) -> dict[str, object]:
    return {str(key): value for key, value in row.items() if key not in known}


class OKXIndexComponentProvider(ExchangeIndexComponentProvider):
    exchange = "okx"
    source = "okx-index-components"
    base_url = "https://www.okx.com/api/v5/market/index-components"

    def _request_symbol(self, market: MarketSnapshot) -> str:
        return _okx_index_symbol(market)

    def _url(self, market: MarketSnapshot) -> str:
        return f"{self.base_url}?index={self._request_symbol(market)}"

    def _snapshot_from_payload(
        self,
        market: MarketSnapshot,
        payload: object,
    ) -> IndexComponentSnapshot | None:
        row = _first_row(payload)
        if row is None:
            return None
        rows = row.get("components")
        if not isinstance(rows, list):
            return None
        components: list[IndexComponent] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            source = item.get("exch") or item.get("exchange")
            symbol = item.get("symbol") or item.get("instId") or item.get("ccy")
            if not source or not symbol:
                continue
            components.append(
                IndexComponent(
                    source=str(source),
                    symbol=str(symbol),
                    weight=parse_float(item.get("wgt") or item.get("weight")),
                    price=parse_float(item.get("symPx") or item.get("price") or item.get("px")),
                    extra=_component_extra(item, {"exch", "exchange", "symbol", "instId", "ccy", "wgt", "weight", "symPx", "price", "px"}),
                )
            )
        if not components:
            return None
        observed_at = parse_datetime_ms(row.get("ts")) or utc_now()
        return IndexComponentSnapshot.from_components(
            exchange=self.exchange,
            symbol=market.symbol,
            components=components,
            source=self.source,
            observed_at=observed_at,
        )


class BybitIndexComponentProvider(ExchangeIndexComponentProvider):
    exchange = "bybit"
    source = "bybit-index-price-components"
    base_url = "https://api.bybit.com/v5/market/index-price-components"

    def _request_symbol(self, market: MarketSnapshot) -> str:
        return market.symbol

    def _url(self, market: MarketSnapshot) -> str:
        return f"{self.base_url}?indexName={self._request_symbol(market)}"

    def _snapshot_from_payload(
        self,
        market: MarketSnapshot,
        payload: object,
    ) -> IndexComponentSnapshot | None:
        row = _first_row(payload)
        if row is None or not isinstance(payload, dict):
            return None
        rows = row.get("quote") or row.get("components") or row.get("constituents")
        if not isinstance(rows, list):
            return None
        components: list[IndexComponent] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            source = item.get("exchange") or item.get("exch")
            symbol = item.get("quoteSymbol") or item.get("symbol") or item.get("indexSymbol")
            if not source or not symbol:
                continue
            components.append(
                IndexComponent(
                    source=str(source),
                    symbol=str(symbol),
                    weight=parse_float(item.get("weight") or item.get("wgt")),
                    price=parse_float(item.get("price") or item.get("quotePrice") or item.get("px")),
                    extra=_component_extra(item, {"exchange", "exch", "quoteSymbol", "symbol", "indexSymbol", "weight", "wgt", "price", "quotePrice", "px"}),
                )
            )
        if not components:
            return None
        observed_at = parse_datetime_ms(payload.get("time")) or utc_now()
        return IndexComponentSnapshot.from_components(
            exchange=self.exchange,
            symbol=market.symbol,
            components=components,
            source=self.source,
            observed_at=observed_at,
        )


class BitgetIndexComponentProvider(ExchangeIndexComponentProvider):
    exchange = "bitget"
    source = "bitget-index-components"
    base_url = "https://api.bitget.com/api/v3/market/index-components"

    def _request_symbol(self, market: MarketSnapshot) -> str:
        return market.symbol

    def _url(self, market: MarketSnapshot) -> str:
        return f"{self.base_url}?symbol={self._request_symbol(market)}"

    def _snapshot_from_payload(
        self,
        market: MarketSnapshot,
        payload: object,
    ) -> IndexComponentSnapshot | None:
        row = _first_row(payload)
        if row is None:
            return None
        rows = row.get("components") or row.get("constituents")
        if not isinstance(rows, list):
            return None
        components: list[IndexComponent] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            source = item.get("exchange") or item.get("exch")
            symbol = item.get("symbol") or item.get("quoteSymbol")
            if not source or not symbol:
                continue
            components.append(
                IndexComponent(
                    source=str(source),
                    symbol=str(symbol),
                    weight=parse_float(item.get("weight") or item.get("wgt")),
                    price=parse_float(item.get("price") or item.get("indexPrice") or item.get("px")),
                    extra=_component_extra(item, {"exchange", "exch", "symbol", "quoteSymbol", "weight", "wgt", "price", "indexPrice", "px"}),
                )
            )
        if not components:
            return None
        observed_at = parse_datetime_ms(row.get("ts") or row.get("time")) or utc_now()
        return IndexComponentSnapshot.from_components(
            exchange=self.exchange,
            symbol=market.symbol,
            components=components,
            source=self.source,
            observed_at=observed_at,
        )


class GateIndexComponentProvider(ExchangeIndexComponentProvider):
    exchange = "gate"
    source = "gate-index-constituents"
    base_url = "https://api.gateio.ws/api/v4/futures/usdt/index_constituents"

    def _request_symbol(self, market: MarketSnapshot) -> str:
        return _gate_index_symbol(market)

    def _url(self, market: MarketSnapshot) -> str:
        return f"{self.base_url}/{self._request_symbol(market)}"

    def _snapshot_from_payload(
        self,
        market: MarketSnapshot,
        payload: object,
    ) -> IndexComponentSnapshot | None:
        if not isinstance(payload, dict):
            return None
        rows = payload.get("constituents") or payload.get("components")
        if not isinstance(rows, list):
            return None
        components: list[IndexComponent] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            source = item.get("exchange") or item.get("exch")
            symbol = item.get("name") or item.get("symbol") or item.get("contract")
            if not source or not symbol:
                continue
            components.append(
                IndexComponent(
                    source=str(source),
                    symbol=str(symbol),
                    weight=parse_float(item.get("weight") or item.get("wgt")),
                    price=parse_float(item.get("index_price") or item.get("price") or item.get("px")),
                    extra=_component_extra(item, {"exchange", "exch", "name", "symbol", "contract", "weight", "wgt", "index_price", "price", "px"}),
                )
            )
        if not components:
            return None
        observed_at = parse_datetime_seconds(payload.get("timestamp")) or parse_datetime_ms(payload.get("ts")) or utc_now()
        return IndexComponentSnapshot.from_components(
            exchange=self.exchange,
            symbol=market.symbol,
            components=components,
            source=self.source,
            observed_at=observed_at,
        )
