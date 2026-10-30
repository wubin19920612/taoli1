"""Quote metadata and fresh-review FX regressions, using offline order books."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.exchanges.lighter import LighterAdapter, RobinhoodLighterAdapter
from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey
from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import RiskSettings
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.spread_engine import build_directional_opportunity


FX_BID = 1.00185
FX_ASK = 1.00186


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type, expected_quote", [
    (LighterAdapter, "USDC"), (RobinhoodLighterAdapter, None),
])
@pytest.mark.parametrize("market_type", [MarketType.FUTURE, MarketType.SPOT])
async def test_lighter_parsed_snapshot_preserves_native_quote(
    monkeypatch, adapter_type, expected_quote, market_type,
):
    async with httpx.AsyncClient() as client:
        adapter = adapter_type(client)
        raw = "ETH" if market_type == MarketType.FUTURE else f"ETH/{adapter.spot_quote}"
        detail = {"symbol": raw, "market_id": 1, "status": "active"}
        details_key = (
            "order_book_details" if market_type == MarketType.FUTURE
            else "spot_order_book_details"
        )

        async def fake_get(url):
            if url.endswith("/orderBookDetails"):
                return {
                    "code": 200, "order_book_details": [], "spot_order_book_details": [],
                    details_key: [detail],
                }
            assert url.endswith("/funding-rates")
            return {"code": 200, "funding_rates": []}

        books = AsyncMock(return_value={1: {
            "code": 200,
            "bids": [{"price": "100", "remaining_base_amount": "1000"}],
            "asks": [{"price": "100.01", "remaining_base_amount": "1000"}],
        }})
        monkeypatch.setattr(adapter, "get_json", fake_get)
        monkeypatch.setattr("app.exchanges.lighter.lighter_order_books", books)
        rows = await (
            adapter.fetch_future_tickers() if market_type == MarketType.FUTURE
            else adapter.fetch_spot_tickers()
        )

        assert len(rows) == 1
        row = rows[0]
        assert row.original_quote_currency == expected_quote
        assert row.quote == "USDT"
        assert row.symbol == "ETHUSDT"
        assert row.raw_symbol == raw
        assert row.exchange == adapter.name
        assert row.market_type == market_type
        books.assert_awaited_once_with([1], ws_url=adapter.ws_url)


def market(exchange, bid, ask, now, original_quote="USDC"):
    return MarketSnapshot(
        symbol="ETHUSDT", base="ETH", quote="USDT", exchange=exchange,
        market_type=MarketType.FUTURE,
        raw_symbol="ETHUSDT" if exchange == "binance" else "ETH",
        dex="main" if exchange == "hyperliquid" else None,
        original_quote_currency=original_quote,
        bid=bid, ask=ask, bid_size=1000, ask_size=1000,
        volume_24h_usdt=2_000_000, timestamp=now, upstream_timestamp=now,
    )


def binding(row, now):
    # Deliberately retain a legacy USDT binding even for native USDC markets.
    return MarketBinding(
        market=MarketKey.from_snapshot(row), asset_id="ethereum", display_name="Ethereum",
        quote_currency="USDT", identity_status="verified", unit_status="verified",
        verified_at=now, valid_until=now + timedelta(days=1),
        evidence=[IdentityEvidence(
            source_type="official_announcement", url="https://example.com/eth",
            summary="Offline ETH identity fixture", fetched_at=now,
        )],
    )


@pytest.mark.parametrize("original_quote, expected", [("USDC", "USDC"), (None, "USDT")])
def test_market_identity_prefers_snapshot_quote_with_binding_fallback(original_quote, expected):
    now = datetime.now(UTC)
    row = market("lighter", 100, 100.01, now, original_quote)
    stored_binding = binding(row, now)
    registry = MarketIdentityRegistry([stored_binding])

    annotated = registry.annotate(row, now)

    assert annotated.original_quote_currency == expected
    assert annotated.quote == "USDT"
    assert stored_binding.quote_currency == "USDT"
    assert registry.get(MarketKey.from_snapshot(row)).quote_currency == "USDT"


async def review_pair(rows, now):
    adapters = []
    for row in rows:
        book = OrderBookSnapshot(
            exchange=row.exchange, symbol=row.symbol, market_type=row.market_type,
            raw_symbol=row.raw_symbol, timestamp=now,
            bids=[OrderBookLevel(price=row.bid, size=1000)],
            asks=[OrderBookLevel(price=row.ask, size=1000)],
        )
        adapters.append(SimpleNamespace(name=row.exchange, fetch_order_book=AsyncMock(return_value=book)))
    service = MarketValidationService(None, SimpleNamespace(), adapters)
    service.registry = MarketIdentityRegistry([binding(row, now) for row in rows])
    service.fx = (FX_BID, FX_ASK, now, now + timedelta(seconds=15))
    try:
        result, buy, sell = await service.review(*rows, RiskSettings())
        assert not result.reason_codes
        assert result.capabilities.manual_card
        for adapter, row in zip(adapters, rows, strict=True):
            adapter.fetch_order_book.assert_awaited_once_with(
                row.symbol, row.market_type, row.raw_symbol, 20,
            )
        return buy, sell
    finally:
        await service.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_market_validation_hl_lighter_review_matches_raw_spread(reverse):
    now = datetime.now(UTC)
    rows = [market("hyperliquid", 100, 100.01, now), market("lighter", 100.2, 100.21, now)]
    if reverse:
        rows.reverse()
    buy, sell = await review_pair(rows, now)

    for raw, reviewed in zip(rows, (buy, sell), strict=True):
        assert reviewed.original_quote_currency == "USDC"
        assert reviewed.bid == pytest.approx(raw.bid * FX_BID)
        assert reviewed.ask == pytest.approx(raw.ask * FX_ASK)
    actual = build_directional_opportunity(buy, sell, mode="FF").open_spread_pct
    raw_spread = 200 * (rows[1].bid - rows[0].ask) / (rows[1].bid + rows[0].ask)
    assert abs(actual - raw_spread) < 0.002
    # The residual comes from the FX bid-ask spread.
    assert abs(actual - raw_spread) <= (FX_ASK / FX_BID - 1) * 100 + 1e-9


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_market_validation_lighter_binance_review_matches_fx_calculation(reverse):
    now = datetime.now(UTC)
    lighter = market("lighter", 100, 100.01, now)
    binance = market("binance", 100.5, 100.51, now, "USDT")
    rows = [binance, lighter] if reverse else [lighter, binance]
    buy, sell = await review_pair(rows, now)

    reviewed = {row.exchange: row for row in (buy, sell)}
    assert reviewed["lighter"].bid == pytest.approx(lighter.bid * FX_BID)
    assert reviewed["lighter"].ask == pytest.approx(lighter.ask * FX_ASK)
    assert reviewed["binance"].bid == binance.bid
    assert reviewed["binance"].ask == binance.ask
    assert reviewed["binance"].quote_conversion_source is None
    buy_ask = binance.ask if reverse else lighter.ask * FX_ASK
    sell_bid = lighter.bid * FX_BID if reverse else binance.bid
    expected = 200 * (sell_bid - buy_ask) / (sell_bid + buy_ask)
    actual = build_directional_opportunity(buy, sell, mode="FF").open_spread_pct
    assert actual == pytest.approx(expected, abs=1e-10)
