import type { PairSpreadCurrentLeg } from "../../api/types";

export type FundingLeg = Pick<PairSpreadCurrentLeg,
  "market_type" | "funding_rate_pct" | "funding_next_rate_pct" | "funding_interval_hours" | "funding_next_time"
>;

function finite(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function hourlyRate(leg: FundingLeg | null | undefined, next: boolean): number | null {
  if (!leg) return null;
  if (leg.market_type === "spot") return 0;
  const rate = finite(next ? leg.funding_next_rate_pct : leg.funding_rate_pct);
  const interval = finite(leg.funding_interval_hours ?? 8);
  return rate !== null && interval !== null && interval > 0 ? finite(rate / interval) : null;
}

export function fundingMetrics(left: FundingLeg | null | undefined, right: FundingLeg | null | undefined) {
  const leftHourly = hourlyRate(left, false);
  const rightHourly = hourlyRate(right, false);
  const hourly = leftHourly !== null && rightHourly !== null ? finite(rightHourly - leftHourly) : null;
  const leftNext = hourlyRate(left, true);
  const rightNext = hourlyRate(right, true);
  const hasFundingLeg = left?.market_type === "future" || right?.market_type === "future";
  return {
    hourly,
    eightHourly: hourly === null ? null : finite(hourly * 8),
    daily: hourly === null ? null : finite(hourly * 24),
    annual: hourly === null ? null : finite(hourly * 24 * 365),
    nextEightHourly: !hasFundingLeg || leftNext === null || rightNext === null ? null : finite((rightNext - leftNext) * 8)
  };
}

export function nextSettlementTime(...legs: Array<FundingLeg | null | undefined>): number | null {
  const times = legs.flatMap((leg) => {
    if (!leg || leg.market_type === "spot" || !leg.funding_next_time) return [];
    const time = Date.parse(leg.funding_next_time);
    return Number.isFinite(time) ? [time] : [];
  });
  return times.length ? Math.min(...times) : null;
}

export function countdown(settlement: number | null, now: number): string {
  if (settlement === null) return "-";
  const minutes = Math.floor(Math.max(0, settlement - now) / 60_000);
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function roundTripMetrics(open: number | null | undefined, close: number | null | undefined) {
  const opening = finite(open);
  const closing = finite(close);
  const difference = opening === null || closing === null ? null : finite(Math.abs(opening - closing));
  return {
    difference,
    relativePct: difference === null || closing === null || closing === 0 ? null : finite(difference / Math.abs(closing) * 100)
  };
}

interface PositionPoint {
  bucket_at: string;
  spread_pct: number;
}

export function spreadPosition(points: PositionPoint[], currentValue?: number | null, currentAt?: string | null) {
  const samples = points.filter((point) => finite(point.spread_pct) !== null);
  const timed = samples.map((point) => ({ value: point.spread_pct, time: Date.parse(point.bucket_at) }))
    .filter((point) => Number.isFinite(point.time)).sort((a, b) => a.time - b.time);
  const latest = timed[timed.length - 1];
  const snapshotValue = finite(currentValue);
  const current = snapshotValue ?? latest?.value ?? samples[samples.length - 1]?.spread_pct ?? null;
  const values = samples.map((point) => point.spread_pct);
  // Iterate instead of spreading a potentially large query window into Math.min/max.
  const min = values.length ? values.reduce((a, b) => Math.min(a, b)) : null;
  const max = values.length ? values.reduce((a, b) => Math.max(a, b)) : null;
  const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  const marker = (value: number | null) => value === null || min === null || max === null
    ? null : max === min ? 50 : Math.max(0, Math.min(100, (value - min) / (max - min) * 100));
  const deviation = current === null || mean === null ? null : current - mean;
  const timestamp = snapshotValue !== null && currentAt ? Date.parse(currentAt) : NaN;
  const target = (Number.isFinite(timestamp) ? timestamp : latest?.time ?? NaN) - 3_600_000;
  // Require a full hour of history; use the closest sample at or before that boundary.
  const hourAgo = timed.filter((point) => point.time <= target).pop();
  return {
    current, min, mean, max,
    percentile: current === null || !values.length ? null
      : Math.round(values.filter((value) => value <= current).length / values.length * 100),
    currentPosition: marker(current),
    meanPosition: marker(mean),
    deviation,
    relativeDeviationPct: deviation === null || mean === null || mean === 0 ? null : finite(deviation / mean * 100),
    change1h: values.length < 2 || current === null || !hourAgo ? null : current - hourAgo.value,
    sigma: values.length < 2 || mean === null ? null
      : Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length)
  };
}
