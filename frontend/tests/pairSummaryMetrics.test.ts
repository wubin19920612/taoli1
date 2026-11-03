import { describe, expect, it } from "vitest";
import {
  countdown, fundingMetrics, nextSettlementTime, roundTripMetrics, spreadPosition,
  type FundingLeg
} from "../src/components/pairSummary/metrics";

function leg(overrides: Partial<FundingLeg> = {}): FundingLeg {
  return {
    market_type: "future", funding_rate_pct: 0.01, funding_interval_hours: 8,
    funding_next_rate_pct: null, funding_next_time: null, ...overrides
  };
}

function points(values: number[], start = "2026-11-03T00:00:00Z") {
  return values.map((spread_pct, index) => ({
    spread_pct, bucket_at: new Date(Date.parse(start) + index * 3_600_000).toISOString()
  }));
}

describe("pair summary funding", () => {
  it("subtracts the long left cost from the short right receipt and annualizes simply", () => {
    const value = fundingMetrics(leg(), leg({ funding_rate_pct: 0.03 }));
    expect(value.hourly).toBeCloseTo(0.0025);
    expect(value.eightHourly).toBeCloseTo(0.02);
    expect(value.daily).toBeCloseTo(0.06);
    expect(value.annual).toBeCloseTo(21.9);
    expect(fundingMetrics(leg({ funding_rate_pct: 0.03 }), leg()).eightHourly).toBeCloseTo(-0.02);
  });

  it("normalizes different intervals and defaults only missing intervals to 8h", () => {
    expect(fundingMetrics(leg(), leg({ funding_rate_pct: 0.03, funding_interval_hours: 4 })).eightHourly).toBeCloseTo(0.05);
    expect(fundingMetrics(leg({ funding_interval_hours: null }), leg()).eightHourly).toBe(0);
  });

  it("treats spot legs as zero even if the funding fields are absent or populated", () => {
    const spot = leg({ market_type: "spot", funding_rate_pct: null });
    expect(fundingMetrics(spot, leg()).eightHourly).toBeCloseTo(0.01);
    expect(fundingMetrics(leg(), spot).eightHourly).toBeCloseTo(-0.01);
    expect(fundingMetrics(spot, leg({ market_type: "spot" })).eightHourly).toBe(0);
    expect(fundingMetrics(spot, spot).nextEightHourly).toBeNull();
  });

  it.each([null, NaN, Infinity])("keeps missing or invalid perpetual rate %s unknown", (funding_rate_pct) => {
    expect(fundingMetrics(leg({ funding_rate_pct }), leg()).eightHourly).toBeNull();
    expect(fundingMetrics(null, leg()).annual).toBeNull();
  });

  it.each([0, -1, NaN, Infinity])("rejects invalid interval %s", (funding_interval_hours) => {
    expect(fundingMetrics(leg({ funding_interval_hours }), leg()).eightHourly).toBeNull();
  });

  it("uses next rates only when both funding-bearing legs have them", () => {
    expect(fundingMetrics(leg(), leg()).nextEightHourly).toBeNull();
    const right = leg({ funding_next_rate_pct: 0.03, funding_interval_hours: 4 });
    expect(fundingMetrics(leg({ funding_next_rate_pct: 0.01 }), right).nextEightHourly).toBeCloseTo(0.05);
    expect(fundingMetrics(leg({ market_type: "spot" }), right).nextEightHourly).toBeCloseTo(0.06);
  });

  it("selects the earlier valid settlement and ignores spot or invalid times", () => {
    const early = "2026-11-03T02:41:00Z";
    const late = "2026-11-03T08:00:00Z";
    expect(nextSettlementTime(leg({ funding_next_time: late }), leg({ funding_next_time: early }))).toBe(Date.parse(early));
    expect(nextSettlementTime(leg({ funding_next_time: early }), leg({ funding_next_time: late }))).toBe(Date.parse(early));
    expect(nextSettlementTime(leg({ funding_next_time: "invalid" }), leg({ funding_next_time: late }))).toBe(Date.parse(late));
    expect(nextSettlementTime(leg({ market_type: "spot", funding_next_time: early }), null)).toBeNull();
    expect(nextSettlementTime(leg(), leg())).toBeNull();
    expect(countdown(Date.parse(early), Date.parse("2026-11-03T00:00:00Z"))).toBe("2h 41m");
    expect(countdown(Date.parse(early), Date.parse(late))).toBe("0h 0m");
    expect(countdown(null, Date.parse(early))).toBe("-");
  });
});

