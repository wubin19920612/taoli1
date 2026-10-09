from __future__ import annotations

import asyncio
from collections.abc import Iterable

from app.exchanges.base import (
    ExchangeAdapter,
    normalize_usdt_symbol,
    order_book_snapshot,
    parse_datetime_ms,
    parse_float,
    utc_now,
)
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot


class HyperliquidAdapter(ExchangeAdapter):
    name = "hyperliquid"
    info_url = "https://api.hyperliquid.xyz/info"
    perp_dex_concurrency = 3
    priority_order_book_markets = frozenset({"io:ANTH"})

    def __init__(self, client=None):
        super().__init__(client)
        self.market_errors: dict[str, str] = {}

    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        payload = await self.post_json(self.info_url, {"type": "spotMetaAndAssetCtxs"})
        meta, contexts = self._split_payload(payload)
        pair_names = self._spot_pair_names(meta)
        return self._parse_spot_rows(contexts, pair_names)

    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        self.market_errors = {}
        perp_dex_names = await self._fetch_perp_dex_names()
        payload = await self.post_json(self.info_url, {"type": "metaAndAssetCtxs"})
        rows = self._parse_perp_payload(payload)
        predicted_fundings = await self._fetch_predicted_fundings()

        if perp_dex_names:
            semaphore = asyncio.Semaphore(self.perp_dex_concurrency)

            async def fetch_limited(dex_name: str) -> list[MarketSnapshot]:
                async with semaphore:
                    return await self._fetch_perp_dex_rows(dex_name)

            dex_results = await asyncio.gather(
                *(fetch_limited(dex_name) for dex_name in perp_dex_names),
                return_exceptions=True,
            )
            for result in dex_results:
                if isinstance(result, Exception):
                    continue
                rows.extend(result)

        rows = self._dedupe_perp_rows_by_market(
            self._attach_predicted_fundings(rows, predicted_fundings)
        )
        return await self._attach_priority_order_books(rows)

    async def fetch_order_book(
        self,
        symbol: str,
        market_type: MarketType,
        raw_symbol: str,
        limit: int = 20,
    ) -> OrderBookSnapshot | None:
        coin = self._l2_book_coin(symbol, raw_symbol, market_type)
        if coin is None:
            return None
        payload = await self.post_json(self.info_url, {"type": "l2Book", "coin": coin})
        if not isinstance(payload, dict):
            return None
        levels = payload.get("levels")
        if not isinstance(levels, list) or len(levels) < 2:
            return None
        return order_book_snapshot(
            exchange=self.name,
            market_type=market_type,
            symbol=symbol,
            raw_symbol=coin,
            bids=levels[0],
            asks=levels[1],
            timestamp=parse_datetime_ms(payload.get("time")),
        )

    async def _fetch_perp_dex_names(self) -> list[str]:
        try:
            payload = await self.post_json(self.info_url, {"type": "perpDexs"})
        except Exception:  # noqa: BLE001 - DEX discovery must not block main markets.
            return []
        if not isinstance(payload, list):
            return []

        names: list[str] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in seen:
                continue
            names.append(name)
            seen.add(name)
        return names

    def _l2_book_coin(self, symbol: str, raw_symbol: str, market_type: MarketType) -> str | None:
        candidate = raw_symbol.strip()
        if candidate and candidate.upper() != symbol.upper():
            return candidate
        try:
            _, base, _ = normalize_usdt_symbol(symbol)
        except ValueError:
            return None
        return base if market_type == MarketType.FUTURE else f"{base}/USDC"

    async def _fetch_perp_dex_rows(self, dex_name: str) -> list[MarketSnapshot]:
        payload = await self.post_json(self.info_url, {"type": "metaAndAssetCtxs", "dex": dex_name})
        return self._parse_perp_payload(payload)

    async def _fetch_predicted_fundings(self) -> dict[str, dict[str, dict]]:
        try:
            payload = await self.post_json(self.info_url, {"type": "predictedFundings"})
        except Exception:  # noqa: BLE001 - predicted funding is optional enrichment.
            return {}
        if not isinstance(payload, list):
            return {}

        predictions: dict[str, dict[str, dict]] = {}
        for item in payload:
            if not isinstance(item, list) or len(item) != 2:
                continue
            asset, sources = item
            if not isinstance(asset, str) or not isinstance(sources, list):
                continue
            asset_key = asset.upper()
            asset_sources: dict[str, dict] = {}
            for source_item in sources:
                if not isinstance(source_item, list) or len(source_item) != 2:
                    continue
                source_name, info = source_item
                if not isinstance(source_name, str) or not isinstance(info, dict):
                    continue
                asset_sources[source_name] = info
            if asset_sources:
                predictions[asset_key] = asset_sources
        return predictions

    def _attach_predicted_fundings(
        self,
        rows: list[MarketSnapshot],
        predicted_fundings: dict[str, dict[str, dict]],
    ) -> list[MarketSnapshot]:
        enriched: list[MarketSnapshot] = []
        for row in rows:
            if ":" in row.raw_symbol:
                enriched.append(row)
                continue

            info = predicted_fundings.get(row.base.upper(), {}).get("HlPerp")
            if not info:
                enriched.append(row)
                continue

            funding_next = parse_float(info.get("fundingRate"))
            next_time = parse_datetime_ms(info.get("nextFundingTime"))
            interval_hours = parse_float(info.get("fundingIntervalHours"))
            enriched.append(
                row.model_copy(
                    update={
                        "funding_next_rate_pct": funding_next * 100 if funding_next is not None else None,
                        "funding_next_time": next_time,
                        "funding_interval_hours": int(interval_hours)
                        if interval_hours is not None
                        else row.funding_interval_hours,
                    }
                )
            )
        return enriched

    def _parse_perp_payload(self, payload: object) -> list[MarketSnapshot]:
        meta, contexts = self._split_payload(payload)
        universe = meta.get("universe", [])
        return self._parse_perp_rows(universe, contexts)

    async def _attach_priority_order_books(
        self,
        rows: list[MarketSnapshot],
    ) -> list[MarketSnapshot]:
        enriched: list[MarketSnapshot] = []
        for row in rows:
            if row.raw_symbol not in self.priority_order_book_markets:
                enriched.append(row)
                continue
            try:
                book = await self.fetch_order_book(
                    row.symbol,
                    row.market_type,
                    row.raw_symbol,
                    limit=20,
                )
                if book is None or not book.bids or not book.asks:
                    raise RuntimeError("empty l2Book response")
                bid = max(book.bids, key=lambda level: level.price)
                ask = min(book.asks, key=lambda level: level.price)
                if bid.price >= ask.price:
                    raise RuntimeError("crossed l2Book response")
                enriched.append(
                    row.model_copy(
                        update={
                            "bid": bid.price,
                            "ask": ask.price,
                            "bid_size": bid.size,
                            "ask_size": ask.size,
                            "upstream_timestamp": book.timestamp,
                            "data_source": (
                                "Hyperliquid public metaAndAssetCtxs + l2Book"
                            ),
                            "is_estimated": False,
                            "estimated_fields": [],
                        }
                    )
                )
            except Exception as exc:  # noqa: BLE001 - isolate one exact market.
                key = f"{self.name}:{row.market_type.value}:{row.raw_symbol}"
                detail = str(exc).strip() or exc.__class__.__name__
                self.market_errors[key] = f"Hyperliquid l2Book failed: {detail}"
                enriched.append(row)
        return enriched

    def _dedupe_perp_rows_by_market(self, rows: list[MarketSnapshot]) -> list[MarketSnapshot]:
        best: dict[tuple[str, str], MarketSnapshot] = {}
        order: list[tuple[str, str]] = []
        for row in rows:
            key = (row.dex or "main", row.raw_symbol)
            current = best.get(key)
            if current is None:
                best[key] = row
                order.append(key)
                continue
            current_volume = current.volume_24h_usdt if current.volume_24h_usdt is not None else -1
            row_volume = row.volume_24h_usdt if row.volume_24h_usdt is not None else -1
            if row_volume > current_volume:
                best[key] = row
        return [best[key] for key in order]

    def _split_payload(self, payload: object) -> tuple[dict, list[dict]]:
        if not isinstance(payload, list) or len(payload) < 2:
            return {}, []

        meta = payload[0] if isinstance(payload[0], dict) else {}
        contexts = payload[1] if isinstance(payload[1], list) else []
        return meta, [item for item in contexts if isinstance(item, dict)]

    def _spot_pair_names(self, meta: dict) -> dict[str, str]:
        token_names = self._spot_token_names(meta.get("tokens", []))
        pairs: dict[str, str] = {}
        universe = meta.get("universe", [])
        for item in universe:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name", "")).strip()
            if not raw_name:
                continue
            pair_name = self._spot_pair_name(item, token_names)
            if pair_name is None:
                continue
            pairs[raw_name.upper()] = pair_name
        return pairs

    def _spot_token_names(self, tokens: Iterable[object]) -> dict[int, str]:
        names: dict[int, str] = {}
        for item in tokens:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            name = str(item.get("name", "")).strip().upper()
            if isinstance(index, int) and name:
                names[index] = name
        return names

    def _spot_pair_name(self, item: dict, token_names: dict[int, str]) -> str | None:
        raw_name = str(item.get("name", "")).strip().upper()
        if "/" in raw_name:
            pair_name = raw_name
        else:
            token_indexes = item.get("tokens")
            if not isinstance(token_indexes, list) or len(token_indexes) < 2:
                return None
            base = token_names.get(token_indexes[0])
            quote = token_names.get(token_indexes[1])
            if not base or not quote:
                return None
            pair_name = f"{base}/{quote}"

        base, quote = pair_name.split("/", 1)
        if not base or quote not in {"USDC", "USDT"}:
            return None
        return pair_name

    def _parse_spot_rows(self, contexts: list[dict], pair_names: dict[str, str]) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for item in contexts:
            raw_coin = str(item.get("coin", "")).strip().upper()
            if not raw_coin:
                continue
            raw_pair = pair_names.get(raw_coin, raw_coin if "/" in raw_coin else "")
            if not raw_pair or "/" not in raw_pair:
                continue
            base, quote = raw_pair.split("/", 1)
            if quote not in {"USDC", "USDT"}:
                continue
            mid = parse_float(item.get("midPx"))
            if mid is None:
                continue
            symbol, base_symbol, normalized_quote = normalize_usdt_symbol(f"{base}USDT")
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base_symbol,
                    quote=normalized_quote,
                    exchange=self.name,
                    market_type=MarketType.SPOT,
                    bid=mid,
                    ask=mid,
                    bid_size=None,
                    ask_size=None,
                    volume_24h_usdt=parse_float(item.get("dayNtlVlm")),
                    mark_price=parse_float(item.get("markPx")),
                    timestamp=now,
                    raw_symbol=raw_coin,
                )
            )
        return rows

    def _parse_perp_rows(self, universe: Iterable[object], contexts: list[dict]) -> list[MarketSnapshot]:
        rows: list[MarketSnapshot] = []
        now = utc_now()
        for asset, item in zip(universe, contexts):
            if not isinstance(asset, dict):
                continue
            if asset.get("isDelisted") is True:
                continue
            raw_coin = str(asset.get("name", "")).strip()
            if not raw_coin or raw_coin.startswith("@") or "/" in raw_coin:
                continue
            base_coin = raw_coin.split(":", 1)[1] if ":" in raw_coin else raw_coin
            mid = parse_float(item.get("midPx"))
            if mid is None:
                continue
            symbol, base, quote = normalize_usdt_symbol(f"{base_coin}USDT")
            funding = parse_float(item.get("funding"))
            dex = raw_coin.split(":", 1)[0].strip().lower() if ":" in raw_coin else "main"
            requires_priority_book = raw_coin in self.priority_order_book_markets
            rows.append(
                MarketSnapshot(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    exchange=self.name,
                    market_type=MarketType.FUTURE,
                    bid=mid,
                    ask=mid,
                    bid_size=None,
                    ask_size=None,
                    volume_24h_usdt=parse_float(item.get("dayNtlVlm")),
                    funding_rate_pct=funding * 100 if funding is not None else None,
                    funding_interval_hours=1,
                    mark_price=parse_float(item.get("markPx")),
                    index_price=parse_float(item.get("oraclePx")),
                    timestamp=now,
                    raw_symbol=raw_coin,
                    dex=dex,
                    contract_size_multiplier=1.0,
                    data_source="Hyperliquid public metaAndAssetCtxs",
                    is_estimated=requires_priority_book,
                    estimated_fields=["bid", "ask"] if requires_priority_book else [],
                )
            )
        return rows
