import { ReloadOutlined, SaveOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, InputNumber, Select, Switch, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import { getCurrentPremiumIndex, queryPremiumIndex } from "../api/client";
import type {
  PremiumIndexCurrentSnapshot,
  PremiumIndexPoint,
  PremiumIndexQueryResult,
  PremiumIndexValueStats
} from "../api/types";

dayjs.extend(utc);

type PremiumIndexFormValues = {
  exchange: string;
  symbol: string;
};

type SavedPremiumIndexPreset = PremiumIndexFormValues & {
  id: string;
  hours: number;
  intervalMinutes: number;
  savedAt: string;
};

type FundingFollowEstimate = {
  intervalHours: number;
  sampledPoints: number;
  sampledFrom: string | null;
  sampledTo: string | null;
  elapsedHours: number | null;
  remainingHours: number | null;
  averagePremiumPct: number | null;
  currentPremiumPct: number | null;
  exchangeFundingPct: number | null;
  estimatedFundingPct: number | null;
  projectedAveragePremiumPct: number | null;
  projectedFundingIfCurrentPremiumPersistsPct: number | null;
  limitPct: number | null;
  lowerLimitAveragePremiumPct: number | null;
  upperLimitAveragePremiumPct: number | null;
  futureAveragePremiumToExitLimitPct: number | null;
};

type WeightedPremiumStats = {
  averagePremiumPct: number | null;
  weightedTotal: number;
  weightTotal: number;
  sampleCount: number;
};

const defaultFormValues: PremiumIndexFormValues = {
  exchange: "binance",
  symbol: "BTC"
};

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"].map(
  (value) => ({ label: value[0].toUpperCase() + value.slice(1), value })
);

const intervalOptions = [
  { label: "1分钟", value: 1 },
  { label: "5分钟", value: 5 },
  { label: "15分钟", value: 15 }
];

const PREMIUM_INDEX_PRESETS_KEY = "taoli1.premiumIndex.presets.v1";
const MAX_SAVED_PREMIUM_PRESETS = 24;