describe("pair summary round trip", () => {
  it("subtracts close from open and reports ratio-relative cost", () => {
    expect(roundTripMetrics(0.8, 1.2).difference).toBeCloseTo(0.4);
    expect(roundTripMetrics(40.2, 40).relativePct).toBeCloseTo(0.5);
    expect(roundTripMetrics(null, 1).difference).toBeNull();
    expect(roundTripMetrics(1, undefined).difference).toBeNull();
    expect(roundTripMetrics(1, NaN).relativePct).toBeNull();
    expect(roundTripMetrics(1, 0).relativePct).toBeNull();
  });
});

describe("pair summary position", () => {
  it("uses the proportion <= current, including ties and the min/max boundaries", () => {
    expect(spreadPosition(points([1, 2, 3]), 1).percentile).toBe(33);
    expect(spreadPosition(points([1, 2, 3]), 3).percentile).toBe(100);
    expect(spreadPosition(points([1, 2, 3]), 0).percentile).toBe(0);
    expect(spreadPosition(points([1, 2, 3]), 4).percentile).toBe(100);
    const equal = spreadPosition(points([2, 2, 2]));
    expect(equal.percentile).toBe(100);
    expect(equal.currentPosition).toBe(50);
    expect(equal.meanPosition).toBe(50);
    expect(equal.sigma).toBe(0);
  });

  it("prefers current, computes mean deviation, range markers and population sigma", () => {
    const value = spreadPosition(points([1, 2, 3]), 2.5);
    expect(value.current).toBe(2.5);
    expect(value.min).toBe(1);
    expect(value.mean).toBe(2);
    expect(value.max).toBe(3);
    expect(value.deviation).toBe(0.5);
    expect(value.relativeDeviationPct).toBe(25);
    expect(value.currentPosition).toBe(75);
    expect(value.meanPosition).toBe(50);
    expect(value.sigma).toBeCloseTo(Math.sqrt(2 / 3));
    expect(spreadPosition(points([-1, 0, 1])).relativeDeviationPct).toBeNull();
    expect(spreadPosition(points([1, 2, 3]), 4).currentPosition).toBe(100);
  });

  it("finds the nearest sample at or before one hour before the current timestamp", () => {
    const samples = points([1, 2, 3]);
    expect(spreadPosition(samples, 4, "2026-11-03T02:10:00Z").change1h).toBe(2);
    expect(spreadPosition([...samples].reverse()).current).toBe(3);
    expect(spreadPosition(samples).change1h).toBe(1);
    expect(spreadPosition(samples, null, "2026-11-03T06:00:00Z").change1h).toBe(1);
    const short = samples.map((point, index) => ({ ...point, bucket_at: `2026-11-03T00:0${index}:00Z` }));
    expect(spreadPosition(short).change1h).toBeNull();
  });

  it("handles empty/singleton and nonfinite samples without inventing statistics", () => {
    const empty = spreadPosition([]);
    expect(empty.percentile).toBeNull();
    expect(empty.mean).toBeNull();
    expect(empty.deviation).toBeNull();
    expect(empty.sigma).toBeNull();
    expect(empty.change1h).toBeNull();
    const single = spreadPosition(points([2]));
    expect(single.percentile).toBe(100);
    expect(single.sigma).toBeNull();
    expect(single.change1h).toBeNull();
    const filtered = spreadPosition(points([1, NaN, 3, Infinity]), NaN);
    expect(filtered.current).toBe(3);
    expect(filtered.mean).toBe(2);
    expect(filtered.sigma).toBe(1);
  });
});
