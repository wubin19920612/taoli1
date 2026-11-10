"""Offline contract tests for the Gate child wire parser and quote merge."""
import ast
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings, get_settings
from app.exchanges.gate import GateAdapter
from app.exchanges.gate_bbo import GateBboWorker
from app.models.market import MarketSnapshot, MarketType
from app.services import gate_bbo_runtime as runtime_module
from app.services.fake_opportunity import prepare_markets
from app.services.gate_bbo_runtime import GateBboRuntime, select_subscriptions
from app.services.spread_engine import _has_executable_book

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
STAMP = int(NOW.timestamp() * 1000)


def wire(kind="spot", **updates):
    result = {"t": STAMP, "u": 10, "s": "BTC_USDT", "b": "100", "B": "2", "a": "101", "A": "3"}
    result.update(updates)
    return json.dumps({"channel": f"{'futures' if kind == 'future' else 'spot'}.book_ticker",
                           "event": "update", "result": result})


def worker():
    result = GateBboWorker()
    result.set_subscriptions({"future": ["BTC_USDT"], "spot": ["BTC_USDT"]})
    return result


@pytest.mark.parametrize("kind", ["future", "spot"])
def test_parser_valid(kind):
    child = worker()
    child.handle_message(kind, wire(kind))
    assert child.quotes[(kind, "BTC_USDT")][:6] == (100, 2, 101, 3, STAMP, 10)


@pytest.mark.parametrize("field", ["b", "B", "a", "A"])
@pytest.mark.parametrize("value", ["", "0", 0])
def test_empty_side_replaces_old_quote(field, value):
    child = worker()
    child.handle_message("spot", wire())
    child.handle_message("spot", wire(u=11, **{field: value}))
    quote = child.quotes[("spot", "BTC_USDT")]
    assert quote[:2] == (None, None) if field in {"b", "B"} else quote[2:4] == (None, None)


@pytest.mark.parametrize("field,value", [
    (field, value) for field in ("b", "B", "a", "A")
    for value in ("nan", "inf", "-1", "garbage", None, True)
] + [("t", True), ("t", "123"), ("t", 0), ("u", -1), ("u", True)])
def test_invalid_update_invalidates_old_book(field, value):
    child = worker()
    child.handle_message("spot", wire())
    child.handle_message("spot", wire(u=11, **{field: value}) if field != "u" else wire(u=value))
    assert child.quotes.get(("spot", "BTC_USDT")) is None
    assert child.dirty[("spot", "BTC_USDT")] is None


def test_ack_error_malformed_unknown_and_out_of_order():
    child = worker()
    child.handle_message("spot", wire())
    for payload in ["not-json", "[]", "null", wire(s="OTHER_USDT"), wire(u=9, b="99"),
                    json.dumps({"channel": "spot.book_ticker", "event": "subscribe", "result": {"status": "success"}})]:
        child.handle_message("spot", payload)
    assert child.quotes[("spot", "BTC_USDT")][0] == 100
    with pytest.raises(ValueError, match="Gate"):
        child.handle_message("spot", json.dumps({"channel": "spot.book_ticker", "error": {"code": 2}}))


def test_invalid_newer_update_cannot_be_resurrected_by_older_valid_update():
    child = worker()
    child.handle_message("spot", wire())
    child.handle_message("spot", wire(u=12, b="nan"))
    child.handle_message("spot", wire(u=11))
    assert child.quotes[("spot", "BTC_USDT")] is None
    child.handle_message("spot", wire(u=13))
    assert child.quotes[("spot", "BTC_USDT")][5] == 13


def test_initial_message_rate_uses_elapsed_window(monkeypatch):
    import queue

    import app.exchanges.gate_bbo as child_module
    monkeypatch.setattr(child_module, "monotonic", lambda: 100)
    child = worker()
    monkeypatch.setattr(child_module, "monotonic", lambda: 102)
    child.handle_message("spot", wire())
    child.handle_message("spot", wire(u=11))
    output = queue.Queue()
    child.publish(output)
    assert output.get_nowait()["connections"]["spot"]["messages_per_second"] == 1


