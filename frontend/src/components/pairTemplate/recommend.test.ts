import { describe, expect, it } from "vitest";
import { backtest, chartLines, quantile, recommend, type Point } from "./recommend";

const points = (values: number[], minutes = 1): Point[] => values.map((spread_pct, i) => ({
  bucket_at: new Date(Date.UTC(2026, 10, 12) + i * minutes * 60000).toISOString(), spread_pct
}));
const history = points(Array.from({ length: 721 }, (_, i) => 0.7 + 0.6 * Math.sin(i / 10)));
const book = { open_spread_pct: 0.8, close_spread_pct: 1 };

describe("pair template recommendations", () => {
  it("interpolates quantiles without mutating input", () => {
    const values = [3, 0, 2, 1];
    expect(quantile(values, 0.75)).toBe(2.25);
    expect(values).toEqual([3, 0, 2, 1]);
  });
  it("uses the specified quantiles and converts mid to executable thresholds", () => {
    const result = recommend(history, book, 0, 0);
    expect(result.h).toBeCloseTo(0.1);
    expect(result.templates[0].open).toBeCloseTo(quantile(history.map((p) => p.spread_pct), 0.85) - 0.1);
    expect(result.templates[1].close).toBeCloseTo(quantile(history.map((p) => p.spread_pct), 0.4) + 0.1);
    expect(result.templates[2].open).toBeCloseTo(quantile(history.map((p) => p.spread_pct), 0.65) - 0.1);
  });
  it("reverses quotes and funding when the historical median is negative", () => {
    const result = recommend(history.map((p) => ({ ...p, spread_pct: -p.spread_pct })), book, 0.16, 0.15, 0.01);
    expect(result.direction).toBe("long2_short1");
    expect(result.openExec).toBe(-1);
    expect(result.closeExec).toBe(-0.8);
    expect(result.netHourly).toBe(-0.01);
    expect(chartLines({ open: 1, close: 0.4 }, 0.1, result.direction)).toEqual({ open: -1.1, close: -0.30000000000000004 });
  });
  it("widens symmetrically and uses fallback book cost", () => {
    const result = recommend(points(Array(361).fill(1)), null);
    expect(result.h).toBe(0.05);
    expect(result.templates[1].open - result.templates[1].close).toBeCloseTo(0.31);
    expect(result.templates[1].open + result.templates[1].close).toBeCloseTo(2);
  });
  it("adds adverse funding based on initial closed-trade holding time", () => {
    const base = recommend(history, book, 0.16, 0.15, 0);
    const adverse = recommend(history, book, 0.16, 0.15, -2);
    expect(adverse.templates[1].fundingAdjustment).toBeCloseTo(2 * base.templates[1].inSample.averageHours);
    expect(adverse.templates[1].open - adverse.templates[1].close).toBeCloseTo(0.31 + adverse.templates[1].fundingAdjustment);
    expect(recommend(history, book, 0.16, 0.15, 2).templates[1].fundingAdjustment).toBe(0);
  });
  it("simulates entries, exits, realized gains and an unclosed tail separately", () => {
    const stats = backtest(points([0.5, 1.2, 0.3, 1.3, 1.1], 60), { open: 1, close: 0.4 }, 0.1, 0.16);
    expect(stats.trades).toBe(1);
    expect(stats.winRate).toBe(100);
    expect(stats.totalNetPct).toBeCloseTo(0.54);
    expect(stats.averageHours).toBe(1);
    expect(stats.longestHours).toBe(1);
    expect(stats.holding).toBe(true);
    expect(stats.floatingNetPct).toBeCloseTo(-0.16);
  });
  it("fits holdout thresholds and direction solely on the first two thirds", () => {
    const original = recommend(history, book);
    const changed = recommend(history.map((p, i) => i < 480 ? p : { ...p, spread_pct: -10 }), book);
    expect(original.templates[1].validationThresholds).toEqual(changed.templates[1].validationThresholds);
    expect(original.templates[1].validationDirection).toEqual(changed.templates[1].validationDirection);
    const training = history.slice(0, 480);
    const expected = backtest(history.slice(480), original.templates[1].validationThresholds, original.h, 0.16);
    expect(original.templates[1].outOfSample).toEqual(expected);
    expect(training).toHaveLength(480);
  });
  it("requires 120 distinct valid points and six hours of coverage", () => {
    expect(recommend(history.slice(0, 119), null).templates).toEqual([]);
    expect(recommend(history.slice(0, 360), null).insufficient).toBe(true);
    expect(recommend(history.slice(0, 361), null).insufficient).toBe(false);
    expect(recommend([...Array(150).fill(history[0]), history[720]], null).insufficient).toBe(true);
    expect(recommend([], null).insufficient).toBe(true);
  });
});
