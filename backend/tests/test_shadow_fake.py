import math
from datetime import timedelta

from app.services.fake_opportunity import FakeOpportunityScorer
from app.services.spread_engine import build_directional_opportunity
from shadow_helpers import NOW, market, pair


def test_outlier_requires_two_other_exchanges_in_agreement_and_same_asset():
    markets, op = pair()
    markets[1].bid = markets[1].ask = 104
    markets.append(market("okx"))
    score = FakeOpportunityScorer().score(markets, [op], NOW)[op.id]
    assert score["fake_flag"] and score["fake_reasons"] == ["median_outlier"]
    assert not FakeOpportunityScorer().score(markets[:2], [op], NOW)[op.id]["fake_flag"]
    markets[2].exchange = "binance"  # duplicate venue is not a second vote
    assert not FakeOpportunityScorer().score(markets, [op], NOW)[op.id]["fake_flag"]
    markets[2].exchange, markets[2].asset_id = "okx", "different-asset"
    assert not FakeOpportunityScorer().score(markets, [op], NOW)[op.id]["fake_flag"]


def test_soft_rules_extreme_volume_and_strict_threshold():
    markets, op = pair(open_spread_pct=5, buy_volume_24h_usdt=None)
    scorer = FakeOpportunityScorer()
    result = scorer.score(markets, [op], NOW)[op.id]
    assert result["fake_reasons"] == ["volume_unknown"] and not result["fake_flag"]
    op.open_spread_pct = 5.01
    assert scorer.score(markets, [op], NOW)[op.id]["fake_flag"]
    op.buy_volume_24h_usdt = 100
    assert not scorer.score(markets, [op], NOW)[op.id]["fake_flag"]


def test_bmt_frozen_and_plateau_regression():
    scorer = FakeOpportunityScorer()
    for minute in range(61):
        now = NOW + timedelta(minutes=minute)
        # >0.3% peak-to-trough movement on three venues; Bitget stays frozen.
        # Actual book-derived spread deviation remains below 5% of its mean.
        price = 100 + 0.16 * math.sin(minute * math.pi / 7)
        markets = [market(ex, price, timestamp=now, symbol="BMTUSDT", raw_symbol="BMTUSDT")
                   for ex in ["binance", "okx", "bybit"]]
        markets.append(market("bitget", 102.5, timestamp=now, symbol="BMTUSDT", raw_symbol="BMTUSDT"))
        op = build_directional_opportunity(markets[0], markets[3], mode="FF")
        result = scorer.score(markets, [op], now)[op.id]
        if minute == 14:
            assert "frozen_leg" not in result["fake_reasons"]
        if minute == 58:
            assert "spread_plateau" not in result["fake_reasons"]
    assert result["fake_flag"]
    assert {"frozen_leg", "spread_plateau"} <= set(result["fake_reasons"])


def test_plateau_needs_full_hour_and_expires_with_gaps():
    scorer = FakeOpportunityScorer()
    markets, op = pair(open_spread_pct=2.5)
    for i in range(60):
        now = NOW + timedelta(minutes=i)
        result = scorer.score([], [op], now)[op.id]
    assert "spread_plateau" in result["fake_reasons"]
    result = scorer.score([], [op], NOW + timedelta(minutes=61))[op.id]
    assert "spread_plateau" not in result["fake_reasons"]
    scorer.score([], [], NOW + timedelta(hours=3))
    assert not scorer.spreads and not scorer.prices


def test_not_frozen_when_all_prices_still_and_bounded_state():
    scorer = FakeOpportunityScorer(max_markets=2, max_series=1)
    markets, op = pair(open_spread_pct=2.5)
    for i in range(20):
        now = NOW + timedelta(minutes=i)
        books = [m.model_copy(update={"timestamp": now}) for m in markets]
        result = scorer.score(books, [op], now)[op.id]
    assert "frozen_leg" not in result["fake_reasons"]
    scorer.score([*books, market("okx", timestamp=now)], [op, op.model_copy(update={"id": "other"})], now)
    assert len(scorer.prices) == 2 and len(scorer.spreads) == 1 and scorer.dropped == 2