def test_real_recorded_updates():
    records = json.loads((Path(__file__).parent / "fixtures/gate_bbo/recorded.json").read_text())
    assert {record["kind"] for record in records if record["message"].get("event") == "update"} == {"spot", "future"}
    child = GateBboWorker()
    child.set_subscriptions({kind: ["AKE_USDT", "JCT_USDT", "BTC_USDT"] for kind in ("spot", "future")})
    for record in records:
        child.handle_message(record["kind"], json.dumps(record["message"]))
    assert any(key[0] == "future" and value for key, value in child.quotes.items())
    assert any(key[0] == "spot" and value for key, value in child.quotes.items())


def test_protocol_edge_case_fixtures():
    path = Path(__file__).parent / "fixtures/gate_bbo/edge-cases.json"
    ack, empty, invalid, error = json.loads(path.read_text())["messages"]
    child = worker()
    child.handle_message("spot", wire())
    child.handle_message("spot", json.dumps(ack))
    assert child.quotes[("spot", "BTC_USDT")][0] == 100
    child.handle_message("spot", json.dumps(empty))
    assert child.quotes[("spot", "BTC_USDT")][:2] == (None, None)
    child.handle_message("spot", json.dumps(invalid))
    assert child.quotes[("spot", "BTC_USDT")] is None
    with pytest.raises(ValueError, match="Gate"):
        child.handle_message("spot", json.dumps(error))


def market(raw="BTC_USDT", kind=MarketType.SPOT, exchange="gate", volume=5_000_000):
    return MarketSnapshot(symbol=raw.replace("_", ""), base=raw.split("_")[0],
        exchange=exchange, market_type=kind, raw_symbol=raw, bid=90, ask=91,
        timestamp=NOW, volume_24h_usdt=volume)


def ready_runtime(monkeypatch, **settings):
    rt = GateBboRuntime(Settings(gate_bbo_enabled=True, **settings))
    rt.set_subscriptions({"spot": ["BTC_USDT"], "future": ["BTC_USDT"]})
    rt._process = SimpleNamespace(is_alive=lambda: True, pid=123)
    rt._connections = {kind: {"connected": True, "last_message_at": 100, "generation": 1}
                       for kind in ("spot", "future")}
    rt._quotes = {(kind, "BTC_USDT"): (100, 2, 101, 3, STAMP, 10, 1)
                  for kind in ("spot", "future")}
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 100)
    monkeypatch.setattr(runtime_module, "time", lambda: NOW.timestamp())
    return rt


@pytest.mark.parametrize("kind", [MarketType.SPOT, MarketType.FUTURE])
@pytest.mark.parametrize("reason", ["fresh", "old", "future", "silent", "dead", "disabled", "generation", "empty", "crossed"])
async def test_adapter_merge_and_executable_consumers(monkeypatch, kind, reason):
    rt = ready_runtime(monkeypatch)
    key = (str(kind), "BTC_USDT")
    if reason in {"old", "future"}:
        quote = list(rt._quotes[key])
        quote[4] += -120001 if reason == "old" else 2001
        rt._quotes[key] = tuple(quote)
    elif reason == "silent":
        rt._connections[str(kind)]["last_message_at"] = 94.99
    elif reason == "dead":
        rt._process = None
    elif reason == "disabled":
        rt.settings = Settings()
    elif reason == "generation":
        rt._connections[str(kind)]["generation"] = 2
    elif reason in {"empty", "crossed"}:
        quote = list(rt._quotes[key])
        quote[0] = None if reason == "empty" else 102
        rt._quotes[key] = tuple(quote)
    adapter = GateAdapter(bbo_runtime=rt)
    adapter.get_json = AsyncMock(return_value=[{"currency_pair": "BTC_USDT", "contract": "BTC_USDT",
        "highest_bid": "90", "lowest_ask": "91", "quote_volume": "5000000", "volume_24h_quote": "5000000"}])
    adapter._fetch_contract_info = AsyncMock(return_value={"BTC_USDT": {"quanto_multiplier": "0.01"}})
    monkeypatch.setattr("app.exchanges.gate.utc_now", lambda: NOW)
    try:
        rows = await (adapter.fetch_spot_tickers() if kind == MarketType.SPOT else adapter.fetch_future_tickers())
    finally:
        await adapter.client.aclose()
    row = rows[0]
    fresh = reason == "fresh"
    assert row.quote_time_source == ("ws" if fresh else "received_batch")
    assert row.is_estimated is not fresh
    assert _has_executable_book(row) is fresh
    assert next(iter(prepare_markets(rows, NOW).values()))[2] is fresh
    assert row.volume_24h_usdt == 5_000_000
    if fresh:
        assert (row.bid, row.ask, row.upstream_timestamp) == (100, 101, NOW)
        scale = .01 if kind == MarketType.FUTURE else 1
        assert (row.bid_size, row.ask_size) == (2 * scale, 3 * scale)
        assert rt.merge_hits == 1
    else:
        assert (row.bid, row.ask) == (90, 91)
        assert set(row.estimated_fields) >= {"bid", "ask"}
        assert rt.merge_fallbacks == 1


