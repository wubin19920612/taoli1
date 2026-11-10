"""Opt-in, offline R4 measurement: 1000 futures plus 2300 spot tickers."""

import json
from datetime import UTC, datetime
from threading import current_thread, main_thread
from time import perf_counter_ns
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.exchanges.gate import GateAdapter
from app.models.market import MarketSnapshot
from app.services.gate_bbo_runtime import GateBboRuntime


@pytest.mark.benchmark
async def test_merge_main_thread_three_rounds():
    assert current_thread() is main_thread()
    now = datetime.now(UTC)
    rt = GateBboRuntime(Settings(gate_bbo_enabled=True))
    symbols = {"future": [f"F{i}_USDT" for i in range(1000)],
               "spot": [f"S{i}_USDT" for i in range(2300)]}
    rt.set_subscriptions({kind: names[:300] for kind, names in symbols.items()})
    rt._process = SimpleNamespace(is_alive=lambda: True, pid=123)
    rt._quotes = {(kind, raw): (100, 2, 101, 3, int(now.timestamp() * 1000), 1, 1)
                  for kind, names in rt._subscriptions.items() for raw in names}
    adapter = GateAdapter(client=Mock(), bbo_runtime=rt)
    payloads = {kind: [{"contract": raw, "currency_pair": raw,
                       "highest_bid": "90", "lowest_ask": "91",
                       "quote_volume": "5000000", "volume_24h_quote": "5000000",
                       "funding_rate": ".0001", "mark_price": "90.5"} for raw in names]
                for kind, names in symbols.items()}
    contracts = [{"name": raw, "quanto_multiplier": ".01"} for raw in symbols["future"]]

    async def get_json(url):
        if url.endswith("contracts"):
            return contracts
        return payloads["future" if "futures" in url else "spot"]

    adapter.get_json = get_json
    integrated = hasattr(adapter, "_bbo_fields")
    # Before R4 this is the actual post-construction production merge; afterwards
    # time the field selection used directly by the constructor. No Pydantic
    # construction, network or synthetic data generation is in the merge interval.
    inputs = [MarketSnapshot(symbol=raw.replace("_", ""), base=raw.split("_")[0],
                             exchange="gate", raw_symbol=raw, market_type=kind,
                             bid=90, ask=91, timestamp=now, contract_size_multiplier=.01)
              for kind, names in symbols.items() for raw in names]
    fields = [(row.market_type, row.raw_symbol,
               .01 if row.market_type == "future" else 1) for row in inputs]
    measurements = []
    for run in range(1, 4):
        from time import monotonic
        rt._connections = {kind: {"connected": True, "last_message_at": monotonic(), "generation": 1}
                           for kind in symbols}
        targets = [row.model_dump() for row in inputs] if integrated else []
        start = perf_counter_ns()
        if integrated:
            for target, args in zip(targets, fields, strict=True):
                target.update(adapter._bbo_fields(*args))
            merged = targets
        else:
            merged = [adapter._merge_bbo(row) for row in inputs]
        merge_ms = (perf_counter_ns() - start) / 1e6
        hits = sum((row["quote_time_source"] if integrated else row.quote_time_source) == "ws"
                   for row in merged)
        assert len(merged) == 3300 and hits == 600
        start = perf_counter_ns()
        futures = await adapter.fetch_future_tickers()
        spot = await adapter.fetch_spot_tickers()
        collection_ms = (perf_counter_ns() - start) / 1e6
        assert len(futures) == 1000 and len(spot) == 2300
        assert sum(row.quote_time_source == "ws" for row in futures + spot) == 600
        measurements.append({"run": run, "merge_ms": merge_ms, "collection_ms": collection_ms})
    print("gate_bbo_R4=" + json.dumps({"integrated": integrated, "futures": 1000,
          "spot": 2300, "ws_hits": 600, "fallbacks": 2700, "measurements": measurements}))
