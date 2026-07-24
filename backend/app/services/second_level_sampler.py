from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from itertools import combinations
from math import isfinite
from typing import Any

import aiosqlite
import httpx

from app.exchanges.base import DEFAULT_HEADERS, DEFAULT_LIMITS, parse_float
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.second_level_sampling import (
    SecondLevelIndexComponentSample,
    SecondLevelIndexComponentSignal,
    SecondLevelMarketSample,
    SecondLevelPairSpreadSnapshot,
    SecondLevelSamplingConfig,
    SecondLevelSamplingStatus,
)

logger = logging.getLogger(__name__)

SETTINGS_KEY = "second_level_sampling"
FETCH_TIMEOUT = httpx.Timeout(8.0, connect=2.5, read=6.0, write=5.0, pool=5.0)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _compact_symbol(symbol: str) -> str:
    return normalize_pair_spread_symbol(symbol)


def _base_quote(symbol: str) -> tuple[str, str]:
    compact = _compact_symbol(symbol)
    return compact.removesuffix("USDT"), "USDT"


def _okx_spot(symbol: str) -> str:
    base, quote = _base_quote(symbol)
    return f"{base}-{quote}"


def _okx_swap(symbol: str) -> str:
    return f"{_okx_spot(symbol)}-SWAP"


def _gate_symbol(symbol: str) -> str:
    base, quote = _base_quote(symbol)
    return f"{base}_{quote}"


def _positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return value


def _mid(bid: float | None, ask: float | None) -> float | None:
    bid = _positive(bid)
    ask = _positive(ask)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _pct_diff(left: float | None, right: float | None) -> float | None:
    left = _positive(left)
    right = _positive(right)
    if left is None or right is None:
        return None
    return (left / right - 1) * 100


def _ratio_to_pct(value: float | None) -> float | None:
    if value is None or not isfinite(value):
        return None
    return value * 100 if abs(value) <= 2 else value


