import { ReloadOutlined, SaveOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, InputNumber, Select, Switch, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
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
  samplingIntervalSeconds: number;
  savedAt: string;
};

const fundingFollowExchanges = ["binance", "aster", "bitget", "bybit", "gate", "hyperliquid", "okx"] as const;
type FundingFollowExchange = typeof fundingFollowExchanges[number];
const withRateScaledFundingExchanges = new Set<FundingFollowExchange>([
  "binance",
  "aster",
  "bitget",
  "gate",
  "hyperliquid",
  "okx"
]);

type FundingFollowEstimate = {
  exchange: FundingFollowExchange;
  title: string;
  formulaNote: string;
  basisNote: string;
  limitNote: string;
  intervalSource: string;
  intervalHours: number;
  sampledPoints: number;
  sampledFrom: string | null;
  sampledTo: string | null;
  elapsedHours: number | null;
  remainingHours: number | null;
  averagePremiumPct: number | null;
  currentPremiumPct: number | null;
  remainingPremiumAssumptionPct: number | null;
  remainingPremiumSampleCount: number;
  remainingPremiumWindowMinutes: number;
  exchangeFundingPct: number | null;
  estimatedFundingPct: number | null;
  projectedAveragePremiumPct: number | null;
  projectedFundingIfCurrentPremiumPersistsPct: number | null;
  limitPct: number | null;
  lowerFundingLimitPct: number | null;
  upperFundingLimitPct: number | null;
  lowerLimitAveragePremiumPct: number | null;
  upperLimitAveragePremiumPct: number | null;
  targetLimitSide: "lower" | "upper" | null;
  targetFundingLimitPct: number | null;
  targetAveragePremiumPct: number | null;
  futureAveragePremiumToReachLimitPct: number | null;
};

type WeightedPremiumStats = {
  averagePremiumPct: number | null;
  weightedTotal: number;
  weightTotal: number;
  sampleCount: number;
};

type RecentPremiumAssumption = {
  premiumPct: number | null;
  sampleCount: number;
  windowMinutes: number;
};

const defaultFormValues: PremiumIndexFormValues = {
  exchange: "binance",
  symbol: "BTC"
};

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"].map(
  (value) => ({ label: displayExchangeName(value), value })
);

const intervalOptions = [
  { label: "历史 1 分钟", value: 1 },
  { label: "历史 5 分钟", value: 5 },
  { label: "历史 15 分钟", value: 15 }
];

const samplingIntervalOptions = [3, 5, 8, 15, 30, 60].map((value) => ({
  label: `采样 ${value} 秒`,
  value
}));

const PREMIUM_INDEX_PRESETS_KEY = "taoli1.premiumIndex.presets.v1";
const PREMIUM_INDEX_SAMPLING_INTERVAL_KEY = "taoli1.premiumIndex.samplingIntervalSeconds.v1";
const MAX_SAVED_PREMIUM_PRESETS = 24;
const DEFAULT_SAMPLING_INTERVAL_SECONDS = 8;

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

function withRateFundingFromAveragePremiumPct(
  averagePremiumPct: number | null | undefined,
  intervalHours: number,
  lowerLimitPct: number | null = null,
  upperLimitPct: number | null = null
): number | null {
  if (
    typeof averagePremiumPct !== "number" ||
    !Number.isFinite(averagePremiumPct) ||
    !Number.isFinite(intervalHours) ||
    intervalHours <= 0
  ) {
    return null;
  }
  const interestPct = 0.01;
  const dampenerPct = 0.05;
  const intervalScale = 8 / intervalHours;
  const uncapped = (
    averagePremiumPct + clamp(interestPct - averagePremiumPct, -dampenerPct, dampenerPct)
  ) / intervalScale;
  const withLowerLimit =
    typeof lowerLimitPct === "number" && Number.isFinite(lowerLimitPct)
      ? Math.max(uncapped, lowerLimitPct)
      : uncapped;
  return typeof upperLimitPct === "number" && Number.isFinite(upperLimitPct)
    ? Math.min(withLowerLimit, upperLimitPct)
    : withLowerLimit;
}

