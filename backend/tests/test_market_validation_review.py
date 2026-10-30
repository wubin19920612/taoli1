from datetime import UTC, datetime, timedelta

import pytest
from test_market_identity_validation import binding, market

from app.models.orderbook import OrderBookLevel, OrderBookSnapshot
from app.models.settings import RiskSettings
from app.services.market_identity import MarketIdentityRegistry
from app.services.market_validation_review import MarketValidationService
from app.services.snapshot_store import SnapshotStore


class Adapter:
    def __init__(self, row):
        self.name = row.exchange
        self.row = row
        self.calls = 0

    async def fetch_order_book(self, symbol, market_type, raw_symbol, limit=20):
        self.calls += 1
        return OrderBookSnapshot(
            exchange=self.name,
            symbol=symbol,
            market_type=market_type,
            raw_symbol=raw_symbol,
            bids=[OrderBookLevel(price=self.row.bid, size=1e7)],
            asks=[OrderBookLevel(price=self.row.ask, size=1e7)],
            timestamp=datetime.now(UTC),
        )


def setup_pair(a, b):
    now = datetime.now(UTC)
    a, b = a.model_copy(update={"timestamp": now}), b.model_copy(update={"timestamp": now})
    store = SnapshotStore()
    store.set_all_markets([a, b])
    adapters = [Adapter(a), Adapter(b)]
    service = MarketValidationService(None, store, adapters)
    service.registry = MarketIdentityRegistry([binding(a), binding(b)])
    return service, a, b, adapters


@pytest.mark.asyncio
async def test_reference_candidate_is_replaced_only_by_exact_real_book():
    service, a, b, adapters = setup_pair(market(), market("bybit", price=0.101))
    b = b.model_copy(update={"quote_kind": "reference", "estimated_fields": ["bid", "ask"]})
    result, first, second = await service.review(a, b, RiskSettings())
    assert result.capabilities.manual_card
    assert second.quote_kind == "book"
    assert not second.estimated_fields
    assert all(adapter.calls == 1 for adapter in adapters)
    assert service.validate(first, second).capabilities.same_asset_opportunity


@pytest.mark.asyncio
async def test_huge_spread_requires_two_independent_books_and_expires():
    service, a, b, adapters = setup_pair(market(), market("bybit", price=0.2))
    result, first, second = await service.review(a, b, RiskSettings())
    assert result.anomaly_status == "passed"
    assert all(adapter.calls == 2 for adapter in adapters)
    assert service.validate(first, second).anomaly_status == "passed"
    result.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert service.validate(first, second).anomaly_status == "pending_review"


@pytest.mark.asyncio
async def test_different_asset_is_blocked_before_any_book_request():
    service, a, b, adapters = setup_pair(market(), market("bybit"))
    service.registry.replace([binding(a), binding(b, "pearl")])
    result, _, _ = await service.review(a, b, RiskSettings())
    assert "DIFFERENT_UNDERLYING_ASSET" in result.reason_codes
    assert all(adapter.calls == 0 for adapter in adapters)


def test_same_ticker_card_audit_is_read_only_and_does_not_infer_positions():
    service, a, b, _ = setup_pair(market(), market("hyperliquid", "io:PRL", dex="io"))
    service.registry.replace([binding(a), binding(b, "pearl")])
    result = service.audit_cards(
        [
            {
                "id": "card1",
                "name": "PRL",
                "type": "FF",
                "buyEx": "bitget",
                "sellEx": "hl",
                "bHlDex": "io",
                "status": False,
            }
        ]
    )
    assert result[0]["action"] == "read_only"
    assert result[0]["position_status"] == "unknown"
    assert result[0]["validation"].identity_relation == "different"
