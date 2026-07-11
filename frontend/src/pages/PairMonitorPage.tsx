import { ReloadOutlined, SaveOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Table,
  Tag,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import { queryPairSpread } from "../api/client";
import type {
  PairSpreadFundingPoint,
  PairSpreadPoint,
  PairSpreadPriceField,
  PairSpreadQueryResult
} from "../api/types";

dayjs.extend(utc);

type PairSpreadFormValues = {
  leg1_exchange: string;
  leg1_symbol: string;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_multiplier: number;
};

type SavedPairSpreadPreset = PairSpreadFormValues & {
  id: string;
  hours: number;
  intervalMinutes: number;
  savedAt: string;
};

const defaultFormValues: PairSpreadFormValues = {
  leg1_exchange: "bitget",
  leg1_symbol: "SKHY",
  leg2_exchange: "bitget",
  leg2_symbol: "SKHYNIX",
  leg2_multiplier: 10
};

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"].map(
  (value) => ({ label: value[0].toUpperCase() + value.slice(1), value })
);

const intervalOptions = [
  { label: "1分钟", value: 1 },
  { label: "5分钟", value: 5 },
  { label: "15分钟", value: 15 }
];

const PAIR_SPREAD_PRESETS_KEY = "taoli1.pairSpread.presets.v1";
const MAX_SAVED_PAIR_PRESETS = 24;

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

function normalizePairForm(values: PairSpreadFormValues): PairSpreadFormValues {
  return {
    leg1_exchange: values.leg1_exchange,
    leg1_symbol: values.leg1_symbol.trim().toUpperCase(),
    leg2_exchange: values.leg2_exchange,
    leg2_symbol: values.leg2_symbol.trim().toUpperCase(),
    leg2_multiplier: Number(values.leg2_multiplier)
  };
}

function pairPresetId(values: PairSpreadFormValues): string {
  const normalized = normalizePairForm(values);
  return [
    normalized.leg1_exchange,
    normalized.leg1_symbol,
    normalized.leg2_exchange,
    normalized.leg2_symbol,
    compactNumber(normalized.leg2_multiplier, 8)
  ].join("|");
}

function isSavedPreset(value: unknown): value is SavedPairSpreadPreset {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Partial<SavedPairSpreadPreset>;
  return (
    typeof item.id === "string" &&
    typeof item.leg1_exchange === "string" &&
    typeof item.leg1_symbol === "string" &&
    typeof item.leg2_exchange === "string" &&
    typeof item.leg2_symbol === "string" &&
    typeof item.leg2_multiplier === "number" &&
    typeof item.hours === "number" &&
    typeof item.intervalMinutes === "number" &&
    typeof item.savedAt === "string"
  );
}

function loadSavedPairPresets(): SavedPairSpreadPreset[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PAIR_SPREAD_PRESETS_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isSavedPreset).slice(0, MAX_SAVED_PAIR_PRESETS) : [];
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
  return spanHours <= 24 ? parsed.format("HH:mm") : parsed.format("MM-DD HH:mm");
}

function durationLabel(hours: number): string {
  if (hours < 24) {
    return `${hours}小时`;
  }
  return `${hours / 24}天`;
}

function dataRangeLabel(result: PairSpreadQueryResult | null, fallbackHours: number): string {
  if (!result?.first_seen_at || !result.last_seen_at) {
    return durationLabel(fallbackHours);
  }
  return `${time(result.first_seen_at)} - ${time(result.last_seen_at)}`;
}

function rightLegLabel(result: PairSpreadQueryResult | null): string {
  if (!result) {
    return "右合约";
  }
  const divisor = result.leg2_multiplier === 1 ? "" : `/${compactNumber(result.leg2_multiplier, 4)}`;
  return `${result.leg2.exchange} · ${result.leg2.symbol}${divisor}`;
}

function savedPresetLabel(preset: SavedPairSpreadPreset): string {
  const divisor = preset.leg2_multiplier === 1 ? "" : `/${compactNumber(preset.leg2_multiplier, 4)}`;
  return `${preset.leg1_exchange} ${preset.leg1_symbol} / ${preset.leg2_exchange} ${preset.leg2_symbol}${divisor}`;
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
  return Math.max(end.diff(start, "minute") / 60, 0);
}

