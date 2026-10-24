from datetime import UTC, datetime, timedelta

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey
from app.services.market_identity import MarketIdentityRegistry
from app.services.pair_validation import validate_pair

NOW = datetime(2026, 10, 15, 2, tzinfo=UTC)


def market(exchange="bitget", raw="PRLUSDT", price=0.1, dex=None):
    return MarketSnapshot(
        symbol="PRLUSDT",
        base="PRL",
        exchange=exchange,
        market_type=MarketType.FUTURE,
        raw_symbol=raw,
        dex=dex,
        bid=price,
        ask=price * 1.001,
        timestamp=NOW,
        volume_24h_usdt=2_000_000,
    )


def binding(row, asset="perle", **updates):
    return MarketBinding(
        market=MarketKey.from_snapshot(row),
        asset_id=asset,
        display_name=asset,
        identity_status="verified",
        unit_status="verified",
        quote_currency="USDT",
        verified_at=NOW,
        valid_until=NOW + timedelta(days=30),
        evidence=[
            IdentityEvidence(
                source_type="official_announcement",
                url="https://www.bitget.com/support/articles/12560603881229",
                summary="Perle PRL listing",
                fetched_at=NOW,
            )
        ],
        **updates,
    )


def test_same_ticker_different_assets_never_becomes_same_asset_opportunity():
    left, right = market(), market("hyperliquid", "io:PRL", 1.94, "io")
    registry = MarketIdentityRegistry([binding(left), binding(right, "pearl")])
    result = validate_pair(registry.annotate(left, NOW), registry.annotate(right, NOW), now=NOW)
    assert result.identity_relation == "different"
    assert "DIFFERENT_UNDERLYING_ASSET" in result.reason_codes
    assert not result.capabilities.same_asset_opportunity
    assert not result.capabilities.manual_card


def test_verified_perle_markets_remain_comparable():
    left, right = market(), market("bybit", price=0.101)
    registry = MarketIdentityRegistry([binding(left), binding(right)])
    result = validate_pair(registry.annotate(left, NOW), registry.annotate(right, NOW), now=NOW)
    assert result.identity_relation == "same"
    assert result.capabilities.same_asset_opportunity


def test_unknown_market_does_not_inherit_same_ticker_or_dex_identity():
    left, right = market(), market("hyperliquid", "xyz:PRL", 1.94, "xyz")
    registry = MarketIdentityRegistry([binding(left)])
    result = validate_pair(registry.annotate(left, NOW), registry.annotate(right, NOW), now=NOW)
    assert "ASSET_IDENTITY_UNVERIFIED" in result.reason_codes
    assert not result.capabilities.automatic_card


def test_explicit_dex_conflict_is_rejected():
    with pytest.raises(ValueError, match="DEX"):
        MarketKey.from_snapshot(market("hyperliquid", "io:PRL", dex="xyz"))


def test_reference_quotes_and_stale_leg_cannot_be_actionable():
    left, right = market(), market("bybit")
    registry = MarketIdentityRegistry([binding(left), binding(right)])
    right = registry.annotate(right, NOW).model_copy(
        update={
            "quote_kind": "reference",
            "estimated_fields": ["bid", "ask"],
            "timestamp": NOW - timedelta(minutes=1),
        }
    )
    result = validate_pair(registry.annotate(left, NOW), right, now=NOW)
    assert {"REFERENCE_QUOTE_ONLY", "STALE_LEG_QUOTE"} <= set(result.reason_codes)
    assert not result.capabilities.manual_card


def test_expired_evidence_and_explicit_cross_asset_mode():
    left, right = market(), market("bybit")
    registry = MarketIdentityRegistry([binding(left), binding(right, "pearl")])
    a, b = registry.annotate(left, NOW), registry.annotate(right, NOW)
    custom = validate_pair(a, b, now=NOW, comparison_kind="custom_cross_asset")
    assert custom.capabilities.reference_comparison
    assert not custom.capabilities.same_asset_opportunity
    assert not custom.capabilities.automatic_card
    expired = registry.annotate(left, NOW + timedelta(days=31))
    assert expired.identity_status == "expired"


def test_price_and_size_conversion_preserves_notional_and_is_idempotent():
    row = market(price=100).model_copy(update={"bid_size": 2, "ask_size": 3})
    registry = MarketIdentityRegistry([binding(row, price_multiplier=0.001)])
    converted = registry.annotate(row, NOW)
    assert converted.bid == pytest.approx(0.1)
    assert converted.bid_size == pytest.approx(2000)
    assert converted.bid * converted.bid_size == pytest.approx(row.bid * row.bid_size)
    assert registry.annotate(converted, NOW) == converted


def test_verified_binding_requires_evidence():
    row = binding(market()).model_dump()
    row["evidence"] = []
    with pytest.raises(ValueError, match="evidence"):
        MarketBinding.model_validate(row)


def test_crypto_and_official_rtoken_equity_cannot_pair_even_when_identity_is_pending():
    a = market().model_copy(update={"asset_kind": "crypto"})
    b = market("bitget", "RPRLUSDT").model_copy(update={"asset_kind": "equity"})
    result = validate_pair(a, b, now=NOW)
    assert result.identity_relation == "different"
    assert "DIFFERENT_UNDERLYING_ASSET" in result.reason_codes


def test_expired_evidence_cannot_revive_a_known_different_asset_pair():
    a, b = market(), market("hyperliquid", "io:PRL", dex="io")
    registry = MarketIdentityRegistry([binding(a), binding(b, "pearl")], mode="shadow")
    later = NOW + timedelta(days=31)
    result = validate_pair(registry.annotate(a, later), registry.annotate(b, later), now=later)
    assert result.identity_relation == "different"
    assert "DIFFERENT_UNDERLYING_ASSET" in result.reason_codes
