from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import httpx

from app.exchanges.base import DEFAULT_HEADERS, DEFAULT_LIMITS, parse_float
from app.models.hyperliquid_trade_status import (
    HyperliquidActionState,
    HyperliquidMarketTradeStatus,
    HyperliquidTradeActionStatus,
    HyperliquidTradeStatusResult,
    HyperliquidTradeStatusWatch,
    HyperliquidTradeStatusWatchEvent,
)

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
AlertSender = Callable[[str], Awaitable[None]]


class HyperliquidTradeStatusError(RuntimeError):
    pass


def normalize_hyperliquid_dex(value: str | None) -> str:
    normalized = (value or "main").strip().lower()
    return normalized or "main"


def normalize_hyperliquid_symbol(value: str) -> str:
    normalized = value.strip().upper().replace("-", "").replace("_", "")
    if normalized.endswith(("USDT", "USDC")):
        normalized = normalized[:-4]
    if not normalized:
        raise ValueError("请输入有效的 Hyperliquid 标的")
    return normalized


def hyperliquid_dex_from_raw_symbol(raw_symbol: str) -> str:
    if ":" not in raw_symbol:
        return "main"
    return normalize_hyperliquid_dex(raw_symbol.split(":", 1)[0])


def _raw_base(raw_symbol: str) -> str:
    return raw_symbol.split(":", 1)[-1].strip().upper()


def _float_or_none(value: Any) -> float | None:
    return parse_float(value)


