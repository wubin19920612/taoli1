"""Read-only Bybit adapter. Credentials never enter results or error messages."""

import asyncio
import hashlib
import hmac
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

PUBLIC_PATHS = {
    "/v5/market/instruments-info",
    "/v5/market/tickers",
    "/v5/market/orderbook",
    "/v5/spot-margin-trade/data",
}
PRIVATE_PATHS = {
    "/v5/spot-margin-trade/state",
    "/v5/account/collateral-info",
    "/v5/spot-margin-trade/max-borrowable",
    "/v5/spot-margin-trade/flexible-available-inventory",
    "/v5/account/fee-rate",
}


class FsDataError(Exception):
    """Only a static, non-sensitive reason code may cross the adapter boundary."""


def number(value, *, nonnegative=False):
    from math import isfinite

    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if isfinite(result) and (not nonnegative or result >= 0) else None


def timestamp(value):
    n = number(value, nonnegative=True)
    if n is None:
        raise FsDataError("missing_source_time")
    try:
        return datetime.fromtimestamp(n / 1000, UTC)
    except (ValueError, OverflowError, OSError):
        raise FsDataError("invalid_source_time") from None


class BybitFsProvider:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self._owns_client = client is None
        self._pace = asyncio.Lock()
        self._last_request = 0.0
        self._metadata = {}

    async def aclose(self):
        if self._owns_client:
            await self.client.aclose()

    async def get(self, path, params=None, credentials=None):
        allowed = PRIVATE_PATHS if credentials else PUBLIC_PATHS
        if path not in allowed:
            raise FsDataError("endpoint_not_allowed")
        query = urlencode(sorted((params or {}).items()))
        headers = {}
        async with self._pace:
            await asyncio.sleep(max(0, 0.12 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
        if credentials:
            key, secret = credentials.api_key, credentials.api_secret
            if not key or not secret:
                raise FsDataError("account_credentials_missing")
            stamp = str(int(time.time() * 1000))
            payload = stamp + key + "5000" + query
            headers = {
                "X-BAPI-API-KEY": key,
                "X-BAPI-TIMESTAMP": stamp,
                "X-BAPI-RECV-WINDOW": "5000",
                "X-BAPI-SIGN": hmac.new(
                    secret.encode(), payload.encode(), hashlib.sha256
                ).hexdigest(),
            }
        try:
            response = await self.client.get(
                "https://api.bybit.com" + path + ("?" + query if query else ""),
                headers=headers,
            )
            if response.status_code in {401, 403}:
                raise FsDataError("permission_denied")
            if response.status_code == 429:
                raise FsDataError("rate_limited")
            response.raise_for_status()
            data = response.json()
            code = data.get("retCode")
            if code in {10003, 10004, 10005, 10007, 10010, 33004}:
                raise FsDataError("permission_denied")
            if code in {10006, 10429}:
                raise FsDataError("rate_limited")
            if code != 0 or not isinstance(data.get("result"), dict):
                raise FsDataError("exchange_query_failed")
            source_at = timestamp(data.get("time"))
            if not -5 <= (datetime.now(UTC) - source_at).total_seconds() <= 30:
                raise FsDataError("stale_response")
            return data["result"], source_at
        except FsDataError:
            raise
        except httpx.TimeoutException:
            raise FsDataError("query_timeout") from None
        except Exception:  # noqa: BLE001 - redact vendor/credential errors and keep monitoring alive.
            raise FsDataError("query_failed") from None

    async def instruments(self, category):
        cached = self._metadata.get(category)
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        rows, cursor, seen = {}, "", set()
        for _ in range(10):
            params = {"category": category, "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            result, _ = await self.get("/v5/market/instruments-info", params)
            for item in result.get("list", []):
                rows[item["symbol"]] = item
            cursor = result.get("nextPageCursor") or ""
            if not cursor:
                self._metadata[category] = (time.monotonic(), rows)
                return rows
            if cursor in seen:
                break
            seen.add(cursor)
        raise FsDataError("metadata_pagination_incomplete")

    async def public_snapshot(self):
        spots, futures = await asyncio.gather(self.instruments("spot"), self.instruments("linear"))
        (st, st_at), (ft, ft_at), (margin, margin_at) = await asyncio.gather(
            self.get("/v5/market/tickers", {"category": "spot"}),
            self.get("/v5/market/tickers", {"category": "linear"}),
            self.get("/v5/spot-margin-trade/data", {"vipLevel": "No VIP"}),
        )
        rates = {
            item["currency"]: item
            for group in margin.get("vipCoinList", [])
            if group.get("vipLevel") == "No VIP"
            for item in group.get("list", [])
        }
        return {
            "spots": spots,
            "futures": futures,
            "spot_tickers": {x["symbol"]: x for x in st.get("list", [])},
            "future_tickers": {x["symbol"]: x for x in ft.get("list", [])},
            "rates": rates,
            "spot_at": st_at,
            "future_at": ft_at,
            "margin_at": margin_at,
        }

    async def books(self, symbol):
        return await asyncio.gather(
            *[
                self.get("/v5/market/orderbook", {"category": c, "symbol": symbol, "limit": "50"})
                for c in ("spot", "linear")
            ]
        )

    async def current_instruments(self, symbol):
        result = []
        responses = await asyncio.gather(
            *[
                self.get("/v5/market/instruments-info", {"category": c, "symbol": symbol})
                for c in ("spot", "linear")
            ]
        )
        for payload, source_at in responses:
            matches = [x for x in payload.get("list", []) if x.get("symbol") == symbol]
            if len(matches) != 1:
                raise FsDataError("market_metadata_missing")
            result.append((matches[0], source_at))
        return result

    async def account_context(self, credentials):
        (state, _), (collateral, source_at) = await asyncio.gather(
            self.get("/v5/spot-margin-trade/state", credentials=credentials),
            self.get("/v5/account/collateral-info", credentials=credentials),
        )
        if state.get("spotMarginMode") != "1":
            raise FsDataError("account_margin_disabled")
        return {x["currency"]: x for x in collateral.get("list", [])}, source_at

    async def account_quote(self, symbol, base, credentials):
        (loan, loan_at), (stock, stock_at), (sf, _), (ff, _) = await asyncio.gather(
            self.get("/v5/spot-margin-trade/max-borrowable", {"currency": base}, credentials),
            self.get(
                "/v5/spot-margin-trade/flexible-available-inventory",
                {"currency": base},
                credentials,
            ),
            self.get("/v5/account/fee-rate", {"category": "spot", "symbol": symbol}, credentials),
            self.get("/v5/account/fee-rate", {"category": "linear", "symbol": symbol}, credentials),
        )
        if loan.get("currency") != base or stock.get("currency") != base:
            raise FsDataError("account_currency_mismatch")
        rates = []
        for response in (sf, ff):
            rows = [x for x in response.get("list", []) if x.get("symbol") == symbol]
            rate = number(rows[0].get("takerFeeRate"), nonnegative=True) if len(rows) == 1 else None
            if rate is None or rate > 0.05:
                raise FsDataError("account_fee_unknown")
            rates.append(rate * 100)
        inventory_at = timestamp(stock.get("updateTime"))
        if not -5 <= (stock_at - inventory_at).total_seconds() <= 30:
            raise FsDataError("stale_inventory")
        return {
            "account_max_loan": number(loan.get("maxLoan"), nonnegative=True),
            "available_inventory": number(stock.get("availableInventory"), nonnegative=True),
            "inventory_at": inventory_at,
            "account_at": min(loan_at, stock_at),
            "spot_fee_pct": rates[0],
            "future_fee_pct": rates[1],
        }