function premiumBasedFundingFromAveragePremiumPct(
  averagePremiumPct: number | null | undefined,
  interestPct: number,
  lowerLimitPct: number | null = null,
  upperLimitPct: number | null = null
): number | null {
  if (typeof averagePremiumPct !== "number" || !Number.isFinite(averagePremiumPct)) {
    return null;
  }
  const dampenerPct = 0.05;
  const uncapped = averagePremiumPct + clamp(interestPct - averagePremiumPct, -dampenerPct, dampenerPct);
  const withLowerLimit =
    typeof lowerLimitPct === "number" && Number.isFinite(lowerLimitPct)
      ? Math.max(uncapped, lowerLimitPct)
      : uncapped;
  return typeof upperLimitPct === "number" && Number.isFinite(upperLimitPct)
    ? Math.min(withLowerLimit, upperLimitPct)
    : withLowerLimit;
}

function bybitFundingFromAveragePremiumPct(
  averagePremiumPct: number | null | undefined,
  intervalHours: number,
  lowerLimitPct: number | null = null,
  upperLimitPct: number | null = null
): number | null {
  return premiumBasedFundingFromAveragePremiumPct(
    averagePremiumPct,
    0.03 / (24 / intervalHours),
    lowerLimitPct,
    upperLimitPct
  );
}

function averagePremiumNeededForWithRateLimitPct(
  targetFundingLimitPct: number | null,
  intervalHours: number
): number | null {
  if (
    typeof targetFundingLimitPct !== "number" ||
    !Number.isFinite(targetFundingLimitPct) ||
    !Number.isFinite(intervalHours) ||
    intervalHours <= 0
  ) {
    return null;
  }
  const interestPct = 0.01;
  const dampenerPct = 0.05;
  const unscaledFundingPct = targetFundingLimitPct * (8 / intervalHours);
  return unscaledFundingPct < interestPct
    ? unscaledFundingPct - dampenerPct
    : unscaledFundingPct + dampenerPct;
}

function averagePremiumNeededForPremiumBasedLimitPct(
  targetFundingLimitPct: number | null,
  interestPct: number
): number | null {
  if (typeof targetFundingLimitPct !== "number" || !Number.isFinite(targetFundingLimitPct)) {
    return null;
  }
  const dampenerPct = 0.05;
  return targetFundingLimitPct < interestPct
    ? targetFundingLimitPct - dampenerPct
    : targetFundingLimitPct + dampenerPct;
}

function sumConsecutiveWeights(start: number, count: number): number {
  if (count <= 0) {
    return 0;
  }
  return (count * (2 * start + count - 1)) / 2;
}