function signedPct(value: number | null | undefined, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function signedBp(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const bp = Math.round(value * 100);
  return `${bp >= 0 ? "+" : ""}${bp}bp`;
}

function compactHours(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value < 1) {
    return `${Math.round(value * 60)}分钟`;
  }
  return `${value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}小时`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const abs = Math.abs(value);
  if (abs >= 1000) {
    return value.toFixed(2);
  }
  if (abs >= 1) {
    return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(8);
}

function premiumSourceLabel(source: string | null | undefined): string {
  if (!source) {
    return "来源未知";
  }
  if (source.includes("mark_index")) {
    return "标记价-指数价";
  }
  if (source.includes("premium_index")) {
    return "交易所 premium index";
  }
  if (source.includes("funding_premium")) {
    return "资金费 premium 锚点";
  }
  if (source.includes("current_anchor")) {
    return "当前标记价锚点";
  }
  return source;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function hoursBetween(start: dayjs.Dayjs, end: dayjs.Dayjs): number {
  return Math.max(end.diff(start, "second") / 3600, 0);
}

function nearestFundingIntervalHours(hours: number): number {
  const candidates = [1, 2, 4, 8];
  return candidates.reduce((best, candidate) => (
    Math.abs(candidate - hours) < Math.abs(best - hours) ? candidate : best
  ), candidates[0]);
}

function bitgetFundingFromAveragePremiumPct(
  averagePremiumPct: number | null | undefined,
  intervalHours: number,
  limitPct: number | null = null
): number | null {
  if (typeof averagePremiumPct !== "number" || !Number.isFinite(averagePremiumPct)) {
    return null;
  }
  const interestPct = 0.01;
  const dampenerPct = 0.05;
  const intervalScale = 8 / intervalHours;
  const uncapped = (
    averagePremiumPct + clamp(interestPct - averagePremiumPct, -dampenerPct, dampenerPct)
  ) / intervalScale;
  return typeof limitPct === "number" && Number.isFinite(limitPct) && limitPct > 0
    ? clamp(uncapped, -limitPct, limitPct)
    : uncapped;
}

function sumConsecutiveWeights(start: number, count: number): number {
  if (count <= 0) {
    return 0;
  }
  return (count * (2 * start + count - 1)) / 2;
}

function bitgetWeightedPremiumStats(points: PremiumIndexPoint[]): WeightedPremiumStats {
  const sorted = [...points]
    .filter((point) => Number.isFinite(point.premium_pct))
    .sort((a, b) => dayjs.utc(a.bucket_at).valueOf() - dayjs.utc(b.bucket_at).valueOf());
  if (!sorted.length) {
    return { averagePremiumPct: null, weightedTotal: 0, weightTotal: 0, sampleCount: 0 };
  }
  let weightedTotal = 0;
  let weightTotal = 0;
  for (let index = 0; index < sorted.length; index += 1) {
    const weight = index + 1;
    weightedTotal += sorted[index].premium_pct * weight;
    weightTotal += weight;
  }
  return {
    averagePremiumPct: weightTotal > 0 ? weightedTotal / weightTotal : null,
    weightedTotal,
    weightTotal,
    sampleCount: sorted.length
  };
}

function likelyFundingLimitPct(fundingPct: number | null | undefined): number | null {
  if (typeof fundingPct !== "number" || !Number.isFinite(fundingPct)) {
    return null;
  }
  const abs = Math.abs(fundingPct);
  return abs >= 0.45 ? abs : null;
}

function buildBitgetFundingFollowEstimate(
  result: PremiumIndexQueryResult | null,
  current: PremiumIndexCurrentSnapshot | null,
  currentPremiumPct: number | null
): FundingFollowEstimate | null {
  if (!result || result.exchange !== "bitget" || !current) {
    return null;
  }
  const intervalHours = nearestFundingIntervalHours(result.hours);
  const observedAt = dayjs.utc(current.observed_at);
  const nextFundingAt = current.funding_next_time ? dayjs.utc(current.funding_next_time) : null;
  const fallbackWindowStart = result.first_seen_at ? dayjs.utc(result.first_seen_at) : null;
  const windowStart = nextFundingAt ? nextFundingAt.subtract(intervalHours, "hour") : fallbackWindowStart;
  const windowEnd = observedAt;
  const windowPoints = result.points.filter((point) => {
    const bucketAt = dayjs.utc(point.bucket_at);
    return (
      (!windowStart || !bucketAt.isBefore(windowStart)) &&
      !bucketAt.isAfter(windowEnd)
    );
  });
  const weightedStats = bitgetWeightedPremiumStats(windowPoints);
  const averagePremiumPct = weightedStats.averagePremiumPct;
  const elapsedHours = windowStart ? Math.min(hoursBetween(windowStart, observedAt), intervalHours) : null;
  const remainingHours = nextFundingAt ? Math.max(hoursBetween(observedAt, nextFundingAt), 0) : null;
  const limitPct = likelyFundingLimitPct(current.funding_rate_pct);
  const estimatedFundingPct = bitgetFundingFromAveragePremiumPct(averagePremiumPct, intervalHours, limitPct);
  const remainingSampleCount = remainingHours !== null
    ? Math.max(Math.round((remainingHours * 60) / Math.max(result.interval_minutes, 1)), 0)
    : 0;
  const futureWeightTotal = sumConsecutiveWeights(weightedStats.sampleCount + 1, remainingSampleCount);
  const projectedAveragePremium =
    typeof averagePremiumPct === "number" &&
    typeof currentPremiumPct === "number" &&
    weightedStats.weightTotal + futureWeightTotal > 0
      ? (weightedStats.weightedTotal + currentPremiumPct * futureWeightTotal) / (
        weightedStats.weightTotal + futureWeightTotal
      )
      : null;
  const projectedFundingIfCurrentPremiumPersistsPct = bitgetFundingFromAveragePremiumPct(
    projectedAveragePremium,
    intervalHours,
    limitPct
  );
  const dampenerPct = 0.05;
  const intervalScale = 8 / intervalHours;
  const lowerLimitAveragePremiumPct = limitPct !== null ? -limitPct * intervalScale - dampenerPct : null;
  const upperLimitAveragePremiumPct = limitPct !== null ? limitPct * intervalScale + dampenerPct : null;
  let futureAveragePremiumToExitLimitPct: number | null = null;
  if (
    limitPct !== null &&
    typeof current.funding_rate_pct === "number" &&
    weightedStats.weightTotal > 0 &&
    futureWeightTotal > 0 &&
    lowerLimitAveragePremiumPct !== null &&
    upperLimitAveragePremiumPct !== null
  ) {
    const exitTarget = current.funding_rate_pct <= -limitPct
      ? lowerLimitAveragePremiumPct
      : current.funding_rate_pct >= limitPct
        ? upperLimitAveragePremiumPct
        : null;
    futureAveragePremiumToExitLimitPct = exitTarget !== null
      ? (
        exitTarget * (weightedStats.weightTotal + futureWeightTotal) - weightedStats.weightedTotal
      ) / futureWeightTotal
      : null;
  }
  return {
    intervalHours,
    sampledPoints: windowPoints.length,
    sampledFrom: windowPoints[0]?.bucket_at ?? null,
    sampledTo: windowPoints[windowPoints.length - 1]?.bucket_at ?? null,
    elapsedHours,
    remainingHours,
    averagePremiumPct,
    currentPremiumPct,
    exchangeFundingPct: current.funding_rate_pct,
    estimatedFundingPct,
    projectedAveragePremiumPct: projectedAveragePremium,
    projectedFundingIfCurrentPremiumPersistsPct,
    limitPct,
    lowerLimitAveragePremiumPct,
    upperLimitAveragePremiumPct,
    futureAveragePremiumToExitLimitPct
  };
}

function clampHours(value: number | null): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 1;
  }
  return Math.min(720, Math.max(1, Math.round(value)));
}

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

