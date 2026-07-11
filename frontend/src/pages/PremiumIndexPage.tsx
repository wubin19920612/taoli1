import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
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

function signedPct(value: number | null | undefined, digits = 4): string {
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
  if (abs >= 1) {
    return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(8);
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

  return (
    <div className="premium-chart-card">
      <svg className="premium-index-chart" role="img" aria-label="隐含加权溢价指数曲线" viewBox={`0 0 ${width} ${height}`}>
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
                {value.toFixed(2)}%
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

const pointColumns: ColumnsType<PremiumIndexPoint> = [
  { title: "时间", dataIndex: "bucket_at", width: 150, render: (value: string) => fullTime(value) },
  { title: "溢价指数", dataIndex: "premium_pct", align: "right", render: (value: number) => signedPct(value) },
  { title: "标记价", dataIndex: "mark_price", align: "right", render: (value: number | null) => price(value) },
  { title: "指数价", dataIndex: "index_price", align: "right", render: (value: number | null) => price(value) },
  { title: "来源", dataIndex: "source", width: 160, render: (value: string) => <Tag>{value}</Tag> }
];

export function PremiumIndexPage() {
  const [form] = Form.useForm<PremiumIndexFormValues>();
  const [hours, setHours] = useState(12);
  const [intervalMinutes, setIntervalMinutes] = useState(1);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [result, setResult] = useState<PremiumIndexQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const recentPoints = useMemo(() => [...(result?.points ?? [])].reverse().slice(0, 180), [result?.points]);

  const runQuery = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const values = await form.validateFields();
      const next = await queryPremiumIndex({
        exchange: values.exchange,
        symbol: normalizeSymbol(values.symbol),
        hours: clampHours(hours),
        interval_minutes: intervalMinutes
      });
      form.setFieldsValue({ ...values, symbol: normalizeSymbol(values.symbol) });
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

  const current = result?.current ?? null;
  const currentPremium = current?.premium_pct ?? result?.premium_pct.current ?? null;
  const tone = typeof currentPremium === "number" ? (currentPremium >= 0 ? "positive" : "negative") : "neutral";

  return (
    <div className="page premium-index-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      {result?.warnings.length ? <Alert type="warning" message={result.warnings.join("；")} showIcon /> : null}

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
            </div>
          </div>
        </Form>
      </section>

      <section className="premium-metric-grid">
        <MetricCard
          label="当前隐含溢价指数"
          value={signedPct(currentPremium)}
          sub={current ? `${current.exchange} · ${current.symbol} · ${current.source}` : "等待查询"}
          tone={tone}
        />
        <MetricCard label="标记价" value={price(current?.mark_price)} sub="mark price" />
        <MetricCard label="指数价" value={price(current?.index_price)} sub="index price" />
        <MetricCard label="盘口中价溢价" value={signedPct(current?.mid_premium_pct)} sub={price(current?.mid_price)} tone={tone} />
        <MetricCard
          label="资金费率"
          value={signedPct(current?.funding_rate_pct)}
          sub={current?.funding_next_time ? `下次 ${time(current.funding_next_time)}` : "-"}
        />
        <MetricCard
          label="数据窗口"
          value={`${result?.interval_minutes ?? intervalMinutes}m`}
          sub={result ? `${time(result.first_seen_at)} - ${time(result.last_seen_at)}` : durationLabel(hours)}
        />
      </section>

      <PremiumIndexChart result={result} />

      <section className="premium-detail-card">
        <div className="premium-detail-head">
          <Typography.Title level={5}>最近溢价指数</Typography.Title>
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
