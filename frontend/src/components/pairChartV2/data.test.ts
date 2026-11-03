import { describe, expect, it } from "vitest";
import type { UTCTimestamp } from "lightweight-charts";
import { chartData, historyRates, indexPrices, latestAt, netSettlements, sortedTimed, thresholdMetric, utcSeconds } from "./data";
import { chartFixture } from "./chartTestUtils";

describe("v2 chart data", () => {
  it("converts UTC including naive dates, rejects invalid dates, sorts and deduplicates seconds", () => {
    const rows = [{ at: "2026-11-03T09:00:02+09:00", v: 2 }, { at: "2026-11-03T00:00:00", v: 0 },
      { at: "bad", v: 3 }, { at: "2026-11-03T00:00:02.900Z", v: 4 }];
    const points = sortedTimed(rows, (row) => row.at);
    expect(points.map((row) => row.v)).toEqual([0, 4]);
    expect(points[1].time - points[0].time).toBe(2);
    expect(utcSeconds(rows[0].at)).toBe(Date.parse("2026-11-03T00:00:02Z") / 1000);
    expect(utcSeconds("bad")).toBeNull();
  });

  it("nets both legs and single-leg settlements with the requested direction; duplicates do not double count", () => {
    const r = chartFixture();
    const history = [...r.funding_history, r.funding_history[0],
      { ...r.funding_history[0], funding_time: "2026-11-03T08:00:00Z", funding_rate_pct: 0.05 },
      { ...r.funding_history[1], funding_time: "2026-11-03T16:00:00Z", funding_rate_pct: -0.02 }];
    const net = netSettlements(history, r.leg1, r.leg2);
    expect(net[0].value).toBeCloseTo(0.02);
    expect(net.slice(1).map((row) => row.value)).toEqual([-0.05, -0.02]);
    expect(net.map((row) => row.color)).toEqual(["#639922", "#E24B4A", "#E24B4A"]);
    netSettlements(history, r.leg1, r.leg2, "short1_long2").forEach((row, index) => expect(row.value).toBeCloseTo(-net[index].value));
  });

  it("keeps same-exchange symbols and Hyperliquid DEX identities distinct", () => {
    const leg = { exchange: "hyperliquid", symbol: "ANTHUSDT", dex: "xyz", market_type: "future" as const };
    const history = ["xyz", "io", undefined].map((dex, i) => ({ exchange: "hyperliquid", dex, symbol: "ANTHUSDT",
      funding_time: "2026-11-03T00:00:00Z", funding_rate_pct: i + 1 }));
    expect(netSettlements(history, leg, { ...leg, dex: "io" })[0].value).toBe(1);
    expect(historyRates(history, { ...leg, symbol: "BTCUSDT" })).toEqual([]);
    expect(historyRates(history, { ...leg, market_type: "spot" })).toEqual([]);
    expect(historyRates([{ ...history[0], dex: undefined, symbol: "xyz:ANTHUSDT" }], leg)[0].value).toBe(1);
    expect(historyRates([{ ...history[0], symbol: "io:ANTHUSDT" }], leg)).toEqual([]);
    expect(netSettlements(history, leg, leg)[0].value).toBe(0);
  });

  it("indexes each leg to its first valid positive price without mutating input", () => {
    const rows = [NaN, 0, 50, 75].map((value, i) => ({ time: i as UTCTimestamp, value }));
    expect(indexPrices(rows).map((p) => p.value)).toEqual([100, 150]);
    expect(rows[3].value).toBe(75);
    expect(indexPrices([{ time: 0 as UTCTimestamp, value: 0 }])).toEqual([]);
  });

  it("uses existing price ratio and indexes the price legs; real-time rates fall back per leg", () => {
    const r = chartFixture();
    r.points = r.points.map((p) => ({ ...p, leg1_close: p.leg1_close * 40 }));
    r.realtime_funding![0].right_rate_pct = null;
    const data = chartData(r, r.points);
    expect(data.ratioMode).toBe(true);
    expect(data.spread[0].value).toBeCloseTo(4000 / 101);
    expect(data.left[0].value).toBe(100);
    expect(data.right[0].value).toBe(100);
    expect(data.leftFunding[0].value).toBe(0.02);
    expect(data.rightFunding[0].value).toBe(0.03);
    expect(r.points[0].spread_pct).toBe(1);
    expect(chartData(null, []).spread).toEqual([]);
  });

  it("maps percentage thresholds to the existing ratio convention and never looks ahead for settlement", () => {
    expect(thresholdMetric(10, false)).toBe(10);
    expect(thresholdMetric(0, true)).toBe(1);
    expect(thresholdMetric(10, true)).toBeCloseTo(190 / 210);
    expect(thresholdMetric(-200, true)).toBeNull();
    expect(thresholdMetric(200, true)).toBeNull();
    const rows = [{ time: 10, value: 1 }, { time: 20, value: 2 }];
    expect(latestAt(rows, 9)).toBeUndefined();
    expect(latestAt(rows, 19)?.value).toBe(1);
    expect(latestAt(rows, 20)?.value).toBe(2);
  });
});
