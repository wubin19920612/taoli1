from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.market import MarketSnapshot

DEFAULT_HEADERS = {"User-Agent": "taoli1-radar/0.1"}
DEFAULT_TIMEOUT = httpx.Timeout(6.0, connect=2.5, read=5.0, write=5.0, pool=2.0)


def parse_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_usdt_symbol(raw_symbol: str) -> tuple[str, str, str]:
    symbol = raw_symbol.upper().replace("_", "-")
    if symbol.endswith("-SWAP"):
        symbol = symbol.removesuffix("-SWAP")
    compact = symbol.replace("-", "")
    if not compact.endswith("USDT"):
        raise ValueError(f"Only USDT symbols are supported: {raw_symbol}")
    base = compact.removesuffix("USDT")
    return compact, base, "USDT"


def utc_now() -> datetime:
    return datetime.now(UTC)


class ExchangeAdapter(ABC):
    name: str

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        )

    async def get_json(self, url: str) -> Any:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to request {url}")

    @abstractmethod
    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        raise NotImplementedError
