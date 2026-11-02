"""Shared exchange funding formula classification."""

FORMULA_FAMILIES: dict[str, str] = {
    "binance": "with_rate_clamp",
    "bitget": "with_rate_clamp",
    "okx": "with_rate_variable_interval",
    "bybit": "bybit_premium_interest_clamp",
    "gate": "gate_indicative",
    "hyperliquid": "hyperliquid_hourly",
    "htx": "htx",
    "aster": "aster",
}

def exchange_formula_family(exchange: str) -> str:
    return FORMULA_FAMILIES.get(exchange.strip().lower(), "unknown")
