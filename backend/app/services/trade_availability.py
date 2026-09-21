from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import aiosqlite
import httpx

from app.exchanges.base import DEFAULT_HEADERS, DEFAULT_LIMITS, ExchangeAdapter, parse_float
from app.models.market import MarketSnapshot, MarketType
from app.models.orderbook import OrderBookSnapshot
from app.models.pair_spread import normalize_pair_spread_symbol
from app.models.trade_availability import (
    MarketTradeAvailability,
    SpotTransferAvailability,
    SpotTransferNetworkStatus,
    TradeActionStatus,
    TradeAvailabilityCoverage,
    TradeAvailabilityResult,
    TradeAvailabilityState,
    TradeAvailabilityWatch,
    TradeAvailabilityWatchEvent,
    TradeDiagnosticEvidence,
    TradeEvidenceScope,
    TradeEvidenceState,
    TransferAvailabilityState,
)
from app.services.hyperliquid_trade_status import HyperliquidTradeStatusService
from app.services.snapshot_store import SnapshotStore

logger = logging.getLogger(__name__)

AlertSender = Callable[[str], Awaitable[None]]
CORE_EXCHANGES = ("binance", "okx", "bybit", "gate", "bitget")
EXCHANGE_ORDER = (*CORE_EXCHANGES, "hyperliquid", "aster", "lighter")
PUBLIC_TRANSFER_EXCHANGES = {"binance", "gate", "bitget"}


@dataclass
class _PublicMarketInfo:
    trading: bool | None
    buy_allowed: bool | None
    sell_allowed: bool | None
    status_code: str
    source: str
    restrictions: list[str] = field(default_factory=list)
    force_reduce_only: bool = False
    maker_fee_pct: float | None = None
    taker_fee_pct: float | None = None
    contract_size_multiplier: float = 1.0
    volume_24h_usdt: float | None = None
    funding_rate_pct: float | None = None
    funding_interval_hours: int | None = None
    mark_price: float | None = None
    index_price: float | None = None


def _coverage_tier(exchange: str) -> str:
    if exchange in CORE_EXCHANGES:
        return "core"
    if exchange == "hyperliquid":
        return "existing"
    return "evaluated"


def _coverage() -> list[TradeAvailabilityCoverage]:
    notes = {
        "binance": "exchangeInfo + 实时深度 + 匿名公开逐链充提状态",
        "okx": "public instruments + 实时深度；充提状态官方接口需 API Key",
        "bybit": "instruments-info + 实时深度；充提状态官方接口需 API Key",
        "gate": "现货方向/永续状态 + 实时深度 + 匿名公开逐链充提状态",
        "bitget": "symbols/contracts + 实时深度 + 匿名公开逐链充提状态",
        "hyperliquid": "DEX 原始市场、OI cap 与 l2Book",
        "aster": "已验证 Binance 兼容交易与深度接口；未验证匿名公开充提接口",
        "lighter": "已验证 active/frozen/force_reduce_only 与 WebSocket 深度",
    }
    return [
        TradeAvailabilityCoverage(
            exchange=exchange,
            tier=_coverage_tier(exchange),
            public_status_supported=True,
            orderbook_supported=True,
            note=notes[exchange],
        )
        for exchange in EXCHANGE_ORDER
    ]


def _diagnostics(info: _PublicMarketInfo, error: str | None) -> list[TradeDiagnosticEvidence]:
    public_message = (
        f"公开状态 {info.status_code}"
        if not info.restrictions
        else "；".join(info.restrictions)
    )
    return [
        TradeDiagnosticEvidence(
            scope=TradeEvidenceScope.PUBLIC_MARKET,
            state=TradeEvidenceState.ERROR if error else TradeEvidenceState.CONFIRMED,
            reason_code="PUBLIC_METADATA_ERROR" if error else "PUBLIC_STATUS_OBSERVED",
            message=error or public_message,
            source=info.source,
        ),
        TradeDiagnosticEvidence(
            scope=TradeEvidenceScope.ACCOUNT,
            state=TradeEvidenceState.NOT_CHECKED,
            reason_code="ACCOUNT_NOT_AUTHORIZED",
            message="未接入账户私有权限；余额、仓位、保证金、地区与风控限制未核验",
        ),
        TradeDiagnosticEvidence(
            scope=TradeEvidenceScope.ORDER_ERROR,
            state=TradeEvidenceState.NOT_PROVIDED,
            reason_code="ORDER_ERROR_NOT_PROVIDED",
            message="没有发送探测订单，也没有本次真实订单原始错误",
        ),
    ]


def _blocked(reason_code: str, reason: str) -> TradeActionStatus:
    return TradeActionStatus(
        state=TradeAvailabilityState.BLOCKED,
        reason_code=reason_code,
        reason=reason,
        scope=TradeEvidenceScope.PUBLIC_MARKET,
    )


def _open_action(
    *,
    side: str,
    info: _PublicMarketInfo,
    price: float | None,
    depth: float | None,
) -> TradeActionStatus:
    allowed = info.buy_allowed if side == "buy" else info.sell_allowed
    if info.trading is False or allowed is False:
        return _blocked(
            "PUBLIC_MARKET_RESTRICTED",
            "；".join(info.restrictions) or f"公开状态 {info.status_code} 不允许该方向交易",
        )
    if info.force_reduce_only:
        return _blocked(
            "FORCE_REDUCE_ONLY",
            "公开市场配置为仅减仓；普通订单不能新开或增加仓位",
        )
    if info.trading is None or allowed is None:
        return TradeActionStatus(
            state=TradeAvailabilityState.UNKNOWN,
            reason_code="PUBLIC_STATUS_UNKNOWN",
            reason="公开市场状态未确认，不能仅凭聚合报价判断可交易",
            scope=TradeEvidenceScope.PUBLIC_MARKET,
        )
    if price is None:
        return TradeActionStatus(
            state=TradeAvailabilityState.UNKNOWN,
            reason_code="LIVE_ORDERBOOK_UNAVAILABLE",
            reason="公开状态允许交易，但未取得该方向实时订单簿",
            scope=TradeEvidenceScope.PUBLIC_MARKET,
        )
    return TradeActionStatus(
        state=TradeAvailabilityState.AVAILABLE,
        reason_code="PUBLIC_MARKET_AVAILABLE",
        reason="公开市场状态允许且实时盘口有报价；账户级订单仍可能被拒绝",
        scope=TradeEvidenceScope.PUBLIC_MARKET,
        executable_price=price,
        depth_1pct_usdt=depth,
    )