function weightedPremiumStats(points: PremiumIndexPoint[]): WeightedPremiumStats {
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

function requiredFutureAveragePremiumPct(
  targetAveragePremiumPct: number | null,
  weightedStats: WeightedPremiumStats,
  futureWeightTotal: number
): number | null {
  if (
    targetAveragePremiumPct === null ||
    weightedStats.weightTotal <= 0 ||
    futureWeightTotal <= 0
  ) {
    return null;
  }
  return (
    targetAveragePremiumPct * (weightedStats.weightTotal + futureWeightTotal) -
    weightedStats.weightedTotal
  ) / futureWeightTotal;
}

function recentPremiumAssumption(
  points: PremiumIndexPoint[],
  observedAt: dayjs.Dayjs,
  intervalMinutes: number
): RecentPremiumAssumption {
  const sorted = [...points]
    .filter((point) => Number.isFinite(point.premium_pct) && !dayjs.utc(point.bucket_at).isAfter(observedAt))
    .sort((a, b) => dayjs.utc(a.bucket_at).valueOf() - dayjs.utc(b.bucket_at).valueOf());
  const windowMinutes = Math.max(15, Math.min(60, intervalMinutes * 10));
  const windowStart = observedAt.subtract(windowMinutes, "minute");
  let recent = sorted.filter((point) => !dayjs.utc(point.bucket_at).isBefore(windowStart));
  if (recent.length < 3) {
    recent = sorted.slice(-Math.min(10, sorted.length));
  } else if (recent.length > 30) {
    recent = recent.slice(-30);
  }
  const values = recent.map((point) => point.premium_pct);
  return {
    premiumPct: values.length ? values.reduce((total, value) => total + value, 0) / values.length : null,
    sampleCount: values.length,
    windowMinutes
  };
}

function likelyFundingLimitPct(fundingPct: number | null | undefined): number | null {
  if (typeof fundingPct !== "number" || !Number.isFinite(fundingPct)) {
    return null;
  }
  const abs = Math.abs(fundingPct);
  return abs >= 0.45 ? abs : null;
}

function isFundingFollowExchange(exchange: string): exchange is FundingFollowExchange {
  return (fundingFollowExchanges as readonly string[]).includes(exchange);
}

function displayExchangeName(exchange: string): string {
  const names: Record<string, string> = {
    aster: "Aster",
    binance: "Binance",
    bitget: "Bitget",
    bybit: "Bybit",
    gate: "Gate",
    hyperliquid: "Hyperliquid",
    okx: "OKX"
  };
  return names[exchange] ?? `${exchange[0]?.toUpperCase() ?? ""}${exchange.slice(1)}`;
}

function fundingFormulaNote(exchange: FundingFollowExchange): string {
  if (exchange === "bybit") {
    return "按 Bybit 公式近似：平均P = (P1×1 + P2×2 + ... + Pn×n) / (1 + 2 + ... + n)，资金费率 = 平均P + clamp(利率I - 平均P, -0.05%, 0.05%)，I = 0.03% / (24 / N)；若交易所返回上下限，再按上下限截断。Bybit 官方P来自 Impact Bid/Ask。";
  }
  if (exchange === "hyperliquid") {
    return "按 Hyperliquid 小时资金费近似：先用加权平均P 估算 8h 资金费，再除以 (8 / N) 折算到 1 小时；若交易所返回上下限，再按上下限截断。";
  }
  if (exchange === "gate") {
    return "按 Gate 资金费近似：平均P = (P1×1 + P2×2 + ... + Pn×n) / (1 + 2 + ... + n)，资金费率 = [平均P + clamp(0.01% - 平均P, -0.05%, 0.05%)] ÷ (8 / N)，再按合约详情里的资金费上下限截断。";
  }
  return "按 withRate 结构近似：平均P = (P1×1 + P2×2 + ... + Pn×n) / (1 + 2 + ... + n)，资金费率 = [平均P + clamp(0.01% - 平均P, -0.05%, 0.05%)] ÷ (8 / N)，再按交易所返回上下限截断。";
}

function fundingBasisNote(exchange: FundingFollowExchange): string {
  if (exchange === "bybit") {
    return "Bybit 本页历史曲线使用官方 premium-index-price-kline 作为 P；剩余时间默认按近期官方P均值继续估算。";
  }
  if (exchange === "hyperliquid") {
    return "Hyperliquid 本页历史曲线优先使用 fundingHistory 锚点加分钟K线重建 P；拿不到锚点时才回落到当前标记价与指数价偏离。";
  }
  if (exchange === "gate") {
    return "Gate 本页历史曲线使用官方 premium_index 作为 P；当前资金周期和资金上限优先从合约详情补齐。";
  }
  if (exchange === "binance" || exchange === "aster") {
    return "Binance/Aster 本页历史曲线使用官方 premiumIndexKlines 作为 P；当前资金周期和资金上限优先从 fundingInfo 补齐。";
  }
  return "OKX 本页历史曲线优先使用官方 premium-history 作为 P；拿不到时才用“标记价-指数价”偏离近似 P，剩余时间默认按近期官方P均值继续估算。";
}

function fundingLimitNote(exchange: FundingFollowExchange): string {
  if (exchange === "bybit") {
    return "未取到 Bybit 上下限时，本面板不做精确上下限截断；最终以交易所返回资金费为准。";
  }
  if (exchange === "hyperliquid") {
    return "Hyperliquid 采用小时资金费上限做截断；若未来接口字段变化，仍以交易所最终返回值为准。";
  }
  if (exchange === "gate") {
    return "优先使用 Gate 合约详情里的资金费上限；缺少合约详情时，仅按当前资金费做保守推断。";
  }
  if (exchange === "binance" || exchange === "aster") {
    return "优先使用 fundingInfo 返回的资金费上限；缺少该接口时，仅在当前资金费已经很大时做保守推断。";
  }
  return "优先使用 OKX 返回的上下限；缺少上下限但当前资金费疑似已触及整值上限/下限时，用当前返回值近似识别。";
}

function buildFundingFollowEstimate(
  result: PremiumIndexQueryResult | null,
  current: PremiumIndexCurrentSnapshot | null,
  currentPremiumPct: number | null
): FundingFollowEstimate | null {
  if (!result || !current || !isFundingFollowExchange(result.exchange)) {
    return null;
  }
  const exchange = result.exchange;
  const exchangeLabel = displayExchangeName(exchange);
  const currentIntervalHours =
    typeof current.funding_interval_hours === "number" && Number.isFinite(current.funding_interval_hours)
      ? current.funding_interval_hours
      : null;
  if (
    currentIntervalHours === null &&
    (current.funding_next_time ?? null) === null &&
    (current.funding_rate_upper_pct ?? null) === null &&
    (current.funding_rate_lower_pct ?? null) === null
  ) {
    return null;
  }
  const intervalHours = currentIntervalHours ?? nearestFundingIntervalHours(result.hours);
  const intervalSource = currentIntervalHours
    ? "交易所 fundingInterval"
    : "未取到交易所周期，按查询窗口近似";
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
  const weightedStats = weightedPremiumStats(windowPoints);
  const averagePremiumPct = weightedStats.averagePremiumPct;
  const elapsedHours = windowStart ? Math.min(hoursBetween(windowStart, observedAt), intervalHours) : null;
  const remainingHours = nextFundingAt ? Math.max(hoursBetween(observedAt, nextFundingAt), 0) : null;
  const explicitLowerLimitPct =
    typeof current.funding_rate_lower_pct === "number" &&
    Number.isFinite(current.funding_rate_lower_pct)
      ? current.funding_rate_lower_pct
      : null;
  const explicitUpperLimitPct =
    typeof current.funding_rate_upper_pct === "number" &&
    Number.isFinite(current.funding_rate_upper_pct)
      ? current.funding_rate_upper_pct
      : null;
  const inferredLimitPct = explicitLowerLimitPct === null && explicitUpperLimitPct === null && exchange !== "bybit"
    ? likelyFundingLimitPct(current.funding_rate_pct)
    : null;
  const lowerFundingLimitPct = explicitLowerLimitPct ?? (inferredLimitPct !== null ? -inferredLimitPct : null);
  const upperFundingLimitPct = explicitUpperLimitPct ?? (inferredLimitPct !== null ? inferredLimitPct : null);
  const usesWithRateScaledFormula = withRateScaledFundingExchanges.has(exchange);
  const bybitInterestPct = 0.03 / (24 / intervalHours);
  const fundingFormula = (premiumPct: number | null | undefined) => (
    usesWithRateScaledFormula
      ? withRateFundingFromAveragePremiumPct(
          premiumPct,
          intervalHours,
          lowerFundingLimitPct,
          upperFundingLimitPct
        )
      : bybitFundingFromAveragePremiumPct(
        premiumPct,
        intervalHours,
        lowerFundingLimitPct,
        upperFundingLimitPct
      )
  );
  const estimatedFundingPct = fundingFormula(averagePremiumPct);
  const assumption = recentPremiumAssumption(windowPoints, observedAt, result.interval_minutes);
  const latestPremiumPct = windowPoints[windowPoints.length - 1]?.premium_pct ?? currentPremiumPct;
  const remainingPremiumAssumptionPct = assumption.premiumPct ?? latestPremiumPct;
  const remainingSampleCount = remainingHours !== null
    ? Math.max(Math.round((remainingHours * 60) / Math.max(result.interval_minutes, 1)), 0)
    : 0;
  const futureWeightTotal = sumConsecutiveWeights(weightedStats.sampleCount + 1, remainingSampleCount);
  const projectedAveragePremium =
    typeof averagePremiumPct === "number" &&
    typeof remainingPremiumAssumptionPct === "number" &&
    weightedStats.weightTotal + futureWeightTotal > 0
      ? (weightedStats.weightedTotal + remainingPremiumAssumptionPct * futureWeightTotal) / (
        weightedStats.weightTotal + futureWeightTotal
      )
      : null;
  const projectedFundingIfCurrentPremiumPersistsPct = fundingFormula(projectedAveragePremium);
  const lowerLimitAveragePremiumPct =
    lowerFundingLimitPct !== null
      ? usesWithRateScaledFormula
        ? averagePremiumNeededForWithRateLimitPct(lowerFundingLimitPct, intervalHours)
        : averagePremiumNeededForPremiumBasedLimitPct(lowerFundingLimitPct, bybitInterestPct)
      : null;
  const upperLimitAveragePremiumPct =
    upperFundingLimitPct !== null
      ? usesWithRateScaledFormula
        ? averagePremiumNeededForWithRateLimitPct(upperFundingLimitPct, intervalHours)
        : averagePremiumNeededForPremiumBasedLimitPct(upperFundingLimitPct, bybitInterestPct)
      : null;
  const directionReference = [
    current.funding_rate_pct,
    estimatedFundingPct,
    averagePremiumPct,
    currentPremiumPct
  ].find((value): value is number => typeof value === "number" && Number.isFinite(value) && Math.abs(value) > 1e-12) ?? null;
  let targetLimitSide: "lower" | "upper" | null = directionReference === null
    ? null
    : directionReference < 0
      ? "lower"
      : "upper";
  if (targetLimitSide === "lower" && (lowerFundingLimitPct === null || lowerLimitAveragePremiumPct === null)) {
    targetLimitSide = upperFundingLimitPct !== null && upperLimitAveragePremiumPct !== null ? "upper" : null;
  }
  if (targetLimitSide === "upper" && (upperFundingLimitPct === null || upperLimitAveragePremiumPct === null)) {
    targetLimitSide = lowerFundingLimitPct !== null && lowerLimitAveragePremiumPct !== null ? "lower" : null;
  }
  const targetFundingLimitPct = targetLimitSide === "lower"
    ? lowerFundingLimitPct
    : targetLimitSide === "upper"
      ? upperFundingLimitPct
      : null;
  const targetAveragePremiumPct = targetLimitSide === "lower"
    ? lowerLimitAveragePremiumPct
    : targetLimitSide === "upper"
      ? upperLimitAveragePremiumPct
      : null;
  const futureAveragePremiumToReachLimitPct = requiredFutureAveragePremiumPct(
    targetAveragePremiumPct,
    weightedStats,
    futureWeightTotal
  );
  return {
    exchange,
    title: `资金费跟随估算（${exchangeLabel}）`,
    formulaNote: fundingFormulaNote(exchange),
    basisNote: fundingBasisNote(exchange),
    limitNote: fundingLimitNote(exchange),
    intervalSource,
    intervalHours,
    sampledPoints: windowPoints.length,
    sampledFrom: windowPoints[0]?.bucket_at ?? null,
    sampledTo: windowPoints[windowPoints.length - 1]?.bucket_at ?? null,
    elapsedHours,
    remainingHours,
    averagePremiumPct,
    currentPremiumPct: latestPremiumPct,
    remainingPremiumAssumptionPct,
    remainingPremiumSampleCount: assumption.sampleCount,
    remainingPremiumWindowMinutes: assumption.windowMinutes,
    exchangeFundingPct: current.funding_rate_pct,
    estimatedFundingPct,
    projectedAveragePremiumPct: projectedAveragePremium,
    projectedFundingIfCurrentPremiumPersistsPct,
    limitPct: explicitLowerLimitPct === null && explicitUpperLimitPct === null ? inferredLimitPct : null,
    lowerFundingLimitPct,
    upperFundingLimitPct,
    lowerLimitAveragePremiumPct,
    upperLimitAveragePremiumPct,
    targetLimitSide,
    targetFundingLimitPct,
    targetAveragePremiumPct,
    futureAveragePremiumToReachLimitPct
  };
}

function clampHours(value: number | null): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 1;
  }
  return Math.min(720, Math.max(1, Math.round(value)));
}

