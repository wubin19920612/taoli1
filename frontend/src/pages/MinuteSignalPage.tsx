import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  InputNumber,
  Row,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";

import { scanMinuteSignalUniverse } from "../api/client";
import type {
  MinuteSignalEventType,
  MinuteSignalUniverseCandidate,
  MinuteSignalUniverseScanResult
} from "../api/types";

type FormValues = {
  hours: number;
  max_symbols: number;
  min_volume_24h_usdt: number;
};

const defaultValues: FormValues = {
  hours: 4,
  max_symbols: 30,
  min_volume_24h_usdt: 100_000
};

const eventLabels: Record<MinuteSignalEventType, string> = {
  SHOCK_ALERT: "价差冲击",
  ENTRY: "候选入场",
  TAKE_PROFIT: "价差收敛",
  STOP_LOSS: "止损",
  TIME_EXIT: "超时退出"
};

const eventColors: Record<MinuteSignalEventType, string> = {
  SHOCK_ALERT: "orange",
  ENTRY: "blue",
  TAKE_PROFIT: "green",
  STOP_LOSS: "red",
  TIME_EXIT: "default"
};

const reasonLabels: Record<string, string> = {
  shock_compressed_and_entry_confirmed: "价差冲击后回压，确认候选入场",
  basis_expansion_with_negative_premium: "价差扩大，且合约溢价为负",
  new_shock_after_expiry: "原冲击过期后再次出现新冲击",
  basis_converged: "价差已收敛，达到止盈条件",
  basis_reversed: "价差方向反转，触发止损",
  no_confirmed_signal: "尚未确认信号",
  scan_failed: "扫描失败"
};

function formatTime(value: string | null | undefined, includeSeconds = false): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value.replace("T", " ").replace(/([+-]\d{2}:\d{2}|Z)$/, "");
  }
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {})
  }).formatToParts(date);
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}${
    includeSeconds ? `:${part("second")}` : ""
  }`;
}

function reasonLabel(value: string): string {
  return reasonLabels[value] ?? `检测条件：${value.replace(/_/g, " ")}`;
}

function bps(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value >= 0 ? "+" : ""}${value.toFixed(2)} bps`
    : "-";
}

