import { ReloadOutlined, ThunderboltOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";

import { scanMinuteSignals } from "../api/client";
import type { MinuteSignalEvent, MinuteSignalPoint, MinuteSignalScanResult } from "../api/types";

type FormValues = {
  symbol: string;
  alpha_symbol: string;
  hours: number;
};

const defaultValues: FormValues = {
  symbol: "AKEUSDT",
  alpha_symbol: "ALPHA_331USDT",
  hours: 4
};

const eventLabels: Record<string, string> = {
  SHOCK_ALERT: "价差冲击",
  ENTRY: "候选入场",
  TAKE_PROFIT: "价差收敛",
  STOP_LOSS: "止损",
  TIME_EXIT: "超时退出"
};

const eventColors: Record<string, string> = {
  SHOCK_ALERT: "orange",
  ENTRY: "blue",
  TAKE_PROFIT: "green",
  STOP_LOSS: "red",
  TIME_EXIT: "default"
};

function bps(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value >= 0 ? "+" : ""}${value.toFixed(2)} bps`
    : "-";
}

function latestNumber(result: MinuteSignalScanResult | null, key: string): number | null {
  const value = result?.latest?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pointKey(point: MinuteSignalPoint): string {
  return `${point.time_cst}-${point.basis_bps ?? "na"}`;
}

const eventColumns: ColumnsType<MinuteSignalEvent> = [
  {
    title: "事件",
    dataIndex: "event_type",
    width: 130,
    render: (value: string) => (
      <Tag color={eventColors[value] ?? "default"}>{eventLabels[value] ?? value}</Tag>
    )
  },
  { title: "信号时间", dataIndex: "signal_time_cst", width: 150 },
  { title: "计划执行", dataIndex: "planned_execution_time_cst", width: 150 },
  {
    title: "信号 basis",
    dataIndex: "signal_basis_bps",
    align: "right",
    width: 120,
    render: (value: number | null) => bps(value)
  },
  {
    title: "premium",
    dataIndex: "premium_bps",
    align: "right",
    width: 120,
    render: (value: number | null) => bps(value)
  },
  {
    title: "15m premium 低点",
    dataIndex: "premium_low_15m_bps",
    align: "right",
    width: 140,
    render: (value: number | null) => bps(value)
  },
  {
    title: "basis 峰值",
    dataIndex: "basis_peak_60m_bps",
    align: "right",
    width: 120,
    render: (value: number | null) => bps(value)
  },
  {
    title: "说明",
    dataIndex: "reason",
    width: 280,
    render: (value: string) => <Typography.Text type="secondary">{value}</Typography.Text>
  }
];

const pointColumns: ColumnsType<MinuteSignalPoint> = [
  { title: "时间", dataIndex: "time_cst", width: 150 },
  {
    title: "basis",
    dataIndex: "basis_bps",
    align: "right",
    width: 120,
    render: (value: number | null) => bps(value)
  },
  {
    title: "premium",
    dataIndex: "premium_bps",
    align: "right",
    width: 120,
    render: (value: number | null) => bps(value)
  },
  {
    title: "60m 峰值",
    dataIndex: "basis_peak_60m_bps",
    align: "right",
    width: 120,
    render: (value: number | null) => bps(value)
  },
  {
    title: "压缩比例",
    dataIndex: "compression_ratio",
    align: "right",
    width: 110,
    render: (value: number | null) =>
      typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "-"
  }
];

export function MinuteSignalPage() {
  const [form] = Form.useForm<FormValues>();
  const [result, setResult] = useState<MinuteSignalScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(
    async (values: FormValues = form.getFieldsValue()) => {
      setLoading(true);
      setError("");
      try {
        const normalized = {
          symbol: values.symbol.trim().toUpperCase(),
          alpha_symbol: values.alpha_symbol.trim().toUpperCase(),
          hours: Number(values.hours)
        };
        setResult(await scanMinuteSignals(normalized));
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

  const currentBasis = latestNumber(result, "basis_bps");
  const currentPremium = latestNumber(result, "premium_bps");
  const currentPeak = latestNumber(result, "basis_peak_60m_bps");
  const currentCompression = latestNumber(result, "compression_ratio");
  const recentPoints = result?.points.slice(-120).reverse() ?? [];

  return (
    <div className="page minute-signal-page">
      {error ? <Alert className="page-alert" type="error" showIcon message={error} /> : null}

      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4} style={{ margin: 0 }}>
            1 分钟价差信号
          </Typography.Title>
          <Typography.Text type="secondary">
            监测 basis 冲击、回压和重新扩张；信号只表示下一分钟的计划执行边界。
          </Typography.Text>
        </div>
        <div className="toolbar-actions">
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={() => void load()}
          >
            刷新扫描
          </Button>
        </div>
      </section>

      <Card size="small">
        <Form
          form={form}
          layout="inline"
          initialValues={defaultValues}
          onFinish={(values) => void load(values)}
        >
          <Form.Item label="期货标的" name="symbol" rules={[{ required: true }]}>
            <Input style={{ width: 150 }} />
          </Form.Item>
          <Form.Item label="Alpha 现货" name="alpha_symbol" rules={[{ required: true }]}>
            <Input style={{ width: 170 }} />
          </Form.Item>
          <Form.Item label="回看小时" name="hours" rules={[{ required: true }]}>
            <InputNumber min={1} max={24} style={{ width: 100 }} />
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
            <Statistic title="当前 basis" value={currentBasis ?? 0} precision={2} suffix="bps" />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="当前 premium" value={currentPremium ?? 0} precision={2} suffix="bps" />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic title="60m basis 峰值" value={currentPeak ?? 0} precision={2} suffix="bps" />
          </Col>
          <Col xs={12} sm={6}>
            <Statistic
              title="压缩比例"
              value={currentCompression === null ? 0 : currentCompression * 100}
              precision={1}
              suffix="%"
            />
          </Col>
        </Row>
      </section>

      <section className="panel">
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Space wrap>
            <Typography.Title level={5} style={{ margin: 0 }}>
              {result?.futures_symbol ?? defaultValues.symbol}
            </Typography.Title>
            <Tag color="blue">{result?.alpha_symbol ?? defaultValues.alpha_symbol}</Tag>
            <Tag>{result ? `${result.bar_count} 根 1m K 线` : "尚未扫描"}</Tag>
            {result ? <Tag>观察时间 {result.observed_at}</Tag> : null}
          </Space>
          <Table<MinuteSignalEvent>
            className="opportunity-table"
            rowKey={(row, index) => `${row.event_type}-${row.signal_time_cst}-${index}`}
            columns={eventColumns}
            dataSource={result?.events ?? []}
            loading={loading}
            pagination={{ pageSize: 10, showSizeChanger: true }}
            scroll={{ x: 1250 }}
            size="small"
          />
        </Space>
      </section>

      <section className="panel">
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            最近 1 分钟特征
          </Typography.Title>
          <Table<MinuteSignalPoint>
            rowKey={pointKey}
            columns={pointColumns}
            dataSource={recentPoints}
            loading={loading}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            scroll={{ x: 650 }}
            size="small"
          />
        </Space>
      </section>

      {result?.warnings.length ? (
        <Alert
          type="warning"
          showIcon
          message="使用边界"
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
