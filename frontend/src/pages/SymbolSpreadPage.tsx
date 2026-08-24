import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, InputNumber, Select, Switch, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import { querySymbolExchangeSpreads } from "../api/client";
import type {
  MarketType,
  SymbolExchangePriceSnapshot,
  SymbolSpreadPoint,
  SymbolSpreadQueryResult,
  SymbolSpreadSeries
} from "../api/types";

dayjs.extend(utc);

type SymbolSpreadFormValues = {
  symbol: string;
  market_type: MarketType;
  base_exchange: string;
  exchanges: string[];
};

type SymbolSpreadQueryParams = {
  symbol: string;
  market_type: MarketType;
  base_exchange: string;
  exchanges: string[];
  hours: number;
  interval_seconds: number;
  include_current: boolean;
};

type DisplaySymbolSpreadSeries = SymbolSpreadSeries & {
  displayPoints: SymbolSpreadPoint[];
};

type TimeAxisTick = {
  ms: number;
  value: string;
};

const futuresExchanges = ["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"];
const spotExchanges = ["binance", "okx", "bybit", "gate", "bitget"];
const exchangeLabels: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "Hyperliquid",
  okx: "OKX"
};
const exchangeShortLabels: Record<string, string> = {
  aster: "Aster",
  binance: "BN",
  bitget: "BG",
  bybit: "BB",
  gate: "Gate",
  hyperliquid: "HL",
  okx: "OKX"
};
const seriesColors = ["#2563eb", "#f97316", "#0f766e", "#7c3aed", "#b42318", "#0891b2", "#64748b"];
const defaultFormValues: SymbolSpreadFormValues = {
  symbol: "BTC",
  market_type: "future",
  base_exchange: "binance",
  exchanges: futuresExchanges
};

function exchangeOptions(marketType: MarketType) {
  const exchanges = marketType === "spot" ? spotExchanges : futuresExchanges;
  return exchanges.map((exchange) => ({
    label: exchangeLabels[exchange] ?? exchange,
    value: exchange
  }));
}

function normalizeSymbol(value: string): string {
  const normalized = value.trim().toUpperCase().replace(/[-_/]/g, "");
  if (!normalized) {
    return "";
  }
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

function shortSymbol(value: string | null | undefined): string {
  return (value ?? "").replace(/(?:USDT|USDC|USD)$/i, "");
}

function clampHours(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 24;
  }
  return Math.min(720, Math.max(1, Math.round(value)));
}

function clampIntervalSeconds(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 60;
  }
  return Math.min(86_400, Math.max(5, Math.round(value)));
}

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

function intervalLabel(seconds: number): string {
  const normalized = clampIntervalSeconds(seconds);
  if (normalized % 60 === 0) {
    const minutes = normalized / 60;
    return `${minutes}分钟`;
  }
  return `${normalized}秒`;
}

function durationLabel(hours: number): string {
  const normalized = clampHours(hours);
  return normalized % 24 === 0 ? `${normalized / 24}天` : `${normalized}小时`;
}

function marketTypeText(value: MarketType): string {
  return value === "spot" ? "现货" : "合约";
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function fullTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("YYYY-MM-DD HH:mm:ss") : "-";
}

function latestPoint(series: DisplaySymbolSpreadSeries | SymbolSpreadSeries): SymbolSpreadPoint | null {
  const points = "displayPoints" in series ? series.displayPoints : series.points;
  return points[points.length - 1] ?? series.current ?? null;
}

function mergeCurrentPoint(series: SymbolSpreadSeries): SymbolSpreadPoint[] {
  const points = [...series.points].sort(
    (left, right) => dayjs.utc(left.bucket_at).valueOf() - dayjs.utc(right.bucket_at).valueOf()
  );
  const current = series.current;
  if (!current) {
    return points;
  }
  const currentMs = dayjs.utc(current.bucket_at).valueOf();
  const last = points[points.length - 1];
  if (!last) {
    return [current];
  }
  const lastMs = dayjs.utc(last.bucket_at).valueOf();
  if (!Number.isFinite(currentMs) || !Number.isFinite(lastMs) || currentMs < lastMs) {
    return points;
  }
  if (currentMs === lastMs) {
    points[points.length - 1] = current;
    return points;
  }
  return [...points, current];
}

