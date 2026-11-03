from datetime import UTC, datetime

from app.models.market import MarketSnapshot
from app.services.spread_engine import build_directional_opportunity

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def market(exchange="binance", price=100, **kwargs):
    values = dict(symbol="BTCUSDT", base="BTC", exchange=exchange, market_type="future",
                  bid=price, ask=price, raw_symbol="BTCUSDT", timestamp=NOW,
                  volume_24h_usdt=10_000_000)
    values.update(kwargs)
    return MarketSnapshot(**values)


def pair(**kwargs):
    buy = market()
    sell = market("bitget", 101)
    op = build_directional_opportunity(buy, sell, mode="FF", buy_fee_pct=0.05, sell_fee_pct=0.06)
    if kwargs:
        op = op.model_copy(update=kwargs)
    return [buy, sell], op