function normalizeSamplingIntervalSeconds(value: unknown): number {
  return samplingIntervalOptions.some((option) => option.value === value)
    ? value as number
    : DEFAULT_SAMPLING_INTERVAL_SECONDS;
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
    return Array.isArray(parsed)
      ? parsed
        .filter(isSavedPreset)
        .map((preset) => ({
          ...preset,
          samplingIntervalSeconds: normalizeSamplingIntervalSeconds(preset.samplingIntervalSeconds)
        }))
        .slice(0, MAX_SAVED_PREMIUM_PRESETS)
      : [];
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

function loadSamplingIntervalSeconds(): number {
  if (typeof window === "undefined") {
    return DEFAULT_SAMPLING_INTERVAL_SECONDS;
  }
  return normalizeSamplingIntervalSeconds(
    Number(window.localStorage.getItem(PREMIUM_INDEX_SAMPLING_INTERVAL_KEY))
  );
}

function storeSamplingIntervalSeconds(value: number): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PREMIUM_INDEX_SAMPLING_INTERVAL_KEY, String(value));
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
  return `${displayExchangeName(preset.exchange)} ${preset.symbol}`;
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

function premiumBucketAt(value: string, intervalMinutes: number): string {
  const parsed = dayjs.utc(value);
  const bucketMinute = parsed.minute() - (parsed.minute() % Math.max(intervalMinutes, 1));
  return parsed.minute(bucketMinute).second(0).millisecond(0).toISOString();
}

