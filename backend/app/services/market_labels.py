from app.models.market import MarketType


def normalize_market_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "").replace("/", "")


def is_bitget_rtoken_spot(
    exchange: str,
    market_type: MarketType | str,
    raw_symbol: str | None,
    canonical_symbol: str | None,
) -> bool:
    market_value = getattr(market_type, "value", market_type)
    if (
        exchange.strip().lower() != "bitget"
        or market_value != MarketType.SPOT.value
        or not raw_symbol
        or not canonical_symbol
    ):
        return False

    raw = normalize_market_symbol(raw_symbol)
    canonical = normalize_market_symbol(canonical_symbol)
    return (
        len(canonical) > len("USDT")
        and len(raw) > 1
        and raw.startswith("R")
        and raw[1:] == canonical
    )


def market_type_label(
    exchange: str,
    market_type: MarketType | str,
    raw_symbol: str | None = None,
    canonical_symbol: str | None = None,
) -> str:
    if is_bitget_rtoken_spot(exchange, market_type, raw_symbol, canonical_symbol):
        return "股票现货"
    market_value = getattr(market_type, "value", market_type)
    if market_value == MarketType.SPOT.value:
        return "现货"
    return "合约"


def market_leg_label(
    exchange: str,
    market_type: MarketType | str,
    raw_symbol: str | None = None,
    canonical_symbol: str | None = None,
) -> str:
    return (
        f"{exchange} "
        f"{market_type_label(exchange, market_type, raw_symbol, canonical_symbol)}"
    )


def astro_exchange_id(
    exchange: str,
    market_type: MarketType | str,
    raw_symbol: str | None = None,
    canonical_symbol: str | None = None,
) -> str:
    if is_bitget_rtoken_spot(exchange, market_type, raw_symbol, canonical_symbol):
        return "bitgetr"
    return exchange.strip().lower()
