import pytest

from app.core.config import Settings, get_settings
from app.services.execution_routes import ExecutionRoutes, parse_catalog
from app.services.risk_labels import apply_risk_labels
from app.services.alert_engine import opportunity_matches_rule
from app.models.settings import RiskSettings
from app.models.alert import AlertRule
from shadow_helpers import NOW, pair


def records():
    return [dict(exchange_type=ex, business_type="FUTURE", symbol=f"{ex}_FUTURE_{base}_USDC", tag=tag, state=state)
            for ex, base, tag, state in [("HYPERLIQUID", "ANTH", "xyz", "live"),
                                       ("HYPERLIQUID", "BTC", "", "live"),
                                       ("LIGHTER", "BTC", "", "live"),
                                       ("BINANCE", "BTC", "", "live"),
                                       ("BITGET", "BTC", "", "live"),
                                       ("OKX", "BTC", "", "halted")]]


def test_exact_routes_and_dex():
    routes = ExecutionRoutes()
    routes.entries = parse_catalog(records())
    assert routes.routes("hyperliquid", "future", "xyz:ANTH", "xyz") == ["native", "crossex"]
    assert routes.routes("hyperliquid", "future", "para:ANTH", "para") == ["native"]
    assert routes.routes("hyperliquid", "future", "xyz:ANTH", "para") == ["native"]
    assert routes.routes("hyperliquid", "future", "BTC", "main") == ["native", "crossex"]
    assert routes.routes("hyperliquid", "future", "xyz:ANTHROPICX", "xyz") == ["native"]
    assert routes.routes("lighter", "future", "BTC") == ["native", "crossex"]
    assert routes.routes("bitget", "future", "BTCUSDT") == ["native"]
    assert routes.routes("binance", "future", "BTCUSDT") == ["crossex"]
    assert routes.routes("binance", "spot", "BTCUSDT") == []
    assert routes.routes("okx", "future", "BTC-USDT-SWAP") == []
    _, op = pair()
    assert routes.fields(op)["executable"]


@pytest.mark.asyncio
async def test_failed_refresh_retains_compact_entries_and_retries_after_15_minutes():
    now = [0]
    calls = []
    async def fetch():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("offline fixture")
        return records()
    routes = ExecutionRoutes(fetch=fetch, clock=lambda: now[0])
    await routes.refresh()
    entries, updated = routes.entries, routes.updated_at
    now[0] = 1800
    await routes.refresh()
    assert routes.entries is entries and routes.updated_at == updated
    assert routes.retry_at == 2700
    await routes.refresh()
    assert len(calls) == 2
    now[0] = 2700
    await routes.refresh()
    assert len(calls) == 3


@pytest.mark.parametrize("buy,sell,expected", [(None, None, True), (None, 2e6, True), (1, None, False)])
def test_unknown_volume_allowed_but_known_low_volume_rejected(buy, sell, expected):
    _, op = pair(buy_volume_24h_usdt=buy, sell_volume_24h_usdt=sell)
    risk = RiskSettings(min_effective_open_pct=0)
    rule = AlertRule(name="volume", types=["FF"], min_volume_24h_usdt=1e6,
                     min_open_spread_pct=0.2, min_fee_adjusted_open_pct=0, excluded_risk_labels=[])
    op = apply_risk_labels(op, risk, NOW)
    assert "volume_unknown" in op.risk_labels
    assert opportunity_matches_rule(rule, op, NOW, risk) is expected
    rule.excluded_risk_labels = ["volume_unknown"]
    assert not opportunity_matches_rule(rule, op, NOW, risk)


def test_config_defaults_and_environment(monkeypatch):
    assert not Settings().shadow_enabled
    monkeypatch.delenv("SHADOW_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().shadow_enabled is False
        monkeypatch.setenv("SHADOW_ENABLED", "0")
        get_settings.cache_clear()
        assert get_settings().shadow_enabled is False
        monkeypatch.setenv("SHADOW_ENABLED", "1")
        monkeypatch.setenv("SHADOW_MAX_OPEN", "100")
        get_settings.cache_clear()
        assert get_settings().shadow_enabled
        assert get_settings().shadow_max_open == 100
    finally:
        get_settings.cache_clear()
    with pytest.raises(ValueError):
        Settings(shadow_slippage_bps='{"unknown": -1}')
