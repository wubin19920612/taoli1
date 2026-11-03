import math
from datetime import timedelta

import pytest

from app.services.shadow_stats import statistics, buckets, event_day
from shadow_helpers import NOW


def events(values):
    return [dict(id=str(i), net_bps=value, closed_at=(NOW + timedelta(days=i % 5)).isoformat(),
                 symbol=f"COIN{i % 3}", variant="conv-0.1", fake_flag=False,
                 spread_bps=value + 4, slippage_bps=1, fees_bps=2, funding_bps=-1)
            for i, value in enumerate(values)]


def test_hand_calculated_wilson_t_components_drawdown_and_tail():
    result = statistics(events([-10, 0, 10, 20]))
    assert result["E"] == 5
    assert result["sigma"] == pytest.approx(math.sqrt(500 / 3))
    assert result["t_lcb"] == pytest.approx(5 - 2.353 * math.sqrt(500 / 3) / 2)
    assert result["win_rate"] == 0.5
    assert result["wilson_low"] == pytest.approx(0.1500357088)
    assert result["wilson_high"] == pytest.approx(0.8499642912)
    assert result["n_min"] == 30
    assert result["spread_bps"] - result["slippage_bps"] - result["fees_bps"] + result["funding_bps"] == result["E"]
    assert result["p5"] == -8.5 and result["cvar5"] == -10
    assert result["max_drawdown"] == 10
    assert result["status"] == "INSUFFICIENT"
    assert result["lcb"] == min(result["t_lcb"], result["bootstrap_lcb"])


def test_deterministic_whole_day_bootstrap_unequal_day_sizes():
    rows = events([1, 2, 3, 4, 5, 100])
    result = statistics(rows)
    assert statistics(list(reversed(rows))) == result
    # One day only: every bootstrap replicate contains all its events.
    for r in rows:
        r["closed_at"] = NOW.isoformat()
    assert statistics(rows)["bootstrap_lcb"] == pytest.approx(115 / 6)
    assert event_day({"closed_at": "2026-11-03T16:00:00+00:00"}) == "2026-11-04"


def test_states_coverage_fake_filter_and_dimensions():
    assert statistics(events([10] * 30))["status"] == "POSITIVE"
    result = statistics(events([-1] * 30))
    assert result["status"] == "NOT_POSITIVE" and result["n_min"] is None
    assert statistics(events([1] * 29))["status"] == "INSUFFICIENT"
    assert statistics([])["status"] == "INSUFFICIENT"
    assert statistics(events([10]))["lcb"] is None
    rows = events([10] * 30)
    for r in rows:
        r["symbol"] = "one"
    assert statistics(rows)["status"] == "INSUFFICIENT"
    rows[0]["fake_flag"] = True
    assert buckets(rows, ["variant"])[0]["n_evt"] == 29
    assert buckets(rows, [], include_fake=True)[0]["n_evt"] == 30
    with pytest.raises(ValueError):
        buckets(rows, ["not-a-dimension"])
