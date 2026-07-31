import {
  DeleteOutlined,
  LineChartOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Segmented,
  Select,
  Switch,
  Table,
  Tag,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import {
  getCurrentPremiumIndex,
  getPairSpreadFundingRecordStatus,
  queryPairSpread,
  queryPremiumIndex,
  startPairSpreadFundingRecord,
  stopPairSpreadFundingRecord
} from "../api/client";
import type {
  MarketType,
  PairSpreadFundingRecordRequest,
  PairSpreadFundingRecordStatus,
  PairSpreadFundingPoint,
  PairSpreadLegQuery,
  PairSpreadPoint,
  PairSpreadPriceField,
  PairSpreadQueryResult,
  PairSpreadRealtimeFundingPoint,
  PremiumIndexCurrentSnapshot,
  PremiumIndexPoint,
  PremiumIndexQueryResult
} from "../api/types";

dayjs.extend(utc);

type PairSpreadFormValues = {
  leg1_exchange: string;
  leg1_market_type: MarketType;
  leg1_symbol: string;
  leg2_exchange: string;
  leg2_market_type: MarketType;
  leg2_symbol: string;
  leg2_multiplier: number;
};

type LegacyPairSpreadFormValues = Omit<PairSpreadFormValues, "leg1_market_type" | "leg2_market_type"> &
  Partial<Pick<PairSpreadFormValues, "leg1_market_type" | "leg2_market_type">>;

type SavedPairSpreadPreset = PairSpreadFormValues & {
  id: string;
  hours: number;
  intervalSeconds: number;
  showDayCompare: boolean;
  dayCompareDays: number;
  dayCompareMode: DayCompareWindowMode;
  dayCompareStartTime: string;
  dayCompareEndTime: string;
  savedAt: string;
};

type LegacySavedPairSpreadPreset = LegacyPairSpreadFormValues & {
  id: string;
  hours: number;
  intervalMinutes?: number;
  intervalSeconds?: number;
  showDayCompare?: boolean;
  dayCompareDays?: number;
  dayCompareMode?: string;
  dayCompareStartTime?: string;
  dayCompareEndTime?: string;
  savedAt: string;
};

type PairPremiumCompareResult = {
  left: PremiumIndexQueryResult | null;
  right: PremiumIndexQueryResult | null;
  warnings: string[];
};

type PairDayCompareSeries = {
  offsetDays: number;
  label: string;
  points: PairSpreadPoint[];
  warnings: string[];
  start_at: string;
  end_at: string;
};

type PairPriceChartMode = "auto" | "raw" | "indexed";
type DayCompareWindowMode = "query" | "custom";
type DayCompareSettings = {
  mode: DayCompareWindowMode;
  startTime: string;
  endTime: string;
};
type DayCompareWindowPlan = {
  mode: DayCompareWindowMode;
  baseStart: ReturnType<typeof dayjs>;
  baseEnd: ReturnType<typeof dayjs>;
  durationHours: number;
  queryHours: number;
  intervalSeconds: number;
  useCurrentResult: boolean;
  rangeLabel: string;
};

type LastPairSpreadState = {
  values: PairSpreadFormValues;
  hours: number;
  intervalSeconds: number;
  showDayCompare: boolean;
  dayCompareDays: number;
  dayCompareMode: DayCompareWindowMode;
  dayCompareStartTime: string;
  dayCompareEndTime: string;
  result: PairSpreadQueryResult;
  savedAt: string;
};

type FundingRateDiffRow = {
  funding_time: string;
  left_rate_pct: number | null;
  right_rate_pct: number | null;
  net_rate_pct: number | null;
  source: "history" | "current" | "realtime" | "minute_record";
};

const defaultFormValues: PairSpreadFormValues = {
  leg1_exchange: "bitget",
  leg1_market_type: "future",
  leg1_symbol: "SKHY",
  leg2_exchange: "bitget",
  leg2_market_type: "future",
  leg2_symbol: "SKHYNIX",
  leg2_multiplier: 1
};

const exchangeLabels: Record<string, string> = {
  binance: "Binance",
  binance_alpha: "Binance Alpha",
  okx: "OKX",
  bybit: "Bybit",
  gate: "Gate",
  bitget: "Bitget",
  aster: "Aster",
  hyperliquid: "Hyperliquid"
};

const exchangeOptions = [
  "binance",
  "binance_alpha",
  "okx",
  "bybit",
  "gate",
  "bitget",
  "aster",
  "hyperliquid"
].map((value) => ({ label: exchangeLabels[value] ?? value, value }));

const intervalOptions = [
  { label: "5秒", value: 5 },
  { label: "10秒", value: 10 },
  { label: "30秒", value: 30 },
  { label: "1分钟", value: 60 },
  { label: "5分钟", value: 300 },
  { label: "15分钟", value: 900 }
];

const CUSTOM_INTERVAL_VALUE = -1;
const DEFAULT_PAIR_INTERVAL_SECONDS = 5;

const marketTypeOptions: Array<{ label: string; value: MarketType }> = [
  { label: "合约", value: "future" },
  { label: "现货", value: "spot" }
];

const premiumIndexExchanges = new Set(["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"]);

const LAST_PAIR_SPREAD_STATE_KEY = "taoli1.pairSpread.lastState.v1";
const PAIR_SPREAD_PRESETS_KEY = "taoli1.pairSpread.presets.v1";
const MAX_SAVED_PAIR_PRESETS = 24;
const DEFAULT_DAY_COMPARE_DAYS = 3;
const MAX_DAY_COMPARE_DAYS = 7;
const DEFAULT_DAY_COMPARE_SETTINGS: DayCompareSettings = {
  mode: "query",
  startTime: "",
  endTime: ""
};
const DAY_COMPARE_CLOCK_PATTERN = /^([01]\d|2[0-3]):([0-5]\d)$/;

const priceFieldLabels: Record<PairSpreadPriceField, string> = {
  mark_price: "标记价",
  mid_price: "盘口中价",
  index_price: "指数价",
  last_price: "最新价"
};

function signedPct(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function finiteRate(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function fundingRateTone(value: number | null | undefined): "positive" | "negative" | "neutral" {
  const rate = finiteRate(value);
  if (rate === null || Math.abs(rate) < 0.000_000_1) {
    return "neutral";
  }
  return rate > 0 ? "positive" : "negative";
}

function signedBp(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const bp = Math.round(value * 100);
  return `${bp >= 0 ? "+" : ""}${bp}bp`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const abs = Math.abs(value);
  if (abs >= 1000) {
    return value.toFixed(2);
  }
  if (abs >= 100) {
    return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (abs >= 1) {
    return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(6);
}

function compactNumber(value: number | null | undefined, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
}

function clampHours(value: number | null): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 1;
  }
  return Math.min(720, Math.max(1, Math.round(value)));
}

function clampIntervalSeconds(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_PAIR_INTERVAL_SECONDS;
  }
  return Math.min(86_400, Math.max(5, Math.round(value)));
}

function clampDayCompareDays(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_DAY_COMPARE_DAYS;
  }
  return Math.min(MAX_DAY_COMPARE_DAYS, Math.max(2, Math.round(value)));
}

function normalizeDayCompareMode(value: unknown): DayCompareWindowMode {
  return value === "custom" ? "custom" : "query";
}

function normalizeClockTime(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  const normalized = value.trim();
  return DAY_COMPARE_CLOCK_PATTERN.test(normalized) ? normalized : "";
}

function normalizeDayCompareSettings(value: {
  mode?: unknown;
  startTime?: unknown;
  endTime?: unknown;
}): DayCompareSettings {
  return {
    mode: normalizeDayCompareMode(value.mode),
    startTime: normalizeClockTime(value.startTime),
    endTime: normalizeClockTime(value.endTime)
  };
}

function dayCompareSettingsLabel(settings: DayCompareSettings): string {
  return settings.mode === "custom" && settings.startTime && settings.endTime
    ? ` · ${settings.startTime}-${settings.endTime}`
    : "";
}

function intervalSecondsFromLegacy(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_PAIR_INTERVAL_SECONDS;
  }
  return clampIntervalSeconds(value * 60);
}

function intervalLabel(seconds: number): string {
  const normalized = clampIntervalSeconds(seconds);
  if (normalized % 60 === 0) {
    const minutes = normalized / 60;
    return `${minutes}分钟`;
  }
  return `${normalized}秒`;
}

function intervalMinutesParam(seconds: number): number {
  const normalized = clampIntervalSeconds(seconds);
  if (normalized === 300) {
    return 5;
  }
  if (normalized === 900) {
    return 15;
  }
  return 1;
}

function historicalCompareIntervalSeconds(seconds: number): number {
  const normalized = clampIntervalSeconds(seconds);
  // 历史K线最小周期是1分钟，秒级主图做多日同时段对比时自动降到1分钟。
  if (normalized <= 60) {
    return 60;
  }
  if (normalized <= 300) {
    return 300;
  }
  return 900;
}

function intervalSelectValue(seconds: number): number {
  const normalized = clampIntervalSeconds(seconds);
  return intervalOptions.some((option) => option.value === normalized)
    ? normalized
    : CUSTOM_INTERVAL_VALUE;
}

function normalizeMarketType(value: unknown): MarketType {
  return value === "spot" ? "spot" : "future";
}

function normalizeAlphaSymbol(value: string): string {
  let normalized = value.trim().toUpperCase().replace(/\//g, "").replace(/-/g, "_");
  if (normalized.endsWith("_USDT")) {
    normalized = `${normalized.slice(0, -5)}USDT`;
  }
  if (/^\d+$/.test(normalized)) {
    normalized = `ALPHA_${normalized}`;
  }
  if (normalized.startsWith("ALPHA") && !normalized.startsWith("ALPHA_")) {
    normalized = `ALPHA_${normalized.slice("ALPHA".length)}`;
  }
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

function normalizeFormSymbol(exchange: string, symbol: string): string {
  return exchange === "binance_alpha" ? normalizeAlphaSymbol(symbol) : symbol.trim().toUpperCase();
}

function shortSavedSymbol(symbol: string): string {
  return symbol.trim().toUpperCase().replace(/(?:USDT|USDC|USD)$/i, "");
}

function marketTypeText(value: MarketType | null | undefined): string {
  return value === "spot" ? "现货" : "合约";
}

function marketTypeShortText(value: MarketType | null | undefined): string {
  return value === "spot" ? "现" : "合";
}

function legDisplay(exchange: string, marketType: MarketType | null | undefined, symbol: string, suffix = ""): string {
  return `${exchangeLabels[exchange] ?? exchange} · ${marketTypeText(marketType)} · ${symbol}${suffix}`;
}

function normalizePairForm(values: LegacyPairSpreadFormValues): PairSpreadFormValues {
  const leg1Exchange = values.leg1_exchange.trim().toLowerCase();
  const leg2Exchange = values.leg2_exchange.trim().toLowerCase();
  return {
    leg1_exchange: leg1Exchange,
    leg1_market_type: normalizeMarketType(values.leg1_market_type),
    leg1_symbol: normalizeFormSymbol(leg1Exchange, values.leg1_symbol),
    leg2_exchange: leg2Exchange,
    leg2_market_type: normalizeMarketType(values.leg2_market_type),
    leg2_symbol: normalizeFormSymbol(leg2Exchange, values.leg2_symbol),
    leg2_multiplier: Number(values.leg2_multiplier)
  };
}

function pairQueryFromUrl(): { values: PairSpreadFormValues; hours: number; intervalSeconds: number } | null {
  if (typeof window === "undefined") {
    return null;
  }
  const params = new URLSearchParams(window.location.search);
  const leg1Exchange = params.get("leg1_exchange");
  const leg1Symbol = params.get("leg1_symbol");
  const leg2Exchange = params.get("leg2_exchange");
  const leg2Symbol = params.get("leg2_symbol");
  if (!leg1Exchange || !leg1Symbol || !leg2Exchange || !leg2Symbol) {
    return null;
  }
  const rawIntervalSeconds = Number(params.get("interval_seconds") ?? NaN);
  const rawIntervalMinutes = Number(params.get("interval_minutes") ?? NaN);
  const intervalSeconds = Number.isFinite(rawIntervalSeconds)
    ? clampIntervalSeconds(rawIntervalSeconds)
    : intervalSecondsFromLegacy(rawIntervalMinutes);
  const multiplier = Number(params.get("leg2_multiplier") ?? 1);
  return {
    values: normalizePairForm({
      leg1_exchange: leg1Exchange,
      leg1_market_type: normalizeMarketType(params.get("leg1_market_type")),
      leg1_symbol: leg1Symbol,
      leg2_exchange: leg2Exchange,
      leg2_market_type: normalizeMarketType(params.get("leg2_market_type")),
      leg2_symbol: leg2Symbol,
      leg2_multiplier: Number.isFinite(multiplier) && multiplier > 0 ? multiplier : 1
    }),
    hours: clampHours(Number(params.get("hours") ?? 4)),
    intervalSeconds
  };
}

function pairPresetId(values: PairSpreadFormValues): string {
  const normalized = normalizePairForm(values);
  return [
    normalized.leg1_exchange,
    normalized.leg1_market_type,
    normalized.leg1_symbol,
    normalized.leg2_exchange,
    normalized.leg2_market_type,
    normalized.leg2_symbol,
    compactNumber(normalized.leg2_multiplier, 8)
  ].join("|");
}

function pairQueryKey(values: PairSpreadFormValues, hours: number, intervalSeconds: number): string {
  return `${pairPresetId(values)}|${clampHours(hours)}|${clampIntervalSeconds(intervalSeconds)}`;
}

function pairFormFromResult(result: PairSpreadQueryResult): PairSpreadFormValues {
  return {
    leg1_exchange: result.leg1.exchange,
    leg1_market_type: result.leg1.market_type,
    leg1_symbol: result.leg1.symbol,
    leg2_exchange: result.leg2.exchange,
    leg2_market_type: result.leg2.market_type,
    leg2_symbol: result.leg2.symbol,
    leg2_multiplier: result.leg2_multiplier
  };
}

function applyPairQueryParams(
  url: URL,
  values: PairSpreadFormValues,
  hours: number,
  intervalSeconds: number
): void {
  url.searchParams.set("leg1_exchange", values.leg1_exchange);
  url.searchParams.set("leg1_market_type", values.leg1_market_type);
  url.searchParams.set("leg1_symbol", values.leg1_symbol);
  url.searchParams.set("leg2_exchange", values.leg2_exchange);
  url.searchParams.set("leg2_market_type", values.leg2_market_type);
  url.searchParams.set("leg2_symbol", values.leg2_symbol);
  url.searchParams.set("leg2_multiplier", compactNumber(values.leg2_multiplier, 8));
  url.searchParams.set("hours", String(clampHours(hours)));
  url.searchParams.set("interval_seconds", String(clampIntervalSeconds(intervalSeconds)));
  url.searchParams.set("interval_minutes", String(intervalMinutesParam(intervalSeconds)));
}

function replacePairQueryInUrl(values: PairSpreadFormValues, hours: number, intervalSeconds: number): void {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  const currentPage = url.searchParams.get("page");
  if (currentPage && currentPage !== "pair-monitor") {
    return;
  }
  url.searchParams.set("page", "pair-monitor");
  url.searchParams.delete("exchange");
  url.searchParams.delete("symbol");
  url.searchParams.delete("from");
  applyPairQueryParams(url, values, hours, intervalSeconds);
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function isPairSpreadLegQuery(value: unknown): value is PairSpreadLegQuery {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Partial<PairSpreadLegQuery>;
  return (
    typeof item.exchange === "string" &&
    typeof item.symbol === "string" &&
    (item.market_type === "future" || item.market_type === "spot")
  );
}

function isPairSpreadQueryResult(value: unknown): value is PairSpreadQueryResult {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Partial<PairSpreadQueryResult>;
  return (
    isPairSpreadLegQuery(item.leg1) &&
    isPairSpreadLegQuery(item.leg2) &&
    typeof item.hours === "number" &&
    typeof item.interval_minutes === "number" &&
    (item.interval_seconds === undefined || typeof item.interval_seconds === "number") &&
    typeof item.leg2_multiplier === "number" &&
    Array.isArray(item.points) &&
    Array.isArray(item.funding_history) &&
    (item.realtime_funding === undefined || Array.isArray(item.realtime_funding)) &&
    Array.isArray(item.warnings)
  );
}

function isLastPairSpreadState(value: unknown): value is LastPairSpreadState {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Partial<LastPairSpreadState>;
  return (
    typeof item.savedAt === "string" &&
    typeof item.hours === "number" &&
    (typeof item.intervalSeconds === "number" || typeof (item as { intervalMinutes?: unknown }).intervalMinutes === "number") &&
    Boolean(item.values) &&
    isPairSpreadQueryResult(item.result)
  );
}

function loadLastPairSpreadState(): LastPairSpreadState | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(LAST_PAIR_SPREAD_STATE_KEY) ?? "null");
    if (!isLastPairSpreadState(parsed)) {
      return null;
    }
    const values = normalizePairForm(parsed.values);
    const intervalSeconds =
      typeof parsed.intervalSeconds === "number"
        ? clampIntervalSeconds(parsed.intervalSeconds)
        : intervalSecondsFromLegacy((parsed as { intervalMinutes?: unknown }).intervalMinutes);
    const result = {
      ...parsed.result,
      interval_seconds: parsed.result.interval_seconds ?? intervalSeconds
    };
    const dayCompareSettings = normalizeDayCompareSettings({
      mode: (parsed as { dayCompareMode?: unknown }).dayCompareMode,
      startTime: (parsed as { dayCompareStartTime?: unknown }).dayCompareStartTime,
      endTime: (parsed as { dayCompareEndTime?: unknown }).dayCompareEndTime
    });
    return {
      values,
      hours: clampHours(parsed.hours),
      intervalSeconds,
      showDayCompare: Boolean((parsed as { showDayCompare?: unknown }).showDayCompare),
      dayCompareDays: clampDayCompareDays((parsed as { dayCompareDays?: number }).dayCompareDays),
      dayCompareMode: dayCompareSettings.mode,
      dayCompareStartTime: dayCompareSettings.startTime,
      dayCompareEndTime: dayCompareSettings.endTime,
      result,
      savedAt: parsed.savedAt
    };
  } catch {
    return null;
  }
}

