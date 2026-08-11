import {
  DeleteOutlined,
  EditOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
  StopOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  blockNegativeBasisExchange,
  blockNegativeBasisExchangeSymbol,
  blockNegativeBasisSymbol,
  collectNegativeBasisWatchItem,
  deleteNegativeBasisWatchItem,
  getRiskSettings,
  getNegativeBasisMonitorStatus,
  listNegativeBasisExchanges,
  queryNegativeBasis,
  refreshNegativeBasisAutoScan,
  unblockNegativeBasisExchange,
  unblockNegativeBasisExchangeSymbol,
  unblockNegativeBasisSymbol,
  updateNegativeBasisAutoScanSettings,
  updateRiskSettings,
  upsertNegativeBasisWatchItem
} from "../api/client";
import type {
  MarketType,
  NegativeBasisAlertEvent,
  NegativeBasisAnalysisResult,
  NegativeBasisAutoCandidate,
  NegativeBasisHourlyStatPoint,
  NegativeBasisMonitorStatus,
  NegativeBasisPoint,
  NegativeBasisSignalLevel,
  NegativeBasisSignalSample,
  NegativeBasisThresholdState,
  NegativeBasisWatchItem,
  RiskSettings,
  SymbolAlias
} from "../api/types";

dayjs.extend(utc);

type NegativeBasisFormValues = NegativeBasisWatchItem & {
  custom_symbols: boolean;
};

type SymbolAliasRow = SymbolAlias & {
  key: string;
  index: number;
};

const exchangeNames: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  binance_alpha: "Binance Alpha",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "Hyperliquid",
  okx: "OKX"
};

const aliasMarketTypeOptions: Array<{ label: string; value: MarketType }> = [
  { label: "现货", value: "spot" },
  { label: "合约", value: "future" }
];

const aliasMarketTypeMeta: Record<MarketType | "all", { label: string; color?: string }> = {
  all: { label: "全部" },
  spot: { label: "现货", color: "green" },
  future: { label: "合约", color: "blue" }
};

const levelMeta: Record<NegativeBasisSignalLevel, { label: string; color: string }> = {
  none: { label: "未触发", color: "default" },
  watch: { label: "观察", color: "blue" },
  building: { label: "启动", color: "cyan" },
  confirmed: { label: "确认", color: "purple" },
  strong: { label: "强信号", color: "orange" },
  extreme: { label: "过热", color: "red" }
};

