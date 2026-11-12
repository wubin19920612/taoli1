export type Direction = "long1_short2" | "long2_short1";
export type Point = { bucket_at: string; spread_pct: number };
export type Thresholds = { open: number; close: number };
export type TemplateLines = { open: number; close: number };
export interface Backtest {
  trades: number; winRate: number | null; totalNetPct: number;
  averageHours: number; longestHours: number; holding: boolean; floatingNetPct: number | null;
}
export interface Template extends Thresholds {
  id: string; label: string; fundingAdjustment: number; inSample: Backtest;
  outOfSample: Backtest; validationThresholds: Thresholds; validationDirection: Direction;
}

export function quantile(values: number[], q: number): number {
  const sorted = values.slice().sort((a, b) => a - b);
  if (!sorted.length) return NaN;
  const index = (sorted.length - 1) * q;
  const lower = Math.floor(index);
  return sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower);
}

function clean(points: Point[]): Point[] {
  return [...new Map(points.filter((p) => Number.isFinite(p.spread_pct) && Number.isFinite(Date.parse(p.bucket_at)))
    .map((p) => [Date.parse(p.bucket_at), p])).values()].sort((a, b) => Date.parse(a.bucket_at) - Date.parse(b.bucket_at));
}

export function backtest(points: Point[], thresholds: Thresholds, h: number, fee: number): Backtest {
  let entry: { spread: number; time: number } | null = null;
  let trades = 0, wins = 0, totalNetPct = 0, hours = 0, longestHours = 0;
  for (const point of points) {
    const time = Date.parse(point.bucket_at);
    if (!entry) {
      if (point.spread_pct - h >= thresholds.open) entry = { spread: point.spread_pct - h, time };
    } else if (point.spread_pct + h <= thresholds.close) {
      const net = entry.spread - point.spread_pct - h - fee;
      const duration = (time - entry.time) / 3_600_000;
      trades++; wins += Number(net > 0); totalNetPct += net;
      hours += duration; longestHours = Math.max(longestHours, duration); entry = null;
    }
  }
  return { trades, winRate: trades ? wins / trades * 100 : null, totalNetPct,
    averageHours: trades ? hours / trades : 0, longestHours, holding: entry !== null,
    floatingNetPct: entry && points.length ? entry.spread - points[points.length - 1].spread_pct - h - fee : null };
}

function widen(thresholds: Thresholds, gap: number): Thresholds {
  if (thresholds.open - thresholds.close >= gap) return thresholds;
  const center = (thresholds.open + thresholds.close) / 2;
  return { open: center + gap / 2, close: center - gap / 2 };
}

function fit(points: Point[], qOpen: number, qClose: number, h: number, fee: number, profit: number, hourly: number) {
  const values = points.map((p) => p.spread_pct);
  const base = widen({ open: quantile(values, qOpen) - h, close: quantile(values, qClose) + h }, fee + profit);
  // One explicit pass: estimate holding time before the funding adjustment; no circular fitting.
  const averageHours = backtest(points, base, h, fee).averageHours;
  const fundingAdjustment = Math.max(-hourly, 0) * averageHours;
  return { ...widen(base, fee + profit + fundingAdjustment), fundingAdjustment };
}

export function chartLines(thresholds: Thresholds, h: number, direction: Direction): TemplateLines {
  const sign = direction === "long1_short2" ? 1 : -1;
  return { open: sign * (thresholds.open + h), close: sign * (thresholds.close - h) };
}

export function recommend(points: Point[], current: { open_spread_pct: number | null; close_spread_pct: number | null } | null,
  fee = 0.16, minProfit = 0.15, netHourly: number | null = null) {
  const samples = clean(points);
  const insufficient = samples.length < 120 || Date.parse(samples[samples.length - 1]?.bucket_at) - Date.parse(samples[0]?.bucket_at) < 6 * 3_600_000;
  const direction: Direction = quantile(samples.map((p) => p.spread_pct), 0.5) < 0 ? "long2_short1" : "long1_short2";
  const sign = direction === "long1_short2" ? 1 : -1;
  const finite = (n: number | null | undefined): n is number => typeof n === "number" && Number.isFinite(n);
  const hasBook = finite(current?.open_spread_pct) && finite(current?.close_spread_pct);
  const h = hasBook ? Math.max((current!.close_spread_pct! - current!.open_spread_pct!) / 2, 0) : 0.05;
  const openExec = hasBook ? sign === 1 ? current!.open_spread_pct : -current!.close_spread_pct! : null;
  const closeExec = hasBook ? sign === 1 ? current!.close_spread_pct : -current!.open_spread_pct! : null;
  const directed = samples.map((p) => ({ ...p, spread_pct: sign * p.spread_pct }));
  const split = Math.floor(samples.length * 2 / 3);
  const training = samples.slice(0, split);
  // Holdout fitting includes its own training-only direction, avoiding future direction leakage.
  const validationDirection: Direction = quantile(training.map((p) => p.spread_pct), 0.5) < 0 ? "long2_short1" : "long1_short2";
  const validationSign = validationDirection === "long1_short2" ? 1 : -1;
  const train = training.map((p) => ({ ...p, spread_pct: validationSign * p.spread_pct }));
  const test = samples.slice(split).map((p) => ({ ...p, spread_pct: validationSign * p.spread_pct }));
  const templates: Template[] = insufficient ? [] : [
    { id: "steady", label: "稳健", qo: 0.85, qc: 0.4 },
    { id: "balanced", label: "均衡（推荐）", qo: 0.75, qc: 0.4 },
    { id: "active", label: "积极", qo: 0.65, qc: 0.45 }
  ].map(({ id, label, qo, qc }) => {
    const fitted = fit(directed, qo, qc, h, fee, minProfit, (netHourly ?? 0) * sign);
    const validationThresholds = fit(train, qo, qc, h, fee, minProfit, (netHourly ?? 0) * validationSign);
    return { id, label, ...fitted, inSample: backtest(directed, fitted, h, fee),
      validationThresholds, validationDirection, outOfSample: backtest(test, validationThresholds, h, fee) };
  });
  return { direction, h, openExec, closeExec, templates, insufficient, directed, fee, minProfit,
    netHourly: netHourly === null ? null : netHourly * sign, validationStart: samples[split]?.bucket_at };
}