@pytest.mark.parametrize("kind", [MarketType.SPOT, MarketType.FUTURE])
def test_rollback_preserves_every_original_field_even_with_fresh_ws(monkeypatch, kind):
    rt = ready_runtime(monkeypatch)
    # This is the exact object constructed by the original ticker path.
    original = market(kind=kind).model_copy(update={"upstream_timestamp": NOW,
        "funding_rate_pct": .01, "contract_size_multiplier": .01})
    adapter = GateAdapter.__new__(GateAdapter)
    adapter.bbo_runtime, adapter.batch_quotes_executable = rt, True
    assert adapter._merge_bbo(original).model_dump() == original.model_dump()


@pytest.mark.parametrize("kind", [MarketType.SPOT, MarketType.FUTURE])
@pytest.mark.parametrize("with_ws", [False, True])
async def test_rollback_full_collection_matches_frozen_baseline(monkeypatch, kind, with_ws):
    from unittest.mock import Mock

    from gate_bbo_legacy_reference import GateAdapter as LegacyGateAdapter

    rt = ready_runtime(monkeypatch) if with_ws else None
    current = GateAdapter(client=Mock(), bbo_runtime=rt, batch_quotes_executable=True)
    legacy = LegacyGateAdapter(client=Mock())
    tickers = [{"currency_pair": "BTC_USDT", "contract": "BTC_USDT", "highest_bid": "90",
        "lowest_ask": "91", "quote_volume": "12345", "volume_24h_quote": "54321",
        "funding_rate": "0.001", "funding_rate_indicative": "0.002", "funding_interval": "14400",
        "funding_next_apply": "1794312000", "mark_price": "90.5", "index_price": "90.4",
        "time": "1794311999"}]
    contracts = [{"name": "BTC_USDT", "quanto_multiplier": "0.0001"}]
    async def get_json(url):
        return contracts if url.endswith("contracts") else tickers
    for adapter in (current, legacy):
        adapter.get_json = get_json
    monkeypatch.setattr("app.exchanges.gate.utc_now", lambda: NOW)
    monkeypatch.setattr("gate_bbo_legacy_reference.utc_now", lambda: NOW)
    method = "fetch_spot_tickers" if kind == MarketType.SPOT else "fetch_future_tickers"
    assert [row.model_dump() for row in await getattr(current, method)()] == [
        row.model_dump() for row in await getattr(legacy, method)()]


@pytest.mark.parametrize("scale", [None, float("inf"), 1e308])
def test_missing_or_invalid_futures_quantity_conversion_falls_back(monkeypatch, scale):
    rt = ready_runtime(monkeypatch)
    original = market(kind=MarketType.FUTURE).model_copy(update={"contract_size_multiplier": scale})
    adapter = GateAdapter.__new__(GateAdapter)
    adapter.bbo_runtime, adapter.batch_quotes_executable = rt, False
    assert adapter._merge_bbo(original).estimated_fields == ["bid", "ask"]


