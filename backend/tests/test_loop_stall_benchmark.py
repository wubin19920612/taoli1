"""Run explicitly: pytest tests/test_loop_stall_benchmark.py -m benchmark -s -q."""
import asyncio
import gc
import json
from statistics import median
from time import perf_counter

import pytest
import loop_stall_reference as legacy
from loop_stall_fixture import Clock, prepared, workload

from app.models.settings import RiskSettings
from app.services import market_validation_review as review, pair_validation
from app.services import compact_markets, multi_exchange_premium as premium
from app.services.market_validation_review import MarketValidationService


class MeasuredBudget:
    def __init__(self):
        self.last = perf_counter()
        self.maximum = 0
        self.yields = 0

    def finish(self):
        self.maximum = max(self.maximum, (perf_counter() - self.last) * 1000)

    async def maybe_yield(self):
        if perf_counter() - self.last >= 0.020:
            self.finish()
            self.yields += 1
            await asyncio.sleep(0)
            self.last = perf_counter()


@pytest.mark.benchmark
async def test_three_locked_venv_runs(monkeypatch):
    for module in (legacy, review, pair_validation):
        monkeypatch.setattr(module, "datetime", Clock)
    data = workload()
    risk = RiskSettings()
    measurements = []
    for run in range(3):
        result = {"run": run + 1, "markets": len(data[0]), "opportunities": len(data[2])}
        for label, cls in (("old", legacy.LegacyValidation), ("new", MarketValidationService)):
            prepare_budgets = []
            def prepare_budget():
                # Align GC phase outside the timed region on BOTH paths. GC stays
                # enabled with unchanged thresholds throughout every measurement.
                gc.collect()
                budget = MeasuredBudget()
                prepare_budgets.append(budget)
                return budget
            monkeypatch.setattr(legacy if label == "old" else review, "YieldBudget", prepare_budget)
            validator, rows, opportunities = await prepared(cls, data)
            prepare_budgets[0].finish()
            result[label + "_prepare_max_segment_ms"] = prepare_budgets[0].maximum
            validations = []
            gc.collect()
            budget = MeasuredBudget()
            start = perf_counter()
            for i in range(len(opportunities)):
                await budget.maybe_yield()
                kwargs = {"annotated": True} if label == "new" else {}
                validations.append(validator.validate(*rows[3*i:3*i+2], risk, **kwargs))
            budget.finish()
            result[label + "_build_ms"] = (perf_counter() - start) * 1000
            result[label + "_build_max_segment_ms"] = budget.maximum
            del validations
            # Record BOTH first use after publication and repeated alert ticks on
            # the same snapshot. Every item requires full validation each time.
            for stage in ("alert_cold", "alert"):
                gc.collect()
                budget = MeasuredBudget()
                start = perf_counter()
                if label == "new":
                    filtered = await validator.filter_opportunities_async(opportunities, risk, budget)
                else:
                    filtered = validator.filter_opportunities(opportunities, risk)
                budget.finish()
                result[label + "_" + stage + "_ms"] = (perf_counter() - start) * 1000
                result[label + "_" + stage + "_max_segment_ms"] = budget.maximum
                result[label + "_" + stage + "_yields"] = budget.yields
                result[label + "_accepted"] = len(filtered)
                del filtered
            for stage in ("pack", "scan"):
                gc.collect()
                budget = MeasuredBudget()
                if stage == "pack":
                    if label == "old":
                        legacy.pack(rows)
                    else:
                        await compact_markets.pack_async(rows, budget)
                elif label == "old":
                    legacy._scan_eligibility(rows, risk)
                else:
                    await premium._scan_eligibility_async(rows, risk, budget)
                budget.finish()
                result[label + "_" + stage + "_max_segment_ms"] = budget.maximum
            await validator.aclose()
        assert result["old_accepted"] == result["new_accepted"]
        measurements.append(result)
        print("LOOP_STALL_BENCH " + json.dumps(result, sort_keys=True))
    summary = {
        stage + "_reduction_pct": 100 * (1 - median(r["new_" + stage + "_ms"] for r in measurements)
                                          / median(r["old_" + stage + "_ms"] for r in measurements))
        for stage in ("build", "alert_cold", "alert")
    }
    summary["new_max_segment_ms"] = max(r["new_" + stage + "_max_segment_ms"]
                                        for r in measurements
                                        for stage in ("prepare", "build", "alert_cold", "alert", "pack", "scan"))
    print("LOOP_STALL_BENCH_SUMMARY " + json.dumps(summary, sort_keys=True))
    assert summary["build_reduction_pct"] >= 40
    assert summary["alert_reduction_pct"] >= 40
    assert summary["alert_cold_reduction_pct"] >= 40
    assert summary["new_max_segment_ms"] <= 200