function displaySeries(result: SymbolSpreadQueryResult | null): DisplaySymbolSpreadSeries[] {
  return (result?.series ?? []).map((series) => ({
    ...series,
    displayPoints: mergeCurrentPoint(series)
  }));
}

function spreadTone(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) < 0.000_001) {
    return "neutral";
  }
  return value > 0 ? "positive" : "negative";
}

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
  return Array.from({ length: count }, (_, index) => {
    const tickMs = start + (spanMs * index) / (count - 1);
    return { ms: tickMs, value: dayjs.utc(tickMs).toISOString() };
  });
}

function chartTime(value: string, spanHours: number): string {
  const parsed = dayjs.utc(value).utcOffset(8);
  if (spanHours <= 24) {
    return parsed.format("HH:mm");
  }
  if (spanHours <= 168) {
    return parsed.format("MM-DD HH:mm");
  }
  return parsed.format("MM-DD");
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
    <div className={`symbol-spread-metric-card symbol-spread-metric-${tone}`}>
      <Typography.Text className="symbol-spread-metric-label">{label}</Typography.Text>
      <div className="symbol-spread-metric-value">{value}</div>
      <Typography.Text className="symbol-spread-metric-sub">{sub}</Typography.Text>
    </div>
  );
}

function SymbolSpreadChart({ result, loading }: { result: SymbolSpreadQueryResult | null; loading: boolean }) {
  const activeSeries = displaySeries(result).filter((series) => series.displayPoints.length > 0);
  const width = 1180;
  const height = 360;
  const padding = { top: 24, right: 28, bottom: 38, left: 58 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!activeSeries.length) {
    return (
      <div className="symbol-spread-chart-empty">
        {loading ? "正在查询跨所价差" : "暂无查询结果"}
      </div>
    );
  }

  const values = activeSeries
    .flatMap((series) => series.displayPoints.map((point) => point.spread_pct))
    .filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values, 0);
  const maxValue = Math.max(...values, 0);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const startMs = Math.min(
    ...activeSeries.flatMap((series) => series.displayPoints.map((point) => dayjs.utc(point.bucket_at).valueOf()))
  );
  const endMs = Math.max(
    ...activeSeries.flatMap((series) => series.displayPoints.map((point) => dayjs.utc(point.bucket_at).valueOf()))
  );
  const spanHours = Math.max((endMs - startMs) / 3_600_000, 0);
  const ticks = chartTimeTicks(startMs, endMs, spanHours <= 12 ? 13 : spanHours <= 24 ? 9 : spanHours >= 168 ? 7 : 6);
  const xAt = (bucketAt: string) =>
    padding.left +
    (startMs === endMs
      ? chartWidth / 2
      : ((dayjs.utc(bucketAt).valueOf() - startMs) / (endMs - startMs)) * chartWidth);
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const linePath = (points: SymbolSpreadPoint[]) =>
    points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(point.bucket_at).toFixed(2)} ${yAt(point.spread_pct).toFixed(2)}`)
      .join(" ");

  return (
    <section className="symbol-spread-chart-card">
      <div className="symbol-spread-chart-head">
        <Typography.Title level={5}>主流交易所价差图</Typography.Title>
        <div className="symbol-spread-chart-tags">
          <Tag color="blue">基准 {exchangeLabels[result?.base_exchange ?? ""] ?? result?.base_exchange}</Tag>
          <Tag>{result?.series.length ?? 0} 条线</Tag>
          <Tag>{intervalLabel(result?.interval_seconds ?? 60)} 周期</Tag>
          <Tag>{durationLabel(result?.hours ?? 24)}</Tag>
        </div>
      </div>
      <svg className="symbol-spread-chart" role="img" aria-label="单标的跨交易所价差图" viewBox={`0 0 ${width} ${height}`}>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="symbol-spread-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="symbol-spread-axis-label" x={padding.left - 10} y={y + 4} textAnchor="end">
                {signedPct(value)}
              </text>
            </g>
          );
        })}
        {ticks.map((tick, index) => {
          const x =
            padding.left +
            (startMs === endMs ? chartWidth / 2 : ((tick.ms - startMs) / (endMs - startMs)) * chartWidth);
          const textAnchor = index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle";
          return (
            <g key={`symbol-spread-tick-${tick.value}`}>
              <line className="symbol-spread-time-tick" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="symbol-spread-axis-label" x={x} y={height - 12} textAnchor={textAnchor}>
                {chartTime(tick.value, spanHours)}
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line className="symbol-spread-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        ) : null}
        {activeSeries.map((series, index) => {
          const color = seriesColors[index % seriesColors.length];
          const latest = latestPoint(series);
          return (
            <g key={`symbol-series-${series.exchange}`}>
              <path className="symbol-spread-line" d={linePath(series.displayPoints)} stroke={color} />
              {latest ? (
                <circle className="symbol-spread-latest-dot" cx={xAt(latest.bucket_at)} cy={yAt(latest.spread_pct)} r="4" fill={color}>
                  <title>
                    {`${exchangeLabels[series.exchange] ?? series.exchange} ${time(latest.bucket_at)} ${signedPct(latest.spread_pct)}，价差 ${price(latest.spread_abs)}`}
                  </title>
                </circle>
              ) : null}
            </g>
          );
        })}
      </svg>
      <div className="symbol-spread-legend">
        {activeSeries.map((series, index) => {
          const latest = latestPoint(series);
          const color = seriesColors[index % seriesColors.length];
          return (
            <span key={`symbol-legend-${series.exchange}`} style={{ color }}>
              {exchangeShortLabels[series.exchange] ?? series.exchange} {signedPct(latest?.spread_pct)}
            </span>
          );
        })}
      </div>
    </section>
  );
}

export function SymbolSpreadPage() {
  const [form] = Form.useForm<SymbolSpreadFormValues>();
  const marketType = Form.useWatch("market_type", form) ?? defaultFormValues.market_type;
  const [hours, setHours] = useState(24);
  const [intervalSeconds, setIntervalSeconds] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [lastQuery, setLastQuery] = useState<SymbolSpreadQueryParams | null>(null);
  const [result, setResult] = useState<SymbolSpreadQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const activeSeries = useMemo(() => displaySeries(result), [result]);
  const latestSeries = useMemo(
    () =>
      activeSeries
        .map((series) => ({ series, point: latestPoint(series) }))
        .filter((item): item is { series: DisplaySymbolSpreadSeries; point: SymbolSpreadPoint } => item.point !== null),
    [activeSeries]
  );
  const strongestLatest = latestSeries.reduce<{
    series: DisplaySymbolSpreadSeries;
    point: SymbolSpreadPoint;
  } | null>((best, item) => {
    if (!best || Math.abs(item.point.spread_pct) > Math.abs(best.point.spread_pct)) {
      return item;
    }
    return best;
  }, null);
  const allValues = activeSeries.flatMap((series) => series.displayPoints.map((point) => point.spread_pct));
  const maxValue = allValues.length ? Math.max(...allValues) : null;
  const minValue = allValues.length ? Math.min(...allValues) : null;

  const buildQueryFromForm = useCallback(async (): Promise<SymbolSpreadQueryParams> => {
    const values = await form.validateFields();
    const symbol = normalizeSymbol(values.symbol);
    const options = exchangeOptions(values.market_type).map((item) => item.value);
    const exchanges = (values.exchanges?.length ? values.exchanges : options)
      .filter((exchange) => options.includes(exchange));
    const baseExchange = options.includes(values.base_exchange) ? values.base_exchange : options[0];
    return {
      symbol,
      market_type: values.market_type,
      base_exchange: baseExchange,
      exchanges,
      hours: clampHours(hours),
      interval_seconds: clampIntervalSeconds(intervalSeconds),
      include_current: true
    };
  }, [form, hours, intervalSeconds]);

  const executeQuery = useCallback(async (query: SymbolSpreadQueryParams, refresh = false) => {
    if (refresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError("");
    try {
      const next = await querySymbolExchangeSpreads(query);
      setResult(next);
      setLastQuery(query);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (refresh) {
        setRefreshing(false);
      } else {
        setLoading(false);
      }
    }
  }, []);

  const runQuery = useCallback(async () => {
    const query = await buildQueryFromForm();
    form.setFieldsValue({
      symbol: query.symbol,
      market_type: query.market_type,
      base_exchange: query.base_exchange,
      exchanges: query.exchanges
    });
    await executeQuery(query);
  }, [buildQueryFromForm, executeQuery, form]);

  const refreshLastQuery = useCallback(async () => {
    if (!lastQuery) {
      return;
    }
    await executeQuery(lastQuery, true);
  }, [executeQuery, lastQuery]);

  useEffect(() => {
    if (!autoRefresh || !lastQuery) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      if (!loading && !refreshing) {
        void executeQuery(lastQuery, true);
      }
    }, clampIntervalSeconds(lastQuery.interval_seconds) * 1_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, executeQuery, lastQuery, loading, refreshing]);

  const seriesColumns = useMemo<ColumnsType<DisplaySymbolSpreadSeries>>(
    () => [
      {
        title: "交易所",
        dataIndex: "exchange",
        width: 112,
        render: (value: string) => <Tag>{exchangeLabels[value] ?? value}</Tag>
      },
      {
        title: "最新价差率",
        key: "latest_spread_pct",
        align: "right",
        render: (_, series) => {
          const point = latestPoint(series);
          return <span className={`symbol-spread-rate symbol-spread-rate-${spreadTone(point?.spread_pct)}`}>{signedPct(point?.spread_pct)}</span>;
        }
      },
      {
        title: "最新价差",
        key: "latest_spread_abs",
        align: "right",
        render: (_, series) => price(latestPoint(series)?.spread_abs)
      },
      {
        title: "交易所价",
        key: "exchange_price",
        align: "right",
        render: (_, series) => price(latestPoint(series)?.exchange_close)
      },
      {
        title: "基准价",
        key: "base_price",
        align: "right",
        render: (_, series) => price(latestPoint(series)?.base_close)
      },
      {
        title: "最高 / 最低",
        key: "range",
        align: "right",
        render: (_, series) => `${signedPct(series.spread_pct.max)} / ${signedPct(series.spread_pct.min)}`
      },
      {
        title: "均值",
        dataIndex: ["spread_pct", "mean"],
        align: "right",
        render: (value: number | null) => signedPct(value)
      },
      {
        title: "点数",
        dataIndex: "point_count",
        align: "right",
        width: 86
      },
      {
        title: "时间",
        key: "time",
        width: 116,
        render: (_, series) => time(latestPoint(series)?.bucket_at)
      }
    ],
    []
  );

  const priceColumns = useMemo<ColumnsType<SymbolExchangePriceSnapshot>>(
    () => [
      {
        title: "交易所",
        dataIndex: "exchange",
        width: 112,
        render: (value: string) => (
          <Tag color={value === result?.base_exchange ? "blue" : undefined}>{exchangeLabels[value] ?? value}</Tag>
        )
      },
      { title: "价格", dataIndex: "price", align: "right", render: (value: number) => price(value) },
      { title: "价格类型", dataIndex: "price_field", width: 110 },
      { title: "资金费率", dataIndex: "funding_rate_pct", align: "right", render: (value: number | null) => signedPct(value, 4) },
      { title: "更新时间", dataIndex: "timestamp", width: 150, render: (value: string) => fullTime(value) }
    ],
    [result?.base_exchange]
  );

  return (
    <div className="page symbol-spread-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      {result?.warnings.length ? <Alert type="warning" message={result.warnings.join("；")} showIcon /> : null}

      <section className="symbol-spread-query-panel">
        <Form form={form} initialValues={defaultFormValues} disabled={loading}>
          <div className="symbol-spread-query-bar">
            <Form.Item name="symbol" rules={[{ required: true, message: "请输入标的" }]}>
              <Input addonBefore="标的" placeholder="BTC" />
            </Form.Item>
            <Form.Item name="market_type" rules={[{ required: true }]}>
              <Select
                options={[
                  { label: "合约", value: "future" },
                  { label: "现货", value: "spot" }
                ]}
              />
            </Form.Item>
            <Form.Item name="base_exchange" rules={[{ required: true }]}>
              <Select options={exchangeOptions(marketType)} showSearch placeholder="基准交易所" />
            </Form.Item>
            <Form.Item name="exchanges" rules={[{ required: true, message: "请选择交易所" }]}>
              <Select mode="multiple" maxTagCount="responsive" options={exchangeOptions(marketType)} />
            </Form.Item>
            <InputNumber
              addonBefore="小时"
              className="symbol-spread-hours"
              min={1}
              max={720}
              precision={0}
              step={1}
              value={hours}
              onChange={(value) => setHours(clampHours(value))}
            />
            <InputNumber
              addonBefore="周期秒"
              className="symbol-spread-interval"
              min={5}
              max={86_400}
              precision={0}
              step={5}
              value={intervalSeconds}
              onChange={(value) => setIntervalSeconds(clampIntervalSeconds(value))}
            />
            <div className="symbol-spread-refresh">
              <Switch checked={autoRefresh} checkedChildren="自动" unCheckedChildren="手动" onChange={setAutoRefresh} />
            </div>
            <div className="symbol-spread-actions">
              <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void runQuery()}>
                查询
              </Button>
              <Button icon={<ReloadOutlined />} disabled={!lastQuery} loading={refreshing} onClick={() => void refreshLastQuery()}>
                刷新
              </Button>
            </div>
          </div>
        </Form>
      </section>

      <section className="symbol-spread-metric-grid">
        <MetricCard
          label="标的"
          value={result ? shortSymbol(result.symbol) : shortSymbol(normalizeSymbol(form.getFieldValue("symbol") ?? ""))}
          sub={result ? `${marketTypeText(result.market_type)} · 基准 ${exchangeLabels[result.base_exchange] ?? result.base_exchange}` : "等待查询"}
        />
        <MetricCard
          label="最新最大价差"
          value={signedPct(strongestLatest?.point.spread_pct)}
          sub={strongestLatest ? `${exchangeLabels[strongestLatest.series.exchange] ?? strongestLatest.series.exchange} - ${exchangeLabels[result?.base_exchange ?? ""] ?? result?.base_exchange}` : "等待查询"}
          tone={spreadTone(strongestLatest?.point.spread_pct)}
        />
        <MetricCard
          label="窗口最高"
          value={signedPct(maxValue)}
          sub={result ? `${durationLabel(result.hours)} · ${intervalLabel(result.interval_seconds)}` : `${durationLabel(hours)} · ${intervalLabel(intervalSeconds)}`}
          tone={spreadTone(maxValue)}
        />
        <MetricCard
          label="窗口最低"
          value={signedPct(minValue)}
          sub={result ? `${result.series.length} 条价差线 · ${result.point_count} 点` : "等待查询"}
          tone={spreadTone(minValue)}
        />
        <MetricCard
          label="更新时间"
          value={result ? time(result.observed_at) : "-"}
          sub={result ? `${fullTime(result.first_seen_at)} - ${fullTime(result.last_seen_at)}` : "等待查询"}
        />
      </section>

      <SymbolSpreadChart result={result} loading={loading} />

      <section className="symbol-spread-detail-grid">
        <div className="symbol-spread-detail-card">
          <div className="symbol-spread-detail-head">
            <Typography.Title level={5}>价差明细</Typography.Title>
            <Tag>{activeSeries.length} 条</Tag>
          </div>
          <Table<DisplaySymbolSpreadSeries>
            rowKey={(series) => series.exchange}
            columns={seriesColumns}
            dataSource={activeSeries}
            loading={loading}
            pagination={false}
            size="small"
            tableLayout="auto"
            scroll={{ x: "max-content" }}
          />
        </div>
        <div className="symbol-spread-detail-card">
          <div className="symbol-spread-detail-head">
            <Typography.Title level={5}>当前价格</Typography.Title>
            <Tag>{result?.current_prices.length ?? 0} 个交易所</Tag>
          </div>
          <Table<SymbolExchangePriceSnapshot>
            rowKey={(row) => `${row.exchange}-${row.symbol}`}
            columns={priceColumns}
            dataSource={result?.current_prices ?? []}
            loading={loading || refreshing}
            pagination={false}
            size="small"
            tableLayout="auto"
            scroll={{ x: "max-content" }}
          />
        </div>
      </section>
    </div>
  );
}
