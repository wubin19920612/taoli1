import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo, useState } from "react";
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
};

const defaultFormValues: PairSpreadFormValues = {
  leg1_exchange: "binance",
  leg1_symbol: "BTCUSDT",
  leg2_exchange: "okx",
  leg2_symbol: "BTCUSDT"
};

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"].map(
  (value) => ({ label: value, value })
);

const rangeOptions = [
  { label: "24h", value: 24 },
  { label: "3d", value: 72 },
  { label: "7d", value: 168 }
];

const priceFieldLabels: Record<PairSpreadPriceField, string> = {
  mark_price: "标记价",
  mid_price: "盘口中价",
  index_price: "指数价",
  last_price: "最新价"
};

function pct(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function signedPct(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1000) {
    return value.toFixed(2);
  }
  if (value >= 1) {
    return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(6);
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm") : "-";
}

function fullTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("YYYY-MM-DD HH:mm") : "-";
}

function legLabel(exchange: string, symbol: string): string {
  return `${exchange} ${symbol}`;
}

function spreadPath(
  points: PairSpreadPoint[],
  xAt: (index: number) => number,
  yAt: (value: number) => number
): string {
  return points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(2)} ${yAt(point.spread_pct).toFixed(2)}`)
    .join(" ");
}

function PairSpreadChart({ result }: { result: PairSpreadQueryResult | null }) {
  const points = result?.points ?? [];
  const width = 920;
  const height = 300;
  const padding = { top: 18, right: 56, bottom: 34, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  if (!result || points.length === 0) {
    return <div className="pair-monitor-empty">暂无查询结果</div>;
  }

  const values = points.map((point) => point.spread_pct).filter((value) => Number.isFinite(value));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const first = points[0];
  const last = points[points.length - 1];
  const firstMs = dayjs.utc(first.bucket_at).valueOf();
  const lastMs = dayjs.utc(last.bucket_at).valueOf();
  const xAt = (index: number) =>
    padding.left + (points.length === 1 ? chartWidth / 2 : (chartWidth * index) / (points.length - 1));
  const xAtTime = (value: string) => {
    const timestamp = dayjs.utc(value).valueOf();
    if (lastMs <= firstMs) {
      return padding.left + chartWidth / 2;
    }
    return padding.left + ((timestamp - firstMs) / (lastMs - firstMs)) * chartWidth;
  };
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const markerRows = result.funding_history.filter((item) => {
    const timestamp = dayjs.utc(item.funding_time).valueOf();
    return timestamp >= firstMs && timestamp <= lastMs;
  });

  return (
    <div className="pair-monitor-chart-wrap">
      <svg className="pair-monitor-chart" role="img" aria-label="分钟价差曲线" viewBox={`0 0 ${width} ${height}`}>
        <rect x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = max - (max - min) * tick;
          return (
            <g key={tick}>
              <line className="pair-monitor-grid-line" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="pair-monitor-axis-label" x={padding.left - 8} y={y + 4} textAnchor="end">
                {value.toFixed(3)}%
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line className="pair-monitor-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        ) : null}
        {markerRows.map((item) => {
          const x = xAtTime(item.funding_time);
          return (
            <line
              key={`${item.exchange}-${item.funding_time}`}
              className={`pair-monitor-funding-marker pair-monitor-funding-${item.exchange === result.leg1.exchange ? "a" : "b"}`}
              x1={x}
              y1={padding.top}
              x2={x}
              y2={padding.top + chartHeight}
            >
              <title>
                {item.exchange} {time(item.funding_time)} {signedPct(item.funding_rate_pct)}
              </title>
            </line>
          );
        })}
        <path className="pair-monitor-line pair-monitor-line-spread" d={spreadPath(points, xAt, yAt)} />
        <text className="pair-monitor-axis-label" x={padding.left} y={height - 10}>
          {time(first.bucket_at)}
        </text>
        <text className="pair-monitor-axis-label" x={padding.left + chartWidth} y={height - 10} textAnchor="end">
          {time(last.bucket_at)}
        </text>
      </svg>
      <div className="pair-monitor-legend">
        <span className="legend-spread">价差</span>
        <span className="legend-funding-a">{result.leg1.exchange} 资金</span>
        <span className="legend-funding-b">{result.leg2.exchange} 资金</span>
      </div>
    </div>
  );
}

const pointColumns: ColumnsType<PairSpreadPoint> = [
  { title: "时间", dataIndex: "bucket_at", width: 120, render: (value: string) => time(value) },
  { title: "A 收盘", dataIndex: "leg1_close", align: "right", render: (value: number) => price(value) },
  { title: "B 收盘", dataIndex: "leg2_close", align: "right", render: (value: number) => price(value) },
  { title: "绝对差", dataIndex: "spread_abs", align: "right", render: (value: number) => price(value) },
  { title: "价差", dataIndex: "spread_pct", align: "right", render: (value: number) => signedPct(value) }
];

const fundingColumns: ColumnsType<PairSpreadFundingPoint> = [
  {
    title: "交易所",
    dataIndex: "exchange",
    width: 100,
    render: (value: string) => <Tag>{value}</Tag>
  },
  { title: "标的", dataIndex: "symbol", width: 110 },
  { title: "结算时间", dataIndex: "funding_time", width: 150, render: (value: string) => fullTime(value) },
  {
    title: "资金费率",
    dataIndex: "funding_rate_pct",
    align: "right",
    render: (value: number) => signedPct(value)
  }
];

export function PairMonitorPage() {
  const [form] = Form.useForm<PairSpreadFormValues>();
  const [hours, setHours] = useState(72);
  const [result, setResult] = useState<PairSpreadQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const recentPoints = useMemo(
    () => [...(result?.points ?? [])].reverse().slice(0, 180),
    [result?.points]
  );
  const recentFunding = useMemo(
    () => [...(result?.funding_history ?? [])].reverse().slice(0, 120),
    [result?.funding_history]
  );

  const runQuery = async () => {
    setLoading(true);
    setError("");
    try {
      const values = await form.validateFields();
      const next = await queryPairSpread({
        ...values,
        hours
      });
      setResult(next);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  const rerun = () => {
    if (!result) {
      void runQuery();
      return;
    }
    form.setFieldsValue({
      leg1_exchange: result.leg1.exchange,
      leg1_symbol: result.leg1.symbol,
      leg2_exchange: result.leg2.exchange,
      leg2_symbol: result.leg2.symbol
    });
    void runQuery();
  };

  const current = result?.current;
  const nextFundingTimes = [current?.leg1.funding_next_time, current?.leg2.funding_next_time].filter(Boolean) as string[];
  const nextFundingTime = nextFundingTimes.length ? nextFundingTimes.sort()[0] : null;

  return (
    <div className="page pair-monitor-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      {result?.warnings.length ? <Alert type="warning" message={result.warnings.join("；")} showIcon /> : null}

      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4}>价差查询</Typography.Title>
          <Typography.Text type="secondary">分钟价差与资金费率</Typography.Text>
        </div>
        <Space className="toolbar-actions" wrap>
          <Segmented value={hours} options={rangeOptions} onChange={(value) => setHours(Number(value))} />
          <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void runQuery()}>
            查询
          </Button>
          <Button icon={<ReloadOutlined />} disabled={!result} loading={loading} onClick={rerun}>
            重查
          </Button>
        </Space>
      </section>

      <section className="panel panel-wide">
        <Form form={form} layout="vertical" initialValues={defaultFormValues} disabled={loading}>
          <div className="pair-monitor-form-grid">
            <Form.Item label="A 交易所" name="leg1_exchange" rules={[{ required: true, message: "请选择交易所" }]}>
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item label="A 标的" name="leg1_symbol" rules={[{ required: true, message: "请输入标的" }]}>
              <Input placeholder="BTCUSDT" />
            </Form.Item>
            <Form.Item label="B 交易所" name="leg2_exchange" rules={[{ required: true, message: "请选择交易所" }]}>
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item label="B 标的" name="leg2_symbol" rules={[{ required: true, message: "请输入标的" }]}>
              <Input placeholder="BTCUSDT" />
            </Form.Item>
          </div>
        </Form>
      </section>

      <section className="panel pair-monitor-detail">
        <div className="pair-monitor-detail-head">
          <Space direction="vertical" size={2}>
            <Typography.Title level={5}>
              {result ? `${legLabel(result.leg1.exchange, result.leg1.symbol)} / ${legLabel(result.leg2.exchange, result.leg2.symbol)}` : "价差结果"}
            </Typography.Title>
            <Typography.Text type="secondary">
              {result ? `${fullTime(result.first_seen_at)} - ${fullTime(result.last_seen_at)}` : "暂无查询结果"}
            </Typography.Text>
          </Space>
          {result ? <Tag color="green">{result.hours === 24 ? "24h" : `${result.hours / 24}d`}</Tag> : null}
        </div>

        <div className="pair-monitor-stats">
          <Statistic title="当前价差" value={current?.spread_pct ?? result?.spread_pct.current ?? 0} precision={3} suffix="%" />
          <Statistic title="价差均值" value={result?.spread_pct.mean ?? 0} precision={3} suffix="%" />
          <Statistic title="分钟点" value={result?.point_count ?? 0} />
          <Statistic title="A 资金" value={current?.leg1.funding_rate_pct ?? 0} precision={4} suffix="%" />
          <Statistic title="B 资金" value={current?.leg2.funding_rate_pct ?? 0} precision={4} suffix="%" />
          <Statistic title="下次结算" value={time(nextFundingTime)} />
        </div>

        {current ? (
          <div className="pair-monitor-current-strip">
            <Tag color="blue">
              A {price(current.leg1.price)} · {priceFieldLabels[current.leg1.price_field]}
            </Tag>
            <Tag color="orange">
              B {price(current.leg2.price)} · {priceFieldLabels[current.leg2.price_field]}
            </Tag>
            <Typography.Text type="secondary">当前绝对差 {price(current.spread_abs)}</Typography.Text>
          </div>
        ) : null}

        <PairSpreadChart result={result} />

        <div className="pair-monitor-tables">
          <Table<PairSpreadPoint>
            className="pair-monitor-points"
            rowKey={(point) => point.bucket_at}
            columns={pointColumns}
            dataSource={recentPoints}
            loading={loading}
            pagination={{ pageSize: 12 }}
            size="small"
            tableLayout="fixed"
            scroll={{ x: 640 }}
          />
          <Table<PairSpreadFundingPoint>
            className="pair-monitor-points"
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
