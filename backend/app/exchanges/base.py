from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.market import MarketSnapshot


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
        self.client = client or httpx.AsyncClient(timeout=10)

    @abstractmethod
    async def fetch_spot_tickers(self) -> list[MarketSnapshot]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_future_tickers(self) -> list[MarketSnapshot]:
        raise NotImplementedError
