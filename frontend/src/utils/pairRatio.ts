import type { PairSpreadPoint, PairSpreadQueryResult } from "../api/types";

// API prices already include market normalization and the right-leg divisor.
export function priceRatio(left: number | null | undefined, right: number | null | undefined): number | null {
  if (typeof left !== "number" || typeof right !== "number" || !Number.isFinite(left) ||
      !Number.isFinite(right) || left <= 0 || right <= 0) return null;
  const value = left / right;
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function pairUsesRatio(result: PairSpreadQueryResult | null): boolean {
  const current = result?.current;
  const last = result?.points[result.points.length - 1];
  const ratio = priceRatio(current?.leg1.mid_price, current?.leg2.mid_price)
    ?? priceRatio(current?.leg1.price, current?.leg2.price)
    ?? priceRatio(last?.leg1_close, last?.leg2_close);
  return ratio !== null && (ratio >= 2 || ratio <= 0.5);
}

export function formatPriceRatio(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "-";
  return value >= 10000 || value < 0.0001 ? value.toPrecision(5) : Number(value.toPrecision(6)).toString();
}

// Copies for chart geometry only; never change API data or diagnostic percentages.
export function ratioChartPoints(points: PairSpreadPoint[], ratioMode: boolean): PairSpreadPoint[] {
  if (!ratioMode) return points;
  return points.flatMap((point) => {
    const ratio = priceRatio(point.leg1_close, point.leg2_close);
    return ratio === null ? [] : [{ ...point, spread_pct: ratio }];
  });
}