function normalizePremiumForm(values: PremiumIndexFormValues): PremiumIndexFormValues {
  return {
    exchange: values.exchange,
    symbol: normalizeSymbol(values.symbol)
  };
}

function presetId(values: PremiumIndexFormValues): string {
  const normalized = normalizePremiumForm(values);
  return `${normalized.exchange}|${normalized.symbol}`;
}

function isSavedPreset(value: unknown): value is SavedPremiumIndexPreset {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Partial<SavedPremiumIndexPreset>;
  return (
    typeof item.id === "string" &&
    typeof item.exchange === "string" &&
    typeof item.symbol === "string" &&
    typeof item.hours === "number" &&
    typeof item.intervalMinutes === "number" &&
    typeof item.savedAt === "string"
  );
}

function loadSavedPresets(): SavedPremiumIndexPreset[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PREMIUM_INDEX_PRESETS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isSavedPreset).slice(0, MAX_SAVED_PREMIUM_PRESETS) : [];
  } catch {
    return [];
  }
}

function storeSavedPresets(presets: SavedPremiumIndexPreset[]): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PREMIUM_INDEX_PRESETS_KEY, JSON.stringify(presets.slice(0, MAX_SAVED_PREMIUM_PRESETS)));
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm") : "-";
}

function fullTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function durationLabel(hours: number): string {
  if (hours < 24) {
    return `${hours}小时`;
  }
  return `${hours / 24}天`;
}

function savedPresetLabel(preset: SavedPremiumIndexPreset): string {
  return `${preset.exchange[0].toUpperCase()}${preset.exchange.slice(1)} ${preset.symbol}`;
}

function premiumStats(points: PremiumIndexPoint[], current?: PremiumIndexCurrentSnapshot | null): PremiumIndexValueStats {
  const values = points.map((point) => point.premium_pct).filter((value) => Number.isFinite(value));
  if (!values.length) {
    return { min: null, max: null, mean: null, current: current?.premium_pct ?? null };
  }
  return {
    min: Math.min(...values),
    max: Math.max(...values),
    mean: values.reduce((total, value) => total + value, 0) / values.length,
    current: current?.premium_pct ?? values[values.length - 1]
  };
}

function currentToPoint(current: PremiumIndexCurrentSnapshot): PremiumIndexPoint | null {
  if (typeof current.premium_pct !== "number" || !Number.isFinite(current.premium_pct)) {
    return null;
  }
  return {
    bucket_at: current.observed_at,
    premium_pct: current.premium_pct,
    mark_price: current.mark_price,
    index_price: current.index_price,
    source: current.source
  };
}

function mergeCurrent(result: PremiumIndexQueryResult, current: PremiumIndexCurrentSnapshot): PremiumIndexQueryResult {
  const nextPoint = currentToPoint(current);
  if (!nextPoint) {
    return { ...result, current, observed_at: current.observed_at };
  }
  const cutoff = dayjs.utc(current.observed_at).subtract(result.hours, "hour");
  const byTime = new Map<string, PremiumIndexPoint>();
  for (const point of result.points) {
    if (dayjs.utc(point.bucket_at).isAfter(cutoff)) {
      byTime.set(point.bucket_at, point);
    }
  }
  byTime.set(nextPoint.bucket_at, nextPoint);
  const points = Array.from(byTime.values())
    .sort((a, b) => dayjs.utc(a.bucket_at).valueOf() - dayjs.utc(b.bucket_at).valueOf())
    .slice(-2400);
  return {
    ...result,
    observed_at: current.observed_at,
    point_count: points.length,
    first_seen_at: points[0]?.bucket_at ?? null,
    last_seen_at: points[points.length - 1]?.bucket_at ?? null,
    premium_pct: premiumStats(points, current),
    current,
    points
  };
}

