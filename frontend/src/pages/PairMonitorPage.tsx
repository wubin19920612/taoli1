import {
  DeleteOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SyncOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Segmented,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import {
  createPairMonitorRule,
  deletePairMonitorRule,
  getPairMonitorHistory,
  listPairMonitorRules,
  samplePairMonitors,
  updatePairMonitorRule
} from "../api/client";
import type {
  MarketType,
  PairMonitorHistory,
  PairMonitorLeg,
  PairMonitorPoint,
  PairMonitorPriceField,
  PairMonitorRule
} from "../api/types";

dayjs.extend(utc);

type PairMonitorFormValues = {
  name?: string;
  leg1_exchange: string;
  leg1_symbol: string;
  leg1_market_type: MarketType;
  leg1_price_field: PairMonitorPriceField;
  leg2_exchange: string;
  leg2_symbol: string;
  leg2_market_type: MarketType;
  leg2_price_field: PairMonitorPriceField;
  retention_days: number;
};

const defaultFormValues: PairMonitorFormValues = {
  name: "",
  leg1_exchange: "binance",
  leg1_symbol: "BTCUSDT",
  leg1_market_type: "future",
  leg1_price_field: "auto",
  leg2_exchange: "okx",
  leg2_symbol: "BTCUSDT",
  leg2_market_type: "future",
  leg2_price_field: "auto",
  retention_days: 7
};

const exchangeOptions = ["binance", "okx", "bybit", "gate", "bitget", "htx", "aster", "hyperliquid"].map(
  (value) => ({ label: value, value })
);
const marketTypeOptions = [
  { label: "future", value: "future" },
  { label: "spot", value: "spot" }
];
const priceFieldOptions = [
  { label: "Auto", value: "auto" },
  { label: "Mid", value: "mid_price" },
  { label: "Mark", value: "mark_price" },
  { label: "Index", value: "index_price" },
  { label: "Bid", value: "bid" },
  { label: "Ask", value: "ask" }
];
const rangeOptions = [
  { label: "24h", value: 24 },
  { label: "3d", value: 72 },
  { label: "7d", value: 168 },
  { label: "14d", value: 336 }
];

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
    return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(6);
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm") : "-";
}

function legLabel(leg: PairMonitorLeg): string {
  return `${leg.exchange} ${leg.market_type} ${leg.symbol}`;
}

function ruleFromForm(values: PairMonitorFormValues): PairMonitorRule {
  return {
    name: values.name?.trim() || "",
    enabled: true,
    sample_interval_seconds: 60,
    retention_days: values.retention_days,
    leg1: {
      exchange: values.leg1_exchange,
      symbol: values.leg1_symbol,
      market_type: values.leg1_market_type,
      price_field: values.leg1_price_field
    },
    leg2: {
      exchange: values.leg2_exchange,
      symbol: values.leg2_symbol,
      market_type: values.leg2_market_type,
      price_field: values.leg2_price_field
    }
  };
}

function valuePath(
  points: PairMonitorPoint[],
  field: "spread_pct" | "leg1_funding_rate_pct" | "leg2_funding_rate_pct",
  xAt: (index: number) => number,
  yAt: (value: number) => number
): string {
  return points
    .map((point, index) => {
      const value = point[field];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return "";
      }
      return `${index === 0 ? "M" : "L"} ${xAt(index).toFixed(2)} ${yAt(value).toFixed(2)}`;
    })
    .filter(Boolean)
    .join(" ");
}