function storeLastPairSpreadState(
  values: PairSpreadFormValues,
  hours: number,
  intervalSeconds: number,
  result: PairSpreadQueryResult,
  showDayCompare = false,
  dayCompareDays = DEFAULT_DAY_COMPARE_DAYS,
  dayCompareSettings = DEFAULT_DAY_COMPARE_SETTINGS
): void {
  if (typeof window === "undefined") {
    return;
  }
  const normalizedDayCompareSettings = normalizeDayCompareSettings(dayCompareSettings);
  const state: LastPairSpreadState = {
    values,
    hours: clampHours(hours),
    intervalSeconds: clampIntervalSeconds(intervalSeconds),
    showDayCompare,
    dayCompareDays: clampDayCompareDays(dayCompareDays),
    dayCompareMode: normalizedDayCompareSettings.mode,
    dayCompareStartTime: normalizedDayCompareSettings.startTime,
    dayCompareEndTime: normalizedDayCompareSettings.endTime,
    result,
    savedAt: new Date().toISOString()
  };
  try {
    window.sessionStorage.setItem(LAST_PAIR_SPREAD_STATE_KEY, JSON.stringify(state));
  } catch {
    window.sessionStorage.removeItem(LAST_PAIR_SPREAD_STATE_KEY);
  }
}

function isSavedPreset(value: unknown): value is LegacySavedPairSpreadPreset {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Partial<LegacySavedPairSpreadPreset>;
  const hasValidMarketTypes =
    (item.leg1_market_type === undefined || item.leg1_market_type === "future" || item.leg1_market_type === "spot") &&
    (item.leg2_market_type === undefined || item.leg2_market_type === "future" || item.leg2_market_type === "spot");
  return (
    typeof item.id === "string" &&
    typeof item.leg1_exchange === "string" &&
    typeof item.leg1_symbol === "string" &&
    typeof item.leg2_exchange === "string" &&
    typeof item.leg2_symbol === "string" &&
    typeof item.leg2_multiplier === "number" &&
    typeof item.hours === "number" &&
    (typeof item.intervalSeconds === "number" || typeof item.intervalMinutes === "number") &&
    typeof item.savedAt === "string" &&
    (item.showDayCompare === undefined || typeof item.showDayCompare === "boolean") &&
    (item.dayCompareDays === undefined || typeof item.dayCompareDays === "number") &&
    (item.dayCompareMode === undefined || typeof item.dayCompareMode === "string") &&
    (item.dayCompareStartTime === undefined || typeof item.dayCompareStartTime === "string") &&
    (item.dayCompareEndTime === undefined || typeof item.dayCompareEndTime === "string") &&
    hasValidMarketTypes
  );
}

function normalizeSavedPreset(preset: LegacySavedPairSpreadPreset): SavedPairSpreadPreset {
  const values = normalizePairForm(preset);
  const dayCompareSettings = normalizeDayCompareSettings({
    mode: preset.dayCompareMode,
    startTime: preset.dayCompareStartTime,
    endTime: preset.dayCompareEndTime
  });
  return {
    ...preset,
    ...values,
    id: pairPresetId(values),
    hours: clampHours(preset.hours),
    intervalSeconds:
      typeof preset.intervalSeconds === "number"
        ? clampIntervalSeconds(preset.intervalSeconds)
        : intervalSecondsFromLegacy(preset.intervalMinutes),
    showDayCompare: Boolean(preset.showDayCompare),
    dayCompareDays: clampDayCompareDays(preset.dayCompareDays),
    dayCompareMode: dayCompareSettings.mode,
    dayCompareStartTime: dayCompareSettings.startTime,
    dayCompareEndTime: dayCompareSettings.endTime,
    savedAt: preset.savedAt
  };
}

function loadSavedPairPresets(): SavedPairSpreadPreset[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PAIR_SPREAD_PRESETS_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter(isSavedPreset).map(normalizeSavedPreset).slice(0, MAX_SAVED_PAIR_PRESETS)
      : [];
  } catch {
    return [];
  }
}

function storeSavedPairPresets(presets: SavedPairSpreadPreset[]): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PAIR_SPREAD_PRESETS_KEY, JSON.stringify(presets.slice(0, MAX_SAVED_PAIR_PRESETS)));
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm") : "-";
}

function fullTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function chartTime(value: string | null | undefined, spanHours: number): string {
  if (!value) {
    return "-";
  }
  const parsed = dayjs.utc(value).utcOffset(8);
  if (spanHours <= 1 && parsed.second() !== 0) {
    return parsed.format("HH:mm:ss");
  }
  return spanHours <= 24 ? parsed.format("HH:mm") : parsed.format("MM-DD HH:mm");
}

function durationLabel(hours: number): string {
  if (hours < 24) {
    return `${compactNumber(hours, hours % 1 === 0 ? 0 : 1)}小时`;
  }
  const days = hours / 24;
  return `${compactNumber(days, days % 1 === 0 ? 0 : 1)}天`;
}

function dataRangeLabel(result: PairSpreadQueryResult | null, fallbackHours: number): string {
  if (!result?.first_seen_at || !result.last_seen_at) {
    return durationLabel(fallbackHours);
  }
  return `${time(result.first_seen_at)} - ${time(result.last_seen_at)}`;
}

function rightLegLabel(result: PairSpreadQueryResult | null): string {
  if (!result) {
    return "右标的";
  }
  const divisor = result.leg2_multiplier === 1 ? "" : `/${compactNumber(result.leg2_multiplier, 4)}`;
  return legDisplay(result.leg2.exchange, result.leg2.market_type, result.leg2.symbol, divisor);
}

function leftLegLabel(result: PairSpreadQueryResult | null): string {
  if (!result) {
    return "左标的";
  }
  return legDisplay(result.leg1.exchange, result.leg1.market_type, result.leg1.symbol);
}

function exchangeToneClass(exchange: string): string {
  const normalized = exchange.trim().toLowerCase().replace(/_/g, "-");
  if (["aster", "gate", "hyperliquid", "bybit", "bitget", "binance", "okx", "binance-alpha"].includes(normalized)) {
    return `pair-exchange-${normalized}`;
  }
  return "pair-exchange-default";
}

function ExchangeChip({
  exchange,
  marketType
}: {
  exchange: string;
  marketType: MarketType;
}) {
  return (
    <span className={`pair-exchange-chip ${exchangeToneClass(exchange)}`}>
      <span>{exchangeLabels[exchange] ?? exchange}</span>
      <span className="pair-exchange-market">{marketTypeShortText(marketType)}</span>
    </span>
  );
}

function SavedPairPresetContent({ preset }: { preset: SavedPairSpreadPreset }) {
  const leftSymbol = shortSavedSymbol(preset.leg1_symbol);
  const rightSymbol = shortSavedSymbol(preset.leg2_symbol);
  const sameSymbol = leftSymbol === rightSymbol;
  const sameVenue = preset.leg1_exchange === preset.leg2_exchange && preset.leg1_market_type === preset.leg2_market_type;
  const multiplierText = preset.leg2_multiplier === 1 ? "" : `右×${compactNumber(preset.leg2_multiplier, 4)}`;
  const dayCompareSettings = normalizeDayCompareSettings({
    mode: preset.dayCompareMode,
    startTime: preset.dayCompareStartTime,
    endTime: preset.dayCompareEndTime
  });
  const compareText = preset.showDayCompare ? `同时段${preset.dayCompareDays}天${dayCompareSettingsLabel(dayCompareSettings)}` : "";
  return (
    <span className="pair-saved-main">
      {sameSymbol ? (
        <>
          <span className="pair-saved-symbol">{leftSymbol}</span>
          <span className="pair-saved-exchanges">
            <ExchangeChip exchange={preset.leg1_exchange} marketType={preset.leg1_market_type} />
            <span className="pair-saved-separator">/</span>
            <ExchangeChip exchange={preset.leg2_exchange} marketType={preset.leg2_market_type} />
          </span>
        </>
      ) : sameVenue ? (
        <>
          <ExchangeChip exchange={preset.leg1_exchange} marketType={preset.leg1_market_type} />
          <span className="pair-saved-symbol">
            {leftSymbol} / {rightSymbol}
          </span>
        </>
      ) : (
        <>
          <ExchangeChip exchange={preset.leg1_exchange} marketType={preset.leg1_market_type} />
          <span className="pair-saved-symbol">{leftSymbol}</span>
          <span className="pair-saved-separator">/</span>
          <ExchangeChip exchange={preset.leg2_exchange} marketType={preset.leg2_market_type} />
          <span className="pair-saved-symbol">{rightSymbol}</span>
        </>
      )}
      {multiplierText ? <span className="pair-saved-badge">{multiplierText}</span> : null}
      {compareText ? <span className="pair-saved-badge">{compareText}</span> : null}
    </span>
  );
}

function resultIntervalSeconds(result: PairSpreadQueryResult): number {
  return clampIntervalSeconds(result.interval_seconds ?? result.interval_minutes * 60);
}

function clockTimeParts(value: string): { hour: number; minute: number } | null {
  const match = DAY_COMPARE_CLOCK_PATTERN.exec(value);
  if (!match) {
    return null;
  }
  return { hour: Number(match[1]), minute: Number(match[2]) };
}

function localDateTimeWithClock(anchor: ReturnType<typeof dayjs>, clockTime: string): ReturnType<typeof dayjs> {
  const parts = clockTimeParts(clockTime);
  if (!parts) {
    throw new Error("同时段开始和结束时间格式不正确。");
  }
  return anchor.hour(parts.hour).minute(parts.minute).second(0).millisecond(0);
}

function filterPairPointsByWindow(
  points: PairSpreadPoint[],
  start: ReturnType<typeof dayjs>,
  end: ReturnType<typeof dayjs>
): PairSpreadPoint[] {
  const startMs = start.valueOf();
  const endMs = end.valueOf();
  return points.filter((point) => {
    const pointMs = dayjs.utc(point.bucket_at).valueOf();
    return Number.isFinite(pointMs) && pointMs >= startMs && pointMs <= endMs;
  });
}

function buildDayCompareWindowPlan(
  pairResult: PairSpreadQueryResult,
  settings: DayCompareSettings
): DayCompareWindowPlan {
  const intervalSeconds = historicalCompareIntervalSeconds(resultIntervalSeconds(pairResult));
  const observedAt = dayjs.utc(pairResult.observed_at);
  if (settings.mode !== "custom") {
    return {
      mode: "query",
      baseStart: observedAt.subtract(pairResult.hours, "hour"),
      baseEnd: observedAt,
      durationHours: pairResult.hours,
      queryHours: pairResult.hours,
      intervalSeconds,
      useCurrentResult: true,
      rangeLabel: "跟随查询"
    };
  }
  if (!settings.startTime || !settings.endTime) {
    throw new Error("请先填写同时段开始时间和结束时间。");
  }

  // 自定义窗口按北京时间解释，然后对每个历史日期平移同一段时间。
  const observedLocal = observedAt.utcOffset(8);
  let baseStart = localDateTimeWithClock(observedLocal, settings.startTime);
  let baseEnd = localDateTimeWithClock(observedLocal, settings.endTime);
  if (!baseEnd.isAfter(baseStart)) {
    baseEnd = baseEnd.add(1, "day");
  }
  while (baseEnd.isAfter(observedLocal)) {
    baseStart = baseStart.subtract(1, "day");
    baseEnd = baseEnd.subtract(1, "day");
  }

  const durationHours = (baseEnd.valueOf() - baseStart.valueOf()) / 3_600_000;
  if (!Number.isFinite(durationHours) || durationHours <= 0) {
    throw new Error("同时段开始时间必须早于结束时间。");
  }
  return {
    mode: "custom",
    baseStart,
    baseEnd,
    durationHours,
    queryHours: clampHours(Math.ceil(durationHours)),
    intervalSeconds,
    useCurrentResult: false,
    rangeLabel: `${settings.startTime}-${settings.endTime}`
  };
}

function shiftedDayCompareWindow(
  plan: DayCompareWindowPlan,
  offsetDays: number
): { start: ReturnType<typeof dayjs>; end: ReturnType<typeof dayjs> } {
  return {
    start: plan.baseStart.subtract(offsetDays, "day"),
    end: plan.baseEnd.subtract(offsetDays, "day")
  };
}

function dayCompareRequestLabel(offsetDays: number, labelDate: string): string {
  return offsetDays === 0 ? `基准 ${labelDate}` : `前${offsetDays}天 ${labelDate}`;
}

function dayCompareTickStepMs(spanHours: number): number {
  const normalizedHours = Math.max(spanHours, 0);
  if (normalizedHours <= 2) {
    return 30 * 60_000;
  }
  if (normalizedHours <= 6) {
    return 60 * 60_000;
  }
  if (normalizedHours <= 24) {
    return 2 * 60 * 60_000;
  }
  if (normalizedHours <= 48) {
    return 4 * 60 * 60_000;
  }
  if (normalizedHours <= 72) {
    return 6 * 60 * 60_000;
  }
  if (normalizedHours <= 168) {
    return 12 * 60 * 60_000;
  }
  if (normalizedHours <= 360) {
    return 24 * 60 * 60_000;
  }
  return 72 * 60 * 60_000;
}

function dayCompareTickTimeLabel(
  baseStart: ReturnType<typeof dayjs>,
  elapsedMs: number,
  spanHours: number
): string {
  const tickTime = baseStart.add(elapsedMs, "millisecond").utcOffset(8);
  if (spanHours < 24) {
    return tickTime.format("HH:mm");
  }
  if (spanHours <= 168) {
    return tickTime.format("MM-DD HH:mm");
  }
  return tickTime.format("MM-DD");
}