@pytest.mark.parametrize("age,expected", [(-2, True), (120, True), (-2.001, False), (120.001, False)])
def test_quote_age_boundaries(monkeypatch, age, expected):
    rt = ready_runtime(monkeypatch)
    monkeypatch.setattr(runtime_module, "time", lambda: NOW.timestamp() + age)
    assert (rt.get_fresh("spot", "BTC_USDT") is not None) is expected


def test_subscription_threshold_cross_venue_caps_and_retention():
    rows = [market(raw) for raw in ("AAA_USDT", "BBB_USDT", "CCC_USDT")]
    rows += [market("AAA_USDT", MarketType.FUTURE), market("BBB_USDT", MarketType.FUTURE)]
    rows += [market("LOW_USDT", volume=4_999_999), market("NAN_USDT", volume=float("nan"))]
    rows += [market(raw, exchange="binance") for raw in ("AAA_USDT", "BBB_USDT", "LOW_USDT", "NAN_USDT")]
    settings = Settings(gate_bbo_max_spot=1, gate_bbo_max_futures=2)
    selected = select_subscriptions(rows, {"spot": ["BBB_USDT"], "future": []}, settings)
    assert selected == {"spot": ["BBB_USDT"], "future": ["AAA_USDT", "BBB_USDT"]}


def test_parent_never_parses_wire_messages():
    source = inspect.getsource(runtime_module)
    tree = ast.parse(source)
    calls = [node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
             for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, (ast.Name, ast.Attribute))]
    assert not set(calls) & {"handle_message", "loads", "parse_quote"}
    assert "._quotes.update(" in source


@pytest.mark.parametrize("field,value", [
    ("gate_bbo_max_futures", 0), ("gate_bbo_max_spot", 1001), ("gate_bbo_max_spot", True),
    ("gate_bbo_min_volume_usd", -1), ("gate_bbo_min_volume_usd", float("nan")),
    ("gate_bbo_child_max_rss_mb", 0), ("gate_bbo_quote_max_age_seconds", float("inf")),
    ("gate_bbo_enabled", "yes"), ("gate_batch_quotes_executable", 1),
])
def test_invalid_config(field, value):
    with pytest.raises(ValueError, match="GATE_"):
        Settings(**{field: value})


def test_config_defaults_environment_and_invalid_bool(monkeypatch):
    monkeypatch.setattr("app.core.config.find_dotenv", lambda **kwargs: "")
    assert Settings().gate_bbo_enabled is False
    assert Settings().gate_batch_quotes_executable is False
    for key, value in {"GATE_BBO_ENABLED": "1", "GATE_BATCH_QUOTES_EXECUTABLE": "1",
                       "GATE_BBO_MAX_FUTURES": "50", "GATE_BBO_MAX_SPOT": "60",
                       "GATE_BBO_MIN_VOLUME_USD": "6000000", "GATE_BBO_CHILD_MAX_RSS_MB": "100",
                       "GATE_BBO_QUOTE_MAX_AGE_SECONDS": "90"}.items():
        monkeypatch.setenv(key, value)
    settings = get_settings.__wrapped__()
    assert settings.gate_bbo_enabled and settings.gate_batch_quotes_executable
    assert (settings.gate_bbo_max_futures, settings.gate_bbo_max_spot) == (50, 60)
    assert settings.gate_bbo_min_volume_usd == 6_000_000
    assert settings.gate_bbo_child_max_rss_mb == 100
    assert settings.gate_bbo_quote_max_age_seconds == 90
    monkeypatch.setenv("GATE_BBO_ENABLED", "typo")
    with pytest.raises(ValueError, match="GATE_BBO_ENABLED"):
        get_settings.__wrapped__()
