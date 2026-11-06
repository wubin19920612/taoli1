"""Real spawn delta protocol, including recovery without resetting the ledger."""
import pickle
from datetime import timedelta

import pytest

from test_shadow_process import runtime_for, tick, rows, fresh_pair
from shadow_helpers import NOW


@pytest.mark.asyncio
async def test_incremental_wire_omits_unchanged_catalog_directory_and_quotes(tmp_path):
    rt = runtime_for(tmp_path)
    sent = []
    encode = rt._encode
    def recording(name, data):
        sent.append((name, data))
        return encode(name, data)
    rt._encode = recording
    try:
        await rt.initialize()
        quotes, op = fresh_pair(NOW)
        await tick(rt, NOW)
        await tick(rt, NOW, quotes, [op])
        sent.clear()
        await tick(rt, NOW, quotes, [op])
        header = next(data for name, data in sent if name == "round")
        assert header[1] is False and header[3] is None and header[5] is None
        assert not any(data for name, data in sent if name in {"directory_delta", "quote_delta"})
        # Replacement objects with identical fields do not cause directory traffic.
        quotes = [q.model_copy() for q in quotes]
        sent.clear()
        await tick(rt, NOW, quotes, [op])
        assert not any(data for name, data in sent if name == "directory_delta")
        quotes[0] = quotes[0].model_copy(update={"asset_id": "changed"})
        sent.clear()
        await tick(rt, NOW, quotes, [op])
        assert sum(len(data) for name, data in sent if name == "directory_delta") == 1
        assert rt.errors == 0
    finally:
        await rt.aclose()


@pytest.mark.asyncio
async def test_delta_and_forced_full_records_identical_including_gap_and_deletion(tmp_path):
    full = runtime_for(tmp_path / "full")
    delta = runtime_for(tmp_path / "delta")
    import json
    def normalized(values):
        return sorted(json.dumps({k: v for k, v in r.items() if k != "id"}, sort_keys=True)
                      for r in values)
    try:
        await full.initialize()
        await delta.initialize()
        quotes, op = fresh_pair(NOW)
        for second in (0, 1, 2, 3, 4, 65, 700, 701):
            now = NOW + timedelta(seconds=second)
            for quote in quotes:
                quote.timestamp = now
            ops = [] if second in (0, 4, 65, 700) else [op]
            if second == 3:
                quotes[1].bid = quotes[1].ask = 100
            if second == 701:
                quotes[1].bid = quotes[1].ask = 101
            current = quotes[:1] if second == 65 else list(reversed(quotes)) if second == 700 else quotes
            if second == 2:
                # Simulate a lost acknowledgement / sequence gap. The rejected
                # round must not mutate ledger state; the next round is full.
                delta._round_sequence += 1
                await tick(delta, now, current, ops)
                assert delta._needs_resync
            full._needs_resync = True
            await tick(full, now, current, ops)
            await tick(delta, now, current, ops)
            assert normalized(await rows(delta)) == normalized(await rows(full))
        assert len(await rows(delta)) >= 4
        assert delta.status()["resync_count"] == 1
        assert delta.errors == 0
    finally:
        await full.aclose()
        await delta.aclose()