function dayCompareTimeTicks(
  baseStart: ReturnType<typeof dayjs>,
  maxElapsedMs: number
): Array<{ elapsedMs: number; label: string }> {
  const spanHours = maxElapsedMs / 3_600_000;
  const stepMs = dayCompareTickStepMs(spanHours);
  const ticks: Array<{ elapsedMs: number; label: string }> = [];
  for (let elapsedMs = 0; elapsedMs < maxElapsedMs; elapsedMs += stepMs) {
    ticks.push({
      elapsedMs,
      label: dayCompareTickTimeLabel(baseStart, elapsedMs, spanHours)
    });
  }
  const lastTick = ticks[ticks.length - 1];
  if (!lastTick || Math.abs(lastTick.elapsedMs - maxElapsedMs) > 60_000) {
    ticks.push({
      elapsedMs: maxElapsedMs,
      label: dayCompareTickTimeLabel(baseStart, maxElapsedMs, spanHours)
    });
  }
  return ticks;
}

function supportsPremiumCompare(result: PairSpreadQueryResult): boolean {
  return result.leg1.market_type === "future" && result.leg2.market_type === "future";
}

function supportsFundingRecord(result: PairSpreadQueryResult | null): result is PairSpreadQueryResult {
  return Boolean(result && result.leg1.market_type === "future" && result.leg2.market_type === "future");
}

function fundingRecordRequestFromResult(result: PairSpreadQueryResult | null): PairSpreadFundingRecordRequest | null {
  if (!supportsFundingRecord(result)) {
    return null;
  }
  return {
    leg1: result.leg1,
    leg2: result.leg2,
    leg2_multiplier: result.leg2_multiplier
  };
}

function fundingRecordStatusQuery(result: PairSpreadQueryResult) {
  return {
    leg1_exchange: result.leg1.exchange,
    leg1_market_type: result.leg1.market_type,
    leg1_symbol: result.leg1.symbol,
    leg2_exchange: result.leg2.exchange,
    leg2_market_type: result.leg2.market_type,
    leg2_symbol: result.leg2.symbol,
    leg2_multiplier: result.leg2_multiplier,
    hours: result.hours
  };
}

function supportsPremiumIndexLeg(leg: PairSpreadLegQuery | null | undefined): leg is PairSpreadLegQuery {
  return Boolean(leg && leg.market_type === "future" && premiumIndexExchanges.has(leg.exchange));
}

function openPremiumIndexFromLeg(
  leg: PairSpreadLegQuery,
  hours: number,
  intervalSeconds: number,
  pairResult: PairSpreadQueryResult | null
) {
  const url = new URL(window.location.href);
  if (pairResult) {
    applyPairQueryParams(url, pairFormFromResult(pairResult), pairResult.hours, resultIntervalSeconds(pairResult));
  }
  url.searchParams.set("page", "premium-index");
  url.searchParams.set("from", "pair-monitor");
  url.searchParams.set("exchange", leg.exchange);
  url.searchParams.set("symbol", leg.symbol);
  url.searchParams.set("hours", String(clampHours(hours)));
  url.searchParams.set("interval_minutes", String(intervalMinutesParam(intervalSeconds)));
  window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
  window.dispatchEvent(new Event("taoli1:navigate"));
}

function pairCurrentToPoint(result: PairSpreadQueryResult): PairSpreadPoint | null {
  const current = result.current;
  if (!current || !Number.isFinite(current.spread_pct) || !Number.isFinite(current.spread_abs)) {
    return null;
  }
  if (!Number.isFinite(current.leg1.price) || !Number.isFinite(current.leg2.price)) {
    return null;
  }
  return {
    bucket_at: current.observed_at,
    leg1_close: current.leg1.price,
    leg2_close: current.leg2.price,
    spread_abs: current.spread_abs,
    spread_pct: current.spread_pct
  };
}

function samePairSpreadPoint(left: PairSpreadPoint, right: PairSpreadPoint): boolean {
  const closeEnough = (a: number, b: number) => Math.abs(a - b) <= Math.max(Math.abs(a), Math.abs(b), 1) * 1e-10;
  return (
    closeEnough(left.leg1_close, right.leg1_close) &&
    closeEnough(left.leg2_close, right.leg2_close) &&
    closeEnough(left.spread_abs, right.spread_abs) &&
    closeEnough(left.spread_pct, right.spread_pct)
  );
}

function pairDisplayPoints(result: PairSpreadQueryResult | null): PairSpreadPoint[] {
  if (!result) {
    return [];
  }
  const points = result.points.slice();
  const currentPoint = pairCurrentToPoint(result);
  if (!currentPoint) {
    return points;
  }
  if (!points.length) {
    return [currentPoint];
  }

  const currentMs = dayjs.utc(currentPoint.bucket_at).valueOf();
  const last = points[points.length - 1];
  const lastMs = dayjs.utc(last.bucket_at).valueOf();
  if (!Number.isFinite(currentMs) || !Number.isFinite(lastMs) || currentMs < lastMs) {
    return points;
  }
  if (currentMs === lastMs) {
    points[points.length - 1] = currentPoint;
    return points;
  }
  if (samePairSpreadPoint(last, currentPoint)) {
    return points;
  }
  return [...points, currentPoint];
}