const thresholdOrder: NegativeBasisSignalLevel[] = [
  "watch",
  "building",
  "confirmed",
  "strong",
  "extreme"
];

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now()}${Math.random().toString(16).slice(2)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

function emptyWatch(): NegativeBasisWatchItem {
  const now = nowIso();
  return {
    id: newId(),
    auto_managed: false,
    enabled: true,
    symbol: "PROMUSDT",
    spot_exchange: "binance",
    future_exchange: "gate",
    spot_symbol: "PROMUSDT",
    future_symbol: "PROMUSDT",
    future_multiplier: 1,
    interval_seconds: 60,
    lookback_hours: 4,
    retention_hours: 720,
    watch_threshold_pct: 0.5,
    building_threshold_pct: 1,
    confirmed_threshold_pct: 2,
    strong_threshold_pct: 3,
    extreme_threshold_pct: 10,
    watch_consecutive_hits: 3,
    building_consecutive_hits: 3,
    confirmed_consecutive_hits: 3,
    strong_consecutive_hits: 2,
    extreme_consecutive_hits: 1,
    spot_volume_growth_threshold: 3,
    oi_confirmed_growth_pct: 20,
    oi_strong_growth_pct: 30,
    min_spot_hourly_volume_usdt: 0,
    alert_min_level: "watch",
    cooldown_seconds: 900,
    note: "",
    created_at: now,
    updated_at: now
  };
}

function normalizeSymbol(value: string | null | undefined): string {
  const normalized = String(value ?? "")
    .trim()
    .toUpperCase()
    .replace(/[-_/]/g, "");
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

function normalizeAliasSymbol(value: string | null | undefined): string {
  const normalized = String(value ?? "")
    .trim()
    .toUpperCase()
    .replace(/[-_/]/g, "");
  if (!normalized) {
    return "";
  }
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

function normalizeSymbolAlias(value: SymbolAlias): SymbolAlias {
  const multiplier = Number(value.price_multiplier ?? 1);
  return {
    exchange: String(value.exchange ?? "").trim().toLowerCase(),
    symbol: normalizeAliasSymbol(value.symbol),
    canonical_symbol: normalizeAliasSymbol(value.canonical_symbol),
    market_type: value.market_type ?? null,
    price_multiplier: Number.isFinite(multiplier) && multiplier > 0 ? multiplier : 1
  };
}

function normalizeSymbolAliases(values: SymbolAlias[] | null | undefined): SymbolAlias[] {
  return (values ?? [])
    .map(normalizeSymbolAlias)
    .filter((item) => item.exchange && item.symbol && item.canonical_symbol);
}

function shortSymbol(symbol: string | null | undefined): string {
  return String(symbol ?? "").replace(/(?:USDT|USDC|USD)$/iu, "");
}

function exchangeText(exchange: string): string {
  return exchangeNames[exchange] ?? exchange;
}

function exchangeSymbolKey(exchange: string, symbol: string): string {
  return `${exchange.toLowerCase()}:${normalizeSymbol(symbol)}`;
}

function exchangeSymbolText(key: string): string {
  const [exchange, symbol] = key.split(":");
  return `${exchangeText(exchange ?? "")} ${shortSymbol(symbol)}`;
}

function time(value: string | null | undefined, seconds = false): string {
  return value ? dayjs.utc(value).utcOffset(8).format(seconds ? "MM-DD HH:mm:ss" : "MM-DD HH:mm") : "-";
}

function pct(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`
    : "-";
}

function numberText(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "")
    : "-";
}

function aliasMultiplierText(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "1";
  }
  if (Math.abs(value - 1) < 0.000_000_001) {
    return "1";
  }
  if (value >= 1) {
    return `${numberText(value, 6)}:1 (${numberText(value, 6)})`;
  }
  return `1:${numberText(1 / value, 6)} (${numberText(value, 8)})`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (Math.abs(value) >= 100) {
    return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (Math.abs(value) >= 1) {
    return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(6);
}

function money(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(2)}B`;
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toFixed(0);
}

function levelTag(level: NegativeBasisSignalLevel) {
  const meta = levelMeta[level];
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function sameSymbolMode(item: NegativeBasisWatchItem): boolean {
  return (
    shortSymbol(item.symbol) === shortSymbol(item.spot_symbol ?? item.symbol) &&
    shortSymbol(item.symbol) === shortSymbol(item.future_symbol ?? item.symbol) &&
    Math.abs(item.future_multiplier - 1) < 0.000_000_001
  );
}

function candidateUsesMapping(item: NegativeBasisAutoCandidate): boolean {
  const spotSymbol = item.spot_symbol ?? item.symbol;
  const futureSymbol = item.future_symbol ?? item.symbol;
  return (
    shortSymbol(spotSymbol) !== shortSymbol(item.symbol) ||
    shortSymbol(futureSymbol) !== shortSymbol(item.symbol) ||
    Math.abs((item.future_multiplier ?? 1) - 1) > 0.000_000_001
  );
}

function formValuesFromWatch(item: NegativeBasisWatchItem): NegativeBasisFormValues {
  return {
    ...item,
    custom_symbols: !sameSymbolMode(item)
  };
}

function payloadFromForm(base: NegativeBasisWatchItem, values: NegativeBasisFormValues): NegativeBasisWatchItem {
  const symbol = normalizeSymbol(values.symbol);
  const custom = Boolean(values.custom_symbols);
  const { custom_symbols: _customSymbols, ...watchValues } = values;
  return {
    ...base,
    ...watchValues,
    symbol,
    spot_symbol: custom ? normalizeSymbol(values.spot_symbol ?? symbol) : symbol,
    future_symbol: custom ? normalizeSymbol(values.future_symbol ?? symbol) : symbol,
    future_multiplier: custom ? Number(values.future_multiplier || 1) : 1,
    updated_at: nowIso()
  };
}

function queryFromPayload(item: NegativeBasisWatchItem) {
  const custom = !sameSymbolMode(item);
  return {
    symbol: item.symbol,
    spot_exchange: item.spot_exchange,
    future_exchange: item.future_exchange,
    spot_symbol: custom ? item.spot_symbol ?? undefined : undefined,
    future_symbol: custom ? item.future_symbol ?? undefined : undefined,
    future_multiplier: item.future_multiplier,
    hours: item.lookback_hours,
    watch_threshold_pct: item.watch_threshold_pct,
    building_threshold_pct: item.building_threshold_pct,
    confirmed_threshold_pct: item.confirmed_threshold_pct,
    strong_threshold_pct: item.strong_threshold_pct,
    extreme_threshold_pct: item.extreme_threshold_pct,
    watch_consecutive_hits: item.watch_consecutive_hits,
    building_consecutive_hits: item.building_consecutive_hits,
    confirmed_consecutive_hits: item.confirmed_consecutive_hits,
    strong_consecutive_hits: item.strong_consecutive_hits,
    extreme_consecutive_hits: item.extreme_consecutive_hits,
    spot_volume_growth_threshold: item.spot_volume_growth_threshold,
    oi_confirmed_growth_pct: item.oi_confirmed_growth_pct,
    oi_strong_growth_pct: item.oi_strong_growth_pct,
    min_spot_hourly_volume_usdt: item.min_spot_hourly_volume_usdt
  };
}

function hourlyTicks(points: NegativeBasisPoint[], widthMs: number): number[] {
  if (!points.length) {
    return [];
  }
  const startMs = dayjs.utc(points[0].bucket_at).valueOf();
  const endMs = dayjs.utc(points[points.length - 1].bucket_at).valueOf();
  if (widthMs <= 24 * 3600_000) {
    const firstHour = dayjs.utc(startMs).startOf("hour").add(1, "hour");
    const ticks: number[] = [startMs];
    let cursor = firstHour;
    while (cursor.valueOf() < endMs) {
      ticks.push(cursor.valueOf());
      cursor = cursor.add(1, "hour");
    }
    ticks.push(endMs);
    return Array.from(new Set(ticks));
  }
  return Array.from({ length: 7 }, (_, index) => startMs + ((endMs - startMs) * index) / 6);
}

function NegativeBasisChart({ analysis }: { analysis: NegativeBasisAnalysisResult | null }) {
  const points = analysis?.points ?? [];
  if (!points.length) {
    return <div className="negative-basis-chart-empty">暂无现货溢价曲线</div>;
  }

  const width = 1120;
  const height = 320;
  const padding = { top: 24, right: 28, bottom: 48, left: 58 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const startMs = dayjs.utc(points[0].bucket_at).valueOf();
  const endMs = dayjs.utc(points[points.length - 1].bucket_at).valueOf();
  const values = points.map((point) => point.spot_premium_pct);
  const thresholdValues = (analysis?.thresholds ?? []).map((item) => item.threshold_pct);
  const minValue = Math.min(0, ...values, ...thresholdValues);
  const maxValue = Math.max(0, ...values, ...thresholdValues);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const xAt = (bucketAt: string) =>
    padding.left + (startMs === endMs ? chartWidth / 2 : ((dayjs.utc(bucketAt).valueOf() - startMs) / (endMs - startMs)) * chartWidth);
  const xAtMs = (ms: number) => padding.left + (startMs === endMs ? chartWidth / 2 : ((ms - startMs) / (endMs - startMs)) * chartWidth);
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(point.bucket_at).toFixed(2)} ${yAt(point.spot_premium_pct).toFixed(2)}`)
    .join(" ");
  const ticks = hourlyTicks(points, endMs - startMs);

  return (
    <div className="negative-basis-chart-wrap">
      <svg className="negative-basis-chart" role="img" aria-label="现货溢价负基差曲线" viewBox={`0 0 ${width} ${height}`}>
        <rect className="negative-basis-chart-bg" x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const y = padding.top + chartHeight * ratio;
          const value = max - (max - min) * ratio;
          return (
            <g key={ratio}>
              <line className="negative-basis-grid" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="negative-basis-axis" x={padding.left - 10} y={y + 4} textAnchor="end">
                {pct(value, 1)}
              </text>
            </g>
          );
        })}
        {ticks.map((tick, index) => {
          const x = xAtMs(tick);
          const label = dayjs.utc(tick).utcOffset(8).format(endMs - startMs <= 24 * 3600_000 ? "HH:mm" : "MM-DD HH:mm");
          return (
            <g key={`${tick}-${index}`}>
              <line className="negative-basis-time-grid" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="negative-basis-axis negative-basis-time-label" x={x} y={height - 14} textAnchor="middle">
                {label}
              </text>
            </g>
          );
        })}
        <line className="negative-basis-zero" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        {(analysis?.thresholds ?? []).map((threshold) => (
          <g key={threshold.name}>
            <line className={`negative-basis-threshold negative-basis-threshold-${threshold.name}`} x1={padding.left} y1={yAt(threshold.threshold_pct)} x2={padding.left + chartWidth} y2={yAt(threshold.threshold_pct)} />
            <text className="negative-basis-threshold-label" x={padding.left + chartWidth - 6} y={yAt(threshold.threshold_pct) - 4} textAnchor="end">
              {levelMeta[threshold.name].label} {threshold.threshold_pct}%
            </text>
          </g>
        ))}
        <path d={path} className="negative-basis-line" />
        {points.slice(-12).map((point) => (
          <circle key={point.bucket_at} className="negative-basis-point" cx={xAt(point.bucket_at)} cy={yAt(point.spot_premium_pct)} r="2.8">
            <title>{`${time(point.bucket_at, true)} 现货溢价 ${pct(point.spot_premium_pct, 3)}`}</title>
          </circle>
        ))}
      </svg>
    </div>
  );
}

function CurrentSignalPanel({ analysis }: { analysis: NegativeBasisAnalysisResult | null }) {
  const current = analysis?.current;
  const latestHour = analysis?.hourly_stats.slice().reverse().find((row) => row.spot_volume_growth !== null || row.open_interest_change_pct !== null);
  return (
    <div className="negative-basis-metrics">
      <Statistic title="当前信号" value={analysis ? levelMeta[analysis.signal_level].label : "-"} />
      <Statistic title="现货溢价" value={analysis?.spot_premium.current ?? 0} precision={3} suffix="%" valueStyle={{ color: (analysis?.spot_premium.current ?? 0) >= 0 ? "#b42318" : "#0f766e" }} />
      <Statistic title="评分" value={analysis?.score ?? 0} precision={1} />
      <Statistic title="现货价格" value={price(current?.spot_leg.price)} />
      <Statistic title="合约价格" value={price(current?.future_leg.price)} />
      <Statistic title="现货24h成交额" value={money(current?.spot_leg.volume_24h_usdt)} />
      <Statistic title="合约24h成交额" value={money(current?.future_leg.volume_24h_usdt)} />
      <Statistic title="OI" value={money(current?.future_leg.open_interest_usdt)} />
      <Statistic title="OI小时变化" value={latestHour?.open_interest_change_pct ?? 0} precision={2} suffix="%" />
      <Statistic title="多空比例" value={numberText(current?.future_leg.long_short_ratio, 2)} />
      <Statistic title="资金费率" value={current?.future_leg.funding_rate_pct ?? 0} precision={4} suffix="%" />
    </div>
  );
}

export function NegativeBasisMonitorPage() {
  const [form] = Form.useForm<NegativeBasisFormValues>();
  const [aliasForm] = Form.useForm<SymbolAlias>();
  const [status, setStatus] = useState<NegativeBasisMonitorStatus | null>(null);
  const [riskSettings, setRiskSettings] = useState<RiskSettings | null>(null);
  const [spotExchanges, setSpotExchanges] = useState<string[]>([]);
  const [futureExchanges, setFutureExchanges] = useState<string[]>([]);
  const [draft, setDraft] = useState<NegativeBasisWatchItem>(() => emptyWatch());
  const [selectedWatchId, setSelectedWatchId] = useState<string | undefined>();
  const [analysis, setAnalysis] = useState<NegativeBasisAnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoScanLoading, setAutoScanLoading] = useState(false);
  const [blockActionLoading, setBlockActionLoading] = useState(false);
  const [globalBlockExchange, setGlobalBlockExchange] = useState<string | undefined>();
  const [saving, setSaving] = useState(false);
  const [aliasSaving, setAliasSaving] = useState(false);
  const [showManualConfig, setShowManualConfig] = useState(false);
  const customSymbols = Form.useWatch("custom_symbols", form);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, exchanges, nextRiskSettings] = await Promise.all([
        getNegativeBasisMonitorStatus(),
        listNegativeBasisExchanges(),
        getRiskSettings()
      ]);
      setStatus(nextStatus);
      setRiskSettings({
        ...nextRiskSettings,
        symbol_aliases: normalizeSymbolAliases(nextRiskSettings.symbol_aliases)
      });
      setSpotExchanges(exchanges.spot);
      setFutureExchanges(exchanges.future);
      if (!selectedWatchId && nextStatus.watchlist.length > 0) {
        setSelectedWatchId(nextStatus.watchlist[0].id);
      }
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [selectedWatchId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 8000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    form.setFieldsValue(formValuesFromWatch(draft));
  }, [draft, form]);

  useEffect(() => {
    aliasForm.setFieldsValue({
      exchange: "gate",
      symbol: "",
      canonical_symbol: "",
      market_type: null,
      price_multiplier: 1
    });
  }, [aliasForm]);

  const selectedWatch = useMemo(
    () => status?.watchlist.find((item) => item.id === selectedWatchId),
    [selectedWatchId, status?.watchlist]
  );

  const latestSamples = useMemo(
    () =>
      (status?.latest_samples ?? [])
        .filter((sample) => !selectedWatchId || sample.watch_id === selectedWatchId)
        .slice(0, 80),
    [selectedWatchId, status?.latest_samples]
  );

  const autoScanSettings = status?.auto_scan_settings;
  const blockedExchanges = autoScanSettings?.blocked_exchanges ?? [];
  const blockedSymbols = autoScanSettings?.blocked_symbols ?? [];
  const blockedExchangeSymbols = autoScanSettings?.blocked_exchange_symbols ?? [];

  const exchangeOptionItems = (values: string[]) =>
    values.map((exchange) => ({ label: exchangeText(exchange), value: exchange }));

  const globalBlockExchangeOptions = useMemo(
    () =>
      Array.from(new Set([...spotExchanges, ...futureExchanges]))
        .sort((left, right) => exchangeText(left).localeCompare(exchangeText(right)))
        .map((exchange) => ({
          label: `${exchangeText(exchange)}${blockedExchanges.includes(exchange) ? "（已屏蔽）" : ""}`,
          value: exchange,
          disabled: blockedExchanges.includes(exchange)
        })),
    [blockedExchanges, futureExchanges, spotExchanges]
  );

  const aliasExchangeOptions = useMemo(
    () =>
      Array.from(new Set([...Object.keys(exchangeNames), ...spotExchanges, ...futureExchanges]))
        .sort((left, right) => exchangeText(left).localeCompare(exchangeText(right)))
        .map((exchange) => ({ label: exchangeText(exchange), value: exchange })),
    [futureExchanges, spotExchanges]
  );

  const symbolAliasRows = useMemo<SymbolAliasRow[]>(
    () =>
      normalizeSymbolAliases(riskSettings?.symbol_aliases).map((item, index) => ({
        ...item,
        key: `${item.exchange}:${item.market_type ?? "all"}:${item.symbol}:${item.canonical_symbol}:${index}`,
        index
      })),
    [riskSettings?.symbol_aliases]
  );

  const toggleAutoScanEnabled = async (enabled: boolean) => {
    if (!autoScanSettings) {
      return;
    }
    setBlockActionLoading(true);
    try {
      await updateNegativeBasisAutoScanSettings({
        ...autoScanSettings,
        enabled,
        updated_at: nowIso()
      });
      message.success(enabled ? "自动扫描已开启" : "自动扫描已暂停");
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const blockSymbol = async (symbol: string) => {
    setBlockActionLoading(true);
    try {
      await blockNegativeBasisSymbol(normalizeSymbol(symbol));
      message.success(`已屏蔽标的 ${shortSymbol(symbol)}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const unblockSymbol = async (symbol: string) => {
    setBlockActionLoading(true);
    try {
      await unblockNegativeBasisSymbol(normalizeSymbol(symbol));
      message.success(`已解除标的 ${shortSymbol(symbol)}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const blockExchange = async (exchange: string) => {
    setBlockActionLoading(true);
    try {
      await blockNegativeBasisExchange(exchange);
      message.success(`已屏蔽交易所 ${exchangeText(exchange)}`);
      if (globalBlockExchange === exchange) {
        setGlobalBlockExchange(undefined);
      }
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const unblockExchange = async (exchange: string) => {
    setBlockActionLoading(true);
    try {
      await unblockNegativeBasisExchange(exchange);
      message.success(`已解除交易所 ${exchangeText(exchange)}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const blockExchangeSymbol = async (exchange: string, symbol: string) => {
    setBlockActionLoading(true);
    try {
      await blockNegativeBasisExchangeSymbol(exchange, normalizeSymbol(symbol));
      message.success(`已屏蔽 ${exchangeText(exchange)} 的 ${shortSymbol(symbol)}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const unblockExchangeSymbol = async (key: string) => {
    const [exchange, symbol] = key.split(":");
    if (!exchange || !symbol) {
      return;
    }
    setBlockActionLoading(true);
    try {
      await unblockNegativeBasisExchangeSymbol(exchange, symbol);
      message.success(`已解除 ${exchangeSymbolText(key)}`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBlockActionLoading(false);
    }
  };

  const blockSelectedExchange = async () => {
    if (!globalBlockExchange) {
      message.warning("先选择要屏蔽的交易所");
      return;
    }
    await blockExchange(globalBlockExchange);
  };

  const saveSymbolAliases = async (aliases: SymbolAlias[], successText: string) => {
    if (!riskSettings) {
      message.warning("风险配置还没有加载完成");
      return;
    }
    setAliasSaving(true);
    try {
      const saved = await updateRiskSettings({
        ...riskSettings,
        symbol_aliases: normalizeSymbolAliases(aliases)
      });
      setRiskSettings({
        ...saved,
        symbol_aliases: normalizeSymbolAliases(saved.symbol_aliases)
      });
      message.success(successText);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAliasSaving(false);
    }
  };

  const addSymbolAlias = async () => {
    const alias = normalizeSymbolAlias(await aliasForm.validateFields());
    const current = normalizeSymbolAliases(riskSettings?.symbol_aliases);
    const existingIndex = current.findIndex(
      (item) =>
        item.exchange === alias.exchange &&
        item.symbol === alias.symbol &&
        (item.market_type ?? null) === (alias.market_type ?? null)
    );
    const next =
      existingIndex >= 0
        ? current.map((item, index) => (index === existingIndex ? alias : item))
        : [...current, alias];
    await saveSymbolAliases(next, existingIndex >= 0 ? "币名映射已更新" : "币名映射已新增");
    aliasForm.setFieldsValue({
      exchange: alias.exchange,
      symbol: "",
      canonical_symbol: "",
      market_type: null,
      price_multiplier: 1
    });
  };

  const deleteSymbolAlias = async (row: SymbolAliasRow) => {
    const current = normalizeSymbolAliases(riskSettings?.symbol_aliases);
    await saveSymbolAliases(
      current.filter((_, index) => index !== row.index),
      "币名映射已删除"
    );
  };

  const renderBlockButtons = (
    symbol: string,
    spotExchange: string,
    futureExchange: string,
    spotSymbol?: string | null,
    futureSymbol?: string | null
  ) => {
    const normalizedSymbol = normalizeSymbol(symbol);
    const normalizedSpotSymbol = normalizeSymbol(spotSymbol || symbol);
    const normalizedFutureSymbol = normalizeSymbol(futureSymbol || symbol);
    const spotKey = exchangeSymbolKey(spotExchange, normalizedSpotSymbol);
    const futureKey = exchangeSymbolKey(futureExchange, normalizedFutureSymbol);
    return (
      <Space
        size={4}
        wrap
        className="negative-basis-block-actions"
        onClick={(event) => event.stopPropagation()}
      >
        <Popconfirm
          title={`屏蔽标的 ${shortSymbol(symbol)}？`}
          onConfirm={() => void blockSymbol(symbol)}
        >
          <Button
            size="small"
            danger
            icon={<StopOutlined />}
            loading={blockActionLoading}
            disabled={blockedSymbols.includes(normalizedSymbol)}
          >
            屏蔽标的
          </Button>
        </Popconfirm>
        <Popconfirm
          title={`屏蔽 ${exchangeText(spotExchange)} 的 ${shortSymbol(normalizedSpotSymbol)}？`}
          onConfirm={() => void blockExchangeSymbol(spotExchange, normalizedSpotSymbol)}
        >
          <Button
            size="small"
            danger
            loading={blockActionLoading}
            disabled={blockedExchangeSymbols.includes(spotKey)}
          >
            屏蔽现货腿
          </Button>
        </Popconfirm>
        <Popconfirm
          title={`屏蔽 ${exchangeText(futureExchange)} 的 ${shortSymbol(normalizedFutureSymbol)}？`}
          onConfirm={() => void blockExchangeSymbol(futureExchange, normalizedFutureSymbol)}
        >
          <Button
            size="small"
            danger
            loading={blockActionLoading}
            disabled={blockedExchangeSymbols.includes(futureKey)}
          >
            屏蔽合约腿
          </Button>
        </Popconfirm>
      </Space>
    );
  };

  const renderBlocklist = () => (
    <div className="negative-basis-blockbar">
      <Space size={6} wrap className="negative-basis-global-block">
        <Typography.Text strong>全局屏蔽交易所</Typography.Text>
        <Select
          size="small"
          allowClear
          showSearch
          placeholder="选择交易所"
          value={globalBlockExchange}
          options={globalBlockExchangeOptions}
          optionFilterProp="label"
          style={{ width: 168 }}
          onChange={(value) => setGlobalBlockExchange(value)}
        />
        <Button
          size="small"
          danger
          icon={<StopOutlined />}
          loading={blockActionLoading}
          disabled={!globalBlockExchange || blockedExchanges.includes(globalBlockExchange)}
          onClick={() => void blockSelectedExchange()}
        >
          屏蔽交易所
        </Button>
      </Space>
      <div className="negative-basis-blocklist">
        <Typography.Text type="secondary">已屏蔽</Typography.Text>
        {blockedSymbols.map((symbol) => (
          <Tag
            key={`symbol-${symbol}`}
            color="red"
            closable={!blockActionLoading}
            onClose={(event) => {
              event.preventDefault();
              void unblockSymbol(symbol);
            }}
          >
            标的 {shortSymbol(symbol)}
          </Tag>
        ))}
        {blockedExchanges.map((exchange) => (
          <Tag
            key={`exchange-${exchange}`}
            color="volcano"
            closable={!blockActionLoading}
            onClose={(event) => {
              event.preventDefault();
              void unblockExchange(exchange);
            }}
          >
            交易所 {exchangeText(exchange)}
          </Tag>
        ))}
        {blockedExchangeSymbols.map((key) => (
          <Tag
            key={`exchange-symbol-${key}`}
            color="magenta"
            closable={!blockActionLoading}
            onClose={(event) => {
              event.preventDefault();
              void unblockExchangeSymbol(key);
            }}
          >
            交易所标的 {exchangeSymbolText(key)}
          </Tag>
        ))}
        {!blockedSymbols.length && !blockedExchanges.length && !blockedExchangeSymbols.length ? <Tag>无</Tag> : null}
      </div>
    </div>
  );

  const readPayload = async (): Promise<NegativeBasisWatchItem> => {
    const values = await form.validateFields();
    return payloadFromForm(draft, values);
  };

  const runQuery = async () => {
    setLoading(true);
    try {
      const payload = await readPayload();
      const result = await queryNegativeBasis(queryFromPayload(payload));
      setAnalysis(result);
      message.success("负基差分析已刷新");
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  const saveWatch = async () => {
    setSaving(true);
    try {
      const payload = await readPayload();
      const saved = await upsertNegativeBasisWatchItem(payload);
      setDraft(saved);
      setSelectedWatchId(saved.id);
      message.success("负基差监控已保存");
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const createWatch = () => {
    const item = emptyWatch();
    setDraft(item);
    setSelectedWatchId(undefined);
    setAnalysis(null);
    setShowManualConfig(true);
  };

  const editWatch = (item: NegativeBasisWatchItem) => {
    setDraft(item);
    setSelectedWatchId(item.id);
    setShowManualConfig(true);
  };

  const toggleWatch = async (item: NegativeBasisWatchItem, enabled: boolean) => {
    try {
      await upsertNegativeBasisWatchItem({ ...item, enabled, updated_at: nowIso() });
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const collectNow = async (item: NegativeBasisWatchItem) => {
    setLoading(true);
    try {
      const result = await collectNegativeBasisWatchItem(item.id);
      setAnalysis(result);
      message.success("已完成一次负基差采样");
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  const runAutoScan = async () => {
    setAutoScanLoading(true);
    try {
      const candidates = await refreshNegativeBasisAutoScan();
      message.success(`自动扫描完成，发现 ${candidates.length} 个候选`);
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setAutoScanLoading(false);
    }
  };

  const deleteWatch = async (item: NegativeBasisWatchItem) => {
    try {
      await deleteNegativeBasisWatchItem(item.id);
      message.success("已删除监控标的");
      if (selectedWatchId === item.id) {
        createWatch();
      }
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const symbolAliasColumns: ColumnsType<SymbolAliasRow> = [
    {
      title: "交易所",
      dataIndex: "exchange",
      width: 130,
      render: (value: string) => <Typography.Text strong>{exchangeText(value)}</Typography.Text>
    },
    {
      title: "类型",
      dataIndex: "market_type",
      width: 92,
      render: (value: MarketType | null | undefined) => {
        const meta = aliasMarketTypeMeta[value ?? "all"];
        return <Tag color={meta.color}>{meta.label}</Tag>;
      }
    },
    {
      title: "原始名",
      dataIndex: "symbol",
      width: 130,
      render: (value: string) => <Typography.Text code>{shortSymbol(value)}</Typography.Text>
    },
    {
      title: "映射名",
      dataIndex: "canonical_symbol",
      width: 140,
      render: (value: string) => <Typography.Text strong>{shortSymbol(value)}</Typography.Text>
    },
    {
      title: "价格汇率",
      dataIndex: "price_multiplier",
      width: 130,
      align: "right",
      render: (value: number) => aliasMultiplierText(value)
    },
    {
      title: "操作",
      width: 88,
      render: (_, row) => (
        <Popconfirm title="删除这个币名映射？" onConfirm={() => void deleteSymbolAlias(row)}>
          <Button type="text" danger icon={<DeleteOutlined />} loading={aliasSaving}>
            删除
          </Button>
        </Popconfirm>
      )
    }
  ];

  const renderCandidateMapping = (item: NegativeBasisAutoCandidate) => {
    if (!candidateUsesMapping(item)) {
      return <Tag>同名</Tag>;
    }
    const spotSymbol = item.spot_symbol ?? item.symbol;
    const futureSymbol = item.future_symbol ?? item.symbol;
    return (
      <Space size={4} wrap>
        {shortSymbol(spotSymbol) !== shortSymbol(item.symbol) ? (
          <Tag color="green">现货 {shortSymbol(spotSymbol)}→{shortSymbol(item.symbol)}</Tag>
        ) : null}
        {shortSymbol(futureSymbol) !== shortSymbol(item.symbol) ? (
          <Tag color="orange">合约 {shortSymbol(futureSymbol)}→{shortSymbol(item.symbol)}</Tag>
        ) : null}
        {Math.abs((item.future_multiplier ?? 1) - 1) > 0.000_000_001 ? (
          <Tag color="purple">倍率 {numberText(item.future_multiplier, 6)}</Tag>
        ) : null}
      </Space>
    );
  };

  const watchColumns: ColumnsType<NegativeBasisWatchItem> = [
    {
      title: "标的",
      dataIndex: "symbol",
      width: 96,
      render: (value: string) => <Typography.Text strong>{shortSymbol(value)}</Typography.Text>
    },
    {
      title: "状态",
      dataIndex: "enabled",
      width: 76,
      render: (_, item) => <Switch size="small" checked={item.enabled} onChange={(checked) => void toggleWatch(item, checked)} />
    },
    {
      title: "交易对",
      render: (_, item) => (
        <Space size={4} wrap>
          {item.auto_managed ? <Tag color="processing">自动</Tag> : <Tag>手动</Tag>}
          <Tag color="green">{exchangeText(item.spot_exchange)} 现货</Tag>
          <Tag color="orange">{exchangeText(item.future_exchange)} 合约</Tag>
          {sameSymbolMode(item) ? (
            <Tag>同标的</Tag>
          ) : (
            <Tooltip
              title={`现货 ${shortSymbol(item.spot_symbol ?? item.symbol)} / 合约 ${shortSymbol(item.future_symbol ?? item.symbol)} / 倍率 ${numberText(item.future_multiplier, 6)}`}
            >
              <Tag color="purple">自定义</Tag>
            </Tooltip>
          )}
        </Space>
      )
    },
    {
      title: "阈值",
      width: 160,
      render: (_, item) => `${item.watch_threshold_pct}/${item.building_threshold_pct}/${item.confirmed_threshold_pct}/${item.strong_threshold_pct}/${item.extreme_threshold_pct}%`
    },
    {
      title: "告警",
      width: 100,
      render: (_, item) => levelTag(item.alert_min_level)
    },
    {
      title: "操作",
      width: 440,
      render: (_, item) => (
        <Space size={4} wrap onClick={(event) => event.stopPropagation()}>
          <Button icon={<EditOutlined />} size="small" onClick={() => editWatch(item)} />
          <Button icon={<PlayCircleOutlined />} size="small" onClick={() => void collectNow(item)} />
          {item.auto_managed
            ? renderBlockButtons(
                item.symbol,
                item.spot_exchange,
                item.future_exchange,
                item.spot_symbol,
                item.future_symbol
              )
            : null}
          <Popconfirm title="删除这个负基差监控？" onConfirm={() => void deleteWatch(item)}>
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      )
    }
  ];

  const candidateColumns: ColumnsType<NegativeBasisAutoCandidate> = [
    {
      title: "标的",
      dataIndex: "symbol",
      width: 92,
      render: (value: string) => <Typography.Text strong>{shortSymbol(value)}</Typography.Text>
    },
    {
      title: "路线",
      width: 190,
      render: (_, item) => (
        <Space size={4} wrap>
          <Tag color="green">{exchangeText(item.spot_exchange)} 现货</Tag>
          <Tag color="orange">{exchangeText(item.future_exchange)} 合约</Tag>
        </Space>
      )
    },
    { title: "映射", width: 200, render: (_, item) => renderCandidateMapping(item) },
    { title: "级别", dataIndex: "signal_level", width: 88, render: levelTag },
    {
      title: "性价比",
      dataIndex: "selection_score",
      width: 92,
      align: "right",
      render: (value: number, item) => (
        <Tooltip title={item.selection_reasons.join(" / ") || "按溢价和现货、合约流动性综合排序"}>
          <Typography.Text strong>{numberText(value, 1)}</Typography.Text>
        </Tooltip>
      )
    },
    { title: "现货溢价", dataIndex: "spot_premium_pct", width: 100, align: "right", render: (value: number) => pct(value, 3) },
    { title: "价格", width: 150, render: (_, item) => `${price(item.spot_price)} / ${price(item.future_price)}` },
    { title: "现货24h", dataIndex: "spot_volume_24h_usdt", width: 110, align: "right", render: (value: number | null) => money(value) },
    { title: "合约24h", dataIndex: "future_volume_24h_usdt", width: 110, align: "right", render: (value: number | null) => money(value) },
    { title: "发现时间", dataIndex: "observed_at", width: 120, render: (value: string) => time(value) },
    {
      title: "操作",
      width: 430,
      render: (_, item) => {
        const watch = status?.watchlist.find((watchItem) => watchItem.id === item.id);
        return (
          <Space size={4} wrap onClick={(event) => event.stopPropagation()}>
            {watch ? (
              <Button size="small" icon={<PlayCircleOutlined />} onClick={() => void collectNow(watch)}>
                采样
              </Button>
            ) : (
              <Tag>待入池</Tag>
            )}
            {renderBlockButtons(
              item.symbol,
              item.spot_exchange,
              item.future_exchange,
              item.spot_symbol,
              item.future_symbol
            )}
          </Space>
        );
      }
    }
  ];

  const thresholdColumns: ColumnsType<NegativeBasisThresholdState> = [
    { title: "级别", dataIndex: "name", width: 90, render: levelTag },
    { title: "阈值", dataIndex: "threshold_pct", width: 88, render: (value: number) => `${value}%` },
    { title: "要求", width: 82, render: (_, row) => `${row.required_hits} 连续` },
    { title: "首次站上", dataIndex: "first_seen_at", width: 120, render: (value: string | null) => time(value) },
    { title: "首次连续", dataIndex: "first_consecutive_at", width: 120, render: (value: string | null) => time(value) },
    { title: "当前连续", dataIndex: "current_consecutive_hits", width: 90 },
    { title: "最大连续", dataIndex: "max_consecutive_hits", width: 90 },
    { title: "当前", dataIndex: "currently_active", width: 82, render: (value: boolean) => (value ? <Tag color="green">命中</Tag> : <Tag>未命中</Tag>) }
  ];

  const hourlyColumns: ColumnsType<NegativeBasisHourlyStatPoint> = [
    { title: "小时", dataIndex: "bucket_at", width: 110, render: (value: string) => time(value) },
    { title: "均值", dataIndex: "spot_premium_mean_pct", width: 84, align: "right", render: (value: number | null) => pct(value, 2) },
    { title: "最高", dataIndex: "spot_premium_max_pct", width: 84, align: "right", render: (value: number | null) => pct(value, 2) },
    { title: "收尾", dataIndex: "spot_premium_last_pct", width: 84, align: "right", render: (value: number | null) => pct(value, 2) },
    { title: "现货成交额", dataIndex: "spot_volume_usdt", width: 120, align: "right", render: (value: number | null) => money(value) },
    { title: "现货放大", dataIndex: "spot_volume_growth", width: 96, align: "right", render: (value: number | null) => (value ? `${value.toFixed(2)}x` : "-") },
    { title: "合约成交额", dataIndex: "future_volume_usdt", width: 120, align: "right", render: (value: number | null) => money(value) },
    { title: "OI", dataIndex: "open_interest_close_usdt", width: 110, align: "right", render: (value: number | null) => money(value) },
    { title: "OI变化", dataIndex: "open_interest_change_pct", width: 96, align: "right", render: (value: number | null) => pct(value, 2) },
    {
      title: "多/空",
      width: 110,
      render: (_, row) => `${numberText(row.long_account_pct, 1)} / ${numberText(row.short_account_pct, 1)}`
    },
    { title: "多空比", dataIndex: "long_short_ratio", width: 88, align: "right", render: (value: number | null) => numberText(value, 2) },
    { title: "费率", dataIndex: "funding_rate_pct", width: 88, align: "right", render: (value: number | null) => pct(value, 4) }
  ];

  const sampleColumns: ColumnsType<NegativeBasisSignalSample> = [
    { title: "时间", dataIndex: "observed_at", width: 122, render: (value: string) => time(value, true) },
    { title: "级别", dataIndex: "signal_level", width: 88, render: levelTag },
    { title: "溢价", dataIndex: "spot_premium_pct", width: 86, align: "right", render: (value: number | null) => pct(value, 3) },
    { title: "分数", dataIndex: "score", width: 76, align: "right", render: (value: number) => value.toFixed(1) },
    { title: "价格", width: 150, render: (_, row) => `${price(row.spot_price)} / ${price(row.future_price)}` },
    { title: "OI变化", dataIndex: "open_interest_change_pct", width: 92, align: "right", render: (value: number | null) => pct(value, 2) },
    { title: "多空比", dataIndex: "long_short_ratio", width: 82, align: "right", render: (value: number | null) => numberText(value, 2) },
    { title: "费率", dataIndex: "funding_rate_pct", width: 88, align: "right", render: (value: number | null) => pct(value, 4) },
    { title: "原因", dataIndex: "reasons", ellipsis: true, render: (reasons: string[]) => reasons.slice(0, 3).join("；") || "-" }
  ];

  const eventColumns: ColumnsType<NegativeBasisAlertEvent> = [
    { title: "时间", dataIndex: "created_at", width: 122, render: (value: string) => time(value, true) },
    { title: "标的", dataIndex: "symbol", width: 88, render: (value: string) => shortSymbol(value) },
    { title: "级别", dataIndex: "signal_level", width: 88, render: levelTag },
    { title: "溢价", dataIndex: "spot_premium_pct", width: 86, align: "right", render: (value: number | null) => pct(value, 3) },
    { title: "分数", dataIndex: "score", width: 76, align: "right", render: (value: number) => value.toFixed(1) },
    { title: "消息", dataIndex: "message", ellipsis: true }
  ];

  return (
    <div className="page negative-basis-page">
      <div className="toolbar">
        <div>
          <Typography.Title level={4}>负基差埋伏监控</Typography.Title>
          <Typography.Text type="secondary">后台自动扫描全市场现货溢价候选，命中后自动入池、采样并按策略上报。</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} loading={autoScanLoading} onClick={() => void runAutoScan()}>立即扫描</Button>
          <Button icon={<PlusOutlined />} onClick={createWatch}>高级配置</Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()}>刷新</Button>
        </Space>
      </div>

      {status?.latest_error ? <Alert type="warning" showIcon message={status.latest_error} /> : null}
      {status?.auto_scan_error ? <Alert type="warning" showIcon message={status.auto_scan_error} /> : null}
      {analysis?.warnings.map((warning) => (
        <Alert key={warning} type="warning" showIcon message={warning} className="negative-basis-warning" />
      ))}

      <div className="negative-basis-status">
        <Statistic title="后台状态" value={status?.running ? "运行中" : "未运行"} />
        <Statistic title="自动候选" value={status?.auto_candidate_count ?? 0} />
        <Statistic title="启用标的" value={status?.enabled_watch_count ?? 0} suffix={`/ ${status?.watch_count ?? 0}`} />
        <Statistic title="样本" value={status?.sample_count ?? 0} />
        <Statistic title="告警" value={status?.event_count ?? 0} />
      </div>

      <Card
        size="small"
        title="自动发现候选"
        extra={
          <Space wrap>
            <Switch
              size="small"
              checked={autoScanSettings?.enabled ?? false}
              loading={blockActionLoading}
              checkedChildren="自动"
              unCheckedChildren="暂停"
              onChange={(checked) => void toggleAutoScanEnabled(checked)}
            />
            <Tag color={status?.auto_scan_enabled ? "green" : undefined}>
              {autoScanSettings?.enabled === false
                ? "自动扫描暂停"
                : status?.auto_scan_enabled
                  ? "自动扫描开启"
                  : "等待行情快照"}
            </Tag>
            <Tag>上次 {time(status?.auto_scan_last_at)}</Tag>
            <Button size="small" icon={<ReloadOutlined />} loading={autoScanLoading} onClick={() => void runAutoScan()}>
              立即扫描
            </Button>
            <Button size="small" onClick={() => setShowManualConfig((value) => !value)}>
              {showManualConfig ? "收起高级配置" : "展开高级配置"}
            </Button>
          </Space>
        }
      >
        {renderBlocklist()}
        <Table
          rowKey="id"
          size="small"
          columns={candidateColumns}
          dataSource={status?.auto_candidates ?? []}
          pagination={{ pageSize: 8 }}
          scroll={{ x: 1610 }}
          locale={{ emptyText: <Empty description="暂未发现现货溢价候选" /> }}
        />
      </Card>

      <Card size="small" title="币名映射" className="negative-basis-alias-card" extra={<Tag>全局生效</Tag>}>
        <Form
          form={aliasForm}
          layout="vertical"
          initialValues={{
            exchange: "gate",
            symbol: "",
            canonical_symbol: "",
            market_type: null,
            price_multiplier: 1
          }}
          onFinish={() => void addSymbolAlias()}
        >
          <div className="negative-basis-alias-form">
            <Form.Item label="交易所" name="exchange" rules={[{ required: true }]}>
              <Select options={aliasExchangeOptions} showSearch optionFilterProp="label" />
            </Form.Item>
            <Form.Item label="类型" name="market_type">
              <Select allowClear options={aliasMarketTypeOptions} placeholder="全部" />
            </Form.Item>
            <Form.Item label="原始名" name="symbol" rules={[{ required: true, message: "请输入原始名" }]}>
              <Input placeholder="NEX" />
            </Form.Item>
            <Form.Item label="映射名" name="canonical_symbol" rules={[{ required: true, message: "请输入映射名" }]}>
              <Input placeholder="10000NEX" />
            </Form.Item>
            <Form.Item
              label="价格汇率"
              name="price_multiplier"
              rules={[{ required: true, type: "number", min: 0.000001 }]}
            >
              <InputNumber min={0.000001} step={1} />
            </Form.Item>
            <Form.Item className="negative-basis-alias-action">
              <Button type="primary" htmlType="submit" icon={<PlusOutlined />} loading={aliasSaving}>
                添加/更新
              </Button>
            </Form.Item>
          </div>
        </Form>
        <Table
          rowKey="key"
          size="small"
          columns={symbolAliasColumns}
          dataSource={symbolAliasRows}
          pagination={false}
          scroll={{ x: 760 }}
          locale={{ emptyText: <Empty description="暂无币名映射" /> }}
        />
      </Card>

      {showManualConfig ? (
        <div className="negative-basis-layout">
        <Card size="small" title="监控参数" className="negative-basis-config-card">
          <Form form={form} layout="vertical" initialValues={formValuesFromWatch(draft)}>
            <div className="negative-basis-form-grid">
              <Form.Item label="启用" name="enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="标的" name="symbol" rules={[{ required: true, message: "请输入标的" }]}>
                <Input placeholder="PROM" />
              </Form.Item>
              <Form.Item label="现货交易所" name="spot_exchange" rules={[{ required: true }]}>
                <Select options={exchangeOptionItems(spotExchanges)} showSearch />
              </Form.Item>
              <Form.Item label="合约交易所" name="future_exchange" rules={[{ required: true }]}>
                <Select options={exchangeOptionItems(futureExchanges)} showSearch />
              </Form.Item>
              <Form.Item label="自定义" name="custom_symbols" valuePropName="checked">
                <Switch />
              </Form.Item>
              {customSymbols ? (
                <>
                  <Form.Item label="现货标的" name="spot_symbol" rules={[{ required: true, message: "请输入现货标的" }]}>
                    <Input placeholder="PROM" />
                  </Form.Item>
                  <Form.Item label="合约标的" name="future_symbol" rules={[{ required: true, message: "请输入合约标的" }]}>
                    <Input placeholder="PROM" />
                  </Form.Item>
                  <Form.Item label="合约倍率" name="future_multiplier" rules={[{ required: true, type: "number", min: 0.000001 }]}>
                    <InputNumber min={0.000001} step={1} />
                  </Form.Item>
                </>
              ) : null}
              <Form.Item label="回看小时" name="lookback_hours">
                <InputNumber min={1} max={720} step={1} />
              </Form.Item>
              <Form.Item label="采样秒" name="interval_seconds">
                <InputNumber min={30} max={3600} step={30} />
              </Form.Item>
              <Form.Item label="保留小时" name="retention_hours">
                <InputNumber min={1} max={2160} step={24} />
              </Form.Item>
              <Form.Item label="观察%" name="watch_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="启动%" name="building_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="确认%" name="confirmed_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="强信号%" name="strong_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="过热%" name="extreme_threshold_pct">
                <InputNumber min={0} step={0.5} />
              </Form.Item>
              <Form.Item label="观察连续" name="watch_consecutive_hits">
                <InputNumber min={1} max={60} step={1} />
              </Form.Item>
              <Form.Item label="启动连续" name="building_consecutive_hits">
                <InputNumber min={1} max={60} step={1} />
              </Form.Item>
              <Form.Item label="确认连续" name="confirmed_consecutive_hits">
                <InputNumber min={1} max={60} step={1} />
              </Form.Item>
              <Form.Item label="强信号连续" name="strong_consecutive_hits">
                <InputNumber min={1} max={60} step={1} />
              </Form.Item>
              <Form.Item label="过热连续" name="extreme_consecutive_hits">
                <InputNumber min={1} max={60} step={1} />
              </Form.Item>
              <Form.Item label="现货量放大" name="spot_volume_growth_threshold">
                <InputNumber min={0} step={0.1} addonAfter="x" />
              </Form.Item>
              <Form.Item label="确认OI%" name="oi_confirmed_growth_pct">
                <InputNumber min={0} step={1} />
              </Form.Item>
              <Form.Item label="强信号OI%" name="oi_strong_growth_pct">
                <InputNumber min={0} step={1} />
              </Form.Item>
              <Form.Item label="告警级别" name="alert_min_level">
                <Select options={thresholdOrder.map((level) => ({ label: levelMeta[level].label, value: level }))} />
              </Form.Item>
              <Form.Item label="冷却秒" name="cooldown_seconds">
                <InputNumber min={0} max={86_400} step={60} />
              </Form.Item>
            </div>
            <Form.Item label="备注" name="note">
              <Input.TextArea rows={2} placeholder="例如：重点看零轴附近启动，过热后只做风险提示" />
            </Form.Item>
            <Space wrap>
              <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void runQuery()}>
                查询
              </Button>
              <Button icon={<SaveOutlined />} loading={saving} onClick={() => void saveWatch()}>
                保存监控
              </Button>
              {selectedWatch ? (
                <Button icon={<PlayCircleOutlined />} loading={loading} onClick={() => void collectNow(selectedWatch)}>
                  立即采样
                </Button>
              ) : null}
            </Space>
          </Form>
        </Card>

        <Card size="small" title="已保存" className="negative-basis-watch-card">
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            columns={watchColumns}
            dataSource={status?.watchlist ?? []}
            rowClassName={(item) => (item.id === selectedWatchId ? "negative-basis-row-active" : "")}
            onRow={(item) => ({
              onClick: () => {
                setSelectedWatchId(item.id);
                setDraft(item);
              }
            })}
            scroll={{ x: 1140 }}
          />
        </Card>
        </div>
      ) : (
        <Card size="small" title="后台监控池">
          <Table
            rowKey="id"
            size="small"
            pagination={{ pageSize: 10 }}
            columns={watchColumns}
            dataSource={status?.watchlist ?? []}
            rowClassName={(item) => (item.id === selectedWatchId ? "negative-basis-row-active" : "")}
            onRow={(item) => ({
              onClick: () => {
                setSelectedWatchId(item.id);
                setDraft(item);
              }
            })}
            scroll={{ x: 1140 }}
          />
        </Card>
      )}

      <Card size="small" title="当前信号">
        {analysis ? (
          <>
            <CurrentSignalPanel analysis={analysis} />
            <div className="negative-basis-reasons">
              {analysis.reasons.map((reason) => (
                <Tag key={reason} color={analysis.signal_level === "none" ? undefined : "blue"}>{reason}</Tag>
              ))}
            </div>
          </>
        ) : (
          <Empty description="先查询或对已保存标的立即采样" />
        )}
      </Card>

      <Card size="small" title="现货溢价曲线">
        <NegativeBasisChart analysis={analysis} />
      </Card>

      <div className="negative-basis-tables">
        <Card size="small" title="阈值命中">
          <Table
            rowKey="name"
            size="small"
            columns={thresholdColumns}
            dataSource={analysis?.thresholds ?? []}
            pagination={false}
            locale={{ emptyText: <Empty description="暂无阈值统计" /> }}
          />
        </Card>
        <Card size="small" title="每小时成交额 / OI / 多空">
          <Table
            rowKey="bucket_at"
            size="small"
            columns={hourlyColumns}
            dataSource={(analysis?.hourly_stats ?? []).slice().reverse()}
            pagination={{ pageSize: 24, showSizeChanger: false }}
            scroll={{ x: 1120 }}
            locale={{ emptyText: <Empty description="暂无小时统计" /> }}
          />
        </Card>
      </div>

      <Card size="small" title="最近样本">
        <Table
          rowKey={(row) => `${row.id ?? row.observed_at}-${row.watch_id}`}
          size="small"
          columns={sampleColumns}
          dataSource={latestSamples}
          loading={loading}
          pagination={{ pageSize: 12 }}
          scroll={{ x: 920 }}
        />
      </Card>

      <Card size="small" title="告警事件">
        <Table
          rowKey="id"
          size="small"
          columns={eventColumns}
          dataSource={status?.latest_events ?? []}
          pagination={{ pageSize: 8 }}
          scroll={{ x: 780 }}
        />
      </Card>
    </div>
  );
}