def _weight_pct(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value < 0:
        return None
    return value * 100 if value <= 1 else value


def _weight_fraction(weight_pct: float | None) -> float | None:
    if weight_pct is None or not isfinite(weight_pct) or weight_pct < 0:
        return None
    return weight_pct / 100


def _contribution_price(weight_pct: float | None, price: float | None) -> float | None:
    fraction = _weight_fraction(weight_pct)
    price = _positive(price)
    if fraction is None or price is None:
        return None
    return fraction * price


def _normalize_component_source(value: object) -> str:
    text = str(value or "").strip().lower()
    return text or "unknown"


def _normalize_component_symbol(value: object, fallback: str) -> str:
    text = str(value or "").strip().upper()
    return text or fallback


def _error_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__


def _leg_label(label: str) -> str:
    if label == "spot":
        return "现货"
    if label == "index_component":
        return "指数组成"
    return "合约"


def _compact_error_detail(text: str) -> str:
    text = " ".join(text.strip().split())
    return text[:117] + "..." if len(text) > 120 else text


def _http_status_error_message(exc: httpx.HTTPStatusError, label: str) -> str:
    status_code = exc.response.status_code
    detail = ""
    with suppress(Exception):
        payload = exc.response.json()
        if isinstance(payload, dict):
            for key in ("msg", "retMsg", "message", "detail", "code"):
                value = payload.get(key)
                if value is not None and str(value).strip():
                    detail = str(value)
                    break
    if not detail:
        detail = exc.response.text
    detail = _compact_error_detail(detail) if detail else ""
    suffix = f"：{detail}" if detail else ""
    return f"{_leg_label(label)}行情不可用（HTTP {status_code}{suffix}）"


def _missing_leg_message(label: str) -> str:
    leg = _leg_label(label)
    return f"{leg}行情不可用：交易所未返回该标的{leg}价格"


def _first_row(rows: Any) -> dict[str, Any]:
    if isinstance(rows, list) and rows:
        first = rows[0]
        return first if isinstance(first, dict) else {}
    if isinstance(rows, dict):
        return rows
    return {}


def _payload_data_row(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, list):
        return _first_row(data)
    if isinstance(data, dict):
        return data
    return {}


def _component_payload_row(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else {}
    if isinstance(data, dict):
        return data
    result = payload.get("result")
    if isinstance(result, dict):
        rows = result.get("list")
        if isinstance(rows, list) and rows:
            return rows[0] if isinstance(rows[0], dict) else {}
        return result
    return {}


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _component_from_row(row: dict[str, Any], *, fallback_symbol: str) -> dict[str, Any] | None:
    source = _first_value(row, "exchange", "exch", "source")
    symbol = _first_value(row, "quoteSymbol", "symbol", "indexSymbol", "spotPair", "instId", "name", "contract")
    if source is None:
        return None
    weight_pct = _weight_pct(parse_float(_first_value(row, "weight", "wgt", "weightPct", "weightRate")))
    price = _positive(
        parse_float(
            _first_value(
                row,
                "price",
                "quotePrice",
                "symPx",
                "indexPrice",
                "equivalentPrice",
                "px",
                "last",
            )
        )
    )
    return {
        "source": _normalize_component_source(source),
        "symbol": _normalize_component_symbol(symbol, fallback_symbol),
        "weight_pct": weight_pct,
        "price": price,
    }


def _leg(
    *,
    raw_symbol: str | None = None,
    bid: float | None = None,
    ask: float | None = None,
    last: float | None = None,
    mark: float | None = None,
    index: float | None = None,
    funding_rate_pct: float | None = None,
) -> dict[str, float | str | None]:
    mid = _mid(bid, ask)
    return {
        "raw_symbol": raw_symbol,
        "bid": _positive(bid),
        "ask": _positive(ask),
        "mid": mid,
        "last": _positive(last),
        "mark": _positive(mark),
        "index": _positive(index),
        "mark_premium_pct": _pct_diff(mark, index),
        "mid_premium_pct": _pct_diff(mid, index),
        "funding_rate_pct": funding_rate_pct,
    }


class SecondLevelSamplingRepository:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def get_config(self) -> SecondLevelSamplingConfig:
        cursor = await self.db.execute(
            "SELECT payload FROM app_settings WHERE key = ?",
            (SETTINGS_KEY,),
        )
        row = await cursor.fetchone()
        if row is None:
            return SecondLevelSamplingConfig()
        return SecondLevelSamplingConfig.model_validate(json.loads(row["payload"]))

    async def set_config(self, config: SecondLevelSamplingConfig) -> SecondLevelSamplingConfig:
        await self.db.execute(
            """
            INSERT INTO app_settings (key, payload)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET payload = excluded.payload
            """,
            (SETTINGS_KEY, config.model_dump_json()),
        )
        await self.db.commit()
        return config

    async def insert_samples(self, samples: list[SecondLevelMarketSample]) -> None:
        if not samples:
            return
        await self.db.executemany(
            """
            INSERT INTO second_level_market_samples (
              observed_at, exchange, symbol, status,
              spot_bid, spot_ask, spot_mid, spot_last,
              future_bid, future_ask, future_mid, future_last,
              mark_price, index_price, mark_premium_pct, mid_premium_pct,
              funding_rate_pct, raw_spot_symbol, raw_future_symbol,
              latency_ms, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.observed_at.isoformat(),
                    item.exchange,
                    item.symbol,
                    item.status,
                    item.spot_bid,
                    item.spot_ask,
                    item.spot_mid,
                    item.spot_last,
                    item.future_bid,
                    item.future_ask,
                    item.future_mid,
                    item.future_last,
                    item.mark_price,
                    item.index_price,
                    item.mark_premium_pct,
                    item.mid_premium_pct,
                    item.funding_rate_pct,
                    item.raw_spot_symbol,
                    item.raw_future_symbol,
                    item.latency_ms,
                    item.error,
                )
                for item in samples
            ],
        )
        await self.db.commit()

    async def insert_component_samples(self, samples: list[SecondLevelIndexComponentSample]) -> None:
        if not samples:
            return
        await self.db.executemany(
            """
            INSERT INTO second_level_index_component_samples (
              observed_at, target_exchange, symbol, component_source, component_symbol,
              weight_pct, component_price, contribution_price,
              official_index_price, reconstructed_index_price,
              mark_price, future_mid, mark_premium_pct, funding_rate_pct,
              latency_ms, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.observed_at.isoformat(),
                    item.target_exchange,
                    item.symbol,
                    item.component_source,
                    item.component_symbol,
                    item.weight_pct,
                    item.component_price,
                    item.contribution_price,
                    item.official_index_price,
                    item.reconstructed_index_price,
                    item.mark_price,
                    item.future_mid,
                    item.mark_premium_pct,
                    item.funding_rate_pct,
                    item.latency_ms,
                    item.error,
                )
                for item in samples
            ],
        )
        await self.db.commit()

    async def count_samples(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS c FROM second_level_market_samples")
        row = await cursor.fetchone()
        return int(row["c"] if row is not None else 0)

    async def count_component_samples(self) -> int:
        cursor = await self.db.execute("SELECT COUNT(*) AS c FROM second_level_index_component_samples")
        row = await cursor.fetchone()
        return int(row["c"] if row is not None else 0)

    async def latest_observed_at(self) -> datetime | None:
        cursor = await self.db.execute("SELECT MAX(observed_at) AS observed_at FROM second_level_market_samples")
        row = await cursor.fetchone()
        if row is None or row["observed_at"] is None:
            return None
        return datetime.fromisoformat(row["observed_at"])

    async def list_latest_samples(
        self,
        *,
        exchanges: list[str] | None = None,
        symbols: list[str] | None = None,
        limit: int = 100,
    ) -> list[SecondLevelMarketSample]:
        clauses: list[str] = []
        params: list[object] = []
        if exchanges:
            clauses.append(f"exchange IN ({','.join('?' for _ in exchanges)})")
            params.extend(exchanges)
        if symbols:
            clauses.append(f"symbol IN ({','.join('?' for _ in symbols)})")
            params.extend(symbols)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM second_level_market_samples
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_sample_from_row(row) for row in rows]

    async def list_samples(
        self,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[SecondLevelMarketSample]:
        clauses: list[str] = []
        params: list[object] = []
        if exchange:
            clauses.append("exchange = ?")
            params.append(exchange)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if since:
            clauses.append("observed_at >= ?")
            params.append(since.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM second_level_market_samples
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_sample_from_row(row) for row in rows]

    async def list_component_samples(
        self,
        *,
        target_exchange: str | None = None,
        symbol: str | None = None,
        component_source: str | None = None,
        since: datetime | None = None,
        limit: int = 1000,
    ) -> list[SecondLevelIndexComponentSample]:
        clauses: list[str] = []
        params: list[object] = []
        if target_exchange:
            clauses.append("target_exchange = ?")
            params.append(target_exchange)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if component_source:
            clauses.append("component_source = ?")
            params.append(component_source)
        if since:
            clauses.append("observed_at >= ?")
            params.append(since.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM second_level_index_component_samples
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_component_sample_from_row(row) for row in rows]

    async def list_latest_component_samples(
        self,
        *,
        target_exchanges: list[str] | None = None,
        symbols: list[str] | None = None,
        limit: int = 500,
    ) -> list[SecondLevelIndexComponentSample]:
        clauses: list[str] = []
        params: list[object] = []
        if target_exchanges:
            clauses.append(f"target_exchange IN ({','.join('?' for _ in target_exchanges)})")
            params.extend(target_exchanges)
        if symbols:
            clauses.append(f"symbol IN ({','.join('?' for _ in symbols)})")
            params.extend(symbols)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await self.db.execute(
            f"""
            SELECT *
            FROM second_level_index_component_samples
            {where}
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        return [_component_sample_from_row(row) for row in rows]

    async def prune(self, retention_hours: float) -> int:
        cutoff = utc_now() - timedelta(hours=retention_hours)
        cursor = await self.db.execute(
            "DELETE FROM second_level_market_samples WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        component_cursor = await self.db.execute(
            "DELETE FROM second_level_index_component_samples WHERE observed_at < ?",
            (cutoff.isoformat(),),
        )
        await self.db.commit()
        deleted = cursor.rowcount if cursor.rowcount is not None else 0
        deleted += component_cursor.rowcount if component_cursor.rowcount is not None else 0
        return deleted


def _sample_from_row(row: aiosqlite.Row) -> SecondLevelMarketSample:
    return SecondLevelMarketSample(
        id=row["id"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        exchange=row["exchange"],
        symbol=row["symbol"],
        status=row["status"],
        spot_bid=row["spot_bid"],
        spot_ask=row["spot_ask"],
        spot_mid=row["spot_mid"],
        spot_last=row["spot_last"],
        future_bid=row["future_bid"],
        future_ask=row["future_ask"],
        future_mid=row["future_mid"],
        future_last=row["future_last"],
        mark_price=row["mark_price"],
        index_price=row["index_price"],
        mark_premium_pct=row["mark_premium_pct"],
        mid_premium_pct=row["mid_premium_pct"],
        funding_rate_pct=row["funding_rate_pct"],
        raw_spot_symbol=row["raw_spot_symbol"],
        raw_future_symbol=row["raw_future_symbol"],
        latency_ms=row["latency_ms"],
        error=row["error"],
    )


def _component_sample_from_row(row: aiosqlite.Row) -> SecondLevelIndexComponentSample:
    return SecondLevelIndexComponentSample(
        id=row["id"],
        observed_at=datetime.fromisoformat(row["observed_at"]),
        target_exchange=row["target_exchange"],
        symbol=row["symbol"],
        component_source=row["component_source"],
        component_symbol=row["component_symbol"],
        weight_pct=row["weight_pct"],
        component_price=row["component_price"],
        contribution_price=row["contribution_price"],
        official_index_price=row["official_index_price"],
        reconstructed_index_price=row["reconstructed_index_price"],
        mark_price=row["mark_price"],
        future_mid=row["future_mid"],
        mark_premium_pct=row["mark_premium_pct"],
        funding_rate_pct=row["funding_rate_pct"],
        latency_ms=row["latency_ms"],
        error=row["error"],
    )


class SecondLevelMarketFetcher:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            limits=DEFAULT_LIMITS,
            http2=False,
            trust_env=True,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _get_json(self, url: str) -> Any:
        response = await self.client.get(url)
        response.raise_for_status()
        return response.json()

    async def _post_json(self, url: str, body: dict[str, Any]) -> Any:
        response = await self.client.post(url, json=body)
        response.raise_for_status()
        return response.json()

    async def fetch(self, exchange: str, symbol: str) -> SecondLevelMarketSample:
        observed_at = utc_now()
        started = time.perf_counter()
        errors: list[str] = []
        spot: dict[str, Any] | None = None
        future: dict[str, Any] | None = None

        async def call(fetcher, label: str) -> dict[str, Any] | None:
            try:
                return await fetcher(symbol)
            except httpx.HTTPStatusError as exc:
                errors.append(f"{label}: {_http_status_error_message(exc, label)}")
                return None
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {_error_message(exc)}")
                return None

        spot_fetcher = getattr(self, f"_fetch_{exchange}_spot", None)
        future_fetcher = getattr(self, f"_fetch_{exchange}_future", None)
        tasks = []
        labels = []
        if spot_fetcher is not None:
            tasks.append(call(spot_fetcher, "spot"))
            labels.append("spot")
        if future_fetcher is not None:
            tasks.append(call(future_fetcher, "future"))
            labels.append("future")
        results = await asyncio.gather(*tasks) if tasks else []
        for label, result in zip(labels, results, strict=True):
            if label == "spot":
                spot = result
            else:
                future = result

        latency_ms = (time.perf_counter() - started) * 1000
        has_spot = spot is not None and any(spot.get(key) is not None for key in ("bid", "ask", "mid", "last"))
        has_future = future is not None and any(
            future.get(key) is not None for key in ("bid", "ask", "mid", "last", "mark", "index")
        )
        if spot_fetcher is not None and not has_spot and not any(error.startswith("spot:") for error in errors):
            errors.append(f"spot: {_missing_leg_message('spot')}")
        if future_fetcher is not None and not has_future and not any(error.startswith("future:") for error in errors):
            errors.append(f"future: {_missing_leg_message('future')}")
        status = "ok" if has_future and (has_spot or spot_fetcher is None) and not errors else "partial"
        if not has_spot and not has_future:
            status = "error"
        return SecondLevelMarketSample(
            observed_at=observed_at,
            exchange=exchange,
            symbol=_compact_symbol(symbol),
            status=status,
            spot_bid=spot.get("bid") if spot else None,
            spot_ask=spot.get("ask") if spot else None,
            spot_mid=spot.get("mid") if spot else None,
            spot_last=spot.get("last") if spot else None,
            future_bid=future.get("bid") if future else None,
            future_ask=future.get("ask") if future else None,
            future_mid=future.get("mid") if future else None,
            future_last=future.get("last") if future else None,
            mark_price=future.get("mark") if future else None,
            index_price=future.get("index") if future else None,
            mark_premium_pct=future.get("mark_premium_pct") if future else None,
            mid_premium_pct=future.get("mid_premium_pct") if future else None,
            funding_rate_pct=future.get("funding_rate_pct") if future else None,
            raw_spot_symbol=spot.get("raw_symbol") if spot else None,
            raw_future_symbol=future.get("raw_symbol") if future else None,
            latency_ms=latency_ms,
            error="; ".join(errors) if errors else None,
        )

    async def fetch_index_components(
        self,
        exchange: str,
        symbol: str,
        market_sample: SecondLevelMarketSample | None = None,
    ) -> list[SecondLevelIndexComponentSample]:
        fetcher = getattr(self, f"_fetch_{exchange}_index_components", None)
        if fetcher is None:
            return []
        observed_at = utc_now()
        started = time.perf_counter()
        raw_symbol = _compact_symbol(symbol)
        try:
            components = await fetcher(symbol)
        except httpx.HTTPStatusError as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return [
                self._component_error_sample(
                    observed_at=observed_at,
                    exchange=exchange,
                    symbol=raw_symbol,
                    latency_ms=latency_ms,
                    error=_http_status_error_message(exc, "index_component"),
                    market_sample=market_sample,
                )
            ]
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000
            return [
                self._component_error_sample(
                    observed_at=observed_at,
                    exchange=exchange,
                    symbol=raw_symbol,
                    latency_ms=latency_ms,
                    error=_error_message(exc),
                    market_sample=market_sample,
                )
            ]

        latency_ms = (time.perf_counter() - started) * 1000
        rows = [
            item
            for item in components
            if item.get("source") and (item.get("price") is not None or item.get("weight_pct") is not None)
        ]
        if not rows:
            return [
                self._component_error_sample(
                    observed_at=observed_at,
                    exchange=exchange,
                    symbol=raw_symbol,
                    latency_ms=latency_ms,
                    error="指数组成不可用：交易所未返回成分源价格",
                    market_sample=market_sample,
                )
            ]
        reconstructed = sum(
            contribution
            for item in rows
            if (contribution := _contribution_price(item.get("weight_pct"), item.get("price"))) is not None
        )
        reconstructed_index_price = reconstructed if reconstructed > 0 else None
        return [
            SecondLevelIndexComponentSample(
                observed_at=observed_at,
                target_exchange=exchange,
                symbol=raw_symbol,
                component_source=str(item["source"]),
                component_symbol=str(item.get("symbol") or raw_symbol),
                weight_pct=item.get("weight_pct"),
                component_price=item.get("price"),
                contribution_price=_contribution_price(item.get("weight_pct"), item.get("price")),
                official_index_price=market_sample.index_price if market_sample else None,
                reconstructed_index_price=reconstructed_index_price,
                mark_price=market_sample.mark_price if market_sample else None,
                future_mid=market_sample.future_mid if market_sample else None,
                mark_premium_pct=market_sample.mark_premium_pct if market_sample else None,
                funding_rate_pct=market_sample.funding_rate_pct if market_sample else None,
                latency_ms=latency_ms,
                error=None,
            )
            for item in rows
        ]

    def _component_error_sample(
        self,
        *,
        observed_at: datetime,
        exchange: str,
        symbol: str,
        latency_ms: float,
        error: str,
        market_sample: SecondLevelMarketSample | None,
    ) -> SecondLevelIndexComponentSample:
        return SecondLevelIndexComponentSample(
            observed_at=observed_at,
            target_exchange=exchange,
            symbol=symbol,
            component_source="unavailable",
            component_symbol=symbol,
            official_index_price=market_sample.index_price if market_sample else None,
            mark_price=market_sample.mark_price if market_sample else None,
            future_mid=market_sample.future_mid if market_sample else None,
            mark_premium_pct=market_sample.mark_premium_pct if market_sample else None,
            funding_rate_pct=market_sample.funding_rate_pct if market_sample else None,
            latency_ms=latency_ms,
            error=error,
        )

    async def _fetch_bybit_index_components(self, symbol: str) -> list[dict[str, Any]]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://api.bybit.com/v5/market/index-price-components?indexName={raw}")
        row = _component_payload_row(payload)
        rows = row.get("quote") or row.get("components") or row.get("constituents") or row.get("list")
        if not isinstance(rows, list):
            return []
        return [
            component
            for item in rows
            if isinstance(item, dict)
            if (component := _component_from_row(item, fallback_symbol=raw)) is not None
        ]

    async def _fetch_bitget_index_components(self, symbol: str) -> list[dict[str, Any]]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://api.bitget.com/api/v3/market/index-components?symbol={raw}")
        row = _component_payload_row(payload)
        rows = row.get("components") or row.get("constituents") or row.get("componentList")
        if not isinstance(rows, list):
            return []
        return [
            component
            for item in rows
            if isinstance(item, dict)
            if (component := _component_from_row(item, fallback_symbol=raw)) is not None
        ]

    async def _fetch_binance_index_components(self, symbol: str) -> list[dict[str, Any]]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://fapi.binance.com/fapi/v1/constituents?symbol={raw}")
        rows = payload.get("constituents") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        return [
            component
            for item in rows
            if isinstance(item, dict)
            if (component := _component_from_row(item, fallback_symbol=raw)) is not None
        ]

    async def _fetch_okx_index_components(self, symbol: str) -> list[dict[str, Any]]:
        raw = _compact_symbol(symbol)
        index_id = _okx_spot(raw)
        payload = await self._get_json(f"https://www.okx.com/api/v5/market/index-components?index={index_id}")
        row = _component_payload_row(payload)
        rows = row.get("components") or row.get("constituents")
        if not isinstance(rows, list):
            return []
        return [
            component
            for item in rows
            if isinstance(item, dict)
            if (component := _component_from_row(item, fallback_symbol=raw)) is not None
        ]

    async def _fetch_gate_index_components(self, symbol: str) -> list[dict[str, Any]]:
        raw = _compact_symbol(symbol)
        contract = _gate_symbol(raw)
        payload = await self._get_json(f"https://api.gateio.ws/api/v4/futures/usdt/index_constituents/{contract}")
        rows = payload.get("constituents") or payload.get("components") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        return [
            component
            for item in rows
            if isinstance(item, dict)
            if (component := _component_from_row(item, fallback_symbol=raw)) is not None
        ]

    async def _fetch_bybit_spot(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={raw}")
        row = _first_row(payload.get("result", {}).get("list", [])) if isinstance(payload, dict) else {}
        return _leg(
            raw_symbol=raw,
            bid=parse_float(row.get("bid1Price")),
            ask=parse_float(row.get("ask1Price")),
            last=parse_float(row.get("lastPrice")),
        )

    async def _fetch_bybit_future(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={raw}")
        row = _first_row(payload.get("result", {}).get("list", [])) if isinstance(payload, dict) else {}
        funding = parse_float(row.get("fundingRate"))
        return _leg(
            raw_symbol=raw,
            bid=parse_float(row.get("bid1Price")),
            ask=parse_float(row.get("ask1Price")),
            last=parse_float(row.get("lastPrice")),
            mark=parse_float(row.get("markPrice")),
            index=parse_float(row.get("indexPrice")),
            funding_rate_pct=funding * 100 if funding is not None else None,
        )

    async def _fetch_bitget_spot(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://api.bitget.com/api/v2/spot/market/tickers?symbol={raw}")
        row = _payload_data_row(payload)
        return _leg(
            raw_symbol=raw,
            bid=parse_float(row.get("bidPr") or row.get("bid")),
            ask=parse_float(row.get("askPr") or row.get("ask")),
            last=parse_float(row.get("lastPr") or row.get("last") or row.get("close")),
        )

    async def _fetch_bitget_future(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(
            "https://api.bitget.com/api/v2/mix/market/ticker"
            f"?symbol={raw}&productType=USDT-FUTURES"
        )
        row = _payload_data_row(payload)
        funding = parse_float(row.get("fundingRate"))
        return _leg(
            raw_symbol=raw,
            bid=parse_float(row.get("bidPr") or row.get("bid")),
            ask=parse_float(row.get("askPr") or row.get("ask")),
            last=parse_float(row.get("lastPr") or row.get("last")),
            mark=parse_float(row.get("markPrice")),
            index=parse_float(row.get("indexPrice")),
            funding_rate_pct=funding * 100 if funding is not None else None,
        )

    async def _fetch_binance_spot(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        payload = await self._get_json(f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={raw}")
        return _leg(raw_symbol=raw, bid=parse_float(payload.get("bidPrice")), ask=parse_float(payload.get("askPrice")))

    async def _fetch_binance_future(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        premium, book = await asyncio.gather(
            self._get_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={raw}"),
            self._get_json(f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={raw}"),
        )
        funding = parse_float(premium.get("lastFundingRate")) if isinstance(premium, dict) else None
        return _leg(
            raw_symbol=raw,
            bid=parse_float(book.get("bidPrice")) if isinstance(book, dict) else None,
            ask=parse_float(book.get("askPrice")) if isinstance(book, dict) else None,
            mark=parse_float(premium.get("markPrice")) if isinstance(premium, dict) else None,
            index=parse_float(premium.get("indexPrice")) if isinstance(premium, dict) else None,
            funding_rate_pct=funding * 100 if funding is not None else None,
        )

    async def _fetch_aster_future(self, symbol: str) -> dict[str, Any]:
        raw = _compact_symbol(symbol)
        premium, book = await asyncio.gather(
            self._get_json(f"https://fapi.asterdex.com/fapi/v1/premiumIndex?symbol={raw}"),
            self._get_json(f"https://fapi.asterdex.com/fapi/v1/ticker/bookTicker?symbol={raw}"),
        )
        funding = parse_float(premium.get("lastFundingRate")) if isinstance(premium, dict) else None
        return _leg(
            raw_symbol=raw,
            bid=parse_float(book.get("bidPrice")) if isinstance(book, dict) else None,
            ask=parse_float(book.get("askPrice")) if isinstance(book, dict) else None,
            mark=parse_float(premium.get("markPrice")) if isinstance(premium, dict) else None,
            index=parse_float(premium.get("indexPrice")) if isinstance(premium, dict) else None,
            funding_rate_pct=funding * 100 if funding is not None else None,
        )

    async def _fetch_okx_spot(self, symbol: str) -> dict[str, Any]:
        inst_id = _okx_spot(symbol)
        payload = await self._get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}")
        row = _first_row(payload.get("data", [])) if isinstance(payload, dict) else {}
        return _leg(
            raw_symbol=inst_id,
            bid=parse_float(row.get("bidPx")),
            ask=parse_float(row.get("askPx")),
            last=parse_float(row.get("last")),
        )

    async def _fetch_okx_future(self, symbol: str) -> dict[str, Any]:
        inst_id = _okx_swap(symbol)
        index_id = inst_id.removesuffix("-SWAP")
        ticker_payload, mark_payload, index_payload, funding_payload = await asyncio.gather(
            self._get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}"),
            self._get_json(f"https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId={inst_id}"),
            self._get_json(f"https://www.okx.com/api/v5/market/index-tickers?instId={index_id}"),
            self._get_json(f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"),
        )
        ticker = _first_row(ticker_payload.get("data", [])) if isinstance(ticker_payload, dict) else {}
        mark = _first_row(mark_payload.get("data", [])) if isinstance(mark_payload, dict) else {}
        index = _first_row(index_payload.get("data", [])) if isinstance(index_payload, dict) else {}
        funding = _first_row(funding_payload.get("data", [])) if isinstance(funding_payload, dict) else {}
        funding_rate = parse_float(funding.get("fundingRate"))
        return _leg(
            raw_symbol=inst_id,
            bid=parse_float(ticker.get("bidPx")),
            ask=parse_float(ticker.get("askPx")),
            last=parse_float(ticker.get("last")),
            mark=parse_float(mark.get("markPx")),
            index=parse_float(index.get("idxPx")),
            funding_rate_pct=funding_rate * 100 if funding_rate is not None else None,
        )

    async def _fetch_gate_spot(self, symbol: str) -> dict[str, Any]:
        raw = _gate_symbol(symbol)
        rows = await self._get_json(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={raw}")
        row = _first_row(rows if isinstance(rows, list) else [])
        return _leg(
            raw_symbol=raw,
            bid=parse_float(row.get("highest_bid")),
            ask=parse_float(row.get("lowest_ask")),
            last=parse_float(row.get("last")),
        )

    async def _fetch_gate_future(self, symbol: str) -> dict[str, Any]:
        raw = _gate_symbol(symbol)
        rows = await self._get_json(f"https://api.gateio.ws/api/v4/futures/usdt/tickers?contract={raw}")
        row = _first_row(rows if isinstance(rows, list) else [])
        funding = parse_float(row.get("funding_rate"))
        return _leg(
            raw_symbol=raw,
            bid=parse_float(row.get("highest_bid")),
            ask=parse_float(row.get("lowest_ask")),
            last=parse_float(row.get("last")),
            mark=parse_float(row.get("mark_price")),
            index=parse_float(row.get("index_price")),
            funding_rate_pct=funding * 100 if funding is not None else None,
        )

    async def _fetch_hyperliquid_future(self, symbol: str) -> dict[str, Any]:
        base, _ = _base_quote(symbol)
        payload = await self._post_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
        if not isinstance(payload, list) or len(payload) < 2:
            raise RuntimeError("unexpected hyperliquid metaAndAssetCtxs payload")
        meta = payload[0] if isinstance(payload[0], dict) else {}
        contexts = payload[1] if isinstance(payload[1], list) else []
        universe = meta.get("universe", [])
        for asset, context in zip(universe, contexts):
            if not isinstance(asset, dict) or not isinstance(context, dict):
                continue
            if str(asset.get("name", "")).upper() != base.upper():
                continue
            funding = parse_float(context.get("funding"))
            return _leg(
                raw_symbol=base,
                bid=None,
                ask=None,
                last=None,
                mark=parse_float(context.get("markPx")),
                index=parse_float(context.get("oraclePx")),
                funding_rate_pct=funding * 100 if funding is not None else None,
            ) | {"mid": _positive(parse_float(context.get("midPx")))}
        raise RuntimeError(f"hyperliquid symbol not found: {symbol}")


class SecondLevelSampler:
    def __init__(
        self,
        repo: SecondLevelSamplingRepository,
        fetcher: SecondLevelMarketFetcher | None = None,
    ) -> None:
        self.repo = repo
        self.fetcher = fetcher or SecondLevelMarketFetcher()
        self._config = SecondLevelSamplingConfig()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._latest_error: str | None = None

    async def initialize(self) -> None:
        config = await self.repo.get_config()
        self._config = config
        if config.enabled:
            await self.start(config)

    @property
    def config(self) -> SecondLevelSamplingConfig:
        return self._config

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def apply_config(self, config: SecondLevelSamplingConfig) -> SecondLevelSamplingConfig:
        saved = await self.repo.set_config(config)
        self._config = saved
        if saved.enabled:
            await self.start(saved)
        else:
            await self.stop()
        return saved

    async def start(self, config: SecondLevelSamplingConfig | None = None) -> None:
        async with self._lock:
            if config is not None:
                self._config = config
            if self.running():
                return
            self._task = asyncio.create_task(self._run(), name="second-level-sampler")

    async def stop(self) -> None:
        async with self._lock:
            task = self._task
            self._task = None
            if task is None:
                return
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def aclose(self) -> None:
        await self.stop()
        await self.fetcher.aclose()

    async def status(self) -> SecondLevelSamplingStatus:
        config = self._config
        latest = await self.repo.list_latest_samples(
            exchanges=config.exchanges or None,
            symbols=config.symbols or None,
            limit=max(100, len(config.exchanges) * len(config.symbols) * 2),
        )
        latest_by_pair: dict[tuple[str, str], SecondLevelMarketSample] = {}
        for sample in latest:
            key = (sample.symbol, sample.exchange)
            if key not in latest_by_pair:
                latest_by_pair[key] = sample
        latest_samples = list(latest_by_pair.values())
        latest_components = await self.repo.list_latest_component_samples(
            target_exchanges=config.exchanges or None,
            symbols=config.symbols or None,
            limit=max(500, len(config.exchanges) * len(config.symbols) * 30),
        )
        latest_component_by_key: dict[tuple[str, str, str, str], SecondLevelIndexComponentSample] = {}
        for sample in latest_components:
            key = _component_sample_key(sample)
            if key not in latest_component_by_key:
                latest_component_by_key[key] = sample
        latest_component_samples = list(latest_component_by_key.values())
        component_signals = await self._component_signals(config, latest_component_samples)
        return SecondLevelSamplingStatus(
            running=self.running(),
            config=config,
            sample_count=await self.repo.count_samples(),
            component_sample_count=await self.repo.count_component_samples(),
            latest_observed_at=await self.repo.latest_observed_at(),
            latest_error=self._latest_error,
            latest_samples=latest_samples,
            latest_spreads=_latest_spreads(latest_samples),
            latest_component_samples=latest_component_samples,
            latest_component_signals=component_signals,
        )

    async def _component_signals(
        self,
        config: SecondLevelSamplingConfig,
        latest_component_samples: list[SecondLevelIndexComponentSample],
    ) -> list[SecondLevelIndexComponentSignal]:
        if not latest_component_samples:
            return []
        since = utc_now() - timedelta(seconds=config.component_signal_window_seconds)
        recent = await self.repo.list_component_samples(
            since=since,
            limit=max(2000, len(config.exchanges) * len(config.symbols) * 500),
        )
        return _build_component_signals(
            latest_component_samples,
            recent,
            window_seconds=config.component_signal_window_seconds,
        )

    async def _run(self) -> None:
        prune_counter = 0
        while True:
            config = self._config
            started = time.perf_counter()
            try:
                await self._collect_config(config)
                self._latest_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._latest_error = _error_message(exc)
                logger.exception("second-level sampler cycle failed")
            prune_counter += 1
            if prune_counter >= 60:
                prune_counter = 0
                with suppress(Exception):
                    await self.repo.prune(config.retention_hours)
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.05, config.interval_seconds - elapsed))

    async def _collect_config(self, config: SecondLevelSamplingConfig) -> None:
        if not config.enabled or not config.exchanges or not config.symbols:
            return
        semaphore = asyncio.Semaphore(config.max_concurrent_requests)

        async def fetch_one(exchange: str, symbol: str) -> SecondLevelMarketSample:
            async with semaphore:
                return await self.fetcher.fetch(exchange, symbol)

        samples = await asyncio.gather(
            *(fetch_one(exchange, symbol) for symbol in config.symbols for exchange in config.exchanges),
        )
        market_samples = list(samples)
        await self.repo.insert_samples(market_samples)
        if not config.capture_index_components:
            return
        fetch_components = getattr(self.fetcher, "fetch_index_components", None)
        if fetch_components is None:
            return
        market_by_pair = {(sample.exchange, sample.symbol): sample for sample in market_samples}

        async def fetch_component_set(exchange: str, symbol: str) -> list[SecondLevelIndexComponentSample]:
            async with semaphore:
                market_sample = market_by_pair.get((exchange, _compact_symbol(symbol)))
                return await fetch_components(exchange, symbol, market_sample)

        component_batches = await asyncio.gather(
            *(fetch_component_set(exchange, symbol) for symbol in config.symbols for exchange in config.exchanges),
        )
        await self.repo.insert_component_samples([sample for batch in component_batches for sample in batch])


def _latest_spreads(samples: list[SecondLevelMarketSample]) -> list[SecondLevelPairSpreadSnapshot]:
    by_symbol: dict[str, list[SecondLevelMarketSample]] = {}
    for sample in samples:
        if sample.future_mid is None and sample.spot_mid is None:
            continue
        by_symbol.setdefault(sample.symbol, []).append(sample)

    spreads: list[SecondLevelPairSpreadSnapshot] = []
    for symbol, rows in by_symbol.items():
        for left, right in combinations(sorted(rows, key=lambda item: item.exchange), 2):
            observed_at = max(left.observed_at, right.observed_at)
            spot_spread = _pct_diff(left.spot_mid, right.spot_mid)
            future_spread = _pct_diff(left.future_mid, right.future_mid)
            left_basis = _pct_diff(left.future_mid, left.spot_mid)
            right_basis = _pct_diff(right.future_mid, right.spot_mid)
            premium_gap = None
            if left.mark_premium_pct is not None and right.mark_premium_pct is not None:
                premium_gap = left.mark_premium_pct - right.mark_premium_pct
            spreads.append(
                SecondLevelPairSpreadSnapshot(
                    symbol=symbol,
                    left_exchange=left.exchange,
                    right_exchange=right.exchange,
                    observed_at=observed_at,
                    left_spot_mid=left.spot_mid,
                    right_spot_mid=right.spot_mid,
                    left_future_mid=left.future_mid,
                    right_future_mid=right.future_mid,
                    spot_spread_pct=spot_spread,
                    future_spread_pct=future_spread,
                    future_spot_spread_gap_pct=_delta(future_spread, spot_spread),
                    left_future_spot_basis_pct=left_basis,
                    right_future_spot_basis_pct=right_basis,
                    future_spot_basis_gap_pct=_delta(left_basis, right_basis),
                    left_mark_premium_pct=left.mark_premium_pct,
                    right_mark_premium_pct=right.mark_premium_pct,
                    premium_gap_pct=premium_gap,
                )
            )
    return spreads


def _component_sample_key(sample: SecondLevelIndexComponentSample) -> tuple[str, str, str, str]:
    return (
        sample.target_exchange,
        sample.symbol,
        sample.component_source,
        sample.component_symbol,
    )


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or not isfinite(left) or not isfinite(right):
        return None
    return left - right


def _abs_or_zero(value: float | None) -> float:
    return abs(value) if value is not None and isfinite(value) else 0


def _signal_level(
    *,
    estimated_index_impact_pct: float | None,
    component_price_change_pct: float | None,
    weight_pct: float | None,
) -> str:
    impact = _abs_or_zero(estimated_index_impact_pct)
    component_change = _abs_or_zero(component_price_change_pct)
    weight = weight_pct or 0
    if impact >= 0.25 or (weight >= 50 and component_change >= 0.5):
        return "high"
    if impact >= 0.08 or (weight >= 20 and component_change >= 0.25):
        return "medium"
    return "watch"


def _signal_reason(
    *,
    component_source: str,
    component_price_change_pct: float | None,
    weight_pct: float | None,
    estimated_index_impact_pct: float | None,
    official_index_change_pct: float | None,
    mark_premium_change_pct: float | None,
) -> str:
    parts: list[str] = []
    if component_price_change_pct is not None:
        parts.append(f"{component_source} 成分源价格变化 {component_price_change_pct:+.4f}%")
    if weight_pct is not None:
        parts.append(f"权重 {weight_pct:.2f}%")
    if estimated_index_impact_pct is not None:
        parts.append(f"预计推动指数 {estimated_index_impact_pct:+.4f}%")
    if official_index_change_pct is not None:
        parts.append(f"官方指数变化 {official_index_change_pct:+.4f}%")
    if mark_premium_change_pct is not None:
        parts.append(f"mark 溢价变化 {mark_premium_change_pct:+.4f}pct")
    return "；".join(parts) if parts else "暂无可计算变化"


def _build_component_signals(
    latest_samples: list[SecondLevelIndexComponentSample],
    recent_samples: list[SecondLevelIndexComponentSample],
    *,
    window_seconds: int,
) -> list[SecondLevelIndexComponentSignal]:
    oldest_by_key: dict[tuple[str, str, str, str], SecondLevelIndexComponentSample] = {}
    for sample in sorted(recent_samples, key=lambda item: item.observed_at):
        if sample.error or sample.component_price is None:
            continue
        key = _component_sample_key(sample)
        oldest_by_key.setdefault(key, sample)

    signals: list[SecondLevelIndexComponentSignal] = []
    for current in latest_samples:
        if current.error or current.component_price is None:
            continue
        previous = oldest_by_key.get(_component_sample_key(current))
        if previous is None or previous.observed_at >= current.observed_at:
            continue
        component_price_change_pct = _pct_diff(current.component_price, previous.component_price)
        estimated_index_impact_pct = None
        if current.contribution_price is not None and previous.contribution_price is not None:
            denominator = current.official_index_price or current.reconstructed_index_price
            if denominator is not None and denominator > 0:
                estimated_index_impact_pct = (
                    (current.contribution_price - previous.contribution_price) / denominator * 100
                )
        official_index_change_pct = _pct_diff(current.official_index_price, previous.official_index_price)
        mark_premium_change_pct = _delta(current.mark_premium_pct, previous.mark_premium_pct)
        lag_vs_official = _delta(estimated_index_impact_pct, official_index_change_pct)
        level = _signal_level(
            estimated_index_impact_pct=estimated_index_impact_pct,
            component_price_change_pct=component_price_change_pct,
            weight_pct=current.weight_pct,
        )
        signals.append(
            SecondLevelIndexComponentSignal(
                observed_at=current.observed_at,
                target_exchange=current.target_exchange,
                symbol=current.symbol,
                component_source=current.component_source,
                component_symbol=current.component_symbol,
                window_seconds=window_seconds,
                weight_pct=current.weight_pct,
                component_price=current.component_price,
                component_price_change_pct=component_price_change_pct,
                estimated_index_impact_pct=estimated_index_impact_pct,
                official_index_change_pct=official_index_change_pct,
                mark_premium_change_pct=mark_premium_change_pct,
                lag_vs_official_index_pct=lag_vs_official,
                signal_level=level,
                reason=_signal_reason(
                    component_source=current.component_source,
                    component_price_change_pct=component_price_change_pct,
                    weight_pct=current.weight_pct,
                    estimated_index_impact_pct=estimated_index_impact_pct,
                    official_index_change_pct=official_index_change_pct,
                    mark_premium_change_pct=mark_premium_change_pct,
                ),
            )
        )
    signals.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "watch": 2}[item.signal_level],
            -_abs_or_zero(item.estimated_index_impact_pct),
            -_abs_or_zero(item.component_price_change_pct),
        )
    )
    return signals[:20]
