"""Production selection ownership, sliced capture and mixed publication regressions."""
import asyncio
from datetime import timedelta

import httpx
import pytest

from app.core.config import Settings
from app.services.fake_opportunity import FakeOpportunityScorer, market_key
from app.services.shadow_runtime import ShadowRuntime
from app.services.snapshot_consumers import PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_directional_opportunity
from shadow_helpers import NOW, market, pair


def test_capacity_protects_related_markets_and_evicts_oldest_seen():
    scorer = FakeOpportunityScorer(max_markets=4)
    related = [market(ex, price) for ex, price in (("binance", 100), ("bitget", 104), ("okx", 100))]
    op = build_directional_opportunity(related[0], related[1], mode="FF")
    old = market(symbol="OLD", raw_symbol="OLD")
    newer = market(symbol="NEW", raw_symbol="NEW", timestamp=NOW + timedelta(seconds=1))
    scorer.score([old], [], NOW, related_assets=set())
    scorer.score([newer], [], NOW + timedelta(seconds=1), related_assets=set())
    result = scorer.score(related, [op], NOW + timedelta(seconds=2))
    assert result[op.id]["fake_reasons"] == ["median_outlier"]
    assert market_key(old) not in scorer.prices
    assert market_key(newer) in scorer.prices
    for i in range(30):
        now = NOW + timedelta(seconds=3 + i)
        incoming = market(symbol=f"OTHER{i}", raw_symbol=f"OTHER{i}", timestamp=now)
        scorer.score([], [], now, related_assets={"BTCUSDT"}, history=[incoming])
        assert all(market_key(m) in scorer.prices for m in related)
        assert len(scorer.prices) == 4
    full = FakeOpportunityScorer(max_markets=3)
    full.score(related, [op], NOW)
    full.score([], [], NOW, related_assets={"BTCUSDT"}, history=[old])
    assert full.dropped == 1 and len(full.prices) == 3
    assert FakeOpportunityScorer().max_markets == 20000


@pytest.mark.asyncio
async def test_full_capture_yields_every_thousand_and_guards_single_flight(monkeypatch):
    import app.services.shadow_runtime as module
    store = SnapshotStore()
    runtime = ShadowRuntime(Settings(shadow_enabled=True), store)
    related, op = pair()
    background = [market(symbol=f"BG{i}", raw_symbol=f"BG{i}") for i in range(2501)]
    snapshot = PublishedSnapshot(1, NOW, tuple(related + background), (op,))
    calls, widths, current_width = [], [], [0]
    original_sleep, original_values = asyncio.sleep, module._market_values

    def values(data):
        current_width[0] += 1
        return original_values(data)

    async def yielding(delay):
        widths.append(current_width[0])
        current_width[0] = 0
        calls.append(delay)
        assert await runtime.submit(PublishedSnapshot(2, NOW, (), ())) is None
        # Later shards may see newer history. Current related prices must stay
        # detached, so this cannot turn the current opening into an outlier.
        related[1].bid = related[1].ask = 999
        await original_sleep(delay)

    monkeypatch.setattr(module, "_market_values", values)
    monkeypatch.setattr(module.asyncio, "sleep", yielding)
    store.mark_published(NOW)
    await runtime.consume(snapshot)
    widths.append(current_width[0])
    assert calls == [0, 0, 0]
    assert max(widths) <= 1000
    assert runtime.status()["skipped_rounds"] == 3
    assert runtime.status()["scorer_price_states"] == 2503
    assert runtime.errors == 0
    assert runtime.scorer.prices[market_key(related[1])].mid == 101
    assert len(runtime.status()["capture_segments_ms"]) == 4
    await runtime.aclose()


def test_production_compact_unpack_replaces_models_and_nested_lists():
    from app.services.compact_markets import pack
    quotes, _ = pair()
    quotes[0].estimated_fields.append("volume")
    packed = pack(quotes)
    first, second = packed.unpack(), packed.unpack()
    first[0].bid = 999
    first[0].estimated_fields.append("bid")
    assert second[0] is not first[0] and second[0].bid == 100
    assert second[0].estimated_fields == ["volume"]
    assert packed.unpack()[0].bid == 100


@pytest.mark.asyncio
async def test_mixed_list_preserves_objects_order_api_serialization_and_alerts(tmp_path, monkeypatch):
    from app.main import create_app
    from app.services.alert_engine import opportunity_matches_rule
    from app.models.alert import AlertRule
    from app.models.settings import RiskSettings
    from auth_helpers import IsolatedSessionLookup, TEST_TOTP_SECRET, session_headers
    import app.api.routes_opportunities as route

    app = create_app(settings=Settings(shadow_enabled=True,
        database_url="sqlite:///" + str(tmp_path / "mixed.db"), dashboard_password="test-only",
        dashboard_totp_secret=TEST_TOTP_SECRET), start_background_workers=False)
    app.state.auth_store = IsolatedSessionLookup()
    runtime = app.state.shadow_runtime
    store = app.state.snapshot_store
    quotes, op = pair()
    low = op.model_copy(update={"id": "unrelated", "open_spread_pct": .1})
    low2 = op.model_copy(update={"id": "unrelated2", "open_spread_pct": -.1})
    original_dump = low.model_dump(mode="json")
    store.mark_published(NOW)
    await runtime.consume(PublishedSnapshot(store.generation, NOW, tuple(quotes), (low, op, low2)))
    mixed = store.get_opportunities()
    assert [o.id for o in mixed] == [low.id, op.id, low2.id]
    assert mixed[0] is low and mixed[2] is low2 and mixed[1] is not op
    assert low.model_dump(mode="json") == original_dump and low.fake_score is None
    assert mixed[1].fake_score == 0 and op.fake_score is None
    rule = AlertRule(name="mixed", min_open_spread_pct=0)
    for source, shown in zip((low, op, low2), mixed):
        assert opportunity_matches_rule(rule, source, NOW, RiskSettings()) == opportunity_matches_rule(rule, shown, NOW, RiskSettings())
    monkeypatch.setattr(route, "utc_now", lambda: NOW)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                headers=session_headers("test-password")) as client:
        response = await client.get("/api/opportunities?include_risky=true")
        assert response.status_code == 200
        data = response.json()
        assert [o["id"] for o in data] == [low.id, op.id, low2.id]
        assert data[0]["fake_score"] is None and data[1]["fake_score"] == 0
    assert set(runtime._display_cache) == {op.id}
    assert set(runtime._input_cache) == {op.id}
    await runtime.aclose()
