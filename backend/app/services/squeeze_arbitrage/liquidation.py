from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class LiquidationUpdate:
    identity: str
    market_key: str
    event_time: datetime
    received_at: datetime
    side: str
    cumulative_filled_qty: float
    average_price: float
    source: str = "binance_!forceOrder@arr_throttled"


def parse_force_order(payload: str | bytes | dict[str, Any], received_at: datetime) -> LiquidationUpdate | None:
    message = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    if not isinstance(message, dict):
        return None
    if "data" in message and isinstance(message["data"], dict):
        message = message["data"]
    order = message.get("o")
    if message.get("e") != "forceOrder" or not isinstance(order, dict):
        return None
    try:
        symbol = str(order["s"])
        side = str(order["S"])
        event_ms = int(order["T"])
        cumulative = float(order["z"])
        average = float(order["ap"])
        original_qty = str(order["q"])
        original_price = str(order["p"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if side not in ("BUY", "SELL") or cumulative < 0 or average <= 0:
        return None
    from .models import market_key

    identity_input = f"{symbol}|{side}|{event_ms}|{original_qty}|{original_price}"
    return LiquidationUpdate(
        hashlib.sha256(identity_input.encode("utf-8")).hexdigest(),
        market_key(symbol),
        datetime.fromtimestamp(event_ms / 1000, UTC),
        received_at,
        side,
        cumulative,
        average,
    )