def _reduce_action(
    *,
    market_type: MarketType,
    closes: str,
    info: _PublicMarketInfo,
    price: float | None,
    depth: float | None,
) -> TradeActionStatus:
    if market_type == MarketType.SPOT:
        return TradeActionStatus(
            state=TradeAvailabilityState.NOT_APPLICABLE,
            reason_code="REDUCE_ONLY_NOT_SUPPORTED_FOR_SPOT",
            reason="现货市场没有 Reduce Only 持仓语义",
            scope=TradeEvidenceScope.PLATFORM_CAPABILITY,
        )
    if info.trading is False and not info.force_reduce_only:
        return _blocked(
            "PUBLIC_MARKET_RESTRICTED",
            "公开合约状态未表明仍可 Reduce Only；需以平台处置规则或真实订单错误为准",
        )
    if info.trading is None:
        return TradeActionStatus(
            state=TradeAvailabilityState.UNKNOWN,
            reason_code="PUBLIC_STATUS_UNKNOWN",
            reason=f"公开市场状态未确认，无法判断 Reduce Only 平{closes}",
            scope=TradeEvidenceScope.PUBLIC_MARKET,
        )
    if price is None:
        return TradeActionStatus(
            state=TradeAvailabilityState.UNKNOWN,
            reason_code="LIVE_ORDERBOOK_UNAVAILABLE",
            reason=f"未取得实时盘口，无法确认 Reduce Only 平{closes}的可成交价格",
            scope=TradeEvidenceScope.PUBLIC_MARKET,
        )
    return TradeActionStatus(
        state=TradeAvailabilityState.CONDITIONAL,
        reason_code="REDUCE_ONLY_REQUIRES_POSITION",
        reason=f"公开市场允许；账户必须有对应{closes}仓，数量不得超过持仓，并使用 Reduce Only",
        scope=TradeEvidenceScope.ACCOUNT,
        executable_price=price,
        depth_1pct_usdt=depth,
    )


def _book_metrics(
    book: OrderBookSnapshot | None,
    *,
    contract_size_multiplier: float,
) -> tuple[float | None, float | None, float | None, float | None]:
    if book is None:
        return None, None, None, None
    bids = sorted(book.bids, key=lambda row: row.price, reverse=True)
    asks = sorted(book.asks, key=lambda row: row.price)
    best_bid = bids[0].price if bids else None
    best_ask = asks[0].price if asks else None
    bid_floor = best_bid * 0.99 if best_bid is not None else None
    ask_ceiling = best_ask * 1.01 if best_ask is not None else None
    bid_depth = (
        sum(row.price * row.size * contract_size_multiplier for row in bids if row.price >= bid_floor)
        if bid_floor is not None
        else None
    )
    ask_depth = (
        sum(row.price * row.size * contract_size_multiplier for row in asks if row.price <= ask_ceiling)
        if ask_ceiling is not None
        else None
    )
    return best_bid, best_ask, bid_depth, ask_depth


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _transfer_state(
    networks: list[SpotTransferNetworkStatus],
    field_name: str,
) -> TransferAvailabilityState:
    values = [getattr(network, field_name) for network in networks]
    if not values or any(value is None for value in values):
        return TransferAvailabilityState.UNKNOWN
    if all(values):
        return TransferAvailabilityState.ENABLED
    if any(values):
        return TransferAvailabilityState.PARTIAL
    return TransferAvailabilityState.DISABLED


def _spot_asset(market: MarketSnapshot) -> str:
    raw = market.raw_symbol.strip().upper()
    for separator in ("-", "_"):
        parts = raw.split(separator)
        if len(parts) > 1 and parts[-1] == market.quote.upper():
            return separator.join(parts[:-1])
    quote_asset = market.quote.upper()
    if raw.endswith(quote_asset) and len(raw) > len(quote_asset):
        return raw[: -len(quote_asset)]
    return market.base.upper()


def _spot_transfer_status(
    *,
    asset: str,
    source: str,
    networks: list[SpotTransferNetworkStatus],
    publicly_queryable: bool = True,
    note: str = "",
    error: str | None = None,
    observed_at: datetime | None = None,
) -> SpotTransferAvailability:
    deposit_state = _transfer_state(networks, "deposit_enabled")
    withdraw_state = _transfer_state(networks, "withdraw_enabled")
    states = {deposit_state, withdraw_state}
    if states == {TransferAvailabilityState.ENABLED}:
        all_enabled = True
    elif states & {TransferAvailabilityState.PARTIAL, TransferAvailabilityState.DISABLED}:
        all_enabled = False
    else:
        all_enabled = None
    return SpotTransferAvailability(
        asset=asset,
        deposit_state=deposit_state,
        withdraw_state=withdraw_state,
        all_enabled=all_enabled,
        publicly_queryable=publicly_queryable,
        source=source,
        observed_at=observed_at,
        networks=networks,
        note=note,
        error=error,
    )