function volume(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(0)}K`;
  }
  return value.toFixed(0);
}

function eventTag(eventType: MinuteSignalEventType | null) {
  if (!eventType) {
    return <Tag>观察</Tag>;
  }
  return <Tag color={eventColors[eventType]}>{eventLabels[eventType]}</Tag>;
}

function parameterTitle(label: string, description: string) {
  return (
    <Tooltip title={description}>
      <span style={{ cursor: "help", borderBottom: "1px dashed currentColor" }}>{label}</span>
    </Tooltip>
  );
}

const candidateColumns: ColumnsType<MinuteSignalUniverseCandidate> = [
  {
    title: "标的",
    dataIndex: "futures_symbol",
    fixed: "left",
    width: 120,
    render: (value: string, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{value}</Typography.Text>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {row.base_asset}
        </Typography.Text>
      </Space>
    )
  },
  {
    title: "Alpha 现货",
    dataIndex: "alpha_symbol",
    width: 150,
    render: (value: string) => <Tag color="blue">{value}</Tag>
  },
  {
    title: "事件",
    dataIndex: "event_type",
    width: 120,
    render: (value: MinuteSignalEventType | null) => eventTag(value)
  },
  {
    title: parameterTitle("信号时间", "北京时间；对应触发信号的 1 分钟 K 线起始时间"),
    dataIndex: "signal_time_cst",
    width: 155,
    render: (value: string | null) => formatTime(value)
  },
  {
    title: parameterTitle(
      "现货-合约价差（basis）",
      "Alpha 现货相对 Futures 合约标记价的价差；正数表示现货高于合约。1 bps = 0.01%。"
    ),
    dataIndex: "basis_bps",
    align: "right",
    width: 155,
    render: (value: number | null) => bps(value)
  },
  {
    title: parameterTitle(
      "合约溢价（premium）",
      "Futures 合约标记价相对指数价的偏离；负数表示合约低于指数。1 bps = 0.01%。"
    ),
    dataIndex: "premium_bps",
    align: "right",
    width: 155,
    render: (value: number | null) => bps(value)
  },
  {
    title: parameterTitle("60 分钟价差峰值", "最近 60 分钟内观测到的最高 basis。"),
    dataIndex: "basis_peak_60m_bps",
    align: "right",
    width: 140,
    render: (value: number | null) => bps(value)
  },
  {
    title: parameterTitle(
      "价差压缩比例",
      "从最近 60 分钟 basis 峰值回落的比例；例如 26.9% 表示已从峰值回落约 26.9%。"
    ),
    dataIndex: "compression_ratio",
    align: "right",
    width: 125,
    render: (value: number | null) =>
      typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "-"
  },
  {
    title: parameterTitle("24 小时成交额", "Futures 合约最近 24 小时的 USDT 计价成交额，用于衡量流动性。"),
    dataIndex: "volume_24h_usdt",
    align: "right",
    width: 140,
    render: (value: number) => `${volume(value)} USDT`
  },
  {
    title: "说明",
    dataIndex: "reason",
    width: 280,
    render: (value: string, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text type={row.error ? "danger" : "secondary"}>
          {row.error ? `扫描失败：${row.error}` : reasonLabel(value)}
        </Typography.Text>
        {row.planned_execution_time_cst ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            计划执行：{formatTime(row.planned_execution_time_cst)}
          </Typography.Text>
        ) : null}
      </Space>
    )
  }
];

export function MinuteSignalPage() {
  const [form] = Form.useForm<FormValues>();
  const [result, setResult] = useState<MinuteSignalUniverseScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    async (values: FormValues = form.getFieldsValue()) => {
      setLoading(true);
      setError("");
      try {
        const normalized = {
          hours: Number(values.hours),
          max_symbols: Number(values.max_symbols),
          min_volume_24h_usdt: Number(values.min_volume_24h_usdt)
        };
        setResult(await scanMinuteSignalUniverse(normalized));
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setLoading(false);
      }
    },
    [form]
  );

  useEffect(() => {
    form.setFieldsValue(defaultValues);
    void load(defaultValues);
  }, [form, load]);

  useEffect(() => {
    if (!autoRefresh || loading) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void load();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load, loading]);

  return (
    <div className="page minute-signal-page">
      {error ? <Alert className="page-alert" type="error" showIcon message={error} /> : null}

      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4} style={{ margin: 0 }}>
            1 分钟全网价差信号
          </Typography.Title>
          <Typography.Text type="secondary">
            自动发现候选标的，不需要手工填写币种；系统先扫描候选池，再用 1 分钟 K 线复核冲击、回压和入场信号。
          </Typography.Text>
        </div>
        <Space className="toolbar-actions" wrap>
          <Space size={6}>
            <Typography.Text type="secondary">每 60 秒自动刷新</Typography.Text>
            <Switch checked={autoRefresh} onChange={setAutoRefresh} />
          </Space>
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={() => void load()}
          >
            扫描全市场
          </Button>
        </Space>
      </section>

      <Card size="small">
        <Form
          form={form}
          layout="inline"
          initialValues={defaultValues}
          onFinish={(values) => void load(values)}
        >
          <Form.Item label="回看小时" name="hours" rules={[{ required: true }]}>
            <InputNumber min={1} max={24} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item label="复核候选数" name="max_symbols" rules={[{ required: true }]}>
            <InputNumber min={5} max={100} style={{ width: 110 }} />
          </Form.Item>
          <Form.Item label="最低24h成交额" name="min_volume_24h_usdt" rules={[{ required: true }]}>
            <InputNumber
              min={0}
              step={100_000}
              style={{ width: 150 }}
              formatter={(value) => `${value ?? ""}`}
            />
          </Form.Item>
          <Form.Item>
            <Button htmlType="submit" icon={<ThunderboltOutlined />} loading={loading}>
              执行扫描
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <section className="metric-row">
        <Row gutter={[16, 12]}>
          <Col xs={12} sm={6}>
            <Statistic title="全市场映射" value={result?.universe_count ?? 0} suffix="个" />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="符合初筛" value={result?.eligible_count ?? 0} suffix="个" />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="1m复核" value={result?.scanned_count ?? 0} suffix="个" />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="当前信号" value={result?.signal_count ?? 0} suffix="个" />
          </Col>
        </Row>
      </section>

      <section className="panel">
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Space wrap>
            <Typography.Title level={5} style={{ margin: 0 }}>
              自动发现候选
            </Typography.Title>
            <Tag color="blue">
              {result ? `观察时间（北京时间）${formatTime(result.observed_at, true)}` : "尚未扫描"}
            </Tag>
            {result?.error_count ? <Tag color="red">{result.error_count} 个扫描失败</Tag> : null}
          </Space>
          <Table<MinuteSignalUniverseCandidate>
            className="opportunity-table"
            rowKey={(row) => `${row.futures_symbol}-${row.alpha_symbol}`}
            columns={candidateColumns}
            dataSource={result?.candidates ?? []}
            loading={loading}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            scroll={{ x: 1400 }}
            size="small"
          />
        </Space>
      </section>

      {result?.warnings.length ? (
        <Alert
          type="warning"
          showIcon
          message="扫描范围与执行边界"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      ) : null}
    </div>
  );
}
