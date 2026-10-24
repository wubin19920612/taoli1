import logging
import math
from datetime import UTC, datetime
from typing import Any

import ccxt.async_support as ccxt
import httpx

from app.exchanges.base import parse_float
from app.models.account_position import AccountPosition, account_position_id
from app.models.market import MarketType
from app.services.account_positions import (
    AccountPositionPermissionError,
    AccountPositionQueryError,
)

logger = logging.getLogger(__name__)

CCXT_CLIENTS: dict[tuple[str, MarketType], str] = {
    ("binance", MarketType.SPOT): "binance",
    ("binance", MarketType.FUTURE): "binanceusdm",
    ("okx", MarketType.SPOT): "okx",
    ("okx", MarketType.FUTURE): "okx",
    ("bybit", MarketType.SPOT): "bybit",
    ("bybit", MarketType.FUTURE): "bybit",
    ("gate", MarketType.SPOT): "gateio",
    ("gate", MarketType.FUTURE): "gateio",
    ("bitget", MarketType.SPOT): "bitget",
    ("bitget", MarketType.FUTURE): "bitget",
}
STABLE_QUOTES = {"USDT", "USDC", "USD", "BUSD", "FDUSD"}


def _finite_float(value: Any) -> float | None:
    parsed = parse_float(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _positive_float(value: Any) -> float | None:
    parsed = _finite_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _timestamp(value: Any, fallback: datetime) -> datetime:
    parsed = _finite_float(value)
    if parsed is None or parsed <= 0:
        return fallback
    if parsed < 10_000_000_000:
        parsed *= 1000
    try:
        return datetime.fromtimestamp(parsed / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return fallback


def _normalized_symbol(base: str, quote: str = "USDT") -> str:
    normalized_base = "".join(char for char in base.upper() if char.isalnum())
    normalized_quote = "".join(char for char in quote.upper() if char.isalnum())
    return f"{normalized_base}{normalized_quote or 'USDT'}"


def _ccxt_error(exc: Exception, exchange: str, market_type: MarketType) -> Exception:
    if isinstance(exc, (ccxt.AuthenticationError, ccxt.PermissionDenied, ccxt.AccountSuspended)):
        return AccountPositionPermissionError("凭据无持仓读取权限或已失效")
    logger.warning(
        "CCXT account query failed exchange=%s market_type=%s failure_type=%s",
        exchange,
        market_type.value,
        exc.__class__.__name__,
    )
    return AccountPositionQueryError("账户持仓接口查询失败")


def create_ccxt_client(
    exchange: str,
    market_type: MarketType,
    credentials: dict[str, str],
) -> Any:
    class_name = CCXT_CLIENTS[(exchange, market_type)]
    exchange_class = getattr(ccxt, class_name)
    config: dict[str, Any] = {
        "apiKey": credentials.get("api_key", ""),
        "secret": credentials.get("api_secret", ""),
        "enableRateLimit": True,
    }
    if credentials.get("passphrase"):
        config["password"] = credentials["passphrase"]
    if class_name not in {"binanceusdm"}:
        config["options"] = {
            "defaultType": "spot" if market_type == MarketType.SPOT else "swap"
        }
    return exchange_class(config)


class CcxtAccountPositionProvider:
    dex = None

    def __init__(
        self,
        *,
        exchange: str,
        account_id: str,
        account_label: str,
        market_type: MarketType,
        credentials: dict[str, str],
        client: Any | None = None,
    ):
        self.exchange = exchange
        self.account_id = account_id
        self.account_label = account_label
        self.market_type = market_type
        self.credentials = credentials
        self.client = client or create_ccxt_client(exchange, market_type, credentials)

    @property
    def configured(self) -> bool:
        return bool(self.credentials.get("api_key") and self.credentials.get("api_secret"))

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            await close()

    async def fetch_positions(self) -> list[AccountPosition]:
        if not self.configured:
            raise AccountPositionQueryError("账户尚未配置凭据")
        try:
            await self.client.load_markets()
            if self.market_type == MarketType.SPOT:
                return await self._fetch_spot_positions()
            return await self._fetch_future_positions()
        except (AccountPositionQueryError, AccountPositionPermissionError):
            raise
        except Exception as exc:
            raise _ccxt_error(exc, self.exchange, self.market_type) from exc

    async def _fetch_future_positions(self) -> list[AccountPosition]:
        try:
            rows = await self.client.fetch_positions()
        except Exception as exc:
            raise _ccxt_error(exc, self.exchange, self.market_type) from exc
        if not isinstance(rows, list):
            raise AccountPositionQueryError("账户持仓接口返回格式无效")
        observed_at = datetime.now(UTC)
        parsed = [self._parse_future(item, observed_at) for item in rows if isinstance(item, dict)]
        return [item for item in parsed if item is not None]

    def _market(self, unified_symbol: str) -> dict[str, Any]:
        try:
            market = self.client.market(unified_symbol)
        except Exception:  # noqa: BLE001 - CCXT adapters may raise exchange-specific types.
            market = None
        return market if isinstance(market, dict) else {}

    def _parse_future(
        self,
        item: dict[str, Any],
        observed_at: datetime,
    ) -> AccountPosition | None:
        unified_symbol = str(item.get("symbol") or "").strip()
        market = self._market(unified_symbol)
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        raw_symbol = str(
            market.get("id")
            or info.get("symbol")
            or info.get("instId")
            or info.get("contract")
            or unified_symbol
        ).strip()
        contracts = _finite_float(item.get("contracts"))
        if contracts is None:
            contracts = _finite_float(info.get("size", info.get("positionAmt")))
        if not raw_symbol or contracts in {None, 0}:
            return None
        side_value = str(item.get("side") or info.get("side") or "").lower()
        if side_value not in {"long", "short"}:
            side_value = "short" if contracts < 0 else "long"
        side = side_value
        absolute_contracts = abs(contracts)
        contract_size = _positive_float(item.get("contractSize"))
        if contract_size is None:
            contract_size = _positive_float(market.get("contractSize"))
        quantity = absolute_contracts * (contract_size or 1)
        base = str(market.get("base") or unified_symbol.split("/")[0] or "资产").upper()
        quote_fragment = unified_symbol.split("/")[1] if "/" in unified_symbol else "USDT"
        quote = str(market.get("quote") or quote_fragment.split(":")[0] or "USDT").upper()
        settle = str(market.get("settle") or quote).upper()
        entry_price = _positive_float(item.get("entryPrice"))
        mark_price = _positive_float(item.get("markPrice"))
        notional = _finite_float(item.get("notional"))
        notional = abs(notional) if notional is not None else None
        estimated_fields: list[str] = []
        if settle not in STABLE_QUOTES:
            notional = None
        elif settle != "USDT" and notional is not None:
            estimated_fields.append("notional_usdt")
        if notional is None and mark_price is not None and settle in STABLE_QUOTES:
            notional = quantity * mark_price
            estimated_fields.append("notional_usdt")
        unrealized_pnl = _finite_float(item.get("unrealizedPnl"))
        if settle not in STABLE_QUOTES:
            unrealized_pnl = None
        elif settle != "USDT" and unrealized_pnl is not None:
            estimated_fields.append("unrealized_pnl_usdt")
        roi_pct = _finite_float(item.get("percentage"))
        leverage = _positive_float(item.get("leverage"))
        updated_at = _timestamp(item.get("timestamp"), observed_at)
        identity = {
            "account_id": self.account_id,
            "account_label": self.account_label,
            "exchange": self.exchange,
            "market_type": MarketType.FUTURE,
            "raw_symbol": raw_symbol,
            "symbol": _normalized_symbol(base, quote),
            "side": side,
            "dex": None,
        }
        return AccountPosition(
            id=account_position_id(
                account_id=self.account_id,
                exchange=self.exchange,
                market_type=MarketType.FUTURE,
                raw_symbol=raw_symbol,
                side=side,
                dex=None,
            ),
            **identity,
            quantity=quantity,
            quantity_unit=base,
            contract_quantity=absolute_contracts,
            contract_multiplier=contract_size,
            entry_price=entry_price,
            mark_price=mark_price,
            notional_usdt=notional,
            unrealized_pnl_usdt=unrealized_pnl,
            roi_pct=roi_pct,
            leverage=leverage,
            price_basis=(
                "交易所合约标记价（CCXT markPrice）"
                if settle == "USDT"
                else f"交易所合约标记价；{settle} 按 1:1 估算为 USDT"
            ),
            estimated_fields=estimated_fields,
            updated_at=updated_at,
        )

    async def _fetch_spot_positions(self) -> list[AccountPosition]:
        try:
            balance = await self.client.fetch_balance()
        except Exception as exc:
            raise _ccxt_error(exc, self.exchange, self.market_type) from exc
        totals = balance.get("total") if isinstance(balance, dict) else None
        if not isinstance(totals, dict):
            raise AccountPositionQueryError("账户余额接口返回格式无效")
        assets = [
            (str(asset).upper(), amount)
            for asset, value in totals.items()
            if (amount := _positive_float(value)) is not None
        ]
        observed_at = datetime.now(UTC)
        return [await self._spot_position(asset, amount, observed_at) for asset, amount in assets]

    def _spot_market(self, asset: str) -> dict[str, Any]:
        markets = getattr(self.client, "markets", {})
        if not isinstance(markets, dict):
            return {}
        for quote in ("USDT", "USDC"):
            market = markets.get(f"{asset}/{quote}")
            if isinstance(market, dict) and market.get("active", True):
                return market
        return {}

    async def _spot_position(
        self,
        asset: str,
        amount: float,
        observed_at: datetime,
    ) -> AccountPosition:
        market = self._spot_market(asset)
        quote = str(market.get("quote") or "USDT").upper()
        raw_symbol = str(market.get("id") or asset)
        mark_price: float | None = None
        notional: float | None = None
        estimated_fields: list[str] = []
        if asset in STABLE_QUOTES:
            mark_price = 1.0
            notional = amount
            estimated_fields.extend(["mark_price", "notional_usdt"])
        elif market:
            try:
                ticker = await self.client.fetch_ticker(str(market.get("symbol")))
                mark_price = _positive_float(ticker.get("last")) if isinstance(ticker, dict) else None
                if mark_price is None and isinstance(ticker, dict):
                    bid = _positive_float(ticker.get("bid"))
                    ask = _positive_float(ticker.get("ask"))
                    if bid is not None and ask is not None:
                        mark_price = (bid + ask) / 2
                if mark_price is not None and quote in STABLE_QUOTES:
                    notional = amount * mark_price
                    estimated_fields.extend(["mark_price", "notional_usdt"])
            except Exception as exc:  # noqa: BLE001 - unknown valuation must remain visible.
                logger.warning(
                    "spot valuation failed exchange=%s asset=%s failure_type=%s",
                    self.exchange,
                    asset,
                    exc.__class__.__name__,
                )
        identity = {
            "account_id": self.account_id,
            "account_label": self.account_label,
            "exchange": self.exchange,
            "market_type": MarketType.SPOT,
            "raw_symbol": raw_symbol,
            "symbol": _normalized_symbol(asset, quote),
            "side": "long",
            "dex": None,
        }
        return AccountPosition(
            id=account_position_id(
                account_id=self.account_id,
                exchange=self.exchange,
                market_type=MarketType.SPOT,
                raw_symbol=raw_symbol,
                side="long",
                dex=None,
            ),
            **identity,
            quantity=amount,
            quantity_unit=asset,
            contract_quantity=None,
            contract_multiplier=None,
            entry_price=None,
            mark_price=mark_price,
            notional_usdt=notional,
            unrealized_pnl_usdt=None,
            roi_pct=None,
            leverage=None,
            price_basis="公开现货行情估值；稳定币按 1 USDT 估算",
            estimated_fields=estimated_fields,
            updated_at=observed_at,
        )


class HyperliquidAccountPositionProvider:
    exchange = "hyperliquid"
    market_type = MarketType.FUTURE
    API_URL = "https://api.hyperliquid.xyz/info"

    def __init__(
        self,
        *,
        account_id: str,
        account_label: str,
        public_address: str,
        dex: str,
        client: httpx.AsyncClient | None = None,
    ):
        self.account_id = account_id
        self.account_label = account_label
        self.public_address = public_address
        self.dex = dex.strip().lower() or "main"
        self.client = client or httpx.AsyncClient(timeout=8.0)
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self.public_address)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _payload(self, request_type: str) -> dict[str, str]:
        payload = {"type": request_type}
        if self.dex != "main":
            payload["dex"] = self.dex
        return payload

    async def _request(self, payload: dict[str, str]) -> Any:
        try:
            response = await self.client.post(self.API_URL, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning(
                "Hyperliquid account query failed dex=%s failure_type=%s",
                self.dex,
                exc.__class__.__name__,
            )
            raise AccountPositionQueryError("Hyperliquid 公开账户接口查询失败") from exc

    async def fetch_positions(self) -> list[AccountPosition]:
        if not self.configured:
            raise AccountPositionQueryError("Hyperliquid 公开地址尚未配置")
        clearing_payload = self._payload("clearinghouseState")
        clearing_payload["user"] = self.public_address
        clearing_state = await self._request(clearing_payload)
        if not isinstance(clearing_state, dict):
            raise AccountPositionQueryError("Hyperliquid 账户接口返回格式无效")
        mark_prices: dict[str, float] = {}
        try:
            meta_contexts = await self._request(self._payload("metaAndAssetCtxs"))
            mark_prices = self._mark_prices(meta_contexts)
        except AccountPositionQueryError:
            logger.warning("Hyperliquid mark prices unavailable dex=%s", self.dex)
        rows = clearing_state.get("assetPositions")
        if not isinstance(rows, list):
            raise AccountPositionQueryError("Hyperliquid 账户接口返回格式无效")
        observed_at = datetime.now(UTC)
        parsed = [self._parse_position(row, mark_prices, observed_at) for row in rows]
        return [item for item in parsed if item is not None]

    @staticmethod
    def _mark_prices(payload: Any) -> dict[str, float]:
        if not isinstance(payload, list) or len(payload) < 2:
            return {}
        meta, contexts = payload[0], payload[1]
        universe = meta.get("universe") if isinstance(meta, dict) else None
        if not isinstance(universe, list) or not isinstance(contexts, list):
            return {}
        result: dict[str, float] = {}
        for market, context in zip(universe, contexts, strict=False):
            if not isinstance(market, dict) or not isinstance(context, dict):
                continue
            coin = str(market.get("name") or "").strip()
            mark_price = _positive_float(context.get("markPx"))
            if coin and mark_price is not None:
                result[coin] = mark_price
        return result

    def _parse_position(
        self,
        row: Any,
        mark_prices: dict[str, float],
        observed_at: datetime,
    ) -> AccountPosition | None:
        if not isinstance(row, dict):
            return None
        item = row.get("position") if isinstance(row.get("position"), dict) else row
        raw_symbol = str(item.get("coin") or "").strip()
        signed_size = _finite_float(item.get("szi"))
        if not raw_symbol or signed_size in {None, 0}:
            return None
        side = "short" if signed_size < 0 else "long"
        quantity = abs(signed_size)
        entry_price = _positive_float(item.get("entryPx"))
        mark_price = mark_prices.get(raw_symbol)
        notional = _finite_float(item.get("positionValue"))
        notional = abs(notional) if notional is not None else None
        estimated_fields: list[str] = ["notional_usdt"] if notional is not None else []
        if mark_price is None and notional is not None and quantity > 0:
            mark_price = notional / quantity
            estimated_fields.append("mark_price")
        if notional is None and mark_price is not None:
            notional = quantity * mark_price
            if "notional_usdt" not in estimated_fields:
                estimated_fields.append("notional_usdt")
        unrealized_pnl = _finite_float(item.get("unrealizedPnl"))
        if unrealized_pnl is not None:
            estimated_fields.append("unrealized_pnl_usdt")
        roi_pct = _finite_float(item.get("returnOnEquity"))
        if roi_pct is not None:
            roi_pct *= 100
        leverage_payload = item.get("leverage")
        leverage = _positive_float(
            leverage_payload.get("value")
            if isinstance(leverage_payload, dict)
            else leverage_payload
        )
        normalized_coin = raw_symbol.split(":", 1)[-1]
        identity = {
            "account_id": self.account_id,
            "account_label": self.account_label,
            "exchange": self.exchange,
            "market_type": MarketType.FUTURE,
            "raw_symbol": raw_symbol,
            "symbol": _normalized_symbol(normalized_coin),
            "side": side,
            "dex": self.dex,
        }
        return AccountPosition(
            id=account_position_id(
                account_id=self.account_id,
                exchange=self.exchange,
                market_type=MarketType.FUTURE,
                raw_symbol=raw_symbol,
                side=side,
                dex=self.dex,
            ),
            **identity,
            quantity=quantity,
            quantity_unit=normalized_coin,
            contract_quantity=quantity,
            contract_multiplier=1,
            entry_price=entry_price,
            mark_price=mark_price,
            notional_usdt=notional,
            unrealized_pnl_usdt=unrealized_pnl,
            roi_pct=roi_pct,
            leverage=leverage,
            price_basis="Hyperliquid Info API 标记价；USDC 按 1:1 估算为 USDT",
            estimated_fields=estimated_fields,
            updated_at=observed_at,
        )