def _book_side(
    levels: object,
    *,
    is_buy: bool,
) -> tuple[float | None, float | None, float | None]:
    if not isinstance(levels, list):
        return None, None, None
    parsed: list[tuple[float, float]] = []
    for item in levels:
        if not isinstance(item, dict):
            continue
        price = parse_float(item.get("px"))
        size = parse_float(item.get("sz"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        parsed.append((price, size))
    if not parsed:
        return None, None, None
    best = parsed[0][0]
    boundary_01 = best * (1.001 if is_buy else 0.999)
    boundary_1 = best * (1.01 if is_buy else 0.99)
    depth_01 = sum(
        price * size
        for price, size in parsed
        if (price <= boundary_01 if is_buy else price >= boundary_01)
    )
    depth_1 = sum(
        price * size
        for price, size in parsed
        if (price <= boundary_1 if is_buy else price >= boundary_1)
    )
    return best, depth_01, depth_1


def _blocked_action(reason_code: str, reason: str) -> HyperliquidTradeActionStatus:
    return HyperliquidTradeActionStatus(
        state=HyperliquidActionState.BLOCKED,
        reason_code=reason_code,
        reason=reason,
    )


def _open_action(
    *,
    price: float | None,
    depth: float | None,
    is_delisted: bool,
    at_cap: bool | None,
) -> HyperliquidTradeActionStatus:
    if is_delisted:
        return _blocked_action("DELISTED", "官方元数据已标记下架，不能新开或增加仓位")
    if at_cap is True:
        return _blocked_action(
            "OPEN_INTEREST_CAP",
            "官方未平仓量已达上限；普通订单不能新开或增加仓位",
        )
    if price is None:
        return HyperliquidTradeActionStatus(
            state=HyperliquidActionState.UNKNOWN,
            reason_code="NO_LIQUIDITY",
            reason="当前公开订单簿没有这一侧报价，无法确认可成交",
        )
    if at_cap is None:
        state = HyperliquidActionState.UNKNOWN
        reason_code = "CAP_STATUS_UNKNOWN"
        reason = "盘口存在，但未取得官方未平仓量上限状态"
    else:
        state = HyperliquidActionState.AVAILABLE
        reason_code = "PUBLIC_MARKET_AVAILABLE"
        reason = "官方未报告 OI 上限且盘口存在；账户级订单仍可能被拒绝"
    return HyperliquidTradeActionStatus(
        state=state,
        reason_code=reason_code,
        reason=reason,
        executable_price=price,
        depth_1pct_usdt=depth,
    )


def _reduce_action(
    *,
    price: float | None,
    depth: float | None,
    is_delisted: bool,
    closes: str,
) -> HyperliquidTradeActionStatus:
    if is_delisted:
        return _blocked_action("DELISTED", "市场已下架；需等待平台结算或按平台指引处理")
    if price is None:
        return HyperliquidTradeActionStatus(
            state=HyperliquidActionState.UNKNOWN,
            reason_code="NO_LIQUIDITY",
            reason="当前公开订单簿没有这一侧报价，无法确认可成交",
        )
    return HyperliquidTradeActionStatus(
        state=HyperliquidActionState.AVAILABLE,
        reason_code="REDUCE_ONLY_AVAILABLE",
        reason=f"公开规则允许已有对应{closes}仓时使用 Reduce Only 平{closes}",
        executable_price=price,
        depth_1pct_usdt=depth,
    )


class HyperliquidTradeStatusService:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0, read=10.0, write=5.0, pool=8.0),
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            limits=DEFAULT_LIMITS,
            http2=False,
            trust_env=True,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def _post(self, payload: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(INFO_URL, json=payload)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.25 * (attempt + 1))
        detail = f"{last_error.__class__.__name__}: {last_error}" if last_error else "unknown error"
        raise HyperliquidTradeStatusError(
            f"Hyperliquid {payload.get('type', 'info')} 请求失败：{detail}"
        ) from last_error

    async def fetch_status(
        self,
        symbol: str,
        *,
        dex: str | None = None,
        raw_symbol: str | None = None,
    ) -> HyperliquidTradeStatusResult:
        query = normalize_hyperliquid_symbol(symbol)
        raw = raw_symbol.strip() if raw_symbol else None
        selected_dex = normalize_hyperliquid_dex(dex) if dex else None
        if raw:
            selected_dex = selected_dex or hyperliquid_dex_from_raw_symbol(raw)

        dexes = await self._candidate_dexes(query, selected_dex, raw)
        rows = await asyncio.gather(
            *(self._fetch_dex_status(query, dex_name, raw) for dex_name in dexes)
        )
        markets = [market for group in rows for market in group]
        observed_at = max(
            (market.observed_at for market in markets),
            default=datetime.now(UTC),
        )
        return HyperliquidTradeStatusResult(
            query=symbol,
            observed_at=observed_at,
            markets=markets,
            limitations=[
                "公开接口只能判断市场级限制；账户余额、仓位方向、子账户和 reduce-only 数量需结合真实订单错误确认。",
                "1% 深度来自当前公开盘口快照，不是成交保证；手续费未计入。",
            ],
        )

    async def _candidate_dexes(
        self,
        query: str,
        selected_dex: str | None,
        raw_symbol: str | None,
    ) -> list[str]:
        if selected_dex:
            return [selected_dex]

        dex_payload, metas_payload = await asyncio.gather(
            self._post({"type": "perpDexs"}),
            self._post({"type": "allPerpMetas"}),
        )
        dex_names = ["main"]
        if isinstance(dex_payload, list):
            dex_names.extend(
                str(item.get("name", "")).strip().lower()
                for item in dex_payload
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            )
        if not isinstance(metas_payload, list):
            return dex_names

        matches: list[str] = []
        for dex_name, meta in zip(dex_names, metas_payload, strict=False):
            universe = meta.get("universe", []) if isinstance(meta, dict) else []
            if any(
                self._asset_matches(asset, query, raw_symbol)
                for asset in universe
                if isinstance(asset, dict)
            ):
                matches.append(dex_name)
        return matches

    @staticmethod
    def _asset_matches(asset: dict[str, Any], query: str, raw_symbol: str | None) -> bool:
        asset_raw = str(asset.get("name", "")).strip()
        if not asset_raw:
            return False
        if raw_symbol:
            return asset_raw.upper() == raw_symbol.upper()
        return _raw_base(asset_raw) == query

    async def _fetch_dex_status(
        self,
        query: str,
        dex: str,
        raw_symbol: str | None,
    ) -> list[HyperliquidMarketTradeStatus]:
        dex_arg = "" if dex == "main" else dex
        meta_body: dict[str, Any] = {"type": "metaAndAssetCtxs"}
        cap_body: dict[str, Any] = {"type": "perpsAtOpenInterestCap"}
        if dex_arg:
            meta_body["dex"] = dex_arg
            cap_body["dex"] = dex_arg
        meta_payload, cap_payload = await asyncio.gather(
            self._post(meta_body),
            self._post(cap_body),
        )
        if not isinstance(meta_payload, list) or len(meta_payload) < 2:
            return []
        meta = meta_payload[0] if isinstance(meta_payload[0], dict) else {}
        universe = meta.get("universe", [])
        contexts = meta_payload[1] if isinstance(meta_payload[1], list) else []
        caps = {
            str(item).strip().upper()
            for item in cap_payload
            if isinstance(item, str) and item.strip()
        } if isinstance(cap_payload, list) else None

        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for asset, context in zip(universe, contexts, strict=False):
            if not isinstance(asset, dict) or not isinstance(context, dict):
                continue
            if self._asset_matches(asset, query, raw_symbol):
                candidates.append((asset, context))

        books = await asyncio.gather(
            *(self._post({"type": "l2Book", "coin": asset["name"]}) for asset, _ in candidates),
            return_exceptions=True,
        )
        return [
            self._build_market_status(dex, asset, context, caps, book)
            for (asset, context), book in zip(candidates, books, strict=True)
        ]

    def _build_market_status(
        self,
        dex: str,
        asset: dict[str, Any],
        context: dict[str, Any],
        caps: set[str] | None,
        book: object,
    ) -> HyperliquidMarketTradeStatus:
        raw_symbol = str(asset.get("name", "")).strip()
        levels = book.get("levels", []) if isinstance(book, dict) else []
        bids = levels[0] if isinstance(levels, list) and len(levels) > 0 else []
        asks = levels[1] if isinstance(levels, list) and len(levels) > 1 else []
        best_bid, bid_depth_01, bid_depth = _book_side(bids, is_buy=False)
        best_ask, ask_depth_01, ask_depth = _book_side(asks, is_buy=True)
        mark_price = _float_or_none(context.get("markPx"))
        open_interest = _float_or_none(context.get("openInterest"))
        at_cap = raw_symbol.upper() in caps if caps is not None else None
        is_delisted = asset.get("isDelisted") is True
        observed_at = datetime.now(UTC)
        if isinstance(book, dict):
            book_time = parse_float(book.get("time"))
            if book_time is not None:
                observed_at = datetime.fromtimestamp(book_time / 1000, tz=UTC)
        return HyperliquidMarketTradeStatus(
            symbol=f"{_raw_base(raw_symbol)}USDT",
            dex=dex,
            raw_symbol=raw_symbol,
            observed_at=observed_at,
            at_open_interest_cap=at_cap,
            is_delisted=is_delisted,
            only_isolated=asset.get("onlyIsolated") is True,
            margin_mode=str(asset.get("marginMode")) if asset.get("marginMode") else None,
            max_leverage=int(asset["maxLeverage"])
            if isinstance(asset.get("maxLeverage"), (int, float))
            else None,
            size_decimals=int(asset["szDecimals"])
            if isinstance(asset.get("szDecimals"), (int, float))
            else None,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth_01pct_usdt=bid_depth_01,
            ask_depth_01pct_usdt=ask_depth_01,
            bid_depth_1pct_usdt=bid_depth,
            ask_depth_1pct_usdt=ask_depth,
            mark_price=mark_price,
            oracle_price=_float_or_none(context.get("oraclePx")),
            open_interest=open_interest,
            open_interest_usdt=(
                open_interest * mark_price
                if open_interest is not None and mark_price is not None
                else None
            ),
            volume_24h_usdt=_float_or_none(context.get("dayNtlVlm")),
            funding_rate_pct=(
                funding * 100
                if (funding := _float_or_none(context.get("funding"))) is not None
                else None
            ),
            buy_open=_open_action(
                price=best_ask,
                depth=ask_depth,
                is_delisted=is_delisted,
                at_cap=at_cap,
            ),
            sell_open=_open_action(
                price=best_bid,
                depth=bid_depth,
                is_delisted=is_delisted,
                at_cap=at_cap,
            ),
            buy_reduce_only=_reduce_action(
                price=best_ask,
                depth=ask_depth,
                is_delisted=is_delisted,
                closes="空",
            ),
            sell_reduce_only=_reduce_action(
                price=best_bid,
                depth=bid_depth,
                is_delisted=is_delisted,
                closes="多",
            ),
        )


class HyperliquidTradeStatusWatchRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def list(self) -> list[HyperliquidTradeStatusWatch]:
        cursor = await self.db.execute(
            "SELECT payload FROM hyperliquid_trade_status_watchlist ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [HyperliquidTradeStatusWatch.model_validate_json(row["payload"]) for row in rows]

    async def upsert(self, watch: HyperliquidTradeStatusWatch) -> HyperliquidTradeStatusWatch:
        payload = watch.model_dump_json()
        await self.db.execute(
            """
            INSERT INTO hyperliquid_trade_status_watchlist (id, raw_symbol, dex, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              raw_symbol = excluded.raw_symbol,
              dex = excluded.dex,
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (
                watch.id,
                watch.raw_symbol,
                watch.dex,
                payload,
                watch.created_at.isoformat(),
                watch.updated_at.isoformat(),
            ),
        )
        await self.db.commit()
        return watch

    async def delete(self, watch_id: str) -> None:
        await self.db.execute(
            "DELETE FROM hyperliquid_trade_status_watchlist WHERE id = ?",
            (watch_id,),
        )
        await self.db.commit()


def build_hyperliquid_recovery_message(event: HyperliquidTradeStatusWatchEvent) -> str:
    market = event.market
    sides = "、".join("买入/做多" if side == "buy" else "卖出/做空" for side in event.recovered_sides)
    return (
        f"[Hyperliquid 增仓恢复] {market.raw_symbol} ({market.dex})\n"
        f"恢复方向：{sides}\n"
        "官方 OI 上限列表：已移除\n"
        f"买一/卖一：{market.best_bid or '-'} / {market.best_ask or '-'}\n"
        f"1% 卖出/买入深度：{market.bid_depth_1pct_usdt or 0:.2f} / "
        f"{market.ask_depth_1pct_usdt or 0:.2f} USDT\n"
        f"24h 成交额：{market.volume_24h_usdt or 0:.2f} USDT\n"
        f"资金费率：{market.funding_rate_pct or 0:.6f}% / {market.funding_interval_hours}h\n"
        "手续费：未计入（账户等级相关）；市场倍率：1x\n"
        "说明：这是公开市场恢复信号，不保证特定账户订单通过；平仓仍应使用 Reduce Only。"
    )


class HyperliquidTradeStatusMonitor:
    def __init__(
        self,
        repository: HyperliquidTradeStatusWatchRepository,
        status_service: HyperliquidTradeStatusService,
        alert_sender: AlertSender | None = None,
    ) -> None:
        self.repository = repository
        self.status_service = status_service
        self.alert_sender = alert_sender

    async def check_once(self) -> list[HyperliquidTradeStatusWatchEvent]:
        events: list[HyperliquidTradeStatusWatchEvent] = []
        for watch in await self.repository.list():
            if not watch.enabled:
                continue
            now = datetime.now(UTC)
            try:
                result = await self.status_service.fetch_status(
                    watch.symbol,
                    dex=watch.dex,
                    raw_symbol=watch.raw_symbol,
                )
                market = next(
                    (
                        item
                        for item in result.markets
                        if item.raw_symbol.upper() == watch.raw_symbol.upper()
                        and item.dex == watch.dex
                    ),
                    None,
                )
                if market is None:
                    raise RuntimeError("未找到指定 Hyperliquid 原始市场")
                recovered: list[str] = []
                if (
                    watch.monitor_buy
                    and watch.last_buy_state is not None
                    and watch.last_buy_state != HyperliquidActionState.AVAILABLE
                    and market.buy_open.state == HyperliquidActionState.AVAILABLE
                ):
                    recovered.append("buy")
                if (
                    watch.monitor_sell
                    and watch.last_sell_state is not None
                    and watch.last_sell_state != HyperliquidActionState.AVAILABLE
                    and market.sell_open.state == HyperliquidActionState.AVAILABLE
                ):
                    recovered.append("sell")
                event = HyperliquidTradeStatusWatchEvent(
                    watch_id=watch.id,
                    recovered_sides=recovered,
                    market=market,
                ) if recovered else None
                updated = watch.model_copy(
                    update={
                        "last_buy_state": market.buy_open.state,
                        "last_sell_state": market.sell_open.state,
                        "last_checked_at": now,
                        "last_notified_at": now if event else watch.last_notified_at,
                        "last_error": None,
                        "updated_at": now,
                    }
                )
                await self.repository.upsert(updated)
                if event is not None:
                    events.append(event)
                    if self.alert_sender is not None:
                        await self.alert_sender(build_hyperliquid_recovery_message(event))
            except Exception as exc:
                logger.exception("Hyperliquid trade status watch failed id=%s", watch.id)
                await self.repository.upsert(
                    watch.model_copy(
                        update={
                            "last_checked_at": now,
                            "last_error": f"{exc.__class__.__name__}: {exc}",
                            "updated_at": now,
                        }
                    )
                )
        return events

    async def run(self, stop_event: asyncio.Event, interval_seconds: float = 15.0) -> None:
        while not stop_event.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
