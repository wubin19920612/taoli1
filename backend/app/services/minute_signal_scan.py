from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import httpx


ALPHA_KLINES_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines"
ALPHA_EXCHANGE_INFO_URL = (
    "https://www.binance.com/bapi/defi/v1/public/alpha-trade/get-exchange-info"
)
ALPHA_TOKEN_LIST_URL = (
    "https://www.binance.com/bapi/defi/v1/public/"
    "wallet-direct/buw/wallet/cex/alpha/all/token/list"
)
ALPHA_TICKER_URL = "https://www.binance.com/bapi/defi/v1/public/alpha-trade/ticker"
FUTURES_KLINES_URL = "https://www.binance.com/fapi/v1/klines"
PREMIUM_KLINES_URL = "https://www.binance.com/fapi/v1/premiumIndexKlines"
FUTURES_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUTURES_24HR_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
FUTURES_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
MINUTE_MS = 60_000
SHANGHAI = ZoneInfo("Asia/Shanghai")


class State(str, Enum):
    IDLE = "idle"
    SHOCK = "shock"
    RESET_READY = "reset_ready"
    HOLD = "hold"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class MinuteSignalConfig:
    shock_basis_bps: float = 120.0
    shock_velocity_5m_bps: float = 80.0
    shock_premium_bps: float = -25.0
    shock_premium_low_5m_bps: float = -70.0
    minimum_peak_bps: float = 120.0
    reset_basis_bps: float = 45.0
    reset_compression_ratio: float = 0.65
    reset_premium_low_15m_bps: float = -50.0
    entry_basis_bps: float = 45.0
    entry_premium_bps: float = -5.0
    entry_premium_low_15m_bps: float = -70.0
    take_profit_bps: float = 350.0
    stop_loss_bps: float = -120.0
    maximum_hold_minutes: int = 180
    shock_expiry_minutes: int = 180
    cooldown_minutes: int = 10


@dataclass(frozen=True)
class GlobalMinuteSignalConfig:
    max_symbols: int = 30
    min_volume_24h_usdt: float = 100_000.0
    max_concurrency: int = 8


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _parse_kline(row: list[Any]) -> dict[str, Any] | None:
    if len(row) < 7:
        return None
    open_time = _finite(row[0])
    close_time = _finite(row[6])
    values = [_finite(row[index]) for index in range(1, 5)]
    if open_time is None or close_time is None or any(value is None for value in values):
        return None
    return {
        "open_time": int(open_time),
        "close_time": int(close_time),
        "open": values[0],
        "high": values[1],
        "low": values[2],
        "close": values[3],
    }