function currentToPoint(current: PremiumIndexCurrentSnapshot, intervalMinutes: number): PremiumIndexPoint | null {
  if (typeof current.premium_pct !== "number" || !Number.isFinite(current.premium_pct)) {
    return null;
  }
  return {
    bucket_at: premiumBucketAt(current.observed_at, intervalMinutes),
    premium_pct: current.premium_pct,
    mark_price: current.mark_price,
    index_price: current.index_price,
    source: current.source
  };
}

function mergeCurrent(result: PremiumIndexQueryResult, current: PremiumIndexCurrentSnapshot): PremiumIndexQueryResult {
  const nextPoint = currentToPoint(current, result.interval_minutes);
  if (!nextPoint) {
    return { ...result, current, observed_at: current.observed_at };
  }
  const cutoff = dayjs.utc(current.observed_at).subtract(result.hours, "hour");
  const byTime = new Map<string, PremiumIndexPoint>();
  for (const point of result.points) {
    if (dayjs.utc(point.bucket_at).isAfter(cutoff)) {
      const bucketAt = premiumBucketAt(point.bucket_at, result.interval_minutes);
      byTime.set(bucketAt, { ...point, bucket_at: bucketAt });
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

function PremiumIndexChart({
  result,
  autoRefresh,
  samplingIntervalSeconds
}: {
  result: PremiumIndexQueryResult | null;
  autoRefresh: boolean;
  samplingIntervalSeconds: number;
}) {
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
          {result.current?.premium_pct != null ? (
            <Tag color={result.current.premium_pct >= 0 ? "red" : "green"}>
              实时P {signedBp(result.current.premium_pct)}
            </Tag>
          ) : null}
          <Tag color={autoRefresh ? "processing" : undefined}>
            {autoRefresh ? `${samplingIntervalSeconds}s 实时采样` : "手动刷新"}
          </Tag>
        </div>
        <Typography.Text type="secondary">
          实时 {fullTime(result.current?.observed_at ?? result.observed_at)} · {premiumSourceLabel(result.current?.source)}
        </Typography.Text>
      </div>
    </div>
  );
}

function MetricCard({ label, value, sub, tone = "neutral", accent }: {
  label: string;
  value: string;
  sub: ReactNode;
  tone?: "positive" | "negative" | "neutral";
  accent?: "live";
}) {
  return (
    <div className={`premium-metric-card premium-metric-${tone}${accent ? ` premium-metric-${accent}` : ""}`}>
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
  const exchangeLabel = displayExchangeName(estimate.exchange);
  const limitText = estimate.limitPct !== null
    ? `当前交易所返回值疑似触及 ±${estimate.limitPct.toFixed(4)}% 上下限`
    : estimate.lowerFundingLimitPct !== null || estimate.upperFundingLimitPct !== null
      ? `${exchangeLabel} 返回上下限 ${signedPct(estimate.lowerFundingLimitPct)} / ${signedPct(estimate.upperFundingLimitPct)}`
      : estimate.limitNote;
  const thresholdText = estimate.lowerLimitAveragePremiumPct !== null || estimate.upperLimitAveragePremiumPct !== null
    ? `触及下限约需平均P ≤ ${signedPct(estimate.lowerLimitAveragePremiumPct)}，触及上限约需平均P ≥ ${signedPct(estimate.upperLimitAveragePremiumPct)}`
    : "未按精确上下限截断";
  const reachLimitOperator = estimate.targetLimitSide === "lower" ? "≤" : "≥";
  const reachLimitName = estimate.targetLimitSide === "lower" ? "下限" : "上限";
  const reachLimitText = estimate.futureAveragePremiumToReachLimitPct !== null
    ? `要在本次结算达到或维持资金费${reachLimitName} ${signedPct(estimate.targetFundingLimitPct)}，剩余 ${compactHours(estimate.remainingHours)} 的平均P需 ${reachLimitOperator} ${signedPct(estimate.futureAveragePremiumToReachLimitPct)}；目标最终平均P ${signedPct(estimate.targetAveragePremiumPct)}`
    : "缺少资金费率上下限、本周期样本或剩余时间，暂时无法反推拉满所需P";

  return (
    <section className="premium-detail-card">
      <div className="premium-detail-head">
        <Typography.Title level={5}>{estimate.title}</Typography.Title>
        <Tag color="orange">近似</Tag>
      </div>
      <Typography.Paragraph type="secondary">
        {estimate.formulaNote}
        {estimate.basisNote}
        最终以交易所返回资金费为准。
      </Typography.Paragraph>
      <div className="premium-metric-grid">
        <MetricCard
          label="估算资金周期"
          value={`${estimate.intervalHours}h`}
          sub={`${estimate.intervalSource} · 样本 ${estimate.sampledPoints} 点 · ${time(estimate.sampledFrom)} - ${time(estimate.sampledTo)}`}
        />
        <MetricCard
          label="本周期平均P"
          value={signedPct(estimate.averagePremiumPct)}
          sub={`递增权重 · 已过 ${compactHours(estimate.elapsedHours)} · 剩余 ${compactHours(estimate.remainingHours)}`}
          tone={typeof estimate.averagePremiumPct === "number" ? (estimate.averagePremiumPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="剩余P假设"
          value={signedPct(estimate.remainingPremiumAssumptionPct)}
          sub={`近${estimate.remainingPremiumWindowMinutes}分钟 ${estimate.remainingPremiumSampleCount}点均值 · 当前P ${signedPct(estimate.currentPremiumPct)}`}
          tone={typeof estimate.remainingPremiumAssumptionPct === "number" ? (estimate.remainingPremiumAssumptionPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="预计结算资金费"
          value={signedPct(estimate.projectedFundingIfCurrentPremiumPersistsPct)}
          sub={`预计结算平均P ${signedPct(estimate.projectedAveragePremiumPct)} · 剩余P按稳定均值加权`}
          tone={typeof estimate.projectedFundingIfCurrentPremiumPersistsPct === "number" ? (
            estimate.projectedFundingIfCurrentPremiumPersistsPct >= 0 ? "positive" : "negative"
          ) : "neutral"}
        />
        <MetricCard
          label="当前交易所资金费"
          value={signedPct(estimate.exchangeFundingPct)}
          sub={limitText}
          tone={typeof estimate.exchangeFundingPct === "number" ? (estimate.exchangeFundingPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="截至当前累计估算"
          value={signedPct(estimate.estimatedFundingPct)}
          sub={`只看已发生平均P，非结算预测 · ${thresholdText}`}
          tone={typeof estimate.estimatedFundingPct === "number" ? (estimate.estimatedFundingPct >= 0 ? "positive" : "negative") : "neutral"}
        />
        <MetricCard
          label="剩余拉满所需P"
          value={signedPct(estimate.futureAveragePremiumToReachLimitPct)}
          sub={reachLimitText}
          tone={typeof estimate.futureAveragePremiumToReachLimitPct === "number" ? (
            estimate.futureAveragePremiumToReachLimitPct >= 0 ? "positive" : "negative"
          ) : "neutral"}
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
  const [samplingIntervalSeconds, setSamplingIntervalSeconds] = useState(loadSamplingIntervalSeconds);
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
    storeSamplingIntervalSeconds(samplingIntervalSeconds);
  }, [samplingIntervalSeconds]);

  useEffect(() => {
    if (!autoRefresh || !result) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      if (!loading && !refreshing) {
        void refreshCurrent();
      }
    }, samplingIntervalSeconds * 1_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loading, refreshCurrent, refreshing, result, samplingIntervalSeconds]);

  const saveCurrentPreset = async () => {
    try {
      const values = normalizePremiumForm(await form.validateFields());
      const preset: SavedPremiumIndexPreset = {
        ...values,
        id: presetId(values),
        hours: clampHours(hours),
        intervalMinutes,
        samplingIntervalSeconds,
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
    setSamplingIntervalSeconds(normalizeSamplingIntervalSeconds(preset.samplingIntervalSeconds));
    void runQuery({ values: preset, hours: preset.hours, intervalMinutes: preset.intervalMinutes });
  };

  const current = result?.current ?? null;
  const currentPremium = current?.premium_pct ?? result?.premium_pct.current ?? null;
  const currentPremiumTone = typeof currentPremium === "number"
    ? currentPremium >= 0 ? "positive" : "negative"
    : "neutral";
  const currentPremiumSub = current ? (
    <>
      <span>当前溢价指数</span>
      <span className="premium-metric-sub-detail"> · {premiumSourceLabel(current.source)} · {fullTime(current.observed_at)}</span>
    </>
  ) : "等待实时采样";
  const fundingFollowEstimate = useMemo(
    () => buildFundingFollowEstimate(result, current, currentPremium),
    [current, currentPremium, result]
  );
  const weightedPremiumPct = fundingFollowEstimate?.averagePremiumPct ?? null;
  const weightedPremiumTone = typeof weightedPremiumPct === "number"
    ? weightedPremiumPct >= 0 ? "positive" : "negative"
    : "neutral";
  const requiredPremiumPct = fundingFollowEstimate?.futureAveragePremiumToReachLimitPct ?? null;
  const requiredPremiumTone = typeof requiredPremiumPct === "number"
    ? requiredPremiumPct >= 0 ? "positive" : "negative"
    : "neutral";
  const requiredPremiumOperator = fundingFollowEstimate?.targetLimitSide === "lower" ? "≤" : "≥";
  const requiredPremiumLimitName = fundingFollowEstimate?.targetLimitSide === "lower" ? "下限" : "上限";
  const requiredPremiumSub = fundingFollowEstimate && requiredPremiumPct !== null
    ? `${requiredPremiumOperator} 该值 · 拉满${requiredPremiumLimitName} ${signedPct(fundingFollowEstimate.targetFundingLimitPct)} · 剩余 ${compactHours(fundingFollowEstimate.remainingHours)}`
    : "缺少上下限、本周期样本或剩余时间";
  const premiumDefinitionMessage = current
    ? fundingFollowEstimate
      ? "实时溢价指数来自当前快照并随自动采样刷新；本周期加权溢价指数只使用当前资金周期内的 Premium Index，并按时间递增权重计算。剩余拉满所需溢价指数表示，为使最终资金费率达到当前方向的上限或下限，剩余时间内 P 必须维持的加权平均水平。触及上下限不代表交易所一定调整结算周期。"
      : "实时溢价指数来自当前快照并随自动采样刷新；当前交易所未提供完整的资金周期和上下限数据，暂时不能精确反推剩余拉满所需溢价指数。"
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
              <Select
                aria-label="实时采样间隔"
                value={samplingIntervalSeconds}
                options={samplingIntervalOptions}
                disabled={!autoRefresh}
                onChange={setSamplingIntervalSeconds}
              />
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
                    {durationLabel(preset.hours)} · {preset.intervalMinutes}m 历史 · {preset.samplingIntervalSeconds}s 采样
                  </span>
                </Tag>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="premium-metric-grid">
        <MetricCard
          label="实时溢价指数"
          value={signedPct(currentPremium)}
          sub={currentPremiumSub}
          tone={currentPremiumTone}
          accent="live"
        />
        <MetricCard
          label="本周期加权溢价指数"
          value={signedPct(weightedPremiumPct)}
          sub={fundingFollowEstimate
            ? `递增权重 · ${fundingFollowEstimate.sampledPoints}点 · 已过 ${compactHours(fundingFollowEstimate.elapsedHours)}`
            : "等待资金周期样本"}
          tone={weightedPremiumTone}
        />
        <MetricCard label="标记价" value={price(current?.mark_price)} sub="mark price" />
        <MetricCard label="指数价" value={price(current?.index_price)} sub="index price" />
        <MetricCard
          label="剩余拉满所需溢价指数"
          value={signedPct(requiredPremiumPct)}
          sub={requiredPremiumSub}
          tone={requiredPremiumTone}
        />
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

      <PremiumIndexChart
        result={result}
        autoRefresh={autoRefresh}
        samplingIntervalSeconds={samplingIntervalSeconds}
      />

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