function PairMonitorChart({ history }: { history: PairMonitorHistory | null }) {
  const points = history?.points ?? [];
  const width = 920;
  const height = 300;
  const padding = { top: 18, right: 56, bottom: 34, left: 56 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const values = points.flatMap((point) =>
    [point.spread_pct, point.leg1_funding_rate_pct, point.leg2_funding_rate_pct].filter(
      (value): value is number => typeof value === "number" && Number.isFinite(value)
    )
  );
  if (!history || points.length === 0 || values.length === 0) {
    return <div className="pair-monitor-empty">暂无分钟样本</div>;
  }
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue || 1;
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const xAt = (index: number) =>
    padding.left + (points.length === 1 ? chartWidth / 2 : (chartWidth * index) / (points.length - 1));
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const first = points[0];
  const last = points[points.length - 1];

  return (
    <div className="pair-monitor-chart-wrap">
      <svg className="pair-monitor-chart" role="img" aria-label="价差与资金费率分钟曲线" viewBox={`0 0 ${width} ${height}`}>
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
        <line className="pair-monitor-zero-line" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        <path className="pair-monitor-line pair-monitor-line-spread" d={valuePath(points, "spread_pct", xAt, yAt)} />
        <path
          className="pair-monitor-line pair-monitor-line-funding-a"
          d={valuePath(points, "leg1_funding_rate_pct", xAt, yAt)}
        />
        <path
          className="pair-monitor-line pair-monitor-line-funding-b"
          d={valuePath(points, "leg2_funding_rate_pct", xAt, yAt)}
        />
        <text className="pair-monitor-axis-label" x={padding.left} y={height - 10}>
          {time(first.bucket_at)}
        </text>
        <text className="pair-monitor-axis-label" x={padding.left + chartWidth} y={height - 10} textAnchor="end">
          {time(last.bucket_at)}
        </text>
      </svg>
      <div className="pair-monitor-legend">
        <span className="legend-spread">价差</span>
        <span className="legend-funding-a">腿1资金</span>
        <span className="legend-funding-b">腿2资金</span>
      </div>
    </div>
  );
}

const pointColumns: ColumnsType<PairMonitorPoint> = [
  { title: "时间", dataIndex: "bucket_at", width: 118, render: (value: string) => time(value) },
  { title: "腿1价", dataIndex: "leg1_price", align: "right", render: (value: number) => price(value) },
  { title: "腿2价", dataIndex: "leg2_price", align: "right", render: (value: number) => price(value) },
  { title: "绝对差", dataIndex: "spread_abs", align: "right", render: (value: number) => price(value) },
  { title: "价差", dataIndex: "spread_pct", align: "right", render: (value: number) => signedPct(value) },
  {
    title: "资金费率",
    align: "right",
    render: (_, row) => `${signedPct(row.leg1_funding_rate_pct)} / ${signedPct(row.leg2_funding_rate_pct)}`
  },
  {
    title: "价格源",
    width: 126,
    render: (_, row) => `${row.leg1_price_field} / ${row.leg2_price_field}`
  }
];

export function PairMonitorPage() {
  const [form] = Form.useForm<PairMonitorFormValues>();
  const [rules, setRules] = useState<PairMonitorRule[]>([]);
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null);
  const [history, setHistory] = useState<PairMonitorHistory | null>(null);
  const [hours, setHours] = useState(72);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sampling, setSampling] = useState(false);
  const [error, setError] = useState("");

  const selectedRule = useMemo(
    () => rules.find((rule) => rule.id === selectedRuleId) ?? rules[0] ?? null,
    [rules, selectedRuleId]
  );

  const loadRules = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextRules = await listPairMonitorRules();
      setRules(nextRules);
      setSelectedRuleId((current) => current ?? nextRules[0]?.id ?? null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(
    async (ruleId: string | null, nextHours = hours) => {
      if (!ruleId) {
        setHistory(null);
        return;
      }
      setHistoryLoading(true);
      setError("");
      try {
        setHistory(await getPairMonitorHistory(ruleId, { hours: nextHours, point_limit: 5000 }));
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setHistoryLoading(false);
      }
    },
    [hours]
  );

  useEffect(() => {
    form.setFieldsValue(defaultFormValues);
    void loadRules();
  }, [form, loadRules]);

  useEffect(() => {
    void loadHistory(selectedRule?.id ?? null);
  }, [loadHistory, selectedRule?.id]);

  const createRule = async () => {
    setSaving(true);
    setError("");
    try {
      const values = await form.validateFields();
      const created = await createPairMonitorRule(ruleFromForm(values));
      message.success("监控对已创建");
      form.setFieldsValue({ ...defaultFormValues, name: "" });
      const nextRules = await listPairMonitorRules();
      setRules(nextRules);
      setSelectedRuleId(created.id ?? null);
      if (created.id) {
        await samplePairMonitors(created.id);
        await loadHistory(created.id);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const toggleRule = async (rule: PairMonitorRule) => {
    if (!rule.id) {
      return;
    }
    const updated = await updatePairMonitorRule(rule.id, { ...rule, enabled: !rule.enabled });
    setRules((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  };

  const removeRule = async (rule: PairMonitorRule) => {
    if (!rule.id) {
      return;
    }
    await deletePairMonitorRule(rule.id);
    message.success("监控对已删除");
    const nextRules = await listPairMonitorRules();
    setRules(nextRules);
    setSelectedRuleId(nextRules[0]?.id ?? null);
  };

  const sampleSelected = async () => {
    if (!selectedRule?.id) {
      return;
    }
    setSampling(true);
    try {
      const results = await samplePairMonitors(selectedRule.id);
      const result = results[0];
      if (result?.status === "recorded") {
        message.success("已记录当前分钟样本");
      } else {
        message.info(result?.reason ?? "暂无可记录样本");
      }
      await loadHistory(selectedRule.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSampling(false);
    }
  };

  const changeRange = (value: string | number) => {
    const nextHours = Number(value);
    setHours(nextHours);
    void loadHistory(selectedRule?.id ?? null, nextHours);
  };

  const ruleColumns: ColumnsType<PairMonitorRule> = [
    {
      title: "监控对",
      width: 230,
      render: (_, rule) => (
        <Space direction="vertical" size={2}>
          <Typography.Text strong>{rule.name}</Typography.Text>
          <Typography.Text type="secondary">{legLabel(rule.leg1)}</Typography.Text>
          <Typography.Text type="secondary">{legLabel(rule.leg2)}</Typography.Text>
        </Space>
      )
    },
    {
      title: "状态",
      width: 82,
      render: (_, rule) => <Tag color={rule.enabled ? "green" : "default"}>{rule.enabled ? "ON" : "OFF"}</Tag>
    },
    {
      title: "保留",
      dataIndex: "retention_days",
      width: 70,
      render: (value: number) => `${value}d`
    },
    {
      title: "操作",
      width: 112,
      render: (_, rule) => (
        <Space size={4}>
          <Button
            type="text"
            icon={rule.enabled ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={(event) => {
              event.stopPropagation();
              void toggleRule(rule);
            }}
            aria-label={rule.enabled ? "暂停" : "启用"}
          />
          <Button
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={(event) => {
              event.stopPropagation();
              void removeRule(rule);
            }}
            aria-label="删除"
          />
        </Space>
      )
    }
  ];

  const latest = history?.latest;

  return (
    <div className="page pair-monitor-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4}>价差监控</Typography.Title>
          <Typography.Text type="secondary">分钟级价差与资金费率</Typography.Text>
        </div>
        <Space className="toolbar-actions" wrap>
          <Segmented value={hours} options={rangeOptions} onChange={changeRange} />
          <Button icon={<SyncOutlined />} onClick={() => void sampleSelected()} loading={sampling} disabled={!selectedRule}>
            采样
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void loadRules()} loading={loading}>
            刷新
          </Button>
        </Space>
      </section>

      <section className="panel panel-wide">
        <Form form={form} layout="vertical" initialValues={defaultFormValues} disabled={saving}>
          <div className="pair-monitor-form-grid">
            <Form.Item label="名称" name="name">
              <Input placeholder="BTC basis" />
            </Form.Item>
            <Form.Item label="腿1交易所" name="leg1_exchange" rules={[{ required: true }]}>
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item label="腿1标的" name="leg1_symbol" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="腿1市场" name="leg1_market_type" rules={[{ required: true }]}>
              <Select options={marketTypeOptions} />
            </Form.Item>
            <Form.Item label="腿1价格" name="leg1_price_field" rules={[{ required: true }]}>
              <Select options={priceFieldOptions} />
            </Form.Item>
            <Form.Item label="腿2交易所" name="leg2_exchange" rules={[{ required: true }]}>
              <Select options={exchangeOptions} showSearch />
            </Form.Item>
            <Form.Item label="腿2标的" name="leg2_symbol" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="腿2市场" name="leg2_market_type" rules={[{ required: true }]}>
              <Select options={marketTypeOptions} />
            </Form.Item>
            <Form.Item label="腿2价格" name="leg2_price_field" rules={[{ required: true }]}>
              <Select options={priceFieldOptions} />
            </Form.Item>
            <Form.Item label="保留天数" name="retention_days" rules={[{ required: true }]}>
              <InputNumber min={1} max={30} className="wide-input" />
            </Form.Item>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => void createRule()} loading={saving}>
            新增监控对
          </Button>
        </Form>
      </section>

      <section className="pair-monitor-layout">
        <Table<PairMonitorRule>
          className="opportunity-table pair-monitor-rules"
          rowKey={(rule) => rule.id ?? rule.name}
          columns={ruleColumns}
          dataSource={rules}
          loading={loading}
          pagination={false}
          size="small"
          tableLayout="fixed"
          onRow={(rule) => ({
            onClick: () => setSelectedRuleId(rule.id ?? null)
          })}
          rowClassName={(rule) => (rule.id === selectedRule?.id ? "pair-monitor-row-selected" : "")}
        />

        <div className="panel pair-monitor-detail">
          <div className="pair-monitor-detail-head">
            <Space direction="vertical" size={2}>
              <Typography.Title level={5}>{selectedRule?.name ?? "未选择监控对"}</Typography.Title>
              {selectedRule ? (
                <Typography.Text type="secondary">
                  {legLabel(selectedRule.leg1)} / {legLabel(selectedRule.leg2)}
                </Typography.Text>
              ) : null}
            </Space>
            <Switch
              checked={selectedRule?.enabled ?? false}
              disabled={!selectedRule}
              onChange={() => selectedRule && void toggleRule(selectedRule)}
            />
          </div>

          <div className="pair-monitor-stats">
            <Statistic title="样本" value={history?.count ?? 0} />
            <Statistic title="最新价差" value={history?.spread_pct.current ?? 0} precision={3} suffix="%" />
            <Statistic title="腿1资金" value={history?.leg1_funding_rate_pct.current ?? 0} precision={3} suffix="%" />
            <Statistic title="腿2资金" value={history?.leg2_funding_rate_pct.current ?? 0} precision={3} suffix="%" />
            <Statistic title="最新价格" value={latest ? `${price(latest.leg1_price)} / ${price(latest.leg2_price)}` : "-"} />
            <Statistic title="最近样本" value={time(history?.last_seen_at)} />
          </div>

          {historyLoading ? <Alert type="info" message="加载历史样本中" showIcon /> : null}
          <PairMonitorChart history={history} />

          <Table<PairMonitorPoint>
            className="pair-monitor-points"
            rowKey={(point) => point.bucket_at}
            columns={pointColumns}
            dataSource={[...(history?.points ?? [])].reverse().slice(0, 120)}
            loading={historyLoading}
            pagination={{ pageSize: 12 }}
            size="small"
            tableLayout="fixed"
          />
        </div>
      </section>
    </div>
  );
}