function chartSpanHours(points: PremiumIndexPoint[]): number {
  if (points.length < 2) {
    return 0;
  }
  const start = dayjs.utc(points[0].bucket_at);
  const end = dayjs.utc(points[points.length - 1].bucket_at);
  return Math.max(end.diff(start, "minute") / 60, 0);
}

function chartTime(value: string, spanHours: number): string {
  const parsed = dayjs.utc(value).utcOffset(8);
  if (spanHours <= 1) {
    return parsed.format("HH:mm:ss");
  }
  return spanHours <= 24 ? parsed.format("HH:mm") : parsed.format("MM-DD HH:mm");
}

function chartTicks(points: PremiumIndexPoint[], maxTicks = 7): Array<{ index: number; point: PremiumIndexPoint }> {
  if (points.length <= 1) {
    return points.map((point, index) => ({ index, point }));
  }
  const count = Math.min(maxTicks, points.length);
  return Array.from({ length: count }, (_, tickIndex) => {
    const index = Math.round((tickIndex * (points.length - 1)) / (count - 1));
    return { index, point: points[index] };
  });
}

type PremiumTurnCandidate = {
  index: number;
  kind: "peak" | "trough";
  score: number;
};

function addPremiumTurnCandidate(
  selected: PremiumTurnCandidate[],
  candidate: PremiumTurnCandidate,
  maxLabels: number,
  minIndexDistance: number
): boolean {
  if (selected.length >= maxLabels) {
    return false;
  }
  if (selected.some((item) => item.index === candidate.index)) {
    return false;
  }
  if (selected.some((item) => Math.abs(item.index - candidate.index) < minIndexDistance)) {
    return false;
  }
  selected.push(candidate);
  return true;
}

function premiumTurningPoints(points: PremiumIndexPoint[], maxLabels = 12): Array<{
  index: number;
  point: PremiumIndexPoint;
  kind: "peak" | "trough";
}> {
  if (points.length < 3) {
    return [];
  }

  const values = points.map((point) => point.premium_pct);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || 1;
  const localWindowSize = Math.max(2, Math.floor(points.length / 180));
  const contextWindowSize = Math.max(localWindowSize + 2, Math.floor(points.length / 55));
  const minProminence = Math.max(0.003, span * 0.004);
  const candidates: PremiumTurnCandidate[] = [];
  let previousDirection = 0;

  for (let index = 1; index < points.length; index += 1) {
    const delta = values[index] - values[index - 1];
    const direction = delta > 0 ? 1 : delta < 0 ? -1 : 0;
    if (direction === 0) {
      continue;
    }
    if (previousDirection !== 0 && direction !== previousDirection) {
      const turnIndex = index - 1;
      const kind = previousDirection > 0 && direction < 0 ? "peak" : "trough";
      const score = Math.max(
        premiumTurnScore(values, turnIndex, kind, localWindowSize) * 1.25,
        premiumTurnScore(values, turnIndex, kind, contextWindowSize)
      );
      if (score >= minProminence) {
        candidates.push({ index: turnIndex, kind, score });
      }
    }
    previousDirection = direction;
  }

  candidates.push({ index: values.indexOf(maxValue), kind: "peak", score: span });
  candidates.push({ index: values.indexOf(minValue), kind: "trough", score: span });

  const selected: PremiumTurnCandidate[] = [];
  const rankedCandidates = candidates.sort((a, b) => b.score - a.score);
  const minIndexDistance = Math.max(5, Math.floor(points.length / 44));
  for (const candidate of rankedCandidates) {
    addPremiumTurnCandidate(selected, candidate, maxLabels, minIndexDistance);
    if (selected.length >= maxLabels) {
      break;
    }
  }

  return selected
    .sort((a, b) => a.index - b.index)
    .map(({ index, kind }) => ({ index, point: points[index], kind }));
}

function premiumTurnScore(values: number[], index: number, kind: "peak" | "trough", windowSize: number): number {
  const start = Math.max(0, index - windowSize);
  const end = Math.min(values.length - 1, index + windowSize);
  const left = values.slice(start, index);
  const right = values.slice(index + 1, end + 1);
  if (!left.length || !right.length) {
    return 0;
  }
  const value = values[index];
  if (kind === "peak") {
    return Math.min(value - Math.min(...left), value - Math.min(...right));
  }
  return Math.min(Math.max(...left) - value, Math.max(...right) - value);
}