function chartTicks(points: PairSpreadPoint[], maxTicks = 7): Array<{ index: number; point: PairSpreadPoint }> {
  if (points.length <= 1) {
    return points.map((point, index) => ({ index, point }));
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

function chartTurningPoints(points: PairSpreadPoint[], maxLabels = 14): Array<{
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
  const minProminence = Math.max(0.012, span * 0.006);
  const candidates: Array<{ index: number; kind: "peak" | "trough"; score: number }> = [];
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

  const minIndexDistance = Math.max(6, Math.floor(points.length / 32));
  const selected: Array<{ index: number; kind: "peak" | "trough"; score: number }> = [];
  for (const candidate of candidates.sort((a, b) => b.score - a.score)) {
    if (selected.some((item) => Math.abs(item.index - candidate.index) < minIndexDistance)) {
      continue;
    }
    selected.push(candidate);
    if (selected.length >= maxLabels) {
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
  tone = "neutral"
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  return (
    <div className={`pair-metric-card pair-metric-${tone}`}>
      <Typography.Text className="pair-metric-label">{label}</Typography.Text>
      <div className="pair-metric-value">{value}</div>
      <Typography.Text className="pair-metric-sub">{sub}</Typography.Text>
    </div>
  );
}

function PairSpreadChart({ result }: { result: PairSpreadQueryResult | null }) {
  const points = result?.points ?? [];
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
  const ticks = chartTicks(points, spanHours >= 168 ? 7 : 6);
  const turningPoints = chartTurningPoints(points);

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
            {result.leg1.exchange} / {result.leg2.exchange}
          </Tag>
          <Tag>{result.point_count} 点</Tag>
          <Tag>{result.interval_minutes}m 周期</Tag>
        </div>
        <Typography.Text type="secondary">最新 {fullTime(result.observed_at)}</Typography.Text>
      </div>
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

const fundingColumns: ColumnsType<PairSpreadFundingPoint> = [
  {
    title: "交易所",
    dataIndex: "exchange",
    width: 100,
    render: (value: string) => <Tag>{value}</Tag>
  },
  { title: "标的", dataIndex: "symbol", width: 120 },
  { title: "结算时间", dataIndex: "funding_time", width: 150, render: (value: string) => fullTime(value) },
  {
    title: "资金费率",
    dataIndex: "funding_rate_pct",
    align: "right",
    render: (value: number) => signedPct(value, 4)
  }
];

export function PairMonitorPage() {
  const [form] = Form.useForm<PairSpreadFormValues>();
  const [hours, setHours] = useState(720);
  const [intervalMinutes, setIntervalMinutes] = useState(5);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [savedPresets, setSavedPresets] = useState<SavedPairSpreadPreset[]>(() => loadSavedPairPresets());
  const [result, setResult] = useState<PairSpreadQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const recentPoints = useMemo(
    () => [...(result?.points ?? [])].reverse().slice(0, 180),
    [result?.points]
  );
  const recentFunding = useMemo(
    () => [...(result?.funding_history ?? [])].reverse().slice(0, 80),
    [result?.funding_history]
  );

  const runQuery = useCallback(async (override?: {
    hours?: number;
    intervalMinutes?: number;
    values?: PairSpreadFormValues;
  }) => {
    setLoading(true);
    setError("");
    try {
      const values = normalizePairForm(override?.values ?? await form.validateFields());
      form.setFieldsValue(values);
      const queryHours = clampHours(override?.hours ?? hours);
      const queryInterval = override?.intervalMinutes ?? intervalMinutes;
      const next = await queryPairSpread({
        leg1_exchange: values.leg1_exchange,
        leg1_symbol: values.leg1_symbol,
        leg2_exchange: values.leg2_exchange,
        leg2_symbol: values.leg2_symbol,
        leg2_multiplier: values.leg2_multiplier,
        interval_minutes: queryInterval,
        hours: queryHours
      });
      setResult(next);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [form, hours, intervalMinutes]);

  useEffect(() => {
    if (!autoRefresh || !result) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      if (!loading) {
        void runQuery();
      }
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, loading, result, runQuery]);

  const rerun = () => {
    if (!result) {
      void runQuery();
      return;
    }
    form.setFieldsValue({
      leg1_exchange: result.leg1.exchange,
      leg1_symbol: result.leg1.symbol,
      leg2_exchange: result.leg2.exchange,
      leg2_symbol: result.leg2.symbol,
      leg2_multiplier: result.leg2_multiplier
    });
    setHours(result.hours);
    setIntervalMinutes(result.interval_minutes);
    void runQuery({ hours: result.hours, intervalMinutes: result.interval_minutes });
  };

  const saveCurrentPreset = async () => {
    try {
      const values = normalizePairForm(await form.validateFields());
      const preset: SavedPairSpreadPreset = {
        ...values,
        id: pairPresetId(values),
        hours: clampHours(hours),
        intervalMinutes,
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
    form.setFieldsValue(preset);
    setHours(clampHours(preset.hours));
    setIntervalMinutes(preset.intervalMinutes);
    void runQuery({ values: preset, hours: preset.hours, intervalMinutes: preset.intervalMinutes });
  };

  const current = result?.current;
  const spreadPct = current?.spread_pct ?? result?.spread_pct.current ?? null;
  const spreadTone = typeof spreadPct === "number" ? (spreadPct < 0 ? "negative" : "positive") : "neutral";
  const ratio =
    current && result && current.leg1.price > 0
      ? (current.leg2.price * result.leg2_multiplier) / current.leg1.price
      : null;

  return (
    <div className="page pair-monitor-page pair-terminal-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      {result?.warnings.length ? <Alert type="warning" message={result.warnings.join("；")} showIcon /> : null}

      <section className="pair-query-panel">
        <Form form={form} initialValues={defaultFormValues} disabled={loading}>
          <div className="pair-query-bar">
            <Form.Item name="leg1_exchange" rules={[{ required: true }]} className="pair-query-item">
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item name="leg1_symbol" rules={[{ required: true, message: "请输入左合约" }]} className="pair-query-contract">
              <Input addonBefore="左合约" placeholder="SKHY" />
            </Form.Item>
            <Form.Item name="leg2_exchange" rules={[{ required: true }]} className="pair-query-item">
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item name="leg2_symbol" rules={[{ required: true, message: "请输入右合约" }]} className="pair-query-contract">
              <Input addonBefore="右合约" placeholder="SKHYNIX" />
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
              value={intervalMinutes}
              options={intervalOptions}
              onChange={setIntervalMinutes}
            />
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
                  <span>{savedPresetLabel(preset)}</span>
                  <span className="pair-saved-meta">
                    {durationLabel(preset.hours)} · {preset.intervalMinutes}m
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
              ? `实时价差 = ${rightLegLabel(result)} - ${result.leg1.exchange} · ${result.leg1.symbol}`
              : "等待查询"
          }
          tone={spreadTone}
        />
        <MetricCard
          label={result ? `${result.leg1.exchange} · ${result.leg1.symbol}` : "左合约"}
          value={price(current?.leg1.price)}
          sub={current ? priceFieldLabels[current.leg1.price_field] : "-"}
        />
        <MetricCard
          label={rightLegLabel(result)}
          value={price(current?.leg2.price)}
          sub={current ? priceFieldLabels[current.leg2.price_field] : "-"}
        />
        <MetricCard
          label="差价"
          value={price(current?.spread_abs ?? result?.spread_abs.current)}
          sub={ratio ? `倍率 ${compactNumber(ratio, 4)}x` : "-"}
          tone={spreadTone}
        />
        <MetricCard
          label="周期"
          value={`${result?.interval_minutes ?? intervalMinutes}m`}
          sub={result ? `${dataRangeLabel(result, hours)} · ${fullTime(result.observed_at)}` : durationLabel(hours)}
        />
      </section>

      <PairSpreadChart result={result} />

      <section className="pair-detail-grid">
        <div className="pair-detail-card">
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
        <div className="pair-detail-card">
          <div className="pair-detail-head">
            <Typography.Title level={5}>资金费率</Typography.Title>
            <Tag>{recentFunding.length} 条</Tag>
          </div>
          <Table<PairSpreadFundingPoint>
            rowKey={(point) => `${point.exchange}-${point.symbol}-${point.funding_time}`}
            columns={fundingColumns}
            dataSource={recentFunding}
            loading={loading}
            pagination={{ pageSize: 8 }}
            size="small"
            tableLayout="fixed"
            scroll={{ x: 560 }}
          />
        </div>
      </section>
    </div>
  );
}
