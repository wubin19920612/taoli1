import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.api.routes_instruments import _lookup_instrument
from app.models.market import MarketSnapshot
from app.services.mobile_summary import summary
from app.services.snapshot_store import SnapshotStore


def market(exchange, bid, ask, *, base="ANTHROPIC", dex=None, age=0, **extra):
    return MarketSnapshot(
        symbol=base + "USDT",
        base=base,
        raw_symbol=f"{dex}:{base}" if dex else base,
        market_type="future",
        exchange=exchange,
        bid=bid,
        ask=ask,
        timestamp=datetime.now(UTC) - timedelta(seconds=age),
        **extra,
    )


def request_for(rows, age=0):
    store = SnapshotStore()
    store.set_all_markets(rows)
    store.get_all_markets = Mock(wraps=store.get_all_markets)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                snapshot_store=store,
                astro_client=SimpleNamespace(_pairs_cached_at=time.monotonic() - age),
            )
        )
    )


# Same card, thresholds and order books as FloatingWatchPanel.test.tsx.
RATIO = {
    "name": "ANTHROPIC-ANTHROPIC",
    "type": "FR",
    "status": True,
    "buyEx": "bitget",
    "sellEx": "gc-okx",
    "openPosition": 9.706375012316485,
    "closePosition": 9.71900419028839,
    "regressionValue": 10,
    "aExPosition": 40,
    "bExPosition": 400,
    "avgOpenAExPrice": 190,
    "avgOpenBExPrice": 21,
    "profit": -1.3,
    "realizedProfit": 10,
}
FF = {
    "name": "OPENAI",
    "type": "FF",
    "status": True,
    "buyEx": "gc-binance",
    "sellEx": "gc-gate",
    "openPosition": "0.0030",
    "closePosition": "-0.0015",
    "aExPosition": 0,
    "bExPosition": 0,
}


@pytest.mark.parametrize("alias_multiplier", [1, 10])
async def test_ratio_matches_floating_panel(alias_multiplier):
    rows = [
        market("bitget", 199, 201, funding_rate_pct=0.01234, funding_interval_hours=8),
        market(
            "okx",
            19.9 * alias_multiplier,
            20.1 * alias_multiplier,
            symbol_alias_price_multiplier=alias_multiplier,
            funding_rate_pct=-0.00456,
            funding_interval_hours=4,
        ),
    ]
    result = await summary(request_for(rows), [RATIO])
    (card,) = result.cards
    assert result.stale is False
    assert card.open_spread_now == pytest.approx(-1)
    assert card.close_spread_now == pytest.approx(1)
    assert card.open_condition == pytest.approx(2.98)
    assert card.close_condition == pytest.approx(2.85)
    assert card.gap_to_open == pytest.approx(-3.98)
    assert card.gap_to_close == pytest.approx(1.85)
    assert card.notional_usdt == 16000
    assert card.unrealized_usdt == pytest.approx(360 + (21 - 20.1 * alias_multiplier) * 400 - 32)
    assert card.astro_profit == -1.3 and card.realized_profit is None
    assert card.funding_diff == pytest.approx(-0.01690)
    assert (card.funding_interval_h_a, card.funding_interval_h_b) == (8, 4)


async def test_ff_order_gaps_stale_and_incomplete_positions():
    rows = [market("binance", 99, 100, base="OPENAI"), market("gate", 101, 102, base="OPENAI")]
    pairs = [
        dict(FF, openPosition=".02"),
        dict(FF, name="MISSING"),
        FF,
        dict(FF, aExPosition="-2", avgOpenAExPrice="98"),
        dict(FF, bExPosition="1", sellEx="gc-bitget", status=False),
    ]
    result = await summary(request_for(rows), pairs)
    paused, running, closest, farthest, missing = result.cards
    assert paused.holding and paused.state == "paused"
    assert paused.metric_error == "未找到 Bitget 永续实时行情"
    assert paused.quote_age_s is None and paused.unrealized_usdt is None
    assert result.stale
    assert running.holding and running.state == "running" and running.qty_a == 2
    assert running.unrealized_usdt == pytest.approx(2 - 196 * 0.002)
    assert closest.open_spread_now == pytest.approx(100 / 100.5)
    assert closest.close_spread_now == pytest.approx(300 / 100.5)
    assert closest.open_condition == pytest.approx(0.30)
    assert closest.close_condition == pytest.approx(-0.15)
    assert closest.gap_to_open > 0 > closest.gap_to_close
    assert closest.unrealized_usdt is None
    assert farthest.gap_to_open < closest.gap_to_open
    assert missing.gap_to_open is None
    assert not (await summary(request_for(rows), [FF, dict(FF, name="MISSING")])).stale