function spreadLinePath(
  points: PairSpreadPoint[],
  xAt: (index: number) => number,
  yAt: (value: number) => number
): string {
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(2)} ${yAt(point.spread_pct).toFixed(2)}`)
    .join(" ");
}

function spreadAreaPath(
  points: PairSpreadPoint[],
  xAt: (index: number) => number,
  yAt: (value: number) => number,
  baselineY: number
): string {
  if (!points.length) {
    return "";
  }
  const firstX = xAt(0);
  const lastX = xAt(points.length - 1);
  const line = points
    .map((point, index) => `L ${xAt(index).toFixed(2)} ${yAt(point.spread_pct).toFixed(2)}`)
    .join(" ");
  return `M ${firstX.toFixed(2)} ${baselineY.toFixed(2)} ${line} L ${lastX.toFixed(2)} ${baselineY.toFixed(2)} Z`;
}

function chartSpanHours(points: PairSpreadPoint[]): number {
  if (points.length < 2) {
    return 0;
  }
  const start = dayjs.utc(points[0].bucket_at);
  const end = dayjs.utc(points[points.length - 1].bucket_at);
  return Math.max(end.diff(start, "second") / 3600, 0);
}

type TimeAxisTick = {
  ms: number;
  value: string;
};

function chooseTimeTickStepMs(spanMs: number, maxTicks: number): number | null {
  const minuteMs = 60_000;
  const hourMs = 60 * minuteMs;
  const dayMs = 24 * hourMs;
  const steps =
    spanMs <= hourMs
      ? [5 * minuteMs, 10 * minuteMs, 15 * minuteMs, 30 * minuteMs, hourMs]
      : spanMs <= 12 * hourMs
        ? [hourMs, 2 * hourMs, 3 * hourMs, 4 * hourMs, 6 * hourMs]
        : spanMs <= 24 * hourMs
          ? [2 * hourMs, 3 * hourMs, 4 * hourMs, 6 * hourMs, 8 * hourMs, 12 * hourMs]
          : spanMs <= 72 * hourMs
            ? [4 * hourMs, 6 * hourMs, 8 * hourMs, 12 * hourMs, dayMs]
            : [12 * hourMs, dayMs, 2 * dayMs, 3 * dayMs, 7 * dayMs];

  return steps.find((stepMs) => Math.floor(spanMs / stepMs) + 1 <= maxTicks) ?? null;
}

function chartTimeTicks(startMs: number, endMs: number, maxTicks = 7): TimeAxisTick[] {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    return [];
  }
  const start = Math.min(startMs, endMs);
  const end = Math.max(startMs, endMs);
  if (start === end) {
    return [{ ms: start, value: dayjs.utc(start).toISOString() }];
  }

  const spanMs = end - start;
  const stepMs = chooseTimeTickStepMs(spanMs, Math.max(2, maxTicks));
  if (stepMs) {
    const chinaOffsetMs = 8 * 3_600_000;
    let cursor = Math.ceil((start + chinaOffsetMs) / stepMs) * stepMs - chinaOffsetMs;
    const ticks: TimeAxisTick[] = [];
    while (cursor <= end + 1_000) {
      if (cursor >= start - 1_000) {
        ticks.push({ ms: cursor, value: dayjs.utc(cursor).toISOString() });
      }
      cursor += stepMs;
    }
    if (ticks.length >= 2 && ticks.length <= maxTicks) {
      return ticks;
    }
  }

  const count = Math.max(2, Math.min(maxTicks, Math.ceil(spanMs / 3_600_000) + 1));
  return Array.from({ length: count }, (_, tickIndex) => {
    const tickMs = start + (spanMs * tickIndex) / (count - 1);
    return { ms: tickMs, value: dayjs.utc(tickMs).toISOString() };
  });
}

function nearestPointIndexByTime(points: PairSpreadPoint[], targetMs: number, seen: Set<number>): number | null {
  let bestIndex: number | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  points.forEach((point, index) => {
    if (seen.has(index)) {
      return;
    }
    const pointMs = dayjs.utc(point.bucket_at).valueOf();
    const distance = Math.abs(pointMs - targetMs);
    if (Number.isFinite(distance) && distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function chartTicks(points: PairSpreadPoint[], maxTicks = 7): Array<{ index: number; point: PairSpreadPoint }> {
  if (points.length <= 1) {
    return points.map((point, index) => ({ index, point }));
  }
  const startMs = dayjs.utc(points[0].bucket_at).valueOf();
  const endMs = dayjs.utc(points[points.length - 1].bucket_at).valueOf();
  const timeTicks = chartTimeTicks(startMs, endMs, maxTicks);
  const timeTickSeen = new Set<number>();
  const alignedTicks: Array<{ index: number; point: PairSpreadPoint }> = [];
  for (const tick of timeTicks) {
    const index = nearestPointIndexByTime(points, tick.ms, timeTickSeen);
    if (index !== null) {
      timeTickSeen.add(index);
      alignedTicks.push({ index, point: points[index] });
    }
  }
  if (alignedTicks.length >= Math.min(2, points.length)) {
    return alignedTicks;
  }

  const count = Math.min(maxTicks, points.length);
  const seen = new Set<number>();
  return Array.from({ length: count }, (_, tickIndex) => {
    const index = Math.round((tickIndex * (points.length - 1)) / (count - 1));
    if (seen.has(index)) {
      return null;
    }
    seen.add(index);
    return { index, point: points[index] };
  }).filter((tick): tick is { index: number; point: PairSpreadPoint } => tick !== null);
}

type ChartTurnCandidate = {
  index: number;
  kind: "peak" | "trough";
  score: number;
};

function addChartTurnCandidate(
  selected: ChartTurnCandidate[],
  candidate: ChartTurnCandidate,
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

function chartTurningPoints(points: PairSpreadPoint[], maxLabels = 18): Array<{
  index: number;
  point: PairSpreadPoint;
  kind: "peak" | "trough";
}> {
  if (points.length < 3) {
    return [];
  }

  const values = points.map((point) => point.spread_pct);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || 1;
  const localWindowSize = Math.max(2, Math.floor(points.length / 160));
  const contextWindowSize = Math.max(localWindowSize + 2, Math.floor(points.length / 45));
  const minProminence = Math.max(0.006, span * 0.0035);
  const candidates: ChartTurnCandidate[] = [];
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
      const localScore = turningPointScore(values, turnIndex, kind, localWindowSize);
      const contextScore = turningPointScore(values, turnIndex, kind, contextWindowSize);
      const score = Math.max(localScore * 1.25, contextScore);
      if (score >= minProminence) {
        candidates.push({ index: turnIndex, kind, score });
      }
    }
    previousDirection = direction;
  }

  const minIndex = values.indexOf(minValue);
  const maxIndex = values.indexOf(maxValue);
  candidates.push({ index: maxIndex, kind: "peak", score: span });
  candidates.push({ index: minIndex, kind: "trough", score: span });

  const rankedCandidates = candidates.sort((a, b) => b.score - a.score);
  const minIndexDistance = Math.max(5, Math.floor(points.length / 48));
  const selected: ChartTurnCandidate[] = [];
  const primaryBudget = Math.max(6, Math.floor(maxLabels * 0.48));

  for (const candidate of rankedCandidates) {
    addChartTurnCandidate(selected, candidate, primaryBudget, minIndexDistance);
    if (selected.length >= primaryBudget) {
      break;
    }
  }

  const segmentCount = Math.min(10, Math.max(5, Math.ceil(points.length / 90)));
  const segmentSize = Math.ceil(points.length / segmentCount);
  for (let segmentIndex = 0; segmentIndex < segmentCount; segmentIndex += 1) {
    const start = segmentIndex * segmentSize;
    const end = Math.min(points.length - 1, start + segmentSize - 1);
    if (selected.some((item) => item.index >= start && item.index <= end)) {
      continue;
    }
    const segmentCandidate = rankedCandidates.find(
      (candidate) =>
        candidate.index >= start &&
        candidate.index <= end &&
        selected.every((item) => Math.abs(item.index - candidate.index) >= minIndexDistance)
    );
    if (segmentCandidate) {
      addChartTurnCandidate(selected, segmentCandidate, maxLabels, minIndexDistance);
    }
  }

  while (selected.length < maxLabels) {
    const selectedByIndex = [...selected].sort((a, b) => a.index - b.index);
    const gaps = [
      { start: 0, end: selectedByIndex[0]?.index ?? points.length - 1 },
      ...selectedByIndex.slice(0, -1).map((item, index) => ({
        start: item.index,
        end: selectedByIndex[index + 1].index
      })),
      { start: selectedByIndex[selectedByIndex.length - 1]?.index ?? 0, end: points.length - 1 }
    ]
      .map((gap) => ({ ...gap, size: gap.end - gap.start }))
      .filter((gap) => gap.size >= minIndexDistance * 2)
      .sort((a, b) => b.size - a.size);

    let added = false;
    for (const gap of gaps) {
      const gapCandidate = rankedCandidates.find(
        (candidate) =>
          candidate.index > gap.start &&
          candidate.index < gap.end &&
          selected.every((item) => Math.abs(item.index - candidate.index) >= minIndexDistance)
      );
      if (gapCandidate) {
        added = addChartTurnCandidate(selected, gapCandidate, maxLabels, minIndexDistance);
        if (added) {
          break;
        }
      }
    }
    if (!added) {
      break;
    }
  }

  return selected
    .sort((a, b) => a.index - b.index)
    .map(({ index, kind }) => ({ index, point: points[index], kind }));
}

function turningPointScore(
  values: number[],
  index: number,
  kind: "peak" | "trough",
  windowSize: number
): number {
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

function MetricCard({
  label,
  value,
  sub,
  tone = "neutral",
  action = null
}: {
  label: string;
  value: string;
  sub: ReactNode;
  tone?: "positive" | "negative" | "neutral";
  action?: ReactNode;
}) {
  return (
    <div className={`pair-metric-card pair-metric-${tone}`}>
      <Typography.Text className="pair-metric-label">{label}</Typography.Text>
      <div className="pair-metric-value">{value}</div>
      <Typography.Text className="pair-metric-sub">{sub}</Typography.Text>
      {action ? <div style={{ marginTop: 6 }}>{action}</div> : null}
    </div>
  );
}

function FundingRateValue({ value, strong = false }: { value: number | null | undefined; strong?: boolean }) {
  const tone = fundingRateTone(value);
  return (
    <span className={`pair-funding-rate-value pair-funding-rate-${tone}${strong ? " pair-funding-rate-strong" : ""}`}>
      {signedPct(value, 4)}
    </span>
  );
}

function fundingDiffTurningPoints(rows: FundingRateDiffRow[], maxLabels = 5): Array<{
  index: number;
  row: FundingRateDiffRow;
  kind: "peak" | "trough";
}> {
  const items = rows
    .map((row, index) => ({ row, index, value: finiteRate(row.net_rate_pct) }))
    .filter((item): item is { row: FundingRateDiffRow; index: number; value: number } => item.value !== null);
  if (items.length === 0) {
    return [];
  }
  if (items.length === 1) {
    return [{ index: items[0].index, row: items[0].row, kind: items[0].value >= 0 ? "peak" : "trough" }];
  }

  const values = items.map((item) => item.value);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 0.0001);
  const localWindowSize = Math.max(1, Math.floor(items.length / 18));
  const contextWindowSize = Math.max(localWindowSize + 1, Math.floor(items.length / 7));
  const minProminence = Math.max(0.0001, span * 0.045);
  const candidates: ChartTurnCandidate[] = [];
  let previousDirection = 0;

  for (let index = 1; index < values.length; index += 1) {
    const delta = values[index] - values[index - 1];
    const direction = delta > 0 ? 1 : delta < 0 ? -1 : 0;
    if (direction === 0) {
      continue;
    }
    if (previousDirection !== 0 && direction !== previousDirection) {
      const turnIndex = index - 1;
      const kind = previousDirection > 0 && direction < 0 ? "peak" : "trough";
      const score = Math.max(
        turningPointScore(values, turnIndex, kind, localWindowSize) * 1.2,
        turningPointScore(values, turnIndex, kind, contextWindowSize)
      );
      if (score >= minProminence) {
        candidates.push({ index: turnIndex, kind, score });
      }
    }
    previousDirection = direction;
  }

  candidates.push({ index: values.indexOf(maxValue), kind: "peak", score: span });
  candidates.push({ index: values.indexOf(minValue), kind: "trough", score: span });

  const selected: ChartTurnCandidate[] = [];
  const rankedCandidates = candidates.sort((a, b) => b.score - a.score);
  const minIndexDistance = Math.max(2, Math.floor(items.length / (maxLabels + 1)));
  for (const candidate of rankedCandidates) {
    addChartTurnCandidate(selected, candidate, maxLabels, minIndexDistance);
    if (selected.length >= maxLabels) {
      break;
    }
  }

  return selected
    .sort((a, b) => a.index - b.index)
    .map(({ index, kind }) => ({ index: items[index].index, row: items[index].row, kind }));
}

function PairMinuteFundingDiffChart({
  rows,
  status,
  loading
}: {
  rows: FundingRateDiffRow[];
  status: PairSpreadFundingRecordStatus | null;
  loading: boolean;
}) {
  const width = 560;
  const height = 328;
  const padding = { top: 18, right: 24, bottom: 34, left: 58 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const latest = rows[rows.length - 1] ?? null;
  const latestValue = finiteRate(latest?.net_rate_pct) ?? 0;
  const latestTone = fundingRateTone(latestValue);
  const latestTagColor = latestTone === "negative" ? "red" : latestTone === "positive" ? "green" : undefined;
  const sampleIntervalSeconds = status?.item?.interval_seconds ?? 60;
  const sampleIntervalLabel = sampleIntervalSeconds % 60 === 0
    ? `${sampleIntervalSeconds / 60}分钟`
    : `${sampleIntervalSeconds}秒`;

  if (!rows.length) {
    return (
      <div className="pair-detail-card pair-funding-chart-card pair-minute-funding-card">
        <div className="pair-detail-head">
          <Typography.Title level={5}>分钟资金费率差图</Typography.Title>
          <div className="pair-funding-chart-tools">
            <Tag color="blue">{sampleIntervalLabel}记录</Tag>
            <Tag>{status?.item?.sample_count ?? 0} 点</Tag>
          </div>
        </div>
        <div className="pair-funding-chart-empty">
          {loading ? "正在加载分钟记录" : "已开启记录，等待第一条分钟采样"}
        </div>
        {status?.warnings.length ? <Alert type="warning" message={status.warnings.join("；")} showIcon /> : null}
      </div>
    );
  }

  const values = rows.map((row) => finiteRate(row.net_rate_pct) ?? 0);
  const valueMin = Math.min(...values, 0);
  const valueMax = Math.max(...values, 0);
  const span = valueMax - valueMin || Math.max(Math.abs(valueMax), 0.0001);
  const min = valueMin - span * 0.16;
  const max = valueMax + span * 0.16;
  const startMs = dayjs.utc(rows[0].funding_time).valueOf();
  const endMs = dayjs.utc(rows[rows.length - 1].funding_time).valueOf();
  const spanHours = Math.max((endMs - startMs) / 3_600_000, 0);
  const ticks = chartTimeTicks(startMs, endMs, spanHours <= 12 ? 7 : spanHours <= 72 ? 6 : 5);
  const xAt = (fundingTime: string) =>
    padding.left +
    (startMs === endMs
      ? chartWidth / 2
      : ((dayjs.utc(fundingTime).valueOf() - startMs) / (endMs - startMs)) * chartWidth);
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const linePath = rows
    .map((row, index) => {
      const value = finiteRate(row.net_rate_pct) ?? 0;
      return `${index === 0 ? "M" : "L"} ${xAt(row.funding_time).toFixed(2)} ${yAt(value).toFixed(2)}`;
    })
    .join(" ");
  const areaPath =
    rows.length > 1
      ? `M ${xAt(rows[0].funding_time).toFixed(2)} ${yAt(0).toFixed(2)} ${rows
          .map((row) => {
            const value = finiteRate(row.net_rate_pct) ?? 0;
            return `L ${xAt(row.funding_time).toFixed(2)} ${yAt(value).toFixed(2)}`;
          })
          .join(" ")} L ${xAt(rows[rows.length - 1].funding_time).toFixed(2)} ${yAt(0).toFixed(2)} Z`
      : "";
  const turningPoints = fundingDiffTurningPoints(rows, spanHours <= 24 ? 8 : 6);
  const latestSampleAt = status?.item?.latest_sample_at ?? latest?.funding_time ?? null;

  return (
    <div className="pair-detail-card pair-funding-chart-card pair-minute-funding-card">
      <div className="pair-detail-head">
        <Typography.Title level={5}>分钟资金费率差图</Typography.Title>
        <div className="pair-funding-chart-tools">
          <Tag color={latestTagColor}>最新 {signedPct(latestValue, 4)}</Tag>
          <Tag color="blue">{sampleIntervalLabel}记录</Tag>
          <Tag>{rows.length} 点</Tag>
          {latestSampleAt ? <Tag>更新 {time(latestSampleAt)}</Tag> : null}
        </div>
      </div>
      <svg className="pair-funding-diff-chart" role="img" aria-label="分钟资金费率差走势" viewBox={`0 0 ${width} ${height}`}>
        <defs>
          <linearGradient id="pairMinuteFundingDiffFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#2563eb" stopOpacity="0.14" />
            <stop offset="100%" stopColor="#2563eb" stopOpacity="0.03" />
          </linearGradient>
        </defs>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="pair-funding-chart-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="pair-funding-chart-axis-label" x={padding.left - 8} y={y + 4} textAnchor="end">
                {signedPct(value, 4)}
              </text>
            </g>
          );
        })}
        {ticks.map((tick, tickIndex) => {
          const x = xAt(tick.value);
          const textAnchor = tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={`minute-funding-tick-${tick.value}-${tickIndex}`}>
              <line className="pair-funding-chart-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="pair-funding-chart-axis-label" x={x} y={height - 10} textAnchor={textAnchor}>
                {chartTime(tick.value, spanHours)}
              </text>
            </g>
          );
        })}
        <line className="pair-funding-chart-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        {areaPath ? <path className="pair-minute-funding-area" d={areaPath} /> : null}
        <path className="pair-funding-chart-line pair-minute-funding-line" d={linePath} />
        {rows.map((row, index) => {
          const value = finiteRate(row.net_rate_pct) ?? 0;
          return (
            <circle
              key={`minute-funding-point-${row.funding_time}-${index}`}
              className="pair-minute-funding-point"
              cx={xAt(row.funding_time)}
              cy={yAt(value)}
              r={rows.length <= 80 ? "2.8" : "2"}
            >
              <title>{`${time(row.funding_time)} 净费率 ${signedPct(value, 4)}`}</title>
            </circle>
          );
        })}
        {turningPoints.map(({ index, row, kind }, labelIndex) => {
          const value = finiteRate(row.net_rate_pct) ?? 0;
          const x = xAt(row.funding_time);
          const y = yAt(value);
          const labelWidth = 76;
          const labelHeight = 22;
          const labelOffset = 22 + (labelIndex % 2) * 12;
          const labelShift = ((labelIndex % 3) - 1) * 10;
          const labelCenterX = Math.min(
            padding.left + chartWidth - labelWidth / 2,
            Math.max(padding.left + labelWidth / 2, x + labelShift)
          );
          const rawLabelY = kind === "trough" ? y + labelOffset : y - labelOffset;
          const labelCenterY = Math.min(
            padding.top + chartHeight - labelHeight / 2,
            Math.max(padding.top + labelHeight / 2, rawLabelY)
          );
          return (
            <g key={`minute-funding-turn-${row.funding_time}-${kind}-${index}`} className={`pair-minute-funding-turning pair-minute-funding-turning-${kind}`}>
              <title>{`${time(row.funding_time)} 关键净费率 ${signedPct(value, 4)}`}</title>
              <line className="pair-minute-funding-leader" x1={x} y1={y} x2={labelCenterX} y2={labelCenterY} />
              <circle className="pair-minute-funding-turning-dot" cx={x} cy={y} r="4" />
              <rect
                className="pair-minute-funding-label-bg"
                x={labelCenterX - labelWidth / 2}
                y={labelCenterY - labelHeight / 2}
                width={labelWidth}
                height={labelHeight}
                rx="5"
              />
              <text className="pair-minute-funding-label" x={labelCenterX} y={labelCenterY + 4} textAnchor="middle">
                {signedPct(value, 4)}
              </text>
            </g>
          );
        })}
        <circle
          className={`pair-funding-chart-current-point pair-funding-chart-current-${latestTone}`}
          cx={xAt(latest?.funding_time ?? rows[rows.length - 1].funding_time)}
          cy={yAt(latestValue)}
          r="4.2"
        />
      </svg>
      <div className="pair-funding-chart-legend">
        <span className="pair-funding-legend-realtime">分钟记录样本</span>
      </div>
      {rows.length < 2 ? (
        <Typography.Text type="secondary">当前只有 1 个分钟点，继续记录后会自动连成曲线。</Typography.Text>
      ) : null}
      {status?.warnings.length ? <Alert type="warning" message={status.warnings.join("；")} showIcon /> : null}
    </div>
  );
}

function PairSpreadChart({ result }: { result: PairSpreadQueryResult | null }) {
  const points = pairDisplayPoints(result);
  const width = 1180;
  const height = 330;
  const padding = { top: 24, right: 28, bottom: 34, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!result || points.length === 0) {
    return <div className="pair-chart-empty">暂无查询结果</div>;
  }

  const values = points.map((point) => point.spread_pct).filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const xAt = (index: number) =>
    padding.left + (points.length === 1 ? chartWidth / 2 : (chartWidth * index) / (points.length - 1));
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const baselineValue = min <= 0 && max >= 0 ? 0 : minValue > 0 ? min : max;
  const baselineY = yAt(baselineValue);
  const spanHours = chartSpanHours(points);
  const ticks = chartTicks(points, spanHours <= 12 ? 13 : spanHours <= 24 ? 9 : spanHours >= 168 ? 7 : 6);
  const turningPoints = chartTurningPoints(points);
  const latestPoint = points[points.length - 1];
  const latestTone = fundingRateTone(latestPoint.spread_pct);

  return (
    <div className="pair-chart-card">
      <svg className="pair-spread-chart" role="img" aria-label="均值价差率曲线" viewBox={`0 0 ${width} ${height}`}>
        <defs>
          <linearGradient id="pairSpreadFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#2f80ed" stopOpacity="0.18" />
            <stop offset="100%" stopColor="#2f80ed" stopOpacity="0.04" />
          </linearGradient>
        </defs>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="pair-chart-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="pair-chart-axis-label" x={padding.left - 10} y={y + 4} textAnchor="end">
                {value.toFixed(0)}%
              </text>
            </g>
          );
        })}
        {ticks.map(({ index, point }, tickIndex) => {
          const x = xAt(index);
          const textAnchor = tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={point.bucket_at}>
              <line className="pair-chart-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="pair-chart-axis-label" x={x} y={height - 10} textAnchor={textAnchor}>
                {chartTime(point.bucket_at, spanHours)}
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line className="pair-chart-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        ) : null}
        <path className="pair-chart-area" d={spreadAreaPath(points, xAt, yAt, baselineY)} />
        <path className="pair-chart-line" d={spreadLinePath(points, xAt, yAt)} />
        <circle
          className={`pair-chart-current-point pair-chart-current-${latestTone}`}
          cx={xAt(points.length - 1)}
          cy={yAt(latestPoint.spread_pct)}
          r="4.6"
        >
          <title>{`${time(latestPoint.bucket_at)} 实时均值价差率 ${signedPct(latestPoint.spread_pct)}，差价 ${price(latestPoint.spread_abs)}`}</title>
        </circle>
        {turningPoints.map(({ index, point, kind }, labelIndex) => {
          const x = xAt(index);
          const y = yAt(point.spread_pct);
          const label = signedPct(point.spread_pct);
          const labelWidth = 66;
          const labelHeight = 22;
          const labelOffset = 22 + (labelIndex % 2) * 12;
          const labelShift = ((labelIndex % 3) - 1) * 10;
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
            <g key={`turn-${point.bucket_at}-${kind}`} className={`pair-chart-turning pair-chart-turning-${kind}`}>
              <title>{`${time(point.bucket_at)} 均值价差率 ${label}，差价 ${price(point.spread_abs)}`}</title>
              <line className="pair-chart-turning-leader" x1={x} y1={y} x2={labelCenterX} y2={labelCenterY} />
              <circle className="pair-chart-turning-dot" cx={x} cy={y} r="4" />
              <rect
                className="pair-chart-turning-label-bg"
                x={labelCenterX - labelWidth / 2}
                y={labelCenterY - labelHeight / 2}
                width={labelWidth}
                height={labelHeight}
                rx="4"
              />
              <text className="pair-chart-turning-label" x={labelCenterX} y={labelCenterY + 4} textAnchor="middle">
                {label}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="pair-chart-footer">
        <div className="pair-footer-tags">
          <Tag color="blue">
            {leftLegLabel(result)} / {rightLegLabel(result)}
          </Tag>
          <Tag>{points.length} 点</Tag>
          <Tag>{intervalLabel(resultIntervalSeconds(result))} 周期</Tag>
        </div>
        <Typography.Text type="secondary">最新 {fullTime(result.observed_at)}</Typography.Text>
      </div>
    </div>
  );
}

function PairDayCompareChart({
  series,
  loading,
  hours,
  intervalSeconds,
  rangeLabel
}: {
  series: PairDayCompareSeries[];
  loading: boolean;
  hours: number;
  intervalSeconds: number;
  rangeLabel: string;
}) {
  const activeSeries = series.filter((item) => item.points.length > 0);
  const width = 1180;
  const height = 330;
  const padding = { top: 24, right: 28, bottom: 38, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!activeSeries.length) {
    return (
      <div className="pair-day-compare-card">
        <div className="pair-day-compare-head">
          <Typography.Title level={5}>同时段价差对比</Typography.Title>
          <Tag>{loading ? "加载中" : "0 条"}</Tag>
        </div>
        <div className="pair-day-compare-empty">{loading ? "正在加载同时段对比" : "暂无同时段对比数据"}</div>
      </div>
    );
  }

  const values = activeSeries
    .flatMap((item) => item.points.map((point) => point.spread_pct))
    .filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values, 0);
  const maxValue = Math.max(...values, 0);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const expectedWindowMs = Math.max(hours, 1) * 3_600_000;
  const maxElapsedMs = Math.max(
    expectedWindowMs,
    ...activeSeries.map((item) => {
      const first = dayjs.utc(item.start_at).valueOf();
      const last = dayjs.utc(item.end_at).valueOf();
      return Math.max(last - first, 0);
    })
  );
  const xAtElapsed = (elapsedMs: number) => padding.left + (Math.max(elapsedMs, 0) / maxElapsedMs) * chartWidth;
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const elapsedAt = (item: PairDayCompareSeries, point: PairSpreadPoint) =>
    Math.max(dayjs.utc(point.bucket_at).valueOf() - dayjs.utc(item.start_at).valueOf(), 0);
  const colors = ["#2563eb", "#f97316", "#0f766e", "#7c3aed", "#b42318", "#0891b2", "#64748b"];
  const linePath = (item: PairDayCompareSeries) =>
    item.points
      .map((point, index) => {
        const x = xAtElapsed(elapsedAt(item, point));
        return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${yAt(point.spread_pct).toFixed(2)}`;
      })
      .join(" ");
  const baseSeries = activeSeries.find((item) => item.offsetDays === 0) ?? activeSeries[0];
  const ticks = dayCompareTimeTicks(dayjs.utc(baseSeries.start_at), maxElapsedMs);

  return (
    <div className="pair-day-compare-card">
      <div className="pair-day-compare-head">
        <Typography.Title level={5}>同时段价差对比</Typography.Title>
        <div className="pair-day-compare-summary">
          <Tag color="blue">{durationLabel(hours)}</Tag>
          {rangeLabel ? <Tag>{rangeLabel}</Tag> : null}
          <Tag>{intervalLabel(intervalSeconds)} 周期</Tag>
          <Tag>{activeSeries.length} 天</Tag>
          {loading ? <Tag color="processing">刷新中</Tag> : null}
        </div>
      </div>
      <svg className="pair-day-compare-chart" role="img" aria-label="多天同时段价差对比" viewBox={`0 0 ${width} ${height}`}>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="pair-day-compare-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="pair-day-compare-axis-label" x={padding.left - 10} y={y + 4} textAnchor="end">
                {signedPct(value)}
              </text>
            </g>
          );
        })}
        {ticks.map((tick, index) => {
          const x = xAtElapsed(tick.elapsedMs);
          const textAnchor = index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={`day-compare-tick-${tick.elapsedMs}`}>
              <line className="pair-day-compare-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="pair-day-compare-axis-label" x={x} y={height - 12} textAnchor={textAnchor}>
                {tick.label}
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line className="pair-day-compare-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        ) : null}
        {activeSeries.map((item, index) => {
          const color = colors[index % colors.length];
          const latest = item.points[item.points.length - 1];
          const latestX = xAtElapsed(elapsedAt(item, latest));
          return (
            <g key={`day-compare-series-${item.offsetDays}`}>
              <path className="pair-day-compare-line" d={linePath(item)} stroke={color} />
              <circle className="pair-day-compare-point" cx={latestX} cy={yAt(latest.spread_pct)} r="4" fill={color}>
                <title>{`${item.label} ${time(latest.bucket_at)} ${signedPct(latest.spread_pct)}`}</title>
              </circle>
            </g>
          );
        })}
      </svg>
      <div className="pair-day-compare-legend">
        {activeSeries.map((item, index) => {
          const latest = item.points[item.points.length - 1];
          const color = colors[index % colors.length];
          return (
            <span key={`day-compare-legend-${item.offsetDays}`} style={{ color }}>
              {item.label} {signedPct(latest?.spread_pct)}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function PairPriceChart({
  result
}: {
  result: PairSpreadQueryResult | null;
}) {
  const [priceChartMode, setPriceChartMode] = useState<PairPriceChartMode>("auto");
  const points = pairDisplayPoints(result);
  const width = 1180;
  const height = 230;
  const padding = { top: 18, right: 28, bottom: 34, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!result || points.length === 0) {
    return null;
  }

  const leftRawValues = points.map((point) => point.leg1_close).filter((value) => Number.isFinite(value));
  const rightRawValues = points.map((point) => point.leg2_close).filter((value) => Number.isFinite(value));
  const rawValues = [...leftRawValues, ...rightRawValues];

  if (!rawValues.length) {
    return null;
  }

  const firstFinite = (field: "leg1_close" | "leg2_close") => {
    const first = points.find((point) => Number.isFinite(point[field]) && point[field] !== 0);
    return first?.[field] ?? null;
  };
  const leftBase = firstFinite("leg1_close");
  const rightBase = firstFinite("leg2_close");
  const canIndexPrices = leftBase !== null && rightBase !== null;
  const average = (items: number[]) => items.reduce((total, value) => total + value, 0) / items.length;
  const leftAverage = leftRawValues.length ? average(leftRawValues) : null;
  const rightAverage = rightRawValues.length ? average(rightRawValues) : null;
  const priceLevelGapPct =
    leftAverage !== null && rightAverage !== null
      ? (Math.abs(leftAverage - rightAverage) / Math.max(Math.min(Math.abs(leftAverage), Math.abs(rightAverage)), 1e-9)) *
        100
      : 0;
  const autoIndexed = canIndexPrices && priceLevelGapPct >= 12;
  const indexedView = canIndexPrices && (priceChartMode === "indexed" || (priceChartMode === "auto" && autoIndexed));
  const chartPoints = points.map((point) => ({
    ...point,
    left_value: indexedView && leftBase !== null ? (point.leg1_close / leftBase) * 100 : point.leg1_close,
    right_value: indexedView && rightBase !== null ? (point.leg2_close / rightBase) * 100 : point.leg2_close,
    left_change_pct: leftBase !== null ? (point.leg1_close / leftBase - 1) * 100 : null,
    right_change_pct: rightBase !== null ? (point.leg2_close / rightBase - 1) * 100 : null
  }));
  const values = chartPoints
    .flatMap((point) => [point.left_value, point.right_value])
    .filter((value) => Number.isFinite(value));

  if (!values.length) {
    return null;
  }

  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.08;
  const max = maxValue + span * 0.08;
  const xAt = (index: number) =>
    padding.left + (points.length === 1 ? chartWidth / 2 : (chartWidth * index) / (points.length - 1));
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const spanHours = chartSpanHours(points);
  const ticks = chartTicks(points, spanHours <= 12 ? 13 : spanHours <= 24 ? 9 : spanHours >= 168 ? 7 : 6);
  const axisValueLabel = (value: number) => (indexedView ? value.toFixed(2) : price(value));
  const changeLabel = (value: number | null) =>
    typeof value === "number" && Number.isFinite(value) ? signedPct(value, 2) : "-";
  const linePath = (field: "left_value" | "right_value") =>
    chartPoints
      .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(2)} ${yAt(point[field]).toFixed(2)}`)
      .join(" ");
  const lastPoint = points[points.length - 1];
  const lastChartPoint = chartPoints[chartPoints.length - 1];
  const modeLabel = indexedView
    ? priceChartMode === "auto"
      ? "自动：相对走势 · 首点=100"
      : "相对走势 · 首点=100"
    : priceChartMode === "auto"
      ? "自动：原始价格"
      : "原始价格";

  return (
    <div className="pair-price-card">
      <div className="pair-price-head">
        <div className="pair-price-title">
          <Typography.Text strong>标的价格</Typography.Text>
          <Typography.Text type="secondary">
            {indexedView
              ? "大价差组合已按各自首个价格归一化，重点看走势和相对涨跌。"
              : "显示两个标的的原始价格。"}
          </Typography.Text>
        </div>
        <div className="pair-price-controls">
          <Tag color={indexedView ? "gold" : "default"}>{modeLabel}</Tag>
          <Segmented
            size="small"
            value={priceChartMode}
            options={[
              { label: "自动", value: "auto" },
              { label: "原始价格", value: "raw" },
              { label: "相对走势", value: "indexed" }
            ]}
            onChange={(value) => setPriceChartMode(value as PairPriceChartMode)}
          />
        </div>
      </div>
      <div className="pair-price-subhead">
        <div className="pair-price-legend">
          <span className="pair-price-legend-left">
            {leftLegLabel(result)}
            {indexedView ? ` ${changeLabel(lastChartPoint.left_change_pct)}` : ""}
          </span>
          <span className="pair-price-legend-right">
            {rightLegLabel(result)}
            {indexedView ? ` ${changeLabel(lastChartPoint.right_change_pct)}` : ""}
          </span>
        </div>
        {indexedView ? (
          <Typography.Text type="secondary">
            原始最新价：左 {price(lastPoint.leg1_close)} / 右 {price(lastPoint.leg2_close)}
          </Typography.Text>
        ) : null}
      </div>
      <svg className="pair-price-chart" role="img" aria-label="左右腿标的价格曲线" viewBox={`0 0 ${width} ${height}`}>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="pair-price-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="pair-price-axis-label" x={padding.left - 10} y={y + 4} textAnchor="end">
                {axisValueLabel(value)}
              </text>
            </g>
          );
        })}
        {ticks.map(({ index, point }, tickIndex) => {
          const x = xAt(index);
          const textAnchor = tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={`price-tick-${point.bucket_at}`}>
              <line className="pair-price-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="pair-price-axis-label" x={x} y={height - 10} textAnchor={textAnchor}>
                {chartTime(point.bucket_at, spanHours)}
              </text>
            </g>
          );
        })}
        {indexedView ? (
          <line className="pair-price-base-line" x1={padding.left} y1={yAt(100)} x2={padding.left + chartWidth} y2={yAt(100)} />
        ) : null}
        <path className="pair-price-line pair-price-line-left" d={linePath("left_value")} />
        <path className="pair-price-line pair-price-line-right" d={linePath("right_value")} />
        <circle className="pair-price-dot-left" cx={xAt(points.length - 1)} cy={yAt(lastChartPoint.left_value)} r="4">
          <title>
            {`${time(lastPoint.bucket_at)} 左腿 ${price(lastPoint.leg1_close)}${
              indexedView ? `，相对首点 ${changeLabel(lastChartPoint.left_change_pct)}` : ""
            }`}
          </title>
        </circle>
        <circle className="pair-price-dot-right" cx={xAt(points.length - 1)} cy={yAt(lastChartPoint.right_value)} r="4">
          <title>
            {`${time(lastPoint.bucket_at)} 右腿 ${price(lastPoint.leg2_close)}${
              indexedView ? `，相对首点 ${changeLabel(lastChartPoint.right_change_pct)}` : ""
            }`}
          </title>
        </circle>
      </svg>
    </div>
  );
}

function premiumCurrentToPoint(current: PremiumIndexCurrentSnapshot): PremiumIndexPoint | null {
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

function premiumStats(points: PremiumIndexPoint[], current?: PremiumIndexCurrentSnapshot | null) {
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

function mergePremiumCurrent(
  result: PremiumIndexQueryResult,
  current: PremiumIndexCurrentSnapshot
): PremiumIndexQueryResult {
  const nextPoint = premiumCurrentToPoint(current);
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

function premiumSpanHours(points: PremiumIndexPoint[]): number {
  if (points.length < 2) {
    return 0;
  }
  const start = dayjs.utc(points[0].bucket_at);
  const end = dayjs.utc(points[points.length - 1].bucket_at);
  return Math.max(end.diff(start, "minute") / 60, 0);
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

function premiumTurningPoints(points: PremiumIndexPoint[], maxLabels = 8): Array<{
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

function PairPremiumCompareChart({
  comparison,
  loading
}: {
  comparison: PairPremiumCompareResult | null;
  loading: boolean;
}) {
  const leftPoints = comparison?.left?.points ?? [];
  const rightPoints = comparison?.right?.points ?? [];
  const allPoints = [...leftPoints, ...rightPoints];
  const width = 1180;
  const height = 280;
  const padding = { top: 24, right: 28, bottom: 34, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!comparison && loading) {
    return <div className="pair-premium-empty">正在加载溢价指数对比</div>;
  }
  if (!allPoints.length) {
    return <div className="pair-premium-empty">暂无溢价指数对比</div>;
  }

  const sortedAllPoints = allPoints
    .slice()
    .sort((a, b) => dayjs.utc(a.bucket_at).valueOf() - dayjs.utc(b.bucket_at).valueOf());
  const spanHours = premiumSpanHours(sortedAllPoints);
  const values = sortedAllPoints.map((point) => point.premium_pct).filter((value) => Number.isFinite(value));
  if (!values.length) {
    return <div className="pair-premium-empty">暂无可用溢价指数对比</div>;
  }
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 0.01);
  const min = minValue - span * 0.16;
  const max = maxValue + span * 0.16;
  const startMs = dayjs.utc(sortedAllPoints[0].bucket_at).valueOf();
  const endMs = dayjs.utc(sortedAllPoints[sortedAllPoints.length - 1].bucket_at).valueOf();
  const xAt = (point: PremiumIndexPoint) =>
    padding.left + (startMs === endMs ? chartWidth / 2 : ((dayjs.utc(point.bucket_at).valueOf() - startMs) / (endMs - startMs)) * chartWidth);
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const linePath = (points: PremiumIndexPoint[]) =>
    points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(point).toFixed(2)} ${yAt(point.premium_pct).toFixed(2)}`)
      .join(" ");
  const renderTurnLabels = (
    points: PremiumIndexPoint[],
    side: "left" | "right"
  ) =>
    premiumTurningPoints(points).map(({ index, point, kind }, labelIndex) => {
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
        <g key={`premium-turn-${side}-${point.bucket_at}-${kind}-${index}`} className={`pair-premium-turning pair-premium-turning-${side}`}>
          <title>{`${time(point.bucket_at)} ${side === "left" ? "左腿" : "右腿"}溢价指数 ${signedPct(point.premium_pct, 4)} (${label})`}</title>
          <line className="pair-premium-turning-leader" x1={x} y1={y} x2={labelCenterX} y2={labelCenterY} />
          <circle className="pair-premium-turning-dot" cx={x} cy={y} r="3.5" />
          <rect
            className="pair-premium-turning-label-bg"
            x={labelCenterX - labelWidth / 2}
            y={labelCenterY - labelHeight / 2}
            width={labelWidth}
            height={labelHeight}
            rx="4"
          />
          <text className="pair-premium-turning-label" x={labelCenterX} y={labelCenterY + 4} textAnchor="middle">
            {label}
          </text>
        </g>
      );
    });
  const ticks = chartTicks(
    sortedAllPoints.map((point) => ({
        bucket_at: point.bucket_at,
        leg1_close: 1,
        leg2_close: 1,
        spread_abs: 0,
        spread_pct: point.premium_pct
      })),
    spanHours <= 12 ? 13 : spanHours <= 24 ? 9 : spanHours >= 168 ? 7 : 6
  );
  const leftCurrent = comparison?.left?.current?.premium_pct ?? comparison?.left?.premium_pct.current ?? null;
  const rightCurrent = comparison?.right?.current?.premium_pct ?? comparison?.right?.premium_pct.current ?? null;
  const diff =
    typeof leftCurrent === "number" && typeof rightCurrent === "number"
      ? rightCurrent - leftCurrent
      : null;

  return (
    <div className="pair-premium-card">
      <div className="pair-premium-head">
        <Typography.Title level={5}>溢价指数对比</Typography.Title>
        <div className="pair-premium-summary">
          <Tag color="blue">左 {signedPct(leftCurrent, 4)}</Tag>
          <Tag color="purple">右 {signedPct(rightCurrent, 4)}</Tag>
          <Tag>右-左 {signedPct(diff, 4)}</Tag>
        </div>
      </div>
      <svg className="pair-premium-chart" role="img" aria-label="左右腿溢价指数对比" viewBox={`0 0 ${width} ${height}`}>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="pair-premium-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="pair-premium-axis-label" x={padding.left - 10} y={y + 4} textAnchor="end">
                {Math.round(value * 100)}bp
              </text>
            </g>
          );
        })}
        {ticks.map(({ index, point }, tickIndex) => {
          const source = sortedAllPoints[index];
          const x = source ? xAt(source) : padding.left;
          const textAnchor = tickIndex === 0 ? "start" : tickIndex === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={`premium-tick-${point.bucket_at}-${tickIndex}`}>
              <line className="pair-premium-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="pair-premium-axis-label" x={x} y={height - 10} textAnchor={textAnchor}>
                {chartTime(point.bucket_at, spanHours)}
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line className="pair-premium-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        ) : null}
        {leftPoints.length ? <path className="pair-premium-line pair-premium-line-left" d={linePath(leftPoints)} /> : null}
        {rightPoints.length ? <path className="pair-premium-line pair-premium-line-right" d={linePath(rightPoints)} /> : null}
        {renderTurnLabels(leftPoints, "left")}
        {renderTurnLabels(rightPoints, "right")}
        {leftPoints.length ? (
          <circle className="pair-premium-dot-left" cx={xAt(leftPoints[leftPoints.length - 1])} cy={yAt(leftPoints[leftPoints.length - 1].premium_pct)} r="4" />
        ) : null}
        {rightPoints.length ? (
          <circle className="pair-premium-dot-right" cx={xAt(rightPoints[rightPoints.length - 1])} cy={yAt(rightPoints[rightPoints.length - 1].premium_pct)} r="4" />
        ) : null}
      </svg>
      <div className="pair-premium-legend">
        <span className="pair-premium-legend-left">
          {comparison?.left ? `${comparison.left.exchange} · ${comparison.left.symbol}` : "左腿无数据"}
        </span>
        <span className="pair-premium-legend-right">
          {comparison?.right ? `${comparison.right.exchange} · ${comparison.right.symbol}` : "右腿无数据"}
        </span>
      </div>
      {comparison?.warnings.length ? <Alert type="warning" message={comparison.warnings.join("；")} showIcon /> : null}
    </div>
  );
}

const pointColumns: ColumnsType<PairSpreadPoint> = [
  { title: "时间", dataIndex: "bucket_at", width: 120, render: (value: string) => time(value) },
  { title: "左价", dataIndex: "leg1_close", align: "right", render: (value: number) => price(value) },
  { title: "右价", dataIndex: "leg2_close", align: "right", render: (value: number) => price(value) },
  { title: "差价", dataIndex: "spread_abs", align: "right", render: (value: number) => price(value) },
  { title: "均值价差率", dataIndex: "spread_pct", align: "right", render: (value: number) => signedPct(value) }
];

function fundingLegKey(exchange: string, symbol: string): string {
  return `${exchange.trim().toLowerCase()}|${symbol.trim().toUpperCase().replace(/[-_/]/g, "")}`;
}

function fundingTimeBucket(value: string): string {
  const parsed = dayjs.utc(value);
  return parsed.isValid() ? parsed.second(0).millisecond(0).toISOString() : value;
}

function fundingPointBelongsTo(point: PairSpreadFundingPoint, leg: PairSpreadLegQuery): boolean {
  if (leg.market_type !== "future") {
    return false;
  }
  return fundingLegKey(point.exchange, point.symbol) === fundingLegKey(leg.exchange, leg.symbol);
}

function fundingRateColumnTitle(leg: PairSpreadLegQuery | null | undefined, fallback: string): ReactNode {
  if (!leg) {
    return fallback;
  }
  return (
    <span className="pair-funding-column-title">
      <span>{exchangeLabels[leg.exchange] ?? leg.exchange} 费率</span>
      <small>{leg.symbol}</small>
    </span>
  );
}

function buildFundingRateDiffRows(result: PairSpreadQueryResult | null): FundingRateDiffRow[] {
  if (!result) {
    return [];
  }

  const rowsByTime = new Map<string, FundingRateDiffRow>();
  for (const point of result.funding_history ?? []) {
    const fundingTime = fundingTimeBucket(point.funding_time);
    const isLeft = fundingPointBelongsTo(point, result.leg1);
    const isRight = fundingPointBelongsTo(point, result.leg2);
    if (!isLeft && !isRight) {
      continue;
    }
    const row =
      rowsByTime.get(fundingTime) ??
      {
        funding_time: fundingTime,
        left_rate_pct: null,
        right_rate_pct: null,
        net_rate_pct: null,
        source: "history" as const
      };
    if (isLeft) {
      row.left_rate_pct = finiteRate(point.funding_rate_pct);
    }
    if (isRight) {
      row.right_rate_pct = finiteRate(point.funding_rate_pct);
    }
    rowsByTime.set(fundingTime, row);
  }

  const historyRows = [...rowsByTime.values()]
    .map((row) => {
      const leftRate = finiteRate(row.left_rate_pct) ?? 0;
      const rightRate = finiteRate(row.right_rate_pct) ?? 0;
      // 净费率按右侧费率减左侧费率，和价差方向保持一致。
      return {
        ...row,
        left_rate_pct: leftRate,
        right_rate_pct: rightRate,
        net_rate_pct: rightRate - leftRate
      };
    })
    .sort((a, b) => dayjs.utc(b.funding_time).valueOf() - dayjs.utc(a.funding_time).valueOf())
    .slice(0, 80);

  if (historyRows.length > 0) {
    return historyRows;
  }

  const current = result.current;
  const leftRate = finiteRate(current?.leg1.funding_rate_pct);
  const rightRate = finiteRate(current?.leg2.funding_rate_pct);
  if (!current || (leftRate === null && rightRate === null)) {
    return [];
  }
  const currentLeftRate = leftRate ?? 0;
  const currentRightRate = rightRate ?? 0;
  return [
    {
      funding_time: current.observed_at,
      left_rate_pct: currentLeftRate,
      right_rate_pct: currentRightRate,
      // 当前快照缺一侧资金费率时按 0 处理，避免净费率展示为空。
      net_rate_pct: currentRightRate - currentLeftRate,
      source: "current"
    }
  ];
}

function realtimeFundingPointToRow(point: PairSpreadRealtimeFundingPoint): FundingRateDiffRow | null {
  const leftRate = finiteRate(point.left_rate_pct);
  const rightRate = finiteRate(point.right_rate_pct);
  const netRate = finiteRate(point.net_rate_pct);
  if (leftRate === null && rightRate === null && netRate === null) {
    return null;
  }
  const normalizedLeftRate = leftRate ?? 0;
  const normalizedRightRate = rightRate ?? 0;
  return {
    funding_time: fundingTimeBucket(point.bucket_at),
    left_rate_pct: normalizedLeftRate,
    right_rate_pct: normalizedRightRate,
    net_rate_pct: netRate ?? normalizedRightRate - normalizedLeftRate,
    source: "realtime"
  };
}

function buildRealtimeFundingRateDiffRows(result: PairSpreadQueryResult | null): FundingRateDiffRow[] {
  if (!result) {
    return [];
  }
  const sampleRows = (result.realtime_funding ?? [])
    .map(realtimeFundingPointToRow)
    .filter((row): row is FundingRateDiffRow => row !== null)
    .sort((a, b) => dayjs.utc(b.funding_time).valueOf() - dayjs.utc(a.funding_time).valueOf())
    .slice(0, 500);
  if (sampleRows.length > 0) {
    return sampleRows;
  }

  const current = result.current;
  const leftRate = finiteRate(current?.leg1.funding_rate_pct);
  const rightRate = finiteRate(current?.leg2.funding_rate_pct);
  if (!current || (leftRate === null && rightRate === null)) {
    return [];
  }
  const currentLeftRate = leftRate ?? 0;
  const currentRightRate = rightRate ?? 0;
  return [
    {
      funding_time: current.observed_at,
      left_rate_pct: currentLeftRate,
      right_rate_pct: currentRightRate,
      net_rate_pct: currentRightRate - currentLeftRate,
      source: "current"
    }
  ];
}

function buildMinuteFundingRateDiffRows(status: PairSpreadFundingRecordStatus | null): FundingRateDiffRow[] {
  if (!status?.watched) {
    return [];
  }
  return (status.samples ?? [])
    .map((point): FundingRateDiffRow | null => {
      const leftRate = finiteRate(point.left_rate_pct);
      const rightRate = finiteRate(point.right_rate_pct);
      const netRate = finiteRate(point.net_rate_pct);
      if (leftRate === null && rightRate === null && netRate === null) {
        return null;
      }
      const normalizedLeftRate = leftRate ?? 0;
      const normalizedRightRate = rightRate ?? 0;
      return {
        funding_time: fundingTimeBucket(point.bucket_at),
        left_rate_pct: normalizedLeftRate,
        right_rate_pct: normalizedRightRate,
        net_rate_pct: netRate ?? normalizedRightRate - normalizedLeftRate,
        source: "minute_record" as const
      };
    })
    .filter((row): row is FundingRateDiffRow => row !== null)
    .sort((a, b) => dayjs.utc(a.funding_time).valueOf() - dayjs.utc(b.funding_time).valueOf())
    .slice(-4000);
}

export function PairMonitorPage() {
  const [form] = Form.useForm<PairSpreadFormValues>();
  const initialUrlQuery = useMemo(() => pairQueryFromUrl(), []);
  const initialCachedState = useMemo(() => {
    const cached = loadLastPairSpreadState();
    if (!cached) {
      return null;
    }
    if (!initialUrlQuery) {
      return cached;
    }
    return pairQueryKey(cached.values, cached.hours, cached.intervalSeconds) ===
      pairQueryKey(initialUrlQuery.values, initialUrlQuery.hours, initialUrlQuery.intervalSeconds)
      ? cached
      : null;
  }, [initialUrlQuery]);
  const initialDayCompareEnabled = initialCachedState?.showDayCompare ?? false;
  const initialDayCompareHistoryDays = initialCachedState?.dayCompareDays ?? DEFAULT_DAY_COMPARE_DAYS;
  const initialDayCompareSettings = normalizeDayCompareSettings({
    mode: initialCachedState?.dayCompareMode,
    startTime: initialCachedState?.dayCompareStartTime,
    endTime: initialCachedState?.dayCompareEndTime
  });
  const initialUrlQueryKey =
    initialCachedState && initialUrlQuery
      ? pairQueryKey(initialUrlQuery.values, initialUrlQuery.hours, initialUrlQuery.intervalSeconds)
      : "";
  const loadedUrlQueryRef = useRef(initialUrlQueryKey);
  const [hours, setHours] = useState(() => initialCachedState?.hours ?? 4);
  const [intervalSeconds, setIntervalSeconds] = useState(() => initialCachedState?.intervalSeconds ?? DEFAULT_PAIR_INTERVAL_SECONDS);
  const [customInterval, setCustomInterval] = useState(
    () => intervalSelectValue(initialCachedState?.intervalSeconds ?? DEFAULT_PAIR_INTERVAL_SECONDS) === CUSTOM_INTERVAL_VALUE
  );
  const [locationSearch, setLocationSearch] = useState(() =>
    typeof window === "undefined" ? "" : window.location.search
  );
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [showPremiumCompare, setShowPremiumCompare] = useState(false);
  const [showDayCompare, setShowDayCompare] = useState(() => initialDayCompareEnabled);
  const [dayCompareDays, setDayCompareDays] = useState(() => initialDayCompareHistoryDays);
  const [dayCompareMode, setDayCompareMode] = useState<DayCompareWindowMode>(() => initialDayCompareSettings.mode);
  const [dayCompareStartTime, setDayCompareStartTime] = useState(() => initialDayCompareSettings.startTime);
  const [dayCompareEndTime, setDayCompareEndTime] = useState(() => initialDayCompareSettings.endTime);
  const [savedPresets, setSavedPresets] = useState<SavedPairSpreadPreset[]>(() => loadSavedPairPresets());
  const [result, setResult] = useState<PairSpreadQueryResult | null>(() => initialCachedState?.result ?? null);
  const [premiumCompare, setPremiumCompare] = useState<PairPremiumCompareResult | null>(null);
  const [dayCompareSeries, setDayCompareSeries] = useState<PairDayCompareSeries[]>([]);
  const [loading, setLoading] = useState(false);
  const [premiumLoading, setPremiumLoading] = useState(false);
  const [dayCompareLoading, setDayCompareLoading] = useState(false);
  const [fundingRecordLoading, setFundingRecordLoading] = useState(false);
  const [error, setError] = useState("");
  const [premiumError, setPremiumError] = useState("");
  const [dayCompareError, setDayCompareError] = useState("");
  const [fundingRecordError, setFundingRecordError] = useState("");
  const [fundingRecordStatus, setFundingRecordStatus] = useState<PairSpreadFundingRecordStatus | null>(null);
  const initialDayCompareLoadedRef = useRef(false);

  const recentPoints = useMemo(
    () => pairDisplayPoints(result).reverse().slice(0, 180),
    [result]
  );
  const fundingDiffRows = useMemo(() => buildFundingRateDiffRows(result), [result]);
  const realtimeFundingDiffRows = useMemo(() => buildRealtimeFundingRateDiffRows(result), [result]);
  const minuteFundingDiffRows = useMemo(
    () => buildMinuteFundingRateDiffRows(fundingRecordStatus),
    [fundingRecordStatus]
  );
  const latestFundingDiff =
    realtimeFundingDiffRows.find((row) => finiteRate(row.net_rate_pct) !== null) ??
    fundingDiffRows.find((row) => finiteRate(row.net_rate_pct) !== null);
  const dayCompareSettings = useMemo(
    () => normalizeDayCompareSettings({
      mode: dayCompareMode,
      startTime: dayCompareStartTime,
      endTime: dayCompareEndTime
    }),
    [dayCompareMode, dayCompareEndTime, dayCompareStartTime]
  );
  const fundingDiffColumns = useMemo<ColumnsType<FundingRateDiffRow>>(
    () => [
      {
        title: "时间",
        dataIndex: "funding_time",
        width: 112,
        render: (value: string, row) => (
          <span className="pair-funding-time-cell">
            <span>{time(value)}</span>
            {row.source === "current" ? <Tag color="blue">当前</Tag> : null}
          </span>
        )
      },
      {
        title: fundingRateColumnTitle(result?.leg1, "左侧费率"),
        dataIndex: "left_rate_pct",
        align: "right",
        width: 138,
        render: (value: number | null) => <FundingRateValue value={value} />
      },
      {
        title: fundingRateColumnTitle(result?.leg2, "右侧费率"),
        dataIndex: "right_rate_pct",
        align: "right",
        width: 138,
        render: (value: number | null) => <FundingRateValue value={value} />
      },
      {
        title: "净费率",
        dataIndex: "net_rate_pct",
        align: "right",
        width: 118,
        render: (value: number | null) => <FundingRateValue value={value} strong />
      }
    ],
    [result?.leg1, result?.leg2]
  );

  const loadFundingRecordStatus = useCallback(async (pairResult: PairSpreadQueryResult) => {
    if (!supportsFundingRecord(pairResult)) {
      setFundingRecordStatus(null);
      setFundingRecordError("");
      return null;
    }
    setFundingRecordLoading(true);
    setFundingRecordError("");
    try {
      const nextStatus = await getPairSpreadFundingRecordStatus(fundingRecordStatusQuery(pairResult));
      setFundingRecordStatus(nextStatus);
      if (nextStatus.warnings.length) {
        setFundingRecordError(nextStatus.warnings.join("；"));
      }
      return nextStatus;
    } catch (exc) {
      setFundingRecordStatus(null);
      setFundingRecordError(exc instanceof Error ? exc.message : String(exc));
      return null;
    } finally {
      setFundingRecordLoading(false);
    }
  }, []);

  const toggleFundingRecord = useCallback(async () => {
    const payload = fundingRecordRequestFromResult(result);
    if (!result || !payload) {
      setFundingRecordError("分钟资金费率记录只支持合约对。");
      return;
    }
    setFundingRecordLoading(true);
    setFundingRecordError("");
    try {
      const nextStatus = fundingRecordStatus?.watched
        ? await stopPairSpreadFundingRecord(payload, result.hours)
        : await startPairSpreadFundingRecord(payload, result.hours);
      setFundingRecordStatus(nextStatus);
      if (nextStatus.warnings.length) {
        setFundingRecordError(nextStatus.warnings.join("；"));
      }
    } catch (exc) {
      setFundingRecordError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setFundingRecordLoading(false);
    }
  }, [fundingRecordStatus?.watched, result]);

  const loadPremiumCompare = useCallback(async (pairResult: PairSpreadQueryResult) => {
    setPremiumLoading(true);
    setPremiumError("");
    try {
      if (!supportsPremiumCompare(pairResult)) {
        setPremiumCompare({
          left: null,
          right: null,
          warnings: ["溢价指数只适用于合约腿；当前价差组合包含现货，已跳过溢价指数对比。"]
        });
        return;
      }
      const [left, right] = await Promise.allSettled([
        queryPremiumIndex({
          exchange: pairResult.leg1.exchange,
          symbol: pairResult.leg1.symbol,
          hours: pairResult.hours,
          interval_minutes: intervalMinutesParam(resultIntervalSeconds(pairResult))
        }),
        queryPremiumIndex({
          exchange: pairResult.leg2.exchange,
          symbol: pairResult.leg2.symbol,
          hours: pairResult.hours,
          interval_minutes: intervalMinutesParam(resultIntervalSeconds(pairResult))
        })
      ]);
      const warnings: string[] = [];
      if (left.status === "rejected") {
        warnings.push(`左腿溢价指数失败：${left.reason instanceof Error ? left.reason.message : String(left.reason)}`);
      }
      if (right.status === "rejected") {
        warnings.push(`右腿溢价指数失败：${right.reason instanceof Error ? right.reason.message : String(right.reason)}`);
      }
      setPremiumCompare({
        left: left.status === "fulfilled" ? left.value : null,
        right: right.status === "fulfilled" ? right.value : null,
        warnings
      });
    } catch (exc) {
      setPremiumError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setPremiumLoading(false);
    }
  }, []);

  const refreshPremiumCompareCurrent = useCallback(async (pairResult: PairSpreadQueryResult) => {
    setPremiumLoading(true);
    setPremiumError("");
    try {
      if (!supportsPremiumCompare(pairResult)) {
        setPremiumCompare((existing) => existing ?? {
          left: null,
          right: null,
          warnings: ["溢价指数只适用于合约腿；当前价差组合包含现货，已跳过溢价指数对比。"]
        });
        return;
      }
      const [left, right] = await Promise.allSettled([
        getCurrentPremiumIndex({
          exchange: pairResult.leg1.exchange,
          symbol: pairResult.leg1.symbol
        }),
        getCurrentPremiumIndex({
          exchange: pairResult.leg2.exchange,
          symbol: pairResult.leg2.symbol
        })
      ]);
      setPremiumCompare((existing) => {
        if (!existing) {
          return existing;
        }
        const warnings = [...existing.warnings].filter(
          (warning) => !warning.startsWith("左腿溢价指数失败") && !warning.startsWith("右腿溢价指数失败")
        );
        let nextLeft = existing.left;
        let nextRight = existing.right;
        if (left.status === "fulfilled" && nextLeft) {
          nextLeft = mergePremiumCurrent(nextLeft, left.value);
        } else if (left.status === "rejected") {
          warnings.push(`左腿溢价指数失败：${left.reason instanceof Error ? left.reason.message : String(left.reason)}`);
        }
        if (right.status === "fulfilled" && nextRight) {
          nextRight = mergePremiumCurrent(nextRight, right.value);
        } else if (right.status === "rejected") {
          warnings.push(`右腿溢价指数失败：${right.reason instanceof Error ? right.reason.message : String(right.reason)}`);
        }
        return {
          left: nextLeft,
          right: nextRight,
          warnings
        };
      });
    } catch (exc) {
      setPremiumError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setPremiumLoading(false);
    }
  }, []);

  const loadDayCompare = useCallback(async (
    pairResult: PairSpreadQueryResult,
    days = dayCompareDays,
    settings = dayCompareSettings
  ) => {
    setDayCompareLoading(true);
    setDayCompareError("");
    try {
      const compareDays = clampDayCompareDays(days);
      const compareSettings = normalizeDayCompareSettings(settings);
      const plan = buildDayCompareWindowPlan(pairResult, compareSettings);
      const baseValues = pairFormFromResult(pairResult);
      const baseEnd = plan.baseEnd;
      const baseWindow = shiftedDayCompareWindow(plan, 0);
      const currentSeries: PairDayCompareSeries = {
        offsetDays: 0,
        label: `${plan.mode === "custom" ? "基准" : "今天"} ${baseEnd.utcOffset(8).format("MM-DD")}`,
        points: plan.useCurrentResult
          ? pairDisplayPoints(pairResult)
          : filterPairPointsByWindow(pairDisplayPoints(pairResult), baseWindow.start, baseWindow.end),
        warnings: pairResult.warnings,
        start_at: baseWindow.start.toISOString(),
        end_at: baseWindow.end.toISOString()
      };
      const offsets = Array.from(
        { length: plan.useCurrentResult ? compareDays - 1 : compareDays },
        (_, index) => plan.useCurrentResult ? index + 1 : index
      );
      const settled = await Promise.allSettled(
        offsets.map((offsetDays) => {
          const window = shiftedDayCompareWindow(plan, offsetDays);
          return queryPairSpread({
            leg1_exchange: baseValues.leg1_exchange,
            leg1_market_type: baseValues.leg1_market_type,
            leg1_symbol: baseValues.leg1_symbol,
            leg2_exchange: baseValues.leg2_exchange,
            leg2_market_type: baseValues.leg2_market_type,
            leg2_symbol: baseValues.leg2_symbol,
            leg2_multiplier: baseValues.leg2_multiplier,
            hours: plan.queryHours,
            interval_minutes: intervalMinutesParam(plan.intervalSeconds),
            interval_seconds: plan.intervalSeconds,
            end_at: window.end.toISOString(),
            include_current: false
          });
        })
      );
      const warnings: string[] = [];
      const historicalSeries = settled.flatMap((item, index): PairDayCompareSeries[] => {
        const offsetDays = offsets[index];
        const window = shiftedDayCompareWindow(plan, offsetDays);
        const labelDate = window.end.utcOffset(8).format("MM-DD");
        const requestLabel = dayCompareRequestLabel(offsetDays, labelDate);
        if (item.status === "rejected") {
          warnings.push(`${requestLabel} 加载失败：${item.reason instanceof Error ? item.reason.message : String(item.reason)}`);
          return [];
        }
        if (item.value.warnings.length) {
          warnings.push(`${requestLabel}：${item.value.warnings.join("；")}`);
        }
        const points = filterPairPointsByWindow(item.value.points, window.start, window.end);
        return [
          {
            offsetDays,
            label: requestLabel,
            points,
            warnings: item.value.warnings,
            start_at: window.start.toISOString(),
            end_at: window.end.toISOString()
          }
        ];
      });
      setDayCompareSeries(plan.useCurrentResult ? [currentSeries, ...historicalSeries] : historicalSeries);
      setDayCompareError(warnings.join("；"));
    } catch (exc) {
      setDayCompareSeries([]);
      setDayCompareError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDayCompareLoading(false);
    }
  }, [dayCompareDays, dayCompareSettings]);

  useEffect(() => {
    if (initialDayCompareLoadedRef.current || !initialDayCompareEnabled || !result || !showDayCompare) {
      return;
    }
    initialDayCompareLoadedRef.current = true;
    void loadDayCompare(result, dayCompareDays, dayCompareSettings);
  }, [dayCompareDays, dayCompareSettings, initialDayCompareEnabled, loadDayCompare, result, showDayCompare]);

  const runQuery = useCallback(async (override?: {
    hours?: number;
    intervalSeconds?: number;
    values?: PairSpreadFormValues;
    premiumMode?: "history" | "current";
    premiumEnabled?: boolean;
    dayCompareEnabled?: boolean;
    dayCompareDays?: number;
    dayCompareSettings?: DayCompareSettings;
  }) => {
    setLoading(true);
    setError("");
    try {
      const values = normalizePairForm(override?.values ?? await form.validateFields());
      form.setFieldsValue(values);
      const queryHours = clampHours(override?.hours ?? hours);
      const queryIntervalSeconds = clampIntervalSeconds(override?.intervalSeconds ?? intervalSeconds);
      const queryShowPremiumCompare = override?.premiumEnabled ?? showPremiumCompare;
      const queryShowDayCompare = override?.dayCompareEnabled ?? showDayCompare;
      const queryDayCompareDays = clampDayCompareDays(override?.dayCompareDays ?? dayCompareDays);
      const queryDayCompareSettings = normalizeDayCompareSettings(override?.dayCompareSettings ?? dayCompareSettings);
      const next = await queryPairSpread({
        leg1_exchange: values.leg1_exchange,
        leg1_market_type: values.leg1_market_type,
        leg1_symbol: values.leg1_symbol,
        leg2_exchange: values.leg2_exchange,
        leg2_market_type: values.leg2_market_type,
        leg2_symbol: values.leg2_symbol,
        leg2_multiplier: values.leg2_multiplier,
        interval_minutes: intervalMinutesParam(queryIntervalSeconds),
        interval_seconds: queryIntervalSeconds,
        hours: queryHours
      });
      setResult(next);
      const resultValues = pairFormFromResult(next);
      form.setFieldsValue(resultValues);
      storeLastPairSpreadState(
        resultValues,
        queryHours,
        queryIntervalSeconds,
        next,
        queryShowDayCompare,
        queryDayCompareDays,
        queryDayCompareSettings
      );
      loadedUrlQueryRef.current = pairQueryKey(resultValues, queryHours, queryIntervalSeconds);
      replacePairQueryInUrl(resultValues, queryHours, queryIntervalSeconds);
      if (queryShowPremiumCompare) {
        if (override?.premiumMode === "current" && premiumCompare) {
          await refreshPremiumCompareCurrent(next);
        } else {
          await loadPremiumCompare(next);
        }
      }
      if (queryShowDayCompare) {
        await loadDayCompare(next, queryDayCompareDays, queryDayCompareSettings);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [
    form,
    hours,
    intervalSeconds,
    loadPremiumCompare,
    loadDayCompare,
    premiumCompare,
    refreshPremiumCompareCurrent,
    dayCompareDays,
    dayCompareSettings,
    showDayCompare,
    showPremiumCompare
  ]);

  const runQueryRef = useRef(runQuery);
  useEffect(() => {
    runQueryRef.current = runQuery;
  }, [runQuery]);

  useEffect(() => {
    if (!result) {
      setFundingRecordStatus(null);
      setFundingRecordError("");
      return;
    }
    void loadFundingRecordStatus(result);
  }, [loadFundingRecordStatus, result]);

  useEffect(() => {
    if (!result || !fundingRecordStatus?.watched) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void loadFundingRecordStatus(result);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [fundingRecordStatus?.watched, loadFundingRecordStatus, result]);

  useEffect(() => {
    const syncLocationSearch = () => {
      setLocationSearch(window.location.search);
    };
    window.addEventListener("popstate", syncLocationSearch);
    window.addEventListener("taoli1:navigate", syncLocationSearch);
    return () => {
      window.removeEventListener("popstate", syncLocationSearch);
      window.removeEventListener("taoli1:navigate", syncLocationSearch);
    };
  }, []);

  useEffect(() => {
    const incoming = pairQueryFromUrl();
    if (!incoming) {
      return;
    }
    const key = pairQueryKey(incoming.values, incoming.hours, incoming.intervalSeconds);
    if (loadedUrlQueryRef.current === key) {
      return;
    }
    loadedUrlQueryRef.current = key;
    form.setFieldsValue(incoming.values);
    setHours(incoming.hours);
    setIntervalSeconds(incoming.intervalSeconds);
    setCustomInterval(intervalSelectValue(incoming.intervalSeconds) === CUSTOM_INTERVAL_VALUE);
    setAutoRefresh(false);
    setShowPremiumCompare(false);
    setShowDayCompare(false);
    setDayCompareDays(DEFAULT_DAY_COMPARE_DAYS);
    setDayCompareMode(DEFAULT_DAY_COMPARE_SETTINGS.mode);
    setDayCompareStartTime(DEFAULT_DAY_COMPARE_SETTINGS.startTime);
    setDayCompareEndTime(DEFAULT_DAY_COMPARE_SETTINGS.endTime);
    setDayCompareSeries([]);
    setDayCompareError("");
    void runQueryRef.current({
      values: incoming.values,
      hours: incoming.hours,
      intervalSeconds: incoming.intervalSeconds,
      premiumEnabled: false,
      dayCompareEnabled: false,
      dayCompareDays: DEFAULT_DAY_COMPARE_DAYS,
      dayCompareSettings: DEFAULT_DAY_COMPARE_SETTINGS
    });
  }, [form, locationSearch]);

  useEffect(() => {
    if (!autoRefresh || !result) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      if (!loading) {
        void runQuery({ premiumMode: "current" });
      }
    }, clampIntervalSeconds(resultIntervalSeconds(result)) * 1000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loading, result, runQuery]);

  const rerun = () => {
    if (!result) {
      void runQuery();
      return;
    }
    form.setFieldsValue({
      leg1_exchange: result.leg1.exchange,
      leg1_market_type: result.leg1.market_type,
      leg1_symbol: result.leg1.symbol,
      leg2_exchange: result.leg2.exchange,
      leg2_market_type: result.leg2.market_type,
      leg2_symbol: result.leg2.symbol,
      leg2_multiplier: result.leg2_multiplier
    });
    setHours(result.hours);
    setIntervalSeconds(resultIntervalSeconds(result));
    setCustomInterval(intervalSelectValue(resultIntervalSeconds(result)) === CUSTOM_INTERVAL_VALUE);
    void runQuery({ hours: result.hours, intervalSeconds: resultIntervalSeconds(result) });
  };

  const saveCurrentPreset = async () => {
    try {
      const values = normalizePairForm(await form.validateFields());
      const preset: SavedPairSpreadPreset = {
        ...values,
        id: pairPresetId(values),
        hours: clampHours(hours),
        intervalSeconds: clampIntervalSeconds(intervalSeconds),
        showDayCompare,
        dayCompareDays: clampDayCompareDays(dayCompareDays),
        dayCompareMode: dayCompareSettings.mode,
        dayCompareStartTime: dayCompareSettings.startTime,
        dayCompareEndTime: dayCompareSettings.endTime,
        savedAt: new Date().toISOString()
      };
      setSavedPresets((currentPresets) => {
        const next = [preset, ...currentPresets.filter((item) => item.id !== preset.id)].slice(0, MAX_SAVED_PAIR_PRESETS);
        storeSavedPairPresets(next);
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
      storeSavedPairPresets(next);
      return next;
    });
  };

  const applySavedPreset = (preset: SavedPairSpreadPreset) => {
    const values = normalizePairForm(preset);
    const nextHours = clampHours(preset.hours);
    const nextIntervalSeconds = clampIntervalSeconds(preset.intervalSeconds);
    const nextShowDayCompare = Boolean(preset.showDayCompare);
    const nextDayCompareDays = clampDayCompareDays(preset.dayCompareDays);
    const nextDayCompareSettings = normalizeDayCompareSettings({
      mode: preset.dayCompareMode,
      startTime: preset.dayCompareStartTime,
      endTime: preset.dayCompareEndTime
    });
    form.setFieldsValue(values);
    setHours(nextHours);
    setIntervalSeconds(nextIntervalSeconds);
    setCustomInterval(intervalSelectValue(nextIntervalSeconds) === CUSTOM_INTERVAL_VALUE);
    setAutoRefresh(false);
    setShowDayCompare(nextShowDayCompare);
    setDayCompareDays(nextDayCompareDays);
    setDayCompareMode(nextDayCompareSettings.mode);
    setDayCompareStartTime(nextDayCompareSettings.startTime);
    setDayCompareEndTime(nextDayCompareSettings.endTime);
    setDayCompareSeries([]);
    setDayCompareError("");
    setError("");
    void runQuery({
      values,
      hours: nextHours,
      intervalSeconds: nextIntervalSeconds,
      dayCompareEnabled: nextShowDayCompare,
      dayCompareDays: nextDayCompareDays,
      dayCompareSettings: nextDayCompareSettings
    });
  };

  const current = result?.current;
  const spreadPct = current?.spread_pct ?? result?.spread_pct.current ?? null;
  const spreadTone = typeof spreadPct === "number" ? (spreadPct < 0 ? "negative" : "positive") : "neutral";
  const ratio =
    current && result && current.leg1.price > 0
      ? (current.leg2.price * result.leg2_multiplier) / current.leg1.price
      : null;
  const leftPremiumLeg = supportsPremiumIndexLeg(result?.leg1) ? result.leg1 : null;
  const rightPremiumLeg = supportsPremiumIndexLeg(result?.leg2) ? result.leg2 : null;
  const premiumLinkHours = result?.hours ?? hours;
  const premiumLinkIntervalSeconds = result ? resultIntervalSeconds(result) : intervalSeconds;
  const fundingRecordSupported = supportsFundingRecord(result);
  const fundingRecordWatched = Boolean(fundingRecordStatus?.watched);
  const fundingRecordSampleCount = fundingRecordStatus?.item?.sample_count ?? minuteFundingDiffRows.length;
  const dayCompareRangeTag =
    dayCompareSettings.mode === "custom" && dayCompareSettings.startTime && dayCompareSettings.endTime
      ? `${dayCompareSettings.startTime}-${dayCompareSettings.endTime}`
      : "";
  const dayCompareChartConfig = useMemo(() => {
    const fallbackHours = result?.hours ?? hours;
    const fallbackIntervalSeconds = result
      ? historicalCompareIntervalSeconds(resultIntervalSeconds(result))
      : historicalCompareIntervalSeconds(intervalSeconds);
    if (!result || dayCompareSettings.mode !== "custom" || !dayCompareSettings.startTime || !dayCompareSettings.endTime) {
      return {
        hours: fallbackHours,
        intervalSeconds: fallbackIntervalSeconds,
        rangeLabel: dayCompareRangeTag
      };
    }
    try {
      const plan = buildDayCompareWindowPlan(result, dayCompareSettings);
      return {
        hours: plan.durationHours,
        intervalSeconds: plan.intervalSeconds,
        rangeLabel: plan.rangeLabel
      };
    } catch {
      return {
        hours: fallbackHours,
        intervalSeconds: fallbackIntervalSeconds,
        rangeLabel: dayCompareRangeTag
      };
    }
  }, [dayCompareRangeTag, dayCompareSettings, hours, intervalSeconds, result]);

  return (
    <div className="page pair-monitor-page pair-terminal-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      {showPremiumCompare && premiumError ? <Alert type="error" message={premiumError} showIcon /> : null}
      {showDayCompare && dayCompareError ? <Alert type="warning" message={dayCompareError} showIcon /> : null}
      {fundingRecordError ? <Alert type="warning" message={fundingRecordError} showIcon /> : null}
      {result?.warnings.length ? <Alert type="warning" message={result.warnings.join("；")} showIcon /> : null}

      <section className="pair-query-panel">
        <Form form={form} initialValues={initialCachedState?.values ?? defaultFormValues} disabled={loading}>
          <div className={customInterval ? "pair-query-bar pair-query-bar-custom" : "pair-query-bar"}>
            <Form.Item name="leg1_exchange" rules={[{ required: true }]} className="pair-query-item">
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item name="leg1_market_type" rules={[{ required: true }]} className="pair-query-market-type">
              <Select options={marketTypeOptions} />
            </Form.Item>
            <Form.Item name="leg1_symbol" rules={[{ required: true, message: "请输入左标的" }]} className="pair-query-contract">
              <Input addonBefore="左标的" placeholder="SKHY" />
            </Form.Item>
            <Form.Item name="leg2_exchange" rules={[{ required: true }]} className="pair-query-item">
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item name="leg2_market_type" rules={[{ required: true }]} className="pair-query-market-type">
              <Select options={marketTypeOptions} />
            </Form.Item>
            <Form.Item name="leg2_symbol" rules={[{ required: true, message: "请输入右标的" }]} className="pair-query-contract">
              <Input addonBefore="右标的" placeholder="SKHYNIX" />
            </Form.Item>
            <Form.Item
              name="leg2_multiplier"
              rules={[{ required: true, type: "number", min: 0.000001, message: "倍率必须大于0" }]}
              className="pair-query-multiplier"
            >
              <InputNumber addonBefore="右侧倍率" min={0.000001} step={1} />
            </Form.Item>
            <InputNumber
              addonBefore="小时"
              className="pair-query-hours"
              min={1}
              max={720}
              precision={0}
              step={1}
              value={hours}
              onChange={(value) => setHours(clampHours(value))}
            />
            <Select
              className="pair-query-select"
              value={customInterval ? CUSTOM_INTERVAL_VALUE : intervalSelectValue(intervalSeconds)}
              options={[
                ...intervalOptions,
                { label: "自定义", value: CUSTOM_INTERVAL_VALUE }
              ]}
              onChange={(value) => {
                if (value === CUSTOM_INTERVAL_VALUE) {
                  setCustomInterval(true);
                } else {
                  setCustomInterval(false);
                  setIntervalSeconds(value);
                }
              }}
            />
            {customInterval ? (
              <InputNumber
                addonBefore="自定义秒"
                aria-label="自定义秒"
                className="pair-query-custom-interval"
                min={5}
                max={86_400}
                precision={0}
                step={1}
                value={intervalSeconds}
                onChange={(value) => setIntervalSeconds(clampIntervalSeconds(value))}
              />
            ) : null}
            <Form.Item className="pair-query-refresh">
              <Switch checked={autoRefresh} checkedChildren="自动" unCheckedChildren="手动" onChange={setAutoRefresh} />
            </Form.Item>
            <div className="pair-query-actions">
              <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void runQuery()}>
                查询
              </Button>
              <Button icon={<SaveOutlined />} disabled={loading} onClick={() => void saveCurrentPreset()}>
                保存
              </Button>
            </div>
          </div>
        </Form>
        <div className="pair-query-options">
          <Typography.Text className="pair-query-option-label">图表扩展</Typography.Text>
          <Switch
            checked={showPremiumCompare}
            checkedChildren="溢价对比"
            unCheckedChildren="溢价对比"
            loading={premiumLoading}
            onChange={(checked) => {
              setShowPremiumCompare(checked);
              if (checked && result) {
                void loadPremiumCompare(result);
              }
            }}
          />
          <Typography.Text type="secondary">
            {showPremiumCompare ? "显示左右腿隐含溢价指数，自动刷新时追加最新点" : "关闭后只显示价差曲线"}
          </Typography.Text>
          <Switch
            checked={showDayCompare}
            checkedChildren="同时段"
            unCheckedChildren="同时段"
            loading={dayCompareLoading}
            onChange={(checked) => {
              setShowDayCompare(checked);
              if (result) {
                storeLastPairSpreadState(
                  pairFormFromResult(result),
                  result.hours,
                  resultIntervalSeconds(result),
                  result,
                  checked,
                  dayCompareDays,
                  dayCompareSettings
                );
              }
              if (checked && result) {
                void loadDayCompare(result, dayCompareDays, dayCompareSettings);
              }
              if (!checked) {
                setDayCompareSeries([]);
                setDayCompareError("");
              }
            }}
          />
          <Segmented
            size="small"
            className="pair-day-compare-mode"
            options={[
              { label: "跟随查询", value: "query" },
              { label: "指定时间", value: "custom" }
            ]}
            value={dayCompareMode}
            disabled={!showDayCompare}
            onChange={(value) => {
              const nextSettings = normalizeDayCompareSettings({
                ...dayCompareSettings,
                mode: value
              });
              setDayCompareMode(nextSettings.mode);
              if (result) {
                storeLastPairSpreadState(
                  pairFormFromResult(result),
                  result.hours,
                  resultIntervalSeconds(result),
                  result,
                  showDayCompare,
                  dayCompareDays,
                  nextSettings
                );
              }
              if (showDayCompare && result && nextSettings.mode === "query") {
                void loadDayCompare(result, dayCompareDays, nextSettings);
              } else if (nextSettings.mode === "custom") {
                setDayCompareSeries([]);
                setDayCompareError("");
              }
            }}
          />
          <InputNumber
            aria-label="同时段对比天数"
            className="pair-day-compare-days"
            min={2}
            max={MAX_DAY_COMPARE_DAYS}
            precision={0}
            size="small"
            value={dayCompareDays}
            disabled={!showDayCompare}
            addonBefore="天数"
            onChange={(value) => {
              const nextDays = clampDayCompareDays(Number(value));
              setDayCompareDays(nextDays);
              if (result) {
                storeLastPairSpreadState(
                  pairFormFromResult(result),
                  result.hours,
                  resultIntervalSeconds(result),
                  result,
                  showDayCompare,
                  nextDays,
                  dayCompareSettings
                );
              }
              if (showDayCompare && result) {
                void loadDayCompare(result, nextDays, dayCompareSettings);
              }
            }}
          />
          {dayCompareMode === "custom" ? (
            <>
              <Input
                aria-label="同时段开始时间"
                className="pair-day-compare-time"
                type="time"
                size="small"
                value={dayCompareStartTime}
                disabled={!showDayCompare}
                addonBefore="开始"
                onChange={(event) => {
                  const nextSettings = normalizeDayCompareSettings({
                    ...dayCompareSettings,
                    startTime: event.target.value
                  });
                  setDayCompareStartTime(nextSettings.startTime);
                  if (result) {
                    storeLastPairSpreadState(
                      pairFormFromResult(result),
                      result.hours,
                      resultIntervalSeconds(result),
                      result,
                      showDayCompare,
                      dayCompareDays,
                      nextSettings
                    );
                  }
                  if (showDayCompare) {
                    setDayCompareSeries([]);
                    setDayCompareError("");
                  }
                }}
              />
              <Input
                aria-label="同时段结束时间"
                className="pair-day-compare-time"
                type="time"
                size="small"
                value={dayCompareEndTime}
                disabled={!showDayCompare}
                addonBefore="结束"
                onChange={(event) => {
                  const nextSettings = normalizeDayCompareSettings({
                    ...dayCompareSettings,
                    endTime: event.target.value
                  });
                  setDayCompareEndTime(nextSettings.endTime);
                  if (result) {
                    storeLastPairSpreadState(
                      pairFormFromResult(result),
                      result.hours,
                      resultIntervalSeconds(result),
                      result,
                      showDayCompare,
                      dayCompareDays,
                      nextSettings
                    );
                  }
                  if (showDayCompare) {
                    setDayCompareSeries([]);
                    setDayCompareError("");
                  }
                }}
              />
              <Button
                size="small"
                icon={<ReloadOutlined />}
                disabled={!showDayCompare || !result}
                loading={dayCompareLoading}
                onClick={() => {
                  if (result) {
                    void loadDayCompare(result, dayCompareDays, dayCompareSettings);
                  }
                }}
              >
                查询同时段
              </Button>
            </>
          ) : null}
        </div>
        {savedPresets.length ? (
          <div className="pair-saved-presets">
            <Typography.Text className="pair-saved-title">已保存</Typography.Text>
            <div className="pair-saved-list">
              {savedPresets.map((preset) => (
                <Tag
                  key={preset.id}
                  closable
                  className="pair-saved-tag"
                  onClick={() => applySavedPreset(preset)}
                  onClose={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    removeSavedPreset(preset.id);
                  }}
                >
                  <SavedPairPresetContent preset={preset} />
                  <span className="pair-saved-meta">
                    {durationLabel(preset.hours)} · {intervalLabel(preset.intervalSeconds)}
                  </span>
                </Tag>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="pair-metric-grid">
        <MetricCard
          label="最新均值价差率"
          value={signedPct(spreadPct)}
            sub={
              result
              ? `实时价差 = ${rightLegLabel(result)} - ${leftLegLabel(result)}`
              : "等待查询"
          }
          tone={spreadTone}
        />
        <MetricCard
          label={leftLegLabel(result)}
          value={price(current?.leg1.price)}
          sub={current ? priceFieldLabels[current.leg1.price_field] : "-"}
          action={
            leftPremiumLeg ? (
              <Button
                size="small"
                type="link"
                icon={<LineChartOutlined />}
                onClick={() =>
                  openPremiumIndexFromLeg(leftPremiumLeg, premiumLinkHours, premiumLinkIntervalSeconds, result)
                }
              >
                查看溢价指数
              </Button>
            ) : null
          }
        />
        <MetricCard
          label={rightLegLabel(result)}
          value={price(current?.leg2.price)}
          sub={current ? priceFieldLabels[current.leg2.price_field] : "-"}
          action={
            rightPremiumLeg ? (
              <Button
                size="small"
                type="link"
                icon={<LineChartOutlined />}
                onClick={() =>
                  openPremiumIndexFromLeg(rightPremiumLeg, premiumLinkHours, premiumLinkIntervalSeconds, result)
                }
              >
                查看溢价指数
              </Button>
            ) : null
          }
        />
        <MetricCard
          label="差价"
          value={price(current?.spread_abs ?? result?.spread_abs.current)}
          sub={ratio ? `倍率 ${compactNumber(ratio, 4)}x` : "-"}
          tone={spreadTone}
        />
        <MetricCard
          label="周期"
          value={intervalLabel(result ? resultIntervalSeconds(result) : intervalSeconds)}
          sub={result ? `${dataRangeLabel(result, hours)} · ${fullTime(result.observed_at)}` : durationLabel(hours)}
        />
      </section>

      <PairSpreadChart result={result} />
      {showDayCompare ? (
        <PairDayCompareChart
          series={dayCompareSeries}
          loading={dayCompareLoading}
          hours={dayCompareChartConfig.hours}
          intervalSeconds={dayCompareChartConfig.intervalSeconds}
          rangeLabel={dayCompareChartConfig.rangeLabel}
        />
      ) : null}
      <PairPriceChart result={result} />

      {showPremiumCompare ? <PairPremiumCompareChart comparison={premiumCompare} loading={premiumLoading} /> : null}

      <section className="pair-funding-grid">
        <div className="pair-detail-card pair-funding-diff-card">
          <div className="pair-detail-head">
            <Typography.Title level={5}>资金费率差</Typography.Title>
            <div className="pair-funding-diff-tools">
              {latestFundingDiff ? (
                <span className="pair-funding-diff-latest">
                  <Typography.Text type="secondary">最新净费率</Typography.Text>
                  <FundingRateValue value={latestFundingDiff.net_rate_pct} strong />
                </span>
              ) : null}
              <Tag color="blue">右-左</Tag>
              <Tag>{fundingDiffRows.length} 条</Tag>
              {result ? (
                fundingRecordSupported ? (
                  <>
                    <Tag color={fundingRecordWatched ? "green" : undefined}>
                      {fundingRecordWatched ? "分钟记录已开启" : "未记录分钟费率"}
                    </Tag>
                    {fundingRecordWatched ? <Tag>{fundingRecordSampleCount} 点</Tag> : <Tag>1分钟</Tag>}
                    <Button
                      size="small"
                      type={fundingRecordWatched ? "default" : "primary"}
                      icon={fundingRecordWatched ? <DeleteOutlined /> : <LineChartOutlined />}
                      loading={fundingRecordLoading}
                      onClick={() => void toggleFundingRecord()}
                    >
                      {fundingRecordWatched ? "停止记录" : "开始记录"}
                    </Button>
                  </>
                ) : (
                  <Tag>分钟记录仅合约对</Tag>
                )
              ) : null}
            </div>
          </div>
          <Table<FundingRateDiffRow>
            rowKey={(row) => `${row.source}-${row.funding_time}`}
            columns={fundingDiffColumns}
            dataSource={fundingDiffRows}
            loading={loading}
            pagination={{ pageSize: 8 }}
            size="small"
            tableLayout="auto"
            scroll={{ x: "max-content" }}
          />
        </div>
        {fundingRecordWatched ? (
          <PairMinuteFundingDiffChart
            rows={minuteFundingDiffRows}
            status={fundingRecordStatus}
            loading={fundingRecordLoading}
          />
        ) : null}
      </section>

      <section className="pair-detail-grid">
        <div className="pair-detail-card pair-spread-points-card">
          <div className="pair-detail-head">
            <Typography.Title level={5}>最近价差</Typography.Title>
            <Button icon={<ReloadOutlined />} disabled={!result} loading={loading} onClick={rerun}>
              重查
            </Button>
          </div>
          <Table<PairSpreadPoint>
            rowKey={(point) => point.bucket_at}
            columns={pointColumns}
            dataSource={recentPoints}
            loading={loading}
            pagination={{ pageSize: 10 }}
            size="small"
            tableLayout="fixed"
            scroll={{ x: 640 }}
          />
        </div>
      </section>
    </div>
  );
}