class TradeAvailabilityService:
    def __init__(
        self,
        store: SnapshotStore,
        adapters: list[ExchangeAdapter],
        hyperliquid_service: HyperliquidTradeStatusService,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.store = store
        self.adapters = {adapter.name.lower(): adapter for adapter in adapters}
        self.hyperliquid_service = hyperliquid_service
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0, read=10.0, write=5.0, pool=8.0),
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            limits=DEFAULT_LIMITS,
            http2=False,
            trust_env=True,
        )
        self._owns_client = client is None
        self._cache: dict[str, tuple[datetime, Any]] = {}
        self._cache_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()
        for adapter in self.adapters.values():
            client = getattr(adapter, "client", None)
            if client is not None and not client.is_closed:
                await client.aclose()

    async def _get_json(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        cache_seconds: float = 20,
    ) -> Any:
        now = datetime.now(UTC)
        key = cache_key or url
        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < timedelta(seconds=cache_seconds):
                return cached[1]
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                payload = response.json()
                async with self._cache_lock:
                    self._cache[key] = (datetime.now(UTC), payload)
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.2)
        detail = f"{last_error.__class__.__name__}: {last_error}" if last_error else "unknown error"
        raise RuntimeError(detail) from last_error

    async def fetch_status(
        self,
        symbol: str,
        *,
        exchange: str | None = None,
        market_type: MarketType | None = None,
        raw_symbol: str | None = None,
        dex: str | None = None,
    ) -> TradeAvailabilityResult:
        normalized = normalize_pair_spread_symbol(symbol)
        selected_exchange = exchange.strip().lower() if exchange else None
        selected_raw = raw_symbol.strip().upper() if raw_symbol else None
        selected_dex = dex.strip().lower() if dex and dex.strip() else None
        candidates = self._matching_markets(
            normalized,
            exchange=selected_exchange,
            market_type=market_type,
            raw_symbol=selected_raw,
            dex=selected_dex,
        )
        results = await asyncio.gather(
            *(self._diagnose_market(market) for market in candidates),
            return_exceptions=True,
        )
        markets: list[MarketTradeAvailability] = []
        errors: dict[str, str] = {}
        for snapshot, result in zip(candidates, results, strict=True):
            key = self._market_key(snapshot)
            if isinstance(result, BaseException):
                errors[key] = f"{result.__class__.__name__}: {result}"
            else:
                market, market_error = result
                markets.append(market)
                if market_error:
                    errors[key] = market_error
        order = {name: index for index, name in enumerate(EXCHANGE_ORDER)}
        markets.sort(
            key=lambda item: (
                order.get(item.exchange, len(order)),
                0 if item.market_type == MarketType.SPOT else 1,
                item.dex or "",
                item.raw_symbol,
            )
        )
        observed_at = max((item.observed_at for item in markets), default=datetime.now(UTC))
        return TradeAvailabilityResult(
            query=symbol,
            observed_at=observed_at,
            markets=markets,
            coverage=_coverage(),
            errors=errors,
            limitations=[
                "公开状态不等于账户可下单：余额、仓位、保证金、地区、权限和风控需要私有账户或真实订单错误才能确认。",
                "系统不发送探测订单；Reduce Only 仅表示公开市场原则上支持，仍必须有方向和数量匹配的真实持仓。",
                "1% 深度为本次订单簿快照且手续费未计入，不构成成交保证。",
                "现货充提按公开逐链开关汇总；未知不等于关闭，账户、地区、地址与维护提示仍可能影响实际充提。",
            ],
        )

    def _matching_markets(
        self,
        symbol: str,
        *,
        exchange: str | None,
        market_type: MarketType | None,
        raw_symbol: str | None,
        dex: str | None,
    ) -> list[MarketSnapshot]:
        latest: dict[tuple[str, MarketType, str, str], MarketSnapshot] = {}
        for market in self.store.get_all_markets():
            market_exchange = market.exchange.lower()
            market_dex = self._dex(market)
            if market.symbol.upper() != symbol:
                continue
            if exchange and market_exchange != exchange:
                continue
            if market_type and market.market_type != market_type:
                continue
            if raw_symbol and market.raw_symbol.upper() != raw_symbol:
                continue
            if dex and market_dex != dex:
                continue
            key = (market_exchange, market.market_type, market.raw_symbol.upper(), market_dex or "")
            previous = latest.get(key)
            if previous is None or market.timestamp > previous.timestamp:
                latest[key] = market
        return list(latest.values())

    @staticmethod
    def _dex(market: MarketSnapshot) -> str | None:
        if market.exchange.lower() != "hyperliquid":
            return None
        if ":" not in market.raw_symbol:
            return "main"
        return market.raw_symbol.split(":", 1)[0].strip().lower() or "main"

    def _market_key(self, market: MarketSnapshot) -> str:
        dex = self._dex(market)
        return ":".join(
            part
            for part in (
                market.exchange.lower(),
                market.market_type.value,
                dex,
                market.raw_symbol,
            )
            if part
        )

    async def _diagnose_market(
        self,
        market: MarketSnapshot,
    ) -> tuple[MarketTradeAvailability, str | None]:
        if market.exchange.lower() == "hyperliquid":
            return await self._diagnose_hyperliquid(market), None
        requests = [self._public_market_info(market), self._fetch_book(market)]
        if market.market_type == MarketType.SPOT:
            requests.append(self._spot_transfer_info(market))
        results = await asyncio.gather(
            *requests,
            return_exceptions=True,
        )
        info_result, book_result = results[:2]
        metadata_error = None
        if isinstance(info_result, BaseException):
            metadata_error = f"公开状态请求失败：{info_result.__class__.__name__}: {info_result}"
            info = _PublicMarketInfo(
                trading=None,
                buy_allowed=None,
                sell_allowed=None,
                status_code="UNKNOWN",
                source="公开市场元数据",
                restrictions=[metadata_error],
            )
        else:
            info = info_result
        book_error = None
        book: OrderBookSnapshot | None
        if isinstance(book_result, BaseException):
            book_error = f"实时订单簿请求失败：{book_result.__class__.__name__}: {book_result}"
            book = None
        else:
            book = book_result
            if book is None:
                book_error = "实时订单簿接口未返回该原始市场"

        transfer_error = None
        spot_transfer: SpotTransferAvailability | None = None
        if market.market_type == MarketType.SPOT:
            transfer_result = results[2]
            if isinstance(transfer_result, BaseException):
                transfer_error = (
                    "现货充提状态请求失败："
                    f"{transfer_result.__class__.__name__}: {transfer_result}"
                )
                spot_transfer = self._unknown_spot_transfer(
                    market,
                    error=transfer_error,
                )
            else:
                spot_transfer = transfer_result

        best_bid, best_ask, bid_depth, ask_depth = _book_metrics(
            book,
            contract_size_multiplier=info.contract_size_multiplier,
        )
        display_bid = best_bid if best_bid is not None else market.bid
        display_ask = best_ask if best_ask is not None else market.ask
        restrictions = list(info.restrictions)
        if book_error:
            restrictions.append(f"{book_error}；买一卖一回退为聚合行情快照")
        now = datetime.now(UTC)
        combined_error = "；".join(
            item for item in (metadata_error, book_error, transfer_error) if item
        ) or None
        result = MarketTradeAvailability(
            exchange=market.exchange.lower(),
            market_type=market.market_type,
            symbol=market.symbol,
            raw_symbol=market.raw_symbol,
            dex=self._dex(market),
            coverage_tier=_coverage_tier(market.exchange.lower()),
            observed_at=now,
            market_data_updated_at=market.timestamp,
            orderbook_updated_at=book.timestamp if book is not None else market.timestamp,
            orderbook_source="实时订单簿" if book is not None else "聚合行情回退",
            public_status_code=info.status_code,
            public_status_source=info.source,
            public_restrictions=restrictions,
            diagnostics=_diagnostics(info, metadata_error),
            best_bid=display_bid,
            best_ask=display_ask,
            bid_depth_1pct_usdt=bid_depth,
            ask_depth_1pct_usdt=ask_depth,
            volume_24h_usdt=market.volume_24h_usdt or info.volume_24h_usdt,
            funding_rate_pct=(
                market.funding_rate_pct
                if market.funding_rate_pct is not None
                else info.funding_rate_pct
            ),
            funding_next_rate_pct=market.funding_next_rate_pct,
            funding_interval_hours=market.funding_interval_hours or info.funding_interval_hours,
            funding_next_time=market.funding_next_time,
            mark_price=market.mark_price or info.mark_price,
            index_price=market.index_price or info.index_price,
            maker_fee_pct=info.maker_fee_pct,
            taker_fee_pct=info.taker_fee_pct,
            market_multiplier=market.symbol_alias_price_multiplier,
            contract_size_multiplier=info.contract_size_multiplier,
            spot_transfer=spot_transfer,
            buy_open=_open_action(
                side="buy",
                info=info,
                price=best_ask,
                depth=ask_depth,
            ),
            sell_open=_open_action(
                side="sell",
                info=info,
                price=best_bid,
                depth=bid_depth,
            ),
            buy_reduce_only=_reduce_action(
                market_type=market.market_type,
                closes="空",
                info=info,
                price=best_ask,
                depth=ask_depth,
            ),
            sell_reduce_only=_reduce_action(
                market_type=market.market_type,
                closes="多",
                info=info,
                price=best_bid,
                depth=bid_depth,
            ),
        )
        return result, combined_error

    async def _fetch_book(self, market: MarketSnapshot) -> OrderBookSnapshot | None:
        adapter = self.adapters.get(market.exchange.lower())
        if adapter is None:
            raise RuntimeError("没有对应交易所订单簿适配器")
        return await adapter.fetch_order_book(
            market.symbol_alias_original_symbol or market.symbol,
            market.market_type,
            market.raw_symbol,
            limit=100,
        )

    async def _diagnose_hyperliquid(self, snapshot: MarketSnapshot) -> MarketTradeAvailability:
        dex = self._dex(snapshot) or "main"
        result = await self.hyperliquid_service.fetch_status(
            snapshot.symbol,
            dex=dex,
            raw_symbol=snapshot.raw_symbol,
        )
        source = next(
            (
                item
                for item in result.markets
                if item.dex == dex and item.raw_symbol.upper() == snapshot.raw_symbol.upper()
            ),
            None,
        )
        if source is None:
            raise RuntimeError("Hyperliquid 公开接口未返回指定 DEX 原始市场")

        def action(value: Any) -> TradeActionStatus:
            return TradeActionStatus(
                state=TradeAvailabilityState(value.state.value),
                reason_code=value.reason_code,
                reason=value.reason,
                scope=(
                    TradeEvidenceScope.ACCOUNT
                    if value.state.value == "conditional"
                    else TradeEvidenceScope.PUBLIC_MARKET
                ),
                executable_price=value.executable_price,
                depth_1pct_usdt=value.depth_1pct_usdt,
            )

        restrictions: list[str] = []
        if source.at_open_interest_cap is True:
            restrictions.append("官方 OI 已达上限，普通增仓受限")
        if source.is_delisted:
            restrictions.append("官方元数据标记已下架")
        info = _PublicMarketInfo(
            trading=not source.is_delisted,
            buy_allowed=not source.is_delisted and source.at_open_interest_cap is False,
            sell_allowed=not source.is_delisted and source.at_open_interest_cap is False,
            status_code=(
                "DELISTED"
                if source.is_delisted
                else "OPEN_INTEREST_CAP"
                if source.at_open_interest_cap is True
                else "TRADING"
                if source.at_open_interest_cap is False
                else "CAP_UNKNOWN"
            ),
            source="Hyperliquid public info API",
            restrictions=restrictions,
        )
        return MarketTradeAvailability(
            exchange="hyperliquid",
            market_type=MarketType.FUTURE,
            symbol=source.symbol,
            raw_symbol=source.raw_symbol,
            dex=source.dex,
            coverage_tier="existing",
            observed_at=source.observed_at,
            market_data_updated_at=snapshot.timestamp,
            orderbook_updated_at=source.observed_at,
            orderbook_source="Hyperliquid l2Book",
            public_status_code=info.status_code,
            public_status_source=info.source,
            public_restrictions=restrictions,
            diagnostics=_diagnostics(info, None),
            best_bid=source.best_bid,
            best_ask=source.best_ask,
            bid_depth_1pct_usdt=source.bid_depth_1pct_usdt,
            ask_depth_1pct_usdt=source.ask_depth_1pct_usdt,
            volume_24h_usdt=source.volume_24h_usdt,
            funding_rate_pct=source.funding_rate_pct,
            funding_interval_hours=source.funding_interval_hours,
            mark_price=source.mark_price,
            index_price=source.oracle_price,
            market_multiplier=snapshot.symbol_alias_price_multiplier,
            buy_open=action(source.buy_open),
            sell_open=action(source.sell_open),
            buy_reduce_only=action(source.buy_reduce_only),
            sell_reduce_only=action(source.sell_reduce_only),
        )

    def _unknown_spot_transfer(
        self,
        market: MarketSnapshot,
        *,
        error: str | None = None,
        note: str | None = None,
    ) -> SpotTransferAvailability:
        exchange = market.exchange.lower()
        sources = {
            "binance": "Binance public asset service",
            "okx": "OKX asset currencies（需 API Key）",
            "bybit": "Bybit coin info（需 API Key）",
            "gate": "Gate spot currencies",
            "bitget": "Bitget spot public coins",
            "aster": "Aster 公开接口",
            "lighter": "Lighter 公开接口",
        }
        unsupported_notes = {
            "okx": "官方充提币种接口需要 API Key；当前未接入账户凭据，不能公开核验",
            "bybit": "官方充提币种接口需要 API Key；当前未接入账户凭据，不能公开核验",
            "aster": "尚未找到已验证、可匿名访问的官方逐币充提状态接口",
            "lighter": "尚未接入可按现货币种核验的公开充提状态接口",
        }
        return _spot_transfer_status(
            asset=_spot_asset(market),
            source=sources.get(exchange, f"{market.exchange} 公开接口"),
            networks=[],
            publicly_queryable=exchange in PUBLIC_TRANSFER_EXCHANGES,
            note=note or unsupported_notes.get(exchange, "公开接口未返回可核验的逐链充提状态"),
            error=error,
        )

    async def _spot_transfer_info(self, market: MarketSnapshot) -> SpotTransferAvailability:
        exchange = market.exchange.lower()
        handler = getattr(self, f"_transfer_{exchange}", None)
        if handler is None:
            return self._unknown_spot_transfer(market)
        return await handler(market)

    async def _transfer_binance(self, market: MarketSnapshot) -> SpotTransferAvailability:
        asset = _spot_asset(market)
        payload = await self._get_json(
            "https://www.binance.com/bapi/capital/v1/public/capital/getNetworkCoinAll",
            cache_key="binance:spot:transfer-assets",
            cache_seconds=60,
        )
        if not isinstance(payload, dict) or str(payload.get("code")) != "000000":
            raise RuntimeError("Binance public asset service 返回异常")
        row = self._find(payload.get("data", []), "coin", asset)
        if row is None:
            return self._unknown_spot_transfer(
                market,
                note="Binance 公开币种列表未返回该现货资产",
            )
        networks = [
            SpotTransferNetworkStatus(
                network=str(item.get("networkDisplay") or item.get("network") or "UNKNOWN"),
                deposit_enabled=_optional_bool(item.get("depositEnable")),
                withdraw_enabled=_optional_bool(item.get("withdrawEnable")),
            )
            for item in row.get("networkList", [])
            if isinstance(item, dict)
        ]
        if not networks:
            networks = [
                SpotTransferNetworkStatus(
                    network="全部网络",
                    deposit_enabled=_optional_bool(row.get("depositAllEnable")),
                    withdraw_enabled=_optional_bool(row.get("withdrawAllEnable")),
                )
            ]
        return _spot_transfer_status(
            asset=asset,
            source="Binance public asset service",
            networks=networks,
            note="按公开返回的逐链开关汇总；账户、地区和地址限制未核验",
            observed_at=datetime.now(UTC),
        )

    async def _transfer_gate(self, market: MarketSnapshot) -> SpotTransferAvailability:
        asset = _spot_asset(market)
        payload = await self._get_json(
            f"https://api.gateio.ws/api/v4/spot/currencies/{quote(asset)}",
            cache_key=f"gate:spot:transfer:{asset}",
            cache_seconds=60,
        )
        if not isinstance(payload, dict):
            raise TypeError("Gate spot currencies 返回异常")
        networks = [
            SpotTransferNetworkStatus(
                network=str(item.get("name") or "UNKNOWN"),
                deposit_enabled=(
                    None
                    if _optional_bool(item.get("deposit_disabled")) is None
                    else not bool(_optional_bool(item.get("deposit_disabled")))
                ),
                withdraw_enabled=(
                    None
                    if _optional_bool(item.get("withdraw_disabled")) is None
                    else not bool(_optional_bool(item.get("withdraw_disabled")))
                ),
            )
            for item in payload.get("chains", [])
            if isinstance(item, dict)
        ]
        if not networks:
            deposit_disabled = _optional_bool(payload.get("deposit_disabled"))
            withdraw_disabled = _optional_bool(payload.get("withdraw_disabled"))
            networks = [
                SpotTransferNetworkStatus(
                    network=str(payload.get("chain") or "默认网络"),
                    deposit_enabled=None if deposit_disabled is None else not deposit_disabled,
                    withdraw_enabled=None if withdraw_disabled is None else not withdraw_disabled,
                )
            ]
        return _spot_transfer_status(
            asset=asset,
            source="Gate spot currencies",
            networks=networks,
            note="按公开返回的逐链开关汇总；延迟提现仍按开放处理并需结合平台提示",
            observed_at=datetime.now(UTC),
        )

    async def _transfer_bitget(self, market: MarketSnapshot) -> SpotTransferAvailability:
        asset = _spot_asset(market)
        payload = await self._get_json(
            f"https://api.bitget.com/api/v2/spot/public/coins?coin={quote(asset)}",
            cache_key=f"bitget:spot:transfer:{asset}",
            cache_seconds=60,
        )
        if not isinstance(payload, dict) or str(payload.get("code")) != "00000":
            raise RuntimeError("Bitget spot public coins 返回异常")
        row = self._find(payload.get("data", []), "coin", asset)
        if row is None:
            return self._unknown_spot_transfer(
                market,
                note="Bitget 公开币种列表未返回该现货资产",
            )
        networks = [
            SpotTransferNetworkStatus(
                network=str(item.get("chain") or "UNKNOWN"),
                deposit_enabled=_optional_bool(item.get("rechargeable")),
                withdraw_enabled=_optional_bool(item.get("withdrawable")),
            )
            for item in row.get("chains", [])
            if isinstance(item, dict)
        ]
        return _spot_transfer_status(
            asset=asset,
            source="Bitget spot public coins",
            networks=networks,
            note="按公开返回的逐链 rechargeable / withdrawable 开关汇总",
            observed_at=datetime.now(UTC),
        )

    async def _public_market_info(self, market: MarketSnapshot) -> _PublicMarketInfo:
        handler = getattr(self, f"_info_{market.exchange.lower()}", None)
        if handler is None:
            raise RuntimeError("该交易所尚无公开状态诊断器")
        return await handler(market)

    @staticmethod
    def _find(rows: Any, field_name: str, value: str) -> dict[str, Any] | None:
        for item in rows if isinstance(rows, list) else []:
            if isinstance(item, dict) and str(item.get(field_name, "")).upper() == value.upper():
                return item
        return None

    async def _info_binance(self, market: MarketSnapshot) -> _PublicMarketInfo:
        volume_24h = None
        if market.market_type == MarketType.SPOT:
            url = f"https://data-api.binance.vision/api/v3/exchangeInfo?symbol={quote(market.raw_symbol)}"
            volume_url = f"https://data-api.binance.vision/api/v3/ticker/24hr?symbol={quote(market.raw_symbol)}"
            payload = await self._get_json(url)
            try:
                ticker = await self._get_json(volume_url)
            except RuntimeError:
                ticker = None
            if isinstance(ticker, dict):
                volume_24h = parse_float(ticker.get("quoteVolume"))
            row = self._find(payload.get("symbols", []), "symbol", market.raw_symbol)
            source = "Binance spot exchangeInfo"
        else:
            url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
            payload = await self._get_json(url, cache_key="binance:future:exchangeInfo")
            row = self._find(payload.get("symbols", []), "symbol", market.raw_symbol)
            source = "Binance futures exchangeInfo"
        if row is None:
            return _PublicMarketInfo(False, False, False, "NOT_FOUND", source, ["公开元数据不存在该原始市场"])
        status = str(row.get("status", "UNKNOWN"))
        spot_allowed = row.get("isSpotTradingAllowed") is not False
        trading = status == "TRADING" and (spot_allowed or market.market_type == MarketType.FUTURE)
        restrictions = [] if trading else [f"公开状态为 {status}"]
        return _PublicMarketInfo(
            trading,
            trading,
            trading,
            status,
            source,
            restrictions,
            volume_24h_usdt=volume_24h,
        )

    async def _info_okx(self, market: MarketSnapshot) -> _PublicMarketInfo:
        inst_type = "SPOT" if market.market_type == MarketType.SPOT else "SWAP"
        url = (
            "https://www.okx.com/api/v5/public/instruments"
            f"?instType={inst_type}&instId={quote(market.raw_symbol)}"
        )
        payload = await self._get_json(url)
        row = self._find(payload.get("data", []), "instId", market.raw_symbol)
        source = "OKX public instruments"
        if row is None:
            return _PublicMarketInfo(False, False, False, "NOT_FOUND", source, ["公开元数据不存在该原始市场"])
        status = str(row.get("state", "UNKNOWN"))
        trading = status == "live"
        restrictions = [] if trading else [f"公开状态为 {status}"]
        contract_size = 1.0
        if market.market_type == MarketType.FUTURE:
            ct_val = parse_float(row.get("ctVal"))
            ct_mult = parse_float(row.get("ctMult")) or 1.0
            if ct_val is not None and ct_val > 0:
                contract_size = ct_val * ct_mult
        return _PublicMarketInfo(
            trading,
            trading,
            trading,
            status,
            source,
            restrictions,
            contract_size_multiplier=contract_size,
        )

    async def _info_bybit(self, market: MarketSnapshot) -> _PublicMarketInfo:
        category = "spot" if market.market_type == MarketType.SPOT else "linear"
        url = (
            "https://api.bybit.com/v5/market/instruments-info"
            f"?category={category}&symbol={quote(market.raw_symbol)}"
        )
        payload = await self._get_json(url)
        rows = payload.get("result", {}).get("list", []) if isinstance(payload, dict) else []
        row = self._find(rows, "symbol", market.raw_symbol)
        source = "Bybit instruments-info"
        if row is None:
            return _PublicMarketInfo(False, False, False, "NOT_FOUND", source, ["公开元数据不存在该原始市场"])
        status = str(row.get("status", "UNKNOWN"))
        trading = status == "Trading"
        restrictions = [] if trading else [f"公开状态为 {status}"]
        if row.get("isPreListing") is True:
            restrictions.append("公开元数据标记为 Pre-Market")
        return _PublicMarketInfo(trading, trading, trading, status, source, restrictions)

    async def _info_gate(self, market: MarketSnapshot) -> _PublicMarketInfo:
        raw = market.raw_symbol.replace("-", "_")
        if market.market_type == MarketType.SPOT:
            url = f"https://api.gateio.ws/api/v4/spot/currency_pairs/{quote(raw)}"
            row = await self._get_json(url)
            source = "Gate spot currency_pairs"
            status = str(row.get("trade_status", "unknown"))
            buy_allowed = status in {"tradable", "buyable"}
            sell_allowed = status in {"tradable", "sellable"}
            restrictions = []
            if not buy_allowed:
                restrictions.append(f"{status} 不允许普通买入")
            if not sell_allowed:
                restrictions.append(f"{status} 不允许普通卖出")
            fee = parse_float(row.get("fee"))
            return _PublicMarketInfo(
                buy_allowed or sell_allowed,
                buy_allowed,
                sell_allowed,
                status,
                source,
                restrictions,
                maker_fee_pct=fee,
                taker_fee_pct=fee,
            )
        url = f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{quote(raw)}"
        row = await self._get_json(url)
        source = "Gate futures contract"
        status = str(row.get("status", "unknown"))
        trading = status == "trading" and row.get("in_delisting") is not True
        restrictions = [] if trading else [f"公开状态为 {status}，delisting={bool(row.get('in_delisting'))}"]
        contract_size = parse_float(row.get("quanto_multiplier")) or 1.0
        maker = parse_float(row.get("maker_fee_rate"))
        taker = parse_float(row.get("taker_fee_rate"))
        return _PublicMarketInfo(
            trading,
            trading,
            trading,
            status,
            source,
            restrictions,
            maker_fee_pct=maker * 100 if maker is not None else None,
            taker_fee_pct=taker * 100 if taker is not None else None,
            contract_size_multiplier=contract_size,
        )

    async def _info_bitget(self, market: MarketSnapshot) -> _PublicMarketInfo:
        if market.market_type == MarketType.SPOT:
            url = f"https://api.bitget.com/api/v2/spot/public/symbols?symbol={quote(market.raw_symbol)}"
            payload = await self._get_json(url)
            row = self._find(payload.get("data", []), "symbol", market.raw_symbol)
            status_field = "status"
            expected = "online"
            source = "Bitget spot symbols"
        else:
            url = (
                "https://api.bitget.com/api/v2/mix/market/contracts"
                f"?productType=USDT-FUTURES&symbol={quote(market.raw_symbol)}"
            )
            payload = await self._get_json(url)
            row = self._find(payload.get("data", []), "symbol", market.raw_symbol)
            status_field = "symbolStatus"
            expected = "normal"
            source = "Bitget futures contracts"
        if row is None:
            return _PublicMarketInfo(False, False, False, "NOT_FOUND", source, ["公开元数据不存在该原始市场"])
        status = str(row.get(status_field, "unknown"))
        trading = status == expected
        restrictions = [] if trading else [f"公开状态为 {status}"]
        maker = parse_float(row.get("makerFeeRate"))
        taker = parse_float(row.get("takerFeeRate"))
        return _PublicMarketInfo(
            trading,
            trading,
            trading,
            status,
            source,
            restrictions,
            maker_fee_pct=maker * 100 if maker is not None else None,
            taker_fee_pct=taker * 100 if taker is not None else None,
        )

    async def _info_aster(self, market: MarketSnapshot) -> _PublicMarketInfo:
        if market.market_type == MarketType.SPOT:
            url = "https://sapi.asterdex.com/api/v1/exchangeInfo"
            key = "aster:spot:exchangeInfo"
            source = "Aster spot exchangeInfo"
            ticker_url = (
                "https://sapi.asterdex.com/api/v1/ticker/24hr"
                f"?symbol={quote(market.raw_symbol)}"
            )
            payload = await self._get_json(url, cache_key=key)
            ticker = await asyncio.gather(
                self._get_json(ticker_url),
                return_exceptions=True,
            )
            ticker = ticker[0] if isinstance(ticker[0], dict) else None
            premium: dict[str, Any] = {}
        else:
            url = "https://fapi.asterdex.com/fapi/v1/exchangeInfo"
            key = "aster:future:exchangeInfo"
            source = "Aster futures exchangeInfo"
            ticker_url = (
                "https://fapi.asterdex.com/fapi/v1/ticker/24hr"
                f"?symbol={quote(market.raw_symbol)}"
            )
            premium_url = (
                "https://fapi.asterdex.com/fapi/v1/premiumIndex"
                f"?symbol={quote(market.raw_symbol)}"
            )
            payload = await self._get_json(url, cache_key=key)
            optional = await asyncio.gather(
                self._get_json(ticker_url),
                self._get_json(premium_url),
                return_exceptions=True,
            )
            ticker = optional[0] if isinstance(optional[0], dict) else None
            premium_payload = optional[1]
            premium = premium_payload if isinstance(premium_payload, dict) else {}
        row = self._find(payload.get("symbols", []), "symbol", market.raw_symbol)
        if row is None:
            return _PublicMarketInfo(False, False, False, "NOT_FOUND", source, ["公开元数据不存在该原始市场"])
        status = str(row.get("status", "UNKNOWN"))
        trading = status == "TRADING"
        restrictions = [] if trading else [f"公开状态为 {status}"]
        volume = parse_float(ticker.get("quoteVolume")) if isinstance(ticker, dict) else None
        funding = parse_float(premium.get("lastFundingRate"))
        interval = parse_float(row.get("fundingIntervalHours"))
        return _PublicMarketInfo(
            trading,
            trading,
            trading,
            status,
            source,
            restrictions,
            volume_24h_usdt=volume,
            funding_rate_pct=funding * 100 if funding is not None else None,
            funding_interval_hours=(
                int(interval)
                if interval is not None and interval > 0
                else 8 if market.market_type == MarketType.FUTURE else None
            ),
            mark_price=parse_float(premium.get("markPrice")),
            index_price=parse_float(premium.get("indexPrice")),
        )

    async def _info_lighter(self, market: MarketSnapshot) -> _PublicMarketInfo:
        url = "https://mainnet.zklighter.elliot.ai/api/v1/orderBookDetails"
        payload = await self._get_json(url, cache_key="lighter:orderBookDetails")
        key = "spot_order_book_details" if market.market_type == MarketType.SPOT else "order_book_details"
        row = self._find(payload.get(key, []), "symbol", market.raw_symbol)
        source = "Lighter orderBookDetails"
        if row is None:
            return _PublicMarketInfo(False, False, False, "NOT_FOUND", source, ["公开元数据不存在该原始市场"])
        status = str(row.get("status", "unknown"))
        frozen = row.get("is_frozen") is True
        config = row.get("market_config", {}) if isinstance(row.get("market_config"), dict) else {}
        force_reduce_only = config.get("force_reduce_only") is True
        trading = status == "active" and not frozen
        restrictions = []
        if not trading:
            restrictions.append(f"公开状态为 {status}，frozen={frozen}")
        if force_reduce_only:
            restrictions.append("公开配置 force_reduce_only=true")
        maker = parse_float(row.get("maker_fee")) if row.get("is_maker_fee_enabled") is not False else 0.0
        taker = parse_float(row.get("taker_fee")) if row.get("is_taker_fee_enabled") is not False else 0.0
        contract_size = parse_float(row.get("multiplier")) or 1.0
        return _PublicMarketInfo(
            trading,
            trading,
            trading,
            status,
            source,
            restrictions,
            force_reduce_only=force_reduce_only,
            maker_fee_pct=maker * 100 if maker is not None else None,
            taker_fee_pct=taker * 100 if taker is not None else None,
            contract_size_multiplier=contract_size,
        )


class TradeAvailabilityWatchRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def list(self) -> list[TradeAvailabilityWatch]:
        cursor = await self.db.execute(
            "SELECT payload FROM trade_availability_watchlist ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [TradeAvailabilityWatch.model_validate_json(row["payload"]) for row in rows]

    async def upsert(self, watch: TradeAvailabilityWatch) -> TradeAvailabilityWatch:
        await self.db.execute(
            """
            INSERT INTO trade_availability_watchlist (
              id, exchange, market_type, raw_symbol, dex, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              exchange = excluded.exchange,
              market_type = excluded.market_type,
              raw_symbol = excluded.raw_symbol,
              dex = excluded.dex,
              payload = excluded.payload,
              updated_at = excluded.updated_at
            """,
            (
                watch.id,
                watch.exchange,
                watch.market_type.value,
                watch.raw_symbol,
                watch.dex or "",
                watch.model_dump_json(),
                watch.created_at.isoformat(),
                watch.updated_at.isoformat(),
            ),
        )
        await self.db.commit()
        return watch

    async def delete(self, watch_id: str) -> None:
        await self.db.execute("DELETE FROM trade_availability_watchlist WHERE id = ?", (watch_id,))
        await self.db.commit()


def build_trade_recovery_message(event: TradeAvailabilityWatchEvent) -> str:
    market = event.market
    sides = "、".join("普通买入/做多" if side == "buy" else "普通卖出/做空" for side in event.recovered_sides)
    dex_text = f" / DEX {market.dex}" if market.dex else ""
    funding = (
        f"{market.funding_rate_pct:.6f}% / {market.funding_interval_hours}h"
        if market.funding_rate_pct is not None and market.funding_interval_hours is not None
        else "-"
    )
    return (
        f"[交易可用性恢复] {market.exchange} / {market.market_type.value}{dex_text} / {market.raw_symbol}\n"
        f"恢复方向：{sides}\n"
        f"公开状态：{market.public_status_code}（{market.public_status_source}）\n"
        f"买一/卖一：{market.best_bid or '-'} / {market.best_ask or '-'}\n"
        f"1% 买盘/卖盘深度：{market.bid_depth_1pct_usdt or 0:.2f} / "
        f"{market.ask_depth_1pct_usdt or 0:.2f} USDT\n"
        f"24h 成交额：{market.volume_24h_usdt or 0:.2f} USDT\n"
        f"资金费率：{funding}\n"
        f"手续费：未计入；市场倍率：{market.market_multiplier:g}x；"
        f"合约数量乘数：{market.contract_size_multiplier:g}\n"
        f"数据时间：{market.observed_at.isoformat()}\n"
        "说明：这是公开市场恢复信号，不代表账户权限或真实订单已经通过。"
    )


class TradeAvailabilityMonitor:
    def __init__(
        self,
        repository: TradeAvailabilityWatchRepository,
        status_service: TradeAvailabilityService,
        alert_sender: AlertSender | None = None,
    ) -> None:
        self.repository = repository
        self.status_service = status_service
        self.alert_sender = alert_sender

    async def check_once(self) -> list[TradeAvailabilityWatchEvent]:
        events: list[TradeAvailabilityWatchEvent] = []
        for watch in await self.repository.list():
            if not watch.enabled:
                continue
            now = datetime.now(UTC)
            try:
                result = await self.status_service.fetch_status(
                    watch.symbol,
                    exchange=watch.exchange,
                    market_type=watch.market_type,
                    raw_symbol=watch.raw_symbol,
                    dex=watch.dex,
                )
                market = next(
                    (
                        item
                        for item in result.markets
                        if item.exchange == watch.exchange
                        and item.market_type == watch.market_type
                        and item.raw_symbol.upper() == watch.raw_symbol.upper()
                        and (item.dex or "") == (watch.dex or "")
                    ),
                    None,
                )
                if market is None:
                    raise RuntimeError("聚合行情中未找到指定原始市场")
                recovered: list[str] = []
                if (
                    watch.monitor_buy
                    and watch.last_buy_state is not None
                    and watch.last_buy_state != TradeAvailabilityState.AVAILABLE
                    and market.buy_open.state == TradeAvailabilityState.AVAILABLE
                ):
                    recovered.append("buy")
                if (
                    watch.monitor_sell
                    and watch.last_sell_state is not None
                    and watch.last_sell_state != TradeAvailabilityState.AVAILABLE
                    and market.sell_open.state == TradeAvailabilityState.AVAILABLE
                ):
                    recovered.append("sell")
                event = (
                    TradeAvailabilityWatchEvent(
                        watch_id=watch.id,
                        recovered_sides=recovered,
                        market=market,
                    )
                    if recovered
                    else None
                )
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
                    if self.alert_sender is not None:
                        await self.alert_sender(build_trade_recovery_message(event))
                    events.append(event)
            except Exception as exc:
                logger.exception("trade availability watch failed id=%s", watch.id)
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

    async def run(self, stop_event: asyncio.Event, interval_seconds: float = 20.0) -> None:
        while not stop_event.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