function PremiumIndexChart({ result }: { result: PremiumIndexQueryResult | null }) {
  const points = result?.points ?? [];
  const width = 1180;
  const height = 330;
  const padding = { top: 24, right: 28, bottom: 34, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!result || points.length === 0) {
    return <div className="premium-chart-empty">暂无查询结果</div>;
  }

  const values = points.map((point) => point.premium_pct).filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 0.01);
  const min = minValue - span * 0.14;
  const max = maxValue + span * 0.14;
  const startMs = dayjs.utc(points[0].bucket_at).valueOf();
  const endMs = dayjs.utc(points[points.length - 1].bucket_at).valueOf();
  const xAt = (point: PremiumIndexPoint) => {
    if (startMs === endMs) {
      return padding.left + chartWidth / 2;
    }
    return padding.left + ((dayjs.utc(point.bucket_at).valueOf() - startMs) / (endMs - startMs)) * chartWidth;
  };
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const baselineValue = min <= 0 && max >= 0 ? 0 : minValue > 0 ? min : max;
  const baselineY = yAt(baselineValue);
  const line = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(point).toFixed(2)} ${yAt(point.premium_pct).toFixed(2)}`)
    .join(" ");
  const area = `M ${xAt(points[0]).toFixed(2)} ${baselineY.toFixed(2)} ${points
    .map((point) => `L ${xAt(point).toFixed(2)} ${yAt(point.premium_pct).toFixed(2)}`)
    .join(" ")} L ${xAt(points[points.length - 1]).toFixed(2)} ${baselineY.toFixed(2)} Z`;
  const spanHours = chartSpanHours(points);
  const ticks = chartTicks(points, spanHours >= 168 ? 7 : 6);
  const turningPoints = premiumTurningPoints(points);

  return (
    <div className="premium-chart-card">
      <svg className="premium-index-chart" role="img" aria-label="合约溢价/偏离曲线" viewBox={`0 0 ${width} ${height}`}>
        <defs>
          <linearGradient id="premiumIndexFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#0f766e" stopOpacity="0.2" />
            <stop offset="100%" stopColor="#0f766e" stopOpacity="0.04" />
          </linearGradient>
        </defs>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="premium-chart-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="premium-chart-axis-label" x={padding.left - 10} y={y + 4} textAnchor="end">
                {Math.round(value * 100)}bp
              </text>
            </g>
          );
        })}
        {ticks.map(({ point }, tickIndex) => {
          const x = xAt(point);
          const textAnchor = tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={point.bucket_at}>
              <line className="premium-chart-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="premium-chart-axis-label" x={x} y={height - 10} textAnchor={textAnchor}>
                {chartTime(point.bucket_at, spanHours)}
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line className="premium-chart-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        ) : null}
        <path className="premium-chart-area" d={area} />
        <path className="premium-chart-line" d={line} />
        {turningPoints.map(({ index, point, kind }, labelIndex) => {
          const x = xAt(point);
          const y = yAt(point.premium_pct);
          const label = signedBp(point.premium_pct);
          const labelWidth = 58;
          const labelHeight = 22;
          const labelOffset = 20 + (labelIndex % 2) * 10;
          const labelShift = ((labelIndex % 3) - 1) * 8;
          const labelCenterX = Math.min(
            padding.left + chartWidth - labelWidth / 2,
            Math.max(padding.left + labelWidth / 2, x + labelShift)
          );
          const rawLabelY = kind === "peak" ? y - labelOffset : y + labelOffset;
          const labelCenterY = Math.min(
            padding.top + chartHeight - labelHeight / 2,
            Math.max(padding.top + labelHeight / 2, rawLabelY)
          );
          return (
            <g key={`premium-turn-${point.bucket_at}-${kind}-${index}`} className={`premium-chart-turning premium-chart-turning-${kind}`}>
              <title>{`${time(point.bucket_at)} 溢价/偏离 ${signedPct(point.premium_pct, 4)} (${label})`}</title>
              <line className="premium-chart-turning-leader" x1={x} y1={y} x2={labelCenterX} y2={labelCenterY} />
              <circle className="premium-chart-turning-dot" cx={x} cy={y} r="3.5" />
              <rect
                className="premium-chart-turning-label-bg"
                x={labelCenterX - labelWidth / 2}
                y={labelCenterY - labelHeight / 2}
                width={labelWidth}
                height={labelHeight}
                rx="4"
              />
              <text className="premium-chart-turning-label" x={labelCenterX} y={labelCenterY + 4} textAnchor="middle">
                {label}
              </text>
            </g>
          );
        })}
        {result.current?.premium_pct != null ? (
          <circle
            className="premium-current-point"
            cx={xAt(points[points.length - 1])}
            cy={yAt(points[points.length - 1].premium_pct)}
            r="4"
          />
        ) : null}
      </svg>
      <div className="premium-chart-footer">
        <div className="premium-footer-tags">
          <Tag color="green">{result.exchange}</Tag>
          <Tag>{result.symbol}</Tag>
          <Tag>{result.point_count} 点</Tag>
          <Tag>{result.interval_minutes}m 周期</Tag>
        </div>
        <Typography.Text type="secondary">最新 {fullTime(result.observed_at)}</Typography.Text>
      </div>
    </div>
  );
}

function MetricCard({ label, value, sub, tone = "neutral" }: {
  label: string;
  value: string;
  sub: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  return (
    <div className={`premium-metric-card premium-metric-${tone}`}>
      <Typography.Text className="premium-metric-label">{label}</Typography.Text>
      <div className="premium-metric-value">{value}</div>
      <Typography.Text className="premium-metric-sub">{sub}</Typography.Text>
    </div>
  );
}

function FundingFollowPanel({ estimate }: { estimate: FundingFollowEstimate | null }) {
  if (!estimate) {
    return null;
  }
  const limitText = estimate.limitPct !== null
    ? `当前交易所返回值疑似触及 ±${estimate.limitPct.toFixed(4)}% 上下限`
    : "未检测到明显上下限";
  const thresholdText = estimate.limitPct !== null
    ? `触及下限约需平均P ≤ ${signedPct(estimate.lowerLimitAveragePremiumPct)}，触及上限约需平均P ≥ ${signedPct(estimate.upperLimitAveragePremiumPct)}`
    : "未按上下限截断";
  const exitLimitText = estimate.futureAveragePremiumToExitLimitPct !== null
    ? `若想本次结算脱离当前上下限，剩余 ${compactHours(estimate.remainingHours)} 的平均P大约需要达到 ${signedPct(estimate.futureAveragePremiumToExitLimitPct)}`
    : "当前未处于明显上下限，或剩余时间不足以估算脱离阈值";

  return (
    <section className="premium-detail-card">
      <div className="premium-detail-head">
        <Typography.Title level={5}>资金费跟随估算（Bitget）</Typography.Title>
        <Tag color="orange">近似</Tag>
      </div>
      <Typography.Paragraph type="secondary">
        按 Bitget 公式近似：平均P = (P1×1 + P2×2 + ... + Pn×n) / (1 + 2 + ... + n)，资金费率 = [平均P + clamp(利率 - 平均P, -0.05%, 0.05%)] ÷ (8 / N)，再套合约资金费上下限。
        这里用页面上的“标记价-指数价”偏离近似 P；Bitget 实际还可能使用 5 秒采样和上一结算周期样本平滑，最终以交易所返回值为准。
      </Typography.Paragraph>
      <div className="premium-metric-grid">
        <MetricCard
          label="估算资金周期"
          value={`${estimate.intervalHours}h`}
          sub={`样本 ${estimate.sampledPoints} 点 · ${time(estimate.sampledFrom)} - ${time(estimate.sampledTo)}`}
        />
        <MetricCard
          label="本周期平均P"
          value={signedPct(estimate.averagePremiumPct)}
          sub={`递增权重 · 已过 ${compactHours(estimate.elapsedHours)} · 剩余 ${compactHours(estimate.remainingHours)}`}
          tone={typeof estimate.averagePremiumPct === "number" ? (estimate.averagePremiumPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="当前交易所资金费"
          value={signedPct(estimate.exchangeFundingPct)}
          sub={limitText}
          tone={typeof estimate.exchangeFundingPct === "number" ? (estimate.exchangeFundingPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="平均P对应估算资金费"
          value={signedPct(estimate.estimatedFundingPct)}
          sub={thresholdText}
          tone={typeof estimate.estimatedFundingPct === "number" ? (estimate.estimatedFundingPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="若当前P保持到结算"
          value={signedPct(estimate.projectedFundingIfCurrentPremiumPersistsPct)}
          sub={`结算平均P约 ${signedPct(estimate.projectedAveragePremiumPct)} · 当前P ${signedPct(estimate.currentPremiumPct)} 继续加权`}
          tone={typeof estimate.projectedFundingIfCurrentPremiumPersistsPct === "number" ? (
            estimate.projectedFundingIfCurrentPremiumPersistsPct >= 0 ? "positive" : "negative"
          ) : "neutral"}
        />
        <MetricCard
          label="脱离上下限所需P"
          value={signedPct(estimate.futureAveragePremiumToExitLimitPct)}
          sub={exitLimitText}
        />
      </div>
    </section>
  );
}

const pointColumns: ColumnsType<PremiumIndexPoint> = [
  { title: "时间", dataIndex: "bucket_at", width: 150, render: (value: string) => fullTime(value) },
  { title: "溢价/偏离", dataIndex: "premium_pct", align: "right", render: (value: number) => signedPct(value) },
  { title: "标记价", dataIndex: "mark_price", align: "right", render: (value: number | null) => price(value) },
  { title: "指数价", dataIndex: "index_price", align: "right", render: (value: number | null) => price(value) },
  { title: "来源", dataIndex: "source", width: 180, render: (value: string) => <Tag>{premiumSourceLabel(value)}</Tag> }
];

export function PremiumIndexPage() {
  const [form] = Form.useForm<PremiumIndexFormValues>();
  const [hours, setHours] = useState(12);
  const [intervalMinutes, setIntervalMinutes] = useState(1);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [savedPresets, setSavedPresets] = useState<SavedPremiumIndexPreset[]>(() => loadSavedPresets());
  const [result, setResult] = useState<PremiumIndexQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const recentPoints = useMemo(() => [...(result?.points ?? [])].reverse().slice(0, 180), [result?.points]);

  const runQuery = useCallback(async (override?: {
    values?: PremiumIndexFormValues;
    hours?: number;
    intervalMinutes?: number;
  }) => {
    setLoading(true);
    setError("");
    try {
      const values = normalizePremiumForm(override?.values ?? await form.validateFields());
      const queryHours = clampHours(override?.hours ?? hours);
      const queryInterval = override?.intervalMinutes ?? intervalMinutes;
      const next = await queryPremiumIndex({
        exchange: values.exchange,
        symbol: values.symbol,
        hours: queryHours,
        interval_minutes: queryInterval
      });
      form.setFieldsValue(values);
      setResult(next);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [form, hours, intervalMinutes]);

  const refreshCurrent = useCallback(async () => {
    if (!result) {
      return;
    }
    setRefreshing(true);
    try {
      const current = await getCurrentPremiumIndex({
        exchange: result.exchange,
        symbol: result.symbol
      });
      setResult((existing) => (existing ? mergeCurrent(existing, current) : existing));
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setRefreshing(false);
    }
  }, [result]);

  useEffect(() => {
    if (!autoRefresh || !result) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      if (!loading && !refreshing) {
        void refreshCurrent();
      }
    }, 8_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loading, refreshCurrent, refreshing, result]);

  const saveCurrentPreset = async () => {
    try {
      const values = normalizePremiumForm(await form.validateFields());
      const preset: SavedPremiumIndexPreset = {
        ...values,
        id: presetId(values),
        hours: clampHours(hours),
        intervalMinutes,
        savedAt: new Date().toISOString()
      };
      setSavedPresets((currentPresets) => {
        const next = [preset, ...currentPresets.filter((item) => item.id !== preset.id)].slice(
          0,
          MAX_SAVED_PREMIUM_PRESETS
        );
        storeSavedPresets(next);
        return next;
      });
      form.setFieldsValue(values);
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const removeSavedPreset = (id: string) => {
    setSavedPresets((currentPresets) => {
      const next = currentPresets.filter((preset) => preset.id !== id);
      storeSavedPresets(next);
      return next;
    });
  };

  const applySavedPreset = (preset: SavedPremiumIndexPreset) => {
    form.setFieldsValue(preset);
    setHours(clampHours(preset.hours));
    setIntervalMinutes(preset.intervalMinutes);
    void runQuery({ values: preset, hours: preset.hours, intervalMinutes: preset.intervalMinutes });
  };

  const current = result?.current ?? null;
  const currentPremium = current?.premium_pct ?? result?.premium_pct.current ?? null;
  const tone = typeof currentPremium === "number" ? (currentPremium >= 0 ? "positive" : "negative") : "neutral";
  const fundingFollowEstimate = useMemo(
    () => buildBitgetFundingFollowEstimate(result, current, currentPremium),
    [current, currentPremium, result]
  );
  const premiumDefinitionMessage = current
    ? "当前标记价溢价 = (标记价 - 指数价) / 指数价；资金费率为交易所返回值，不由这个即时偏离直接计算。交易所资金费通常参考整个结算周期的平均 premium、冲击买卖价、利率项和上下限。"
    : "";

  return (
    <div className="page premium-index-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      {result?.warnings.length ? <Alert type="warning" message={result.warnings.join("；")} showIcon /> : null}
      {premiumDefinitionMessage ? <Alert type="info" message={premiumDefinitionMessage} showIcon /> : null}

      <section className="premium-query-panel">
        <Form form={form} initialValues={defaultFormValues} disabled={loading}>
          <div className="premium-query-bar">
            <Form.Item name="exchange" rules={[{ required: true }]}>
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item name="symbol" rules={[{ required: true, message: "请输入合约" }]}>
              <Input addonBefore="合约" placeholder="BTC" />
            </Form.Item>
            <InputNumber
              addonBefore="小时"
              min={1}
              max={720}
              precision={0}
              step={1}
              value={hours}
              onChange={(value) => setHours(clampHours(value))}
            />
            <Select value={intervalMinutes} options={intervalOptions} onChange={setIntervalMinutes} />
            <div className="premium-query-refresh">
              <Switch checked={autoRefresh} checkedChildren="自动" unCheckedChildren="手动" onChange={setAutoRefresh} />
            </div>
            <div className="premium-query-actions">
              <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void runQuery()}>
                查询
              </Button>
              <Button icon={<ReloadOutlined />} disabled={!result} loading={refreshing} onClick={() => void refreshCurrent()}>
                最新
              </Button>
              <Button icon={<SaveOutlined />} disabled={loading} onClick={() => void saveCurrentPreset()}>
                保存
              </Button>
            </div>
          </div>
        </Form>
        {savedPresets.length ? (
          <div className="premium-saved-presets">
            <Typography.Text className="premium-saved-title">已保存</Typography.Text>
            <div className="premium-saved-list">
              {savedPresets.map((preset) => (
                <Tag
                  key={preset.id}
                  closable
                  className="premium-saved-tag"
                  onClick={() => applySavedPreset(preset)}
                  onClose={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    removeSavedPreset(preset.id);
                  }}
                >
                  <span>{savedPresetLabel(preset)}</span>
                  <span className="premium-saved-meta">
                    {durationLabel(preset.hours)} · {preset.intervalMinutes}m
                  </span>
                </Tag>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="premium-metric-grid">
        <MetricCard
          label="当前标记价溢价"
          value={signedPct(currentPremium)}
          sub={current ? `${current.exchange} · ${current.symbol} · ${premiumSourceLabel(current.source)}` : "等待查询"}
          tone={tone}
        />
        <MetricCard label="标记价" value={price(current?.mark_price)} sub="mark price" />
        <MetricCard label="指数价" value={price(current?.index_price)} sub="index price" />
        <MetricCard label="盘口中价溢价" value={signedPct(current?.mid_premium_pct)} sub={price(current?.mid_price)} tone={tone} />
        <MetricCard
          label="资金费率"
          value={signedPct(current?.funding_rate_pct)}
          sub={current?.funding_next_time ? `交易所返回 · 下次 ${time(current.funding_next_time)}` : "交易所返回"}
        />
        <MetricCard
          label="数据窗口"
          value={`${result?.interval_minutes ?? intervalMinutes}m`}
          sub={result ? `${time(result.first_seen_at)} - ${time(result.last_seen_at)}` : durationLabel(hours)}
        />
      </section>

      <FundingFollowPanel estimate={fundingFollowEstimate} />

      <PremiumIndexChart result={result} />

      <section className="premium-detail-card">
        <div className="premium-detail-head">
          <Typography.Title level={5}>最近溢价/偏离</Typography.Title>
          <Tag>{recentPoints.length} 条</Tag>
        </div>
        <Table<PremiumIndexPoint>
          rowKey={(point) => point.bucket_at}
          columns={pointColumns}
          dataSource={recentPoints}
          loading={loading}
          pagination={{ pageSize: 10 }}
          size="small"
          tableLayout="fixed"
          scroll={{ x: 720 }}
        />
      </section>
    </div>
  );
}