async def test_exact_hl_dex_and_fee_even_when_raw_tickers_match():
    pair = dict(
        FF,
        buyEx="hl",
        aHlDex="xyz",
        aEffectiveHlDex="io",
        sellEx="hl",
        bHlDex="xyz",
        aExPosition=1,
        avgOpenAExPrice=100,
    )
    rows = [
        market("hyperliquid", 99, 100, base="OPENAI", dex="io"),
        market("hyperliquid", 101, 102, base="OPENAI", dex="xyz"),
        market("hyperliquid", 1, 2, base="OPENAI"),
    ]
    card, wrong = (await summary(request_for(rows), [pair, dict(pair, bHlDex="unused")])).cards
    assert (card.dex_a, card.dex_b) == ("io", "xyz")
    assert (card.raw_symbol_a, card.raw_symbol_b) == ("io:OPENAI", "xyz:OPENAI")
    assert card.open_spread_now == pytest.approx(100 / 100.5)
    assert card.unrealized_usdt == pytest.approx(-1.05)
    assert (
        wrong.close_spread_now is None and wrong.metric_error == "未找到 Hyperliquid 永续实时行情"
    )
    single_hl = dict(pair, sellEx="gate")
    mixed = await summary(
        request_for([*rows, market("gate", 101, 102, base="OPENAI")]), [single_hl]
    )
    assert mixed.cards[0].unrealized_usdt == pytest.approx(-1.13)


async def test_staleness_uses_older_leg_and_astro_cache_and_frontend_errors():
    rows = [
        market("binance", 99, 100, base="OPENAI", age=31),
        market("gate", 101, 102, base="OPENAI"),
    ]
    held = dict(FF, aExPosition=5)
    result = await summary(request_for(rows), [held])
    assert result.stale and result.cards[0].quote_age_s >= 31
    assert not (await summary(request_for(rows), [FF])).stale
    assert (await summary(request_for([], age=31), [])).stale
    (invalid,) = (
        await summary(request_for([]), [dict(RATIO, name="bad", openPosition="Infinity")])
    ).cards
    assert invalid.metric_error == "无法从卡片名称识别两腿标的" and invalid.open_condition is None


async def test_fifty_cards_time_and_single_snapshot_read():
    # Distinct symbols and two routes per symbol exercise both lookup and reuse.
    pairs = [dict(FF, name=f"S{i // 2}", disableOpen=bool(i % 2)) for i in range(50)]
    rows = [market(ex, 99, 101, base=f"S{i}") for i in range(25) for ex in ("binance", "gate")]
    request = request_for(rows)
    started = time.perf_counter()
    result = await summary(request, pairs)
    duration = time.perf_counter() - started
    assert len(result.cards) == 50 and all(card.metric_error == "" for card in result.cards)
    request.app.state.snapshot_store.get_all_markets.assert_called_once()
    print(f"50 cards: {duration * 1000:.1f} ms")
    assert duration < 1


@pytest.mark.parametrize(("card_dex", "age", "expected_raw"), [
    ("", 0, None), (" MAIN ", 0, "ANTHROPIC"), ("io", 0, "xyz:ANTH"),
    ("io", 3600, None),
])
async def test_hl_matches_floating_panel_instrument_candidates(card_dex, age, expected_raw):
    # ANTHROPIC resolves to io on an unscoped instrument query. The main book
    # exists in the store but is absent from the page's returned candidates.
    pair = dict(FF, name="ANTHROPIC", buyEx="hl", aHlDex=card_dex)
    io = market("hyperliquid", 101, 102, age=age).model_copy(
        update={"dex": "io", "raw_symbol": "xyz:ANTH"}
    )
    request = request_for([market("hyperliquid", 90, 91), io, market("gate", 110, 111)])
    result = await _lookup_instrument(
        "ANTHROPICUSDT", request, card_dex.strip().lower() or None, include_astro_routes=False
    )
    candidates = [row for row in result.markets if row.exchange == "hyperliquid"]
    assert candidates
    requested = card_dex.strip().lower() or "main"
    # FloatingWatchPanel.astroMarket searches result.markets before exchanges.
    web_match = next((
        row for row in candidates
        if row.market_type == "future" and row.data_status == "live"
        and (row.dex or (row.raw_symbol.split(":", 1)[0] if ":" in row.raw_symbol else "main")).lower() == requested
    ), None)
    assert (web_match.raw_symbol if web_match else None) == expected_raw
    (card,) = (await summary(request, [pair])).cards
    assert card.raw_symbol_a == expected_raw and card.dex_a == requested
    if web_match is None:
        assert card.open_spread_now is None and card.metric_error == "未找到 Hyperliquid 永续实时行情"
    else:
        assert card.open_spread_now is not None and card.metric_error == ""


async def test_card_lookup_exception_preserves_other_quotes():
    rows = [market(ex, 99, 101, base=base) for ex in ("gate", "binance") for base in ("BTC", "ETH")]
    request = request_for(rows)

    def annotate(row):
        if row.base == "BTC":
            raise ValueError("unreadable snapshot")
        return row

    request.app.state.market_validation = SimpleNamespace(annotate=annotate)
    result = await summary(request, [dict(FF, name="BTC", aExPosition=1), dict(FF, name="ETH")])
    bad, good = result.cards
    assert bad.name == "BTC" and bad.qty_a == 1 and bad.metric_error == "卡片数据格式异常"
    assert good.name == "ETH" and good.open_spread_now is not None and good.metric_error == ""
