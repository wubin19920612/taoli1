import { describe, expect, it } from "vitest";
import type { PairSpreadQueryResult } from "../src/api/types";
import { formatPriceRatio, pairUsesRatio, priceRatio, ratioChartPoints } from "../src/utils/pairRatio";

describe("pair price ratios", () => {
  it.each([[4000, 100, true], [100, 4000, true], [200, 100, true], [100, 200, true], [199, 100, false], [100, 199, false]])(
    "selects ratio display for %s / %s", (left, right, expected) => {
      const result = { current: null, points: [{ leg1_close: left, leg2_close: right }] } as PairSpreadQueryResult;
      expect(pairUsesRatio(result)).toBe(expected);
    }
  );
  it("uses already scaled prices once and gives live prices priority over history", () => {
    const result = { leg2_multiplier: 40, current: { leg1: { mid_price: 100 }, leg2: { mid_price: 100 } }, points: [{ leg1_close: 4000, leg2_close: 100 }] } as PairSpreadQueryResult;
    expect(pairUsesRatio(result)).toBe(false);
    expect(priceRatio(100, 100)).toBe(1);
  });
  it.each([0, -1, NaN, Infinity, null, undefined])("rejects missing or invalid price %s", (invalid) => {
    expect(priceRatio(invalid, 100)).toBeNull();
    expect(priceRatio(100, invalid)).toBeNull();
  });
  it("preserves small reciprocal ratios and never formats missing values as zero", () => {
    expect(formatPriceRatio(priceRatio(4000, 100))).toBe("40");
    expect(formatPriceRatio(priceRatio(100, 4000))).toBe("0.025");
    expect(Number(formatPriceRatio(1e-9))).toBe(1e-9);
    expect(formatPriceRatio(null)).toBe("-");
  });
  it("plots actual ratios instead of percentage geometry and preserves API points", () => {
    const points = [
      { bucket_at: "2026-10-23T00:00:00Z", leg1_close: 4000, leg2_close: 100, spread_abs: -3900, spread_pct: -190.24 },
      { bucket_at: "2026-10-23T00:01:00Z", leg1_close: 4000, leg2_close: 80, spread_abs: -3920, spread_pct: -192.16 }
    ];
    expect(ratioChartPoints(points, true).map(p => p.spread_pct)).toEqual([40, 50]);
    expect(points[0].spread_pct).toBe(-190.24);
    expect(ratioChartPoints(points, false)).toBe(points);
    expect(ratioChartPoints([{ ...points[0], leg2_close: 0 }], true)).toEqual([]);
  });
});
