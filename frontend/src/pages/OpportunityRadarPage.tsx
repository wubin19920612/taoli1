import { BellOutlined, ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
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
import { useCallback, useEffect, useState } from "react";

import {
  getOpportunityRadarPreview,
  getOpportunityRadarSettings,
  testOpportunityRadarNotification,
  updateOpportunityRadarSettings
} from "../api/client";
import type {
  OpportunityRadarCandidate,
  OpportunityRadarPreview,
  OpportunityRadarSettings,
  OpportunityRadarSignalLevel
} from "../api/types";

const exchangeOptions = ["bybit", "binance", "okx", "gate", "bitget", "aster", "hyperliquid"].map(
  (value) => ({ label: value, value })
);

const levelColor: Record<OpportunityRadarSignalLevel, string> = {
  HIGH: "red",
  MEDIUM: "orange",
  WATCH: "blue"
};

const levelText: Record<OpportunityRadarSignalLevel, string> = {
  HIGH: "强",
  MEDIUM: "中",
  WATCH: "观察"
};

const riskText: Record<string, string> = {
  UNKNOWN_DEPTH: "盘口深度未知",
  FUNDING_UNCONFIRMED: "资金收益未确认",
  FUNDING_AGAINST: "资金方向不利"
};

function signedPct(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function pct(value: number | null | undefined, digits = 3): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}%` : "-";
}

function compactMoney(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return value.toFixed(0);
}

function fundingLeg(rate: number | null, interval: number | null): string {
  const intervalText = typeof interval === "number" ? `${interval}h` : "-";
  return `${pct(rate)} / ${intervalText}`;
}

const columns: ColumnsType<OpportunityRadarCandidate> = [
  {
    title: "信号",
    width: 86,
    fixed: "left",
    render: (_, row) => (
      <div className="radar-stack-cell">
        <Tag color={levelColor[row.signal_level]}>{levelText[row.signal_level]}</Tag>
        <Typography.Text strong>{row.score.toFixed(0)}</Typography.Text>
      </div>
    )
  },
  {
    title: "标的",
    dataIndex: "symbol",
    width: 126,
    fixed: "left",
    render: (value: string) => <Typography.Text strong>{value}</Typography.Text>
  },
  {
    title: "试错方向",
    width: 190,
    render: (_, row) => (
      <div className="radar-route-cell">
        <span><Tag color="green">多</Tag>{row.long_exchange}</span>
        <span><Tag color="red">空</Tag>{row.short_exchange}</span>
      </div>
    )
  },
  {
    title: "溢价指数",
    width: 190,
    align: "right",
    render: (_, row) => (
      <div className="radar-stack-cell">
        <Typography.Text strong>{`${row.anchor_exchange} ${signedPct(row.anchor_premium_pct)}`}</Typography.Text>
        <Typography.Text type="secondary">{`${row.peer_exchange} ${signedPct(row.peer_premium_pct)}`}</Typography.Text>
        <Typography.Text type="danger">{`相对差 ${signedPct(row.relative_premium_gap_pct)}`}</Typography.Text>
      </div>
    )
  },
  {
    title: "可执行价差",
    dataIndex: "entry_spread_pct",
    width: 116,
    align: "right",
    sorter: (a, b) => a.entry_spread_pct - b.entry_spread_pct,
    render: (value: number) => <Typography.Text strong>{signedPct(value)}</Typography.Text>
  },
  {
    title: "资金费率/周期",
    width: 202,
    align: "right",
    render: (_, row) => (
      <div className="radar-stack-cell">
        <Typography.Text>{`多 ${fundingLeg(row.long_funding_pct, row.long_funding_interval_hours)}`}</Typography.Text>
        <Typography.Text>{`空 ${fundingLeg(row.short_funding_pct, row.short_funding_interval_hours)}`}</Typography.Text>
        <Typography.Text type={(row.hourly_funding_edge_pct ?? 0) >= 0 ? "success" : "danger"}>
          {`每小时 ${signedPct(row.hourly_funding_edge_pct, 4)}`}
        </Typography.Text>
      </div>
    )
  },
  {
    title: "流动性",
    width: 142,
    align: "right",
    render: (_, row) => (
      <div className="radar-stack-cell">
        <Typography.Text>{`成交额 ${compactMoney(row.volume_24h_usdt)}`}</Typography.Text>
        <Typography.Text type="secondary">{`深度 ${compactMoney(row.depth_usdt)}`}</Typography.Text>
      </div>
    )
  },
  {
    title: "延迟",
    dataIndex: "data_age_seconds",
    width: 82,
    align: "right",
    render: (value: number) => `${value.toFixed(0)}s`
  }
];

function expandedRow(row: OpportunityRadarCandidate) {
  return (
    <div className="radar-expanded-row">
      <div>
        <Typography.Text type="secondary">参考中位溢价</Typography.Text>
        <Typography.Text>{signedPct(row.peer_median_premium_pct)}</Typography.Text>
      </div>
      <div>
        <Typography.Text type="secondary">开仓价格</Typography.Text>
        <Typography.Text>{`多 ${row.long_entry_price} / 空 ${row.short_entry_price}`}</Typography.Text>
      </div>
      <div>
        <Typography.Text type="secondary">风险</Typography.Text>
        <Space size={4} wrap>
          {row.risk_labels.length > 0
            ? row.risk_labels.map((label) => <Tag key={label}>{riskText[label] ?? label}</Tag>)
            : <Tag color="green">无额外风险标签</Tag>}
        </Space>
      </div>
    </div>
  );
}

export function OpportunityRadarPage() {
  const [form] = Form.useForm<OpportunityRadarSettings>();
  const anomalyExchange = Form.useWatch("anchor_exchange", form) ?? "bybit";
  const [preview, setPreview] = useState<OpportunityRadarPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingNotification, setTestingNotification] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [error, setError] = useState("");
  const peerExchangeOptions = exchangeOptions.filter((option) => option.value !== anomalyExchange);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [settings, nextPreview] = await Promise.all([
        getOpportunityRadarSettings(),
        getOpportunityRadarPreview()
      ]);
      form.setFieldsValue(settings);
      setPreview(nextPreview);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [form]);

  const refreshPreview = useCallback(async () => {
    try {
      setPreview(await getOpportunityRadarPreview());
      setError("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) {
      return;
    }
    const timer = window.setInterval(() => void refreshPreview(), 60_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refreshPreview]);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const values = await form.validateFields();
      const settings = await updateOpportunityRadarSettings(values);
      form.setFieldsValue(settings);
      setPreview(await getOpportunityRadarPreview());
      message.success("机会雷达参数已保存");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const changeAnomalyExchange = (value: string) => {
    const currentPeers = (form.getFieldValue("peer_exchanges") ?? []) as string[];
    const nextPeers = currentPeers.filter((exchange) => exchange !== value);
    form.setFieldValue(
      "peer_exchanges",
      nextPeers.length > 0
        ? nextPeers
        : exchangeOptions.map((option) => option.value).filter((exchange) => exchange !== value)
    );
  };

  const testNotification = async () => {
    setTestingNotification(true);
    setError("");
    try {
      await testOpportunityRadarNotification();
      message.success("飞书测试通知已发送");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setTestingNotification(false);
    }
  };

  return (
    <div className="page radar-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <section className="toolbar">
        <div className="toolbar-controls">
          <Typography.Title level={4}>机会雷达</Typography.Title>
          <Typography.Text type="secondary">异常交易所 vs 对手交易所</Typography.Text>
        </div>
        <div className="toolbar-actions">
          <Space size={8}>
            <Typography.Text type="secondary">自动刷新</Typography.Text>
            <Switch checked={autoRefresh} onChange={setAutoRefresh} />
            <Button
              icon={<BellOutlined />}
              onClick={() => void testNotification()}
              loading={testingNotification}
            >
              测试飞书
            </Button>
            <Button icon={<ReloadOutlined />} onClick={() => void refreshPreview()} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>
      </section>

      <section className="metric-row radar-metrics">
        <Statistic title="候选" value={preview?.displayed_candidates ?? 0} />
        <Statistic title="强信号" value={preview?.high_count ?? 0} />
        <Statistic title="中等" value={preview?.medium_count ?? 0} />
        <Statistic title="观察" value={preview?.watch_count ?? 0} />
        <Statistic title="异常所市场" value={preview?.anchor_markets ?? 0} />
        <Statistic title="跨所比较" value={preview?.total_pairs_evaluated ?? 0} />
      </section>

      <section className="panel panel-wide radar-settings-panel">
        <Form form={form} layout="vertical" disabled={loading || saving}>
          <div className="radar-settings-grid">
            <Form.Item label="启用扫描" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item
              label="飞书通知"
              name="feishu_notifications_enabled"
              valuePropName="checked"
              tooltip="服务器需要配置 FEISHU_WEBHOOK_URL；机器人启用签名校验时还要配置 FEISHU_SECRET。"
            >
              <Switch />
            </Form.Item>
            <Form.Item label="最低通知评分" name="min_alert_score" rules={[{ required: true }]}>
              <InputNumber min={0} max={100} step={5} />
            </Form.Item>
            <Form.Item label="连续命中次数" name="alert_consecutive_hits" rules={[{ required: true }]}>
              <InputNumber min={1} max={60} step={1} />
            </Form.Item>
            <Form.Item label="通知冷却时间" name="alert_cooldown_seconds" rules={[{ required: true }]}>
              <InputNumber min={0} max={86400} step={60} suffix="秒" />
            </Form.Item>
            <Form.Item label="异常溢价交易所" name="anchor_exchange" rules={[{ required: true }]}>
              <Select options={exchangeOptions} onChange={changeAnomalyExchange} />
            </Form.Item>
            <Form.Item label="对手交易所" name="peer_exchanges" rules={[{ required: true }]}>
              <Select mode="multiple" maxTagCount={3} options={peerExchangeOptions} />
            </Form.Item>
            <Form.Item label="溢价方向" name="premium_direction" rules={[{ required: true }]}>
              <Segmented
                block
                options={[
                  { label: "负溢价", value: "negative" },
                  { label: "双向", value: "both" },
                  { label: "正溢价", value: "positive" }
                ]}
              />
            </Form.Item>
            <Form.Item
              label="最低绝对溢价"
              name="min_abs_premium_pct"
              tooltip="异常交易所的溢价绝对值达到这个下限才入选；例如阈值 1.5%，-2% 会通过。"
              rules={[{ required: true }]}
            >
              <InputNumber min={0} step={0.1} suffix="%" />
            </Form.Item>
            <Form.Item
              label="最低跨所溢价差"
              name="min_relative_premium_gap_pct"
              tooltip="异常交易所与对手交易所的溢价差至少达到这个值，用于排除全市场同步极端的情况。"
              rules={[{ required: true }]}
            >
              <InputNumber min={0} step={0.1} suffix="%" />
            </Form.Item>
            <Form.Item
              label="最大试错价差"
              name="max_abs_entry_spread_pct"
              tooltip="两家交易所按真实 ask/bid 计算的价差上限；超过上限表示价格可能已经反映溢价异常。"
              rules={[{ required: true }]}
            >
              <InputNumber min={0} step={0.1} suffix="%" />
            </Form.Item>
            <Form.Item label="要求资金方向一致" name="require_funding_alignment" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="最小每小时资金优势" name="min_hourly_funding_edge_pct" rules={[{ required: true }]}>
              <InputNumber step={0.001} suffix="%" />
            </Form.Item>
            <Form.Item label="最小 24h 成交额" name="min_volume_24h_usdt" rules={[{ required: true }]}>
              <InputNumber min={0} step={100_000} suffix="USDT" />
            </Form.Item>
            <Form.Item label="计划仓位" name="notional_per_symbol_usdt" rules={[{ required: true }]}>
              <InputNumber min={1} step={100} suffix="USDT" />
            </Form.Item>
            <Form.Item label="最小深度倍数" name="min_depth_multiple" rules={[{ required: true }]}>
              <InputNumber min={0} step={1} suffix="x" />
            </Form.Item>
            <Form.Item label="最大数据延迟" name="max_data_age_seconds" rules={[{ required: true }]}>
              <InputNumber min={10} max={3600} step={10} suffix="秒" />
            </Form.Item>
            <Form.Item label="最多候选" name="max_candidates" rules={[{ required: true }]}>
              <InputNumber min={1} max={500} step={10} />
            </Form.Item>
          </div>
          <Button type="primary" icon={<SaveOutlined />} onClick={() => void save()} loading={saving}>
            保存策略参数
          </Button>
        </Form>
      </section>

      <Table
        className="opportunity-table radar-table"
        columns={columns}
        dataSource={preview?.candidates ?? []}
        loading={loading}
        rowKey="id"
        size="small"
        tableLayout="fixed"
        pagination={{ pageSize: 50, showSizeChanger: true }}
        scroll={{ x: 1140 }}
        expandable={{ expandedRowRender: expandedRow }}
      />
    </div>
  );
}
