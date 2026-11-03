import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import type { UTCTimestamp } from "lightweight-charts";
import type { PairSpreadFundingPoint, PairSpreadLegQuery, PairSpreadPoint, PairSpreadQueryResult } from "../../api/types";
import { pairUsesRatio, ratioChartPoints } from "../../utils/pairRatio";

dayjs.extend(utc);
export type ValuePoint = { time: UTCTimestamp; value: number };
export const isFiniteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
export const MAX_CHART_HOURS = 168;

export function priceFormatFor(value: number | undefined, indexed = false) {
  const magnitude = Math.abs(value ?? 0);
  const precision = indexed || magnitude >= 1000 ? 2 : magnitude >= 1 ? 4 : 6;
  return { type: "price" as const, precision, minMove: 10 ** -precision };
}

// Naive API dates are UTC too. Last input wins within the same UTC second.
export function utcSeconds(value: string): UTCTimestamp | null {
  const ms = dayjs.utc(value).valueOf();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) as UTCTimestamp : null;
}

export function sortedTimed<T>(rows: readonly T[], date: (row: T) => string): (T & { time: UTCTimestamp })[] {
  const byTime = new Map<number, T & { time: UTCTimestamp }>();
  for (const row of rows) {
    const time = utcSeconds(date(row));
    if (time !== null) byTime.set(time, { ...row, time });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

export function indexPrices(points: ValuePoint[]): ValuePoint[] {
  const base = points.find((point) => isFiniteNumber(point.value) && point.value > 0)?.value;
  if (base === undefined) return [];
  return points.filter((point) => isFiniteNumber(point.value) && point.value > 0)
    .map((point) => ({ ...point, value: point.value / base * 100 }));
}

function matchesLeg(row: PairSpreadFundingPoint, leg: PairSpreadLegQuery): boolean {
  if (leg.market_type !== "future" || row.exchange.toLowerCase() !== leg.exchange.toLowerCase()) return false;
  if (leg.exchange.toLowerCase() !== "hyperliquid") return row.symbol.toUpperCase() === leg.symbol.toUpperCase();
  const identity = (symbol: string, dex?: string | null) => {
    const parts = symbol.split(":");
    const prefix = parts.length > 1 ? parts[0].toLowerCase() : undefined;
    const market = dex?.toLowerCase() || prefix || "main";
    if (prefix && prefix !== market) return null;
    return `${market}:${parts[parts.length - 1].toUpperCase()}`;
  };
  const key = identity(row.symbol, row.dex);
  return key !== null && key === identity(leg.symbol, leg.dex);
}

export function historyRates(history: PairSpreadFundingPoint[], leg: PairSpreadLegQuery): ValuePoint[] {
  const byMinute = new Map<UTCTimestamp, ValuePoint>();
  // Match the exact exchange/market first, then round independently per leg.
  // Keep the latest record in each minute so duplicates cannot create extra bars.
  for (const row of sortedTimed(history.filter((row) => matchesLeg(row, leg) && isFiniteNumber(row.funding_rate_pct)), (row) => row.funding_time)) {
    const time = Math.round(row.time / 60) * 60 as UTCTimestamp;
    byMinute.set(time, { time, value: row.funding_rate_pct });
  }
  return [...byMinute.values()];
}

export function netSettlements(history: PairSpreadFundingPoint[], left: PairSpreadLegQuery, right: PairSpreadLegQuery,
  direction: "long1_short2" | "short1_long2" = "long1_short2") {
  const leftRates = new Map(historyRates(history, left).map((row) => [row.time, row.value]));
  const rightRates = new Map(historyRates(history, right).map((row) => [row.time, row.value]));
  return [...new Set([...leftRates.keys(), ...rightRates.keys()])].sort((a, b) => a - b).map((time) => {
    const value = ((rightRates.get(time) ?? 0) - (leftRates.get(time) ?? 0)) * (direction === "long1_short2" ? 1 : -1);
    return { time, value, color: value >= 0 ? "#639922" : "#E24B4A" };
  });
}

export function chartData(result: PairSpreadQueryResult | null, points: PairSpreadPoint[]) {
  const ratioMode = pairUsesRatio(result);
  const prices = sortedTimed(points, (row) => row.bucket_at);
  const spread = sortedTimed(ratioChartPoints(points, ratioMode), (row) => row.bucket_at)
    .filter((row) => isFiniteNumber(row.spread_pct)).map((row) => ({ time: row.time, value: row.spread_pct }));
  const priceLine = (field: "leg1_close" | "leg2_close") => {
    const line = prices.filter((row) => isFiniteNumber(row[field])).map((row) => ({ time: row.time, value: row[field] }));
    return ratioMode ? indexPrices(line) : line;
  };
  const realtime = sortedTimed(result?.realtime_funding ?? [], (row) => row.bucket_at);
  const rateLine = (field: "left_rate_pct" | "right_rate_pct", leg?: PairSpreadLegQuery) => {
    const line = realtime.filter((row) => isFiniteNumber(row[field])).map((row) => ({ time: row.time, value: row[field]! }));
    if (line.length >= 2) return line;
    const history = leg ? historyRates(result?.funding_history ?? [], leg) : [];
    const latest = line[0];
    return latest ? [...history.filter((row) => row.time < latest.time), latest] : history;
  };
  return {
    ratioMode, prices, spread, left: priceLine("leg1_close"), right: priceLine("leg2_close"),
    leftFunding: rateLine("left_rate_pct", result?.leg1), rightFunding: rateLine("right_rate_pct", result?.leg2),
    settlements: result ? netSettlements(result.funding_history, result.leg1, result.leg2) : []
  };
}

// Existing spread = 200 * (right - left) / (right + left); ratio = left / right.
export function thresholdMetric(percent: number, ratioMode: boolean): number | null {
  if (!isFiniteNumber(percent)) return null;
  if (!ratioMode) return percent;
  return percent > -200 && percent < 200 ? (200 - percent) / (200 + percent) : null;
}

export function latestAt<T extends { time: number }>(rows: readonly T[], time: number): T | undefined {
  let low = 0, high = rows.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (rows[mid].time <= time) low = mid + 1;
    else high = mid;
  }
  return rows[low - 1];
}