class MinuteSignalScanService:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
        config: MinuteSignalConfig | None = None,
    ) -> None:
        self._client = client
        self._owned_client = client is None
        self._timeout_seconds = timeout_seconds
        self.config = config or MinuteSignalConfig()

    async def _get_payload(self, url: str, params: dict[str, Any] | None = None) -> Any:
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=True)
            self._client = client
        response = await client.get(url, params=params or {})
        response.raise_for_status()
        payload = response.json()
        if url.startswith("https://www.binance.com/bapi/defi/"):
            if not isinstance(payload, dict) or payload.get("success") is False:
                raise RuntimeError(f"Binance Alpha response is not successful: {payload!r}")
            payload = payload.get("data")
        return payload

    async def _get(self, url: str, params: dict[str, Any]) -> list[list[Any]]:
        payload = await self._get_payload(url, params)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected kline response from {url}")
        return payload

    @staticmethod
    def _alpha_id(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().upper()
        if not text:
            return None
        return text if text.startswith("ALPHA_") else f"ALPHA_{text}"

    @staticmethod
    def _upper(value: Any) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def _records(payload: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    async def _fetch_alpha_ticker_prices(self, symbols: list[str]) -> dict[str, float]:
        unique_symbols = sorted(
            {
                symbol
                for symbol in (self._upper(item) for item in symbols)
                if symbol
            }
        )
        semaphore = asyncio.Semaphore(12)

        async def fetch(symbol: str) -> tuple[str, float | None]:
            async with semaphore:
                try:
                    payload = await self._get_payload(ALPHA_TICKER_URL, {"symbol": symbol})
                except Exception:  # noqa: BLE001 - keep one stale Alpha ticker from aborting discovery.
                    return symbol, None
                ticker = payload if isinstance(payload, dict) else {}
                price = _finite(ticker.get("lastPrice")) or _finite(ticker.get("weightedAvgPrice"))
                return symbol, price if price is not None and price > 0 else None

        rows = await asyncio.gather(*(fetch(symbol) for symbol in unique_symbols))
        return {symbol: price for symbol, price in rows if price is not None}

    async def _fetch_global_universe(self) -> list[dict[str, Any]]:
        exchange_info, futures_rows, premium_rows, alpha_info, token_rows = await asyncio.gather(
            self._get_payload(FUTURES_EXCHANGE_INFO_URL),
            self._get_payload(FUTURES_24HR_URL),
            self._get_payload(FUTURES_PREMIUM_URL),
            self._get_payload(ALPHA_EXCHANGE_INFO_URL),
            self._get_payload(ALPHA_TOKEN_LIST_URL),
        )
        futures_info_rows = self._records(exchange_info, "symbols")
        futures_ticker_rows = self._records(futures_rows)
        futures_premium_rows = self._records(premium_rows)
        alpha_info_rows = self._records(alpha_info, "symbols")
        token_info_rows = self._records(token_rows, "list", "tokens", "data")
        futures_by_symbol = {
            self._upper(item.get("symbol")): item
            for item in futures_info_rows
            if self._upper(item.get("status")) == "TRADING"
            and self._upper(item.get("contractType")) == "PERPETUAL"
            and self._upper(item.get("quoteAsset")) == "USDT"
        }
        ticker_by_symbol = {
            self._upper(item.get("symbol")): item
            for item in futures_ticker_rows
        }
        premium_by_symbol = {
            self._upper(item.get("symbol")): item
            for item in futures_premium_rows
        }
        alpha_symbols = alpha_info_rows
        alpha_by_id = {
            self._alpha_id(item.get("baseAsset")): item
            for item in alpha_symbols
            if isinstance(item, dict)
            and self._upper(item.get("status")) == "TRADING"
            and self._upper(item.get("quoteAsset")) == "USDT"
            and self._alpha_id(item.get("baseAsset")) is not None
        }
        tokens_by_alpha_id = {
            self._alpha_id(item.get("alphaId")): item
            for item in token_info_rows
            if self._alpha_id(item.get("alphaId")) is not None
        }
        futures_by_base: dict[str, list[dict[str, Any]]] = {}
        for symbol, item in futures_by_symbol.items():
            base = self._upper(item.get("baseAsset"))
            futures_by_base.setdefault(base, []).append(
                {
                    **item,
                    "symbol": symbol,
                }
            )

        mapped_alpha: list[dict[str, Any]] = []
        alpha_symbols_for_pricing: list[str] = []
        for alpha_id, alpha in alpha_by_id.items():
            token = tokens_by_alpha_id.get(alpha_id, {})
            base_asset = self._upper(token.get("symbol"))
            if not base_asset:
                continue
            futures = futures_by_base.get(base_asset, [])
            if not futures:
                continue
            alpha_symbol = self._upper(alpha.get("symbol"))
            if not alpha_symbol:
                continue
            mapped_alpha.append(
                {
                    "alpha_id": alpha_id,
                    "alpha_symbol": alpha_symbol,
                    "base_asset": base_asset,
                    "token_price": _finite(token.get("price")),
                    "futures": futures,
                }
            )
            alpha_symbols_for_pricing.append(alpha_symbol)

        alpha_prices_by_symbol = await self._fetch_alpha_ticker_prices(alpha_symbols_for_pricing)
        universe: list[dict[str, Any]] = []
        for item in mapped_alpha:
            alpha_symbol = item["alpha_symbol"]
            alpha_price = alpha_prices_by_symbol.get(alpha_symbol)
            alpha_price_source = "binance_alpha_ticker_last_price"
            if alpha_price is None or alpha_price <= 0:
                alpha_price = item["token_price"]
                alpha_price_source = "binance_alpha_token_list_price"
            for future in item["futures"]:
                futures_symbol = self._upper(future.get("symbol"))
                ticker = ticker_by_symbol.get(futures_symbol, {})
                premium = premium_by_symbol.get(futures_symbol, {})
                future_price = (
                    _finite(premium.get("markPrice"))
                    or _finite(ticker.get("lastPrice"))
                )
                index_price = _finite(premium.get("indexPrice"))
                volume = _finite(ticker.get("quoteVolume")) or 0.0
                if (
                    not alpha_symbol
                    or alpha_price is None
                    or alpha_price <= 0
                    or future_price is None
                    or future_price <= 0
                    or volume < 0
                ):
                    continue
                basis = (alpha_price - future_price) / alpha_price * 10_000
                premium_bps = (
                    (future_price - index_price) / index_price * 10_000
                    if index_price and index_price > 0
                    else None
                )
                universe.append(
                    {
                        "base_asset": item["base_asset"],
                        "alpha_id": item["alpha_id"],
                        "alpha_symbol": alpha_symbol,
                        "futures_symbol": futures_symbol,
                        "alpha_price": alpha_price,
                        "alpha_price_source": alpha_price_source,
                        "futures_price": future_price,
                        "index_price": index_price,
                        "volume_24h_usdt": volume,
                        "initial_basis_bps": basis,
                        "initial_premium_bps": premium_bps,
                    }
                )
        return universe

    @staticmethod
    def _global_candidate_score(item: dict[str, Any]) -> float:
        basis = item.get("initial_basis_bps")
        premium = item.get("initial_premium_bps")
        volume = item.get("volume_24h_usdt") or 0.0
        positive_basis = max(float(basis or 0), 0.0)
        negative_premium = max(-float(premium or 0), 0.0)
        liquidity_bonus = min(math.log10(max(volume, 1.0)), 10.0)
        return positive_basis * 2.0 + negative_premium + liquidity_bonus

    async def _scan_global_candidate(
        self,
        item: dict[str, Any],
        *,
        hours: int,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await self.scan_symbol(
                    alpha_symbol=item["alpha_symbol"],
                    futures_symbol=item["futures_symbol"],
                    hours=hours,
                )
                latest = result.get("latest") or {}
                events = result.get("events") or []
                event = events[-1] if events else None
                event_priority = {
                    "ENTRY": 5,
                    "SHOCK_ALERT": 4,
                    "TAKE_PROFIT": 3,
                    "STOP_LOSS": 2,
                    "TIME_EXIT": 1,
                }.get(event.get("event_type") if event else "", 0)
                basis = latest.get("basis_bps")
                premium = latest.get("premium_bps")
                score = (
                    event_priority * 10_000
                    + max(float(basis or 0), 0.0) * 2
                    + max(-float(premium or 0), 0.0)
                    + math.log10(max(item["volume_24h_usdt"], 1.0))
                )
                return {
                    **item,
                    "score": score,
                    "event_type": event.get("event_type") if event else None,
                    "signal_time_cst": event.get("signal_time_cst") if event else None,
                    "planned_execution_time_cst": (
                        event.get("planned_execution_time_cst") if event else None
                    ),
                    "reason": event.get("reason") if event else "no_confirmed_signal",
                    "basis_bps": basis,
                    "premium_bps": premium,
                    "basis_peak_60m_bps": latest.get("basis_peak_60m_bps"),
                    "compression_ratio": latest.get("compression_ratio"),
                    "bar_count": result.get("bar_count", 0),
                    "recent_events": events[-5:],
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001 - keep one bad symbol from aborting the scan.
                return {
                    **item,
                    "score": self._global_candidate_score(item),
                    "event_type": None,
                    "signal_time_cst": None,
                    "planned_execution_time_cst": None,
                    "reason": "scan_failed",
                    "basis_bps": None,
                    "premium_bps": None,
                    "basis_peak_60m_bps": None,
                    "compression_ratio": None,
                    "bar_count": 0,
                    "recent_events": [],
                    "error": str(exc),
                }

    async def scan_all(
        self,
        *,
        hours: int = 4,
        max_symbols: int | None = None,
        min_volume_24h_usdt: float | None = None,
    ) -> dict[str, Any]:
        defaults = GlobalMinuteSignalConfig()
        max_symbols = max_symbols or defaults.max_symbols
        min_volume_24h_usdt = (
            defaults.min_volume_24h_usdt
            if min_volume_24h_usdt is None
            else min_volume_24h_usdt
        )
        universe = await self._fetch_global_universe()
        eligible = [
            item
            for item in universe
            if item["volume_24h_usdt"] >= min_volume_24h_usdt
            and (
                item["initial_basis_bps"] >= 0
                or (
                    item["initial_premium_bps"] is not None
                    and item["initial_premium_bps"] <= 0
                )
            )
        ]
        eligible.sort(key=self._global_candidate_score, reverse=True)
        selected = eligible[:max(1, min(max_symbols, 100))]
        semaphore = asyncio.Semaphore(defaults.max_concurrency)
        candidates = await asyncio.gather(
            *(
                self._scan_global_candidate(
                    item,
                    hours=hours,
                    semaphore=semaphore,
                )
                for item in selected
            )
        )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        errors = sum(1 for item in candidates if item["error"])
        signal_count = sum(1 for item in candidates if item["event_type"] is not None)
        return {
            "observed_at": datetime.now(UTC).isoformat(),
            "hours": hours,
            "max_symbols": max_symbols,
            "min_volume_24h_usdt": min_volume_24h_usdt,
            "universe_count": len(universe),
            "eligible_count": len(eligible),
            "scanned_count": len(candidates),
            "signal_count": signal_count,
            "error_count": errors,
            "candidates": candidates,
            "warnings": [
                (
                    "全市场扫描采用两阶段：先用 Binance Alpha ticker 最新价、Futures 成交额、basis 和 premium 发现候选，"
                    "再对候选做 1 分钟历史复核。"
                ),
                (
                    "当前候选池是 Binance Alpha 现货与 Binance Futures 永续的可映射交集，"
                    "不等同于所有交易所的全部币种。"
                ),
                (
                    "信号是下一分钟计划执行边界，不包含真实成交、滑点和盘口容量；"
                    "执行前仍需检查流动性。"
                ),
            ],
        }

    async def _fetch_klines(
        self,
        url: str,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        cursor = start_ms
        rows: dict[int, dict[str, Any]] = {}
        while cursor <= end_ms:
            page = await self._get(
                url,
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            parsed = [item for row in page if (item := _parse_kline(row)) is not None]
            if not parsed:
                break
            for item in parsed:
                if start_ms <= item["open_time"] <= end_ms:
                    rows[item["open_time"]] = item
            page_max = max(item["open_time"] for item in parsed)
            next_cursor = page_max + MINUTE_MS
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return [rows[key] for key in sorted(rows)]

    async def _fetch_rows(
        self,
        alpha_symbol: str,
        futures_symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        spot, futures, premium = await asyncio.gather(
            self._fetch_klines(ALPHA_KLINES_URL, alpha_symbol, start_ms, end_ms),
            self._fetch_klines(FUTURES_KLINES_URL, futures_symbol, start_ms, end_ms),
            self._fetch_klines(PREMIUM_KLINES_URL, futures_symbol, start_ms, end_ms),
        )
        spot_by_time = {item["open_time"]: item for item in spot}
        futures_by_time = {item["open_time"]: item for item in futures}
        premium_by_time = {item["open_time"]: item for item in premium}
        rows: list[dict[str, Any]] = []
        for open_time in sorted(set(spot_by_time) & set(futures_by_time) & set(premium_by_time)):
            s = spot_by_time[open_time]
            f = futures_by_time[open_time]
            p = premium_by_time[open_time]
            if any(item["close_time"] > end_ms for item in (s, f, p)):
                continue
            rows.append(
                {
                    "open_time": open_time,
                    "time_cst": datetime.fromtimestamp(open_time / 1000, UTC)
                    .astimezone(SHANGHAI)
                    .isoformat(timespec="minutes"),
                    "spot_close": s["close"],
                    "fut_close": f["close"],
                    "spot_open": s["open"],
                    "fut_open": f["open"],
                    "premium_low": p["low"],
                    "premium_high": p["high"],
                    "premium_close": p["close"],
                }
            )
        return rows

    @staticmethod
    def _window(
        rows: list[dict[str, Any]],
        now: int,
        minutes: int,
        *,
        include_current: bool,
    ) -> list[dict[str, Any]]:
        start = now - minutes * MINUTE_MS
        return [
            row
            for row in rows
            if start <= row["open_time"] < now or (include_current and row["open_time"] == now)
        ]

    def _features(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        features: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            spot = row["spot_close"]
            spot_open = row["spot_open"]
            basis = (spot - row["fut_close"]) / spot * 10000 if spot > 0 else None
            basis_open = (
                (spot_open - row["fut_open"]) / spot_open * 10000 if spot_open > 0 else None
            )
            previous = rows[index - 1] if index else None
            gap_before = (
                previous is not None
                and row["open_time"] - previous["open_time"] != MINUTE_MS
            )
            previous_5m = next(
                (
                    item
                    for item in reversed(rows[:index])
                    if item["open_time"] == row["open_time"] - 5 * MINUTE_MS
                ),
                None,
            )
            previous_5m_basis = None
            if previous_5m is not None:
                previous_spot = previous_5m["spot_close"]
                previous_5m_basis = (
                    (previous_spot - previous_5m["fut_close"]) / previous_spot * 10000
                    if previous_spot > 0
                    else None
                )
            prior_window = self._window(rows[:index], row["open_time"], 60, include_current=False)
            premium_window_5 = self._window(
                rows[: index + 1],
                row["open_time"],
                5,
                include_current=True,
            )
            premium_window_15 = self._window(
                rows[: index + 1],
                row["open_time"],
                15,
                include_current=True,
            )
            peak = max(
                (
                    (item["spot_close"] - item["fut_close"]) / item["spot_close"] * 10000
                    for item in prior_window
                    if item["spot_close"] > 0
                ),
                default=None,
            )
            low5 = min((item["premium_low"] * 10000 for item in premium_window_5), default=None)
            low15 = min((item["premium_low"] * 10000 for item in premium_window_15), default=None)
            premium = row["premium_close"] * 10000
            drawdown = peak - basis if peak is not None and basis is not None else None
            compression = drawdown / peak if peak and drawdown is not None else None
            features.append(
                {
                    **row,
                    "gap_before": gap_before,
                    "basis_bps": basis,
                    "basis_open_bps": basis_open,
                    "basis_change_5m_bps": (
                        basis - previous_5m_basis
                        if basis is not None and previous_5m_basis is not None
                        else None
                    ),
                    "premium_bps": premium,
                    "premium_low_5m_bps": low5,
                    "premium_low_15m_bps": low15,
                    "basis_peak_60m_bps": peak,
                    "basis_drawdown_bps": drawdown,
                    "compression_ratio": compression,
                }
            )
        return features

    def _event(
        self,
        row: dict[str, Any],
        event_type: str,
        before: str,
        after: str,
        reason: str,
        entry_basis: float | None,
        gain: float | None,
    ) -> dict[str, Any]:
        signal_time = datetime.fromtimestamp(row["open_time"] / 1000, UTC)
        return {
            "event_type": event_type,
            "state_before": before,
            "state_after": after,
            "signal_time_cst": row["time_cst"],
            "planned_execution_time_cst": (
                signal_time + timedelta(minutes=1)
            ).astimezone(SHANGHAI).isoformat(timespec="minutes"),
            "reason": reason,
            "signal_basis_bps": row["basis_bps"],
            "premium_bps": row["premium_bps"],
            "premium_low_5m_bps": row["premium_low_5m_bps"],
            "premium_low_15m_bps": row["premium_low_15m_bps"],
            "basis_peak_60m_bps": row["basis_peak_60m_bps"],
            "basis_drawdown_bps": row["basis_drawdown_bps"],
            "compression_ratio": row["compression_ratio"],
            "signal_entry_basis_bps": entry_basis,
            "signal_basis_gain_bps": gain,
        }

    def _shock(self, row: dict[str, Any]) -> bool:
        c = self.config
        return bool(
            row["basis_bps"] is not None
            and row["basis_change_5m_bps"] is not None
            and row["premium_bps"] <= c.shock_premium_bps
            and row["premium_low_5m_bps"] <= c.shock_premium_low_5m_bps
            and (
                row["basis_bps"] >= c.shock_basis_bps
                or row["basis_change_5m_bps"] >= c.shock_velocity_5m_bps
            )
        )

    def _reset(self, row: dict[str, Any]) -> bool:
        c = self.config
        return bool(
            row["basis_peak_60m_bps"] is not None
            and row["basis_bps"] is not None
            and row["compression_ratio"] is not None
            and row["basis_peak_60m_bps"] >= c.minimum_peak_bps
            and row["basis_bps"] <= c.reset_basis_bps
            and row["compression_ratio"] >= c.reset_compression_ratio
            and row["premium_low_15m_bps"] <= c.reset_premium_low_15m_bps
        )

    def _entry(self, row: dict[str, Any]) -> bool:
        c = self.config
        return bool(
            self._reset(row)
            and row["basis_bps"] <= c.entry_basis_bps
            and row["premium_bps"] <= c.entry_premium_bps
            and row["premium_low_15m_bps"] <= c.entry_premium_low_15m_bps
        )

    def scan(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        state = State.IDLE.value
        shock_time: int | None = None
        entry_time: int | None = None
        entry_basis: float | None = None
        cooldown_until: int | None = None
        events: list[dict[str, Any]] = []
        for row in self._features(rows):
            now = row["open_time"]
            if state == State.COOLDOWN.value:
                if cooldown_until is not None and now < cooldown_until:
                    continue
                state, cooldown_until = State.IDLE.value, None
            if state == State.IDLE.value and self._shock(row):
                before = state
                state, shock_time = State.SHOCK.value, now
                events.append(
                    self._event(
                        row,
                        "SHOCK_ALERT",
                        before,
                        state,
                        "basis_expansion_with_negative_premium",
                        None,
                        None,
                    )
                )
                continue
            if state == State.SHOCK.value:
                if (
                    shock_time is not None
                    and now - shock_time > self.config.shock_expiry_minutes * MINUTE_MS
                ):
                    state, shock_time = State.IDLE.value, None
                    if self._shock(row):
                        before = state
                        state, shock_time = State.SHOCK.value, now
                        events.append(
                            self._event(
                                row,
                                "SHOCK_ALERT",
                                before,
                                state,
                                "new_shock_after_expiry",
                                None,
                                None,
                            )
                        )
                        continue
                if self._entry(row):
                    before = state
                    state, entry_time, entry_basis = (
                        State.HOLD.value,
                        now + MINUTE_MS,
                        row["basis_bps"],
                    )
                    events.append(
                        self._event(
                            row,
                            "ENTRY",
                            before,
                            state,
                            "shock_compressed_and_entry_confirmed",
                            entry_basis,
                            None,
                        )
                    )
                    continue
                if self._reset(row):
                    state = State.RESET_READY.value
            if state == State.RESET_READY.value:
                if self._entry(row):
                    before = state
                    state, entry_time, entry_basis = (
                        State.HOLD.value,
                        now + MINUTE_MS,
                        row["basis_bps"],
                    )
                    events.append(
                        self._event(
                            row,
                            "ENTRY",
                            before,
                            state,
                            "reset_held_and_entry_confirmed",
                            entry_basis,
                            None,
                        )
                    )
                    continue
                if not self._reset(row):
                    state, shock_time = State.SHOCK.value, now
            if state == State.HOLD.value and entry_time is not None and entry_basis is not None:
                basis = row["basis_bps"]
                if basis is None:
                    continue
                gain = basis - entry_basis
                held = int((now - entry_time) / MINUTE_MS)
                exit_type = (
                    ("TAKE_PROFIT", "basis_converged")
                    if gain >= self.config.take_profit_bps
                    else ("STOP_LOSS", "basis_reversed")
                    if gain <= self.config.stop_loss_bps
                    else ("TIME_EXIT", "maximum_hold")
                    if held >= self.config.maximum_hold_minutes
                    else None
                )
                if exit_type is not None:
                    before = state
                    execution_time = now + MINUTE_MS
                    state = State.COOLDOWN.value
                    cooldown_until = execution_time + self.config.cooldown_minutes * MINUTE_MS
                    events.append(
                        self._event(
                            row,
                            exit_type[0],
                            before,
                            state,
                            exit_type[1],
                            entry_basis,
                            gain,
                        )
                    )
                    shock_time = entry_time = entry_basis = None
        return events

    async def scan_symbol(
        self,
        *,
        alpha_symbol: str,
        futures_symbol: str,
        hours: int,
    ) -> dict[str, Any]:
        end = datetime.now(UTC).replace(second=0, microsecond=0)
        start = end - timedelta(hours=hours)
        rows = await self._fetch_rows(
            alpha_symbol,
            futures_symbol,
            _timestamp_ms(start),
            _timestamp_ms(end),
        )
        features = self._features(rows)
        events = self.scan(rows)
        return {
            "alpha_symbol": alpha_symbol,
            "futures_symbol": futures_symbol,
            "hours": hours,
            "observed_at": end.isoformat(),
            "bar_count": len(features),
            "latest": features[-1] if features else None,
            "points": [
                {
                    "time_cst": item["time_cst"],
                    "basis_bps": item["basis_bps"],
                    "premium_bps": item["premium_bps"],
                    "basis_peak_60m_bps": item["basis_peak_60m_bps"],
                    "compression_ratio": item["compression_ratio"],
                }
                for item in features[-240:]
            ],
            "events": events[-100:],
            "warnings": [
                (
                    "仅作为信号提示：planned_execution_time_cst 表示下一分钟执行边界；"
                    "未建模真实成交、滑点和盘口深度。"
                ),
                (
                    "Binance Alpha 现货与 Binance Futures 永续来自不同市场源；"
                    "执行前请复核流动性。"
                ),
            ],
        }

    async def aclose(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None
