import { LinkOutlined, ReloadOutlined, SaveOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Descriptions,
  Form,
  InputNumber,
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
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useMemo, useState } from "react";

import {
  getOilNewsSettings,
  listOilNews,
  refreshOilNews,
  updateOilNewsSettings
} from "../api/client";
import type {
  OilNewsDirection,
  OilNewsItem,
  OilNewsSettings,
  OilNewsSeverity
} from "../api/types";

dayjs.extend(utc);

const defaultSettings: OilNewsSettings = {
  enabled: true,
  poll_interval_seconds: 300,
  feishu_notifications_enabled: true,
  alert_min_severity: "high",
  alert_max_age_minutes: 120,
  bootstrap_alerts_enabled: false
};

const severityOptions: Array<{ label: string; value: "" | OilNewsSeverity }> = [
  { label: "全部级别", value: "" },
  { label: "极重大", value: "critical" },
  { label: "严重", value: "high" },
  { label: "中等", value: "medium" },
  { label: "一般", value: "low" }
];

const directionOptions: Array<{ label: string; value: "" | OilNewsDirection }> = [
  { label: "全部方向", value: "" },
  { label: "做多倾向", value: "long" },
  { label: "做空倾向", value: "short" },
  { label: "观察", value: "watch" }
];

function friendlyError(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function formatUtcPlus8(value: string): string {
  return dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss");
}

function directionTag(direction: OilNewsDirection) {
  const labels: Record<OilNewsDirection, string> = {
    long: "做多倾向",
    short: "做空倾向",
    watch: "观察"
  };
  const colors: Record<OilNewsDirection, string> = {
    long: "red",
    short: "green",
    watch: "default"
  };
  return <Tag color={colors[direction]}>{labels[direction]}</Tag>;
}

function severityTag(severity: OilNewsSeverity) {
  const labels: Record<OilNewsSeverity, string> = {
    critical: "极重大",
    high: "严重",
    medium: "中等",
    low: "一般"
  };
  const colors: Record<OilNewsSeverity, string> = {
    critical: "magenta",
    high: "orange",
    medium: "blue",
    low: "default"
  };
  return <Tag color={colors[severity]}>{labels[severity]}</Tag>;
}

function marketConfirmation(row: OilNewsItem) {
  const change = row.market?.change_1h_pct;
  if (change == null || row.direction === "watch" || Math.abs(change) < 0.3) {
    return <Tag>待确认</Tag>;
  }
  const confirmed =
    (row.direction === "long" && change > 0) ||
    (row.direction === "short" && change < 0);
  return <Tag color={confirmed ? "blue" : "gold"}>{confirmed ? "行情确认" : "行情背离"}</Tag>;
}

function alertStatusTag(status: string) {
  const labels: Record<string, string> = {
    sent: "已推送",
    pending: "待处理",
    failed: "推送失败",
    filtered: "未达门槛",
    skipped_bootstrap: "首次回填"
  };
  const color = status === "sent" ? "green" : status === "failed" ? "red" : "default";
  return <Tag color={color}>{labels[status] ?? status}</Tag>;
}

function details(row: OilNewsItem) {
  return (
    <div className="oil-news-details">
      <Descriptions size="small" column={2}>
        {row.title_zh ? (
          <Descriptions.Item label="中文标题" span={2}>{row.title_zh}</Descriptions.Item>
        ) : null}
        <Descriptions.Item label={row.title_zh ? "英文标题" : "完整标题"} span={2}>
          <Typography.Link href={row.url} target="_blank" rel="noreferrer">
            {row.title} <LinkOutlined />
          </Typography.Link>
        </Descriptions.Item>
        <Descriptions.Item label="来源">{row.source}</Descriptions.Item>
        <Descriptions.Item label="影响周期">{row.horizon}</Descriptions.Item>
        <Descriptions.Item label="发布时间">{formatUtcPlus8(row.published_at)} UTC+8</Descriptions.Item>
        <Descriptions.Item label="抓取时间">{formatUtcPlus8(row.fetched_at)} UTC+8</Descriptions.Item>
        <Descriptions.Item label="类别" span={2}>
          <Space wrap>{row.categories.map((category) => <Tag key={category}>{category}</Tag>)}</Space>
        </Descriptions.Item>
        {row.summary_zh ? <Descriptions.Item label="中文摘要" span={2}>{row.summary_zh}</Descriptions.Item> : null}
        {row.summary ? <Descriptions.Item label={row.summary_zh ? "英文摘要" : "摘要"} span={2}>{row.summary}</Descriptions.Item> : null}
        <Descriptions.Item label="判定依据" span={2}>
          <div className="oil-news-reasons">
            {row.rationale.map((reason) => <Typography.Text key={reason}>- {reason}</Typography.Text>)}
          </div>
        </Descriptions.Item>
        <Descriptions.Item label="反转条件" span={2}>{row.risk_note}</Descriptions.Item>
        {row.market ? (
          <Descriptions.Item label="采集时行情" span={2}>
            {row.market.symbol} {row.market.price?.toFixed(2) ?? "-"} / 近1小时 {row.market.change_1h_pct == null ? "-" : `${row.market.change_1h_pct >= 0 ? "+" : ""}${row.market.change_1h_pct.toFixed(2)}%`}
          </Descriptions.Item>
        ) : null}
      </Descriptions>
    </div>
  );
}

const columns: ColumnsType<OilNewsItem> = [
  { title: "发布时间(UTC+8)", dataIndex: "published_at", width: 142, render: formatUtcPlus8 },
  {
    title: "新闻",
    dataIndex: "title",
    width: 440,
    render: (title: string, row) => (
      <div className="oil-news-title-cell">
        <Typography.Link href={row.url} target="_blank" rel="noreferrer" ellipsis>
          {row.title_zh || title}
        </Typography.Link>
        {row.title_zh ? <Typography.Text type="secondary" ellipsis>{title}</Typography.Text> : null}
      </div>
    )
  },
  { title: "来源", dataIndex: "source", width: 150, ellipsis: true },
  { title: "级别", dataIndex: "severity", width: 88, render: severityTag },
  { title: "方向", dataIndex: "direction", width: 105, render: directionTag },
  { title: "置信度", dataIndex: "confidence", width: 88, render: (value: number) => `${(value * 100).toFixed(0)}%` },
  { title: "影响分", dataIndex: "impact_score", width: 78, sorter: (left, right) => left.impact_score - right.impact_score },
  { title: "价格验证", width: 95, render: (_, row) => marketConfirmation(row) },
  { title: "推送", dataIndex: "alert_status", width: 95, render: alertStatusTag }
];

export function OilNewsPage() {
  const [form] = Form.useForm<OilNewsSettings>();
  const [settings, setSettings] = useState(defaultSettings);
  const [rows, setRows] = useState<OilNewsItem[]>([]);
  const [severity, setSeverity] = useState<"" | OilNewsSeverity>("");
  const [direction, setDirection] = useState<"" | OilNewsDirection>("");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const loadRows = async () => {
    setLoading(true);
    setError("");
    try {
      setRows(await listOilNews({
        severity: severity || undefined,
        direction: direction || undefined,
        limit: 200
      }));
    } catch (exc) {
      const text = friendlyError(exc);
      setError(text);
      message.error(text);
    } finally {
      setLoading(false);
    }
  };

  const loadSettings = async () => {
    try {
      const next = await getOilNewsSettings();
      setSettings(next);
      form.setFieldsValue(next);
    } catch (exc) {
      const text = friendlyError(exc);
      setError(text);
      message.error(text);
    }
  };

  const saveSettings = async () => {
    const saved = await updateOilNewsSettings(await form.validateFields());
    setSettings(saved);
    form.setFieldsValue(saved);
    message.success("原油新闻监测配置已保存");
  };

  const runRefresh = async () => {
    setRefreshing(true);
    setError("");
    try {
      const result = await refreshOilNews();
      await loadRows();
      if (result.errors.length > 0) {
        message.warning(`刷新完成，${result.errors.length} 个数据源失败`);
      } else {
        message.success(`刷新完成，新增 ${result.inserted_count} 条，推送 ${result.alerted_count} 条`);
      }
    } catch (exc) {
      const text = friendlyError(exc);
      setError(text);
      message.error(text);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    form.setFieldsValue(defaultSettings);
    void loadSettings();
    // Settings are independent from the news filters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadRows();
    const timer = window.setInterval(() => void loadRows(), 60_000);
    return () => window.clearInterval(timer);
    // Keep automatic refresh aligned with the active filters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [severity, direction]);

  const metrics = useMemo(() => {
    const latestMarket = rows.find((row) => row.market?.price != null)?.market;
    return {
      latestMarket,
      major: rows.filter((row) => row.severity === "critical" || row.severity === "high").length,
      long: rows.filter((row) => row.direction === "long").length,
      short: rows.filter((row) => row.direction === "short").length
    };
  }, [rows]);

  return (
    <div className="page oil-news-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <div className="page-heading-row">
        <Space align="center" wrap>
          <Typography.Title level={3}>原油重大新闻</Typography.Title>
          <Tag color="orange">临时监控</Tag>
        </Space>
        <Space wrap>
          <Tag color={settings.enabled ? "green" : "default"}>监测 {settings.enabled ? "开启" : "关闭"}</Tag>
          <Tag color={settings.feishu_notifications_enabled ? "blue" : "default"}>飞书 {settings.feishu_notifications_enabled ? "开启" : "关闭"}</Tag>
        </Space>
      </div>

      <section className="oil-news-metrics">
        <div><Statistic title="CLUSDT" value={metrics.latestMarket?.price ?? 0} precision={2} /></div>
        <div><Statistic title="近1小时" value={metrics.latestMarket?.change_1h_pct ?? 0} precision={2} suffix="%" /></div>
        <div><Statistic title="重大新闻" value={metrics.major} /></div>
        <div><Statistic title="做多倾向" value={metrics.long} /></div>
        <div><Statistic title="做空倾向" value={metrics.short} /></div>
      </section>

      <section className="panel panel-wide oil-news-settings-panel">
        <Form
          form={form}
          layout="vertical"
          onFinish={saveSettings}
          onValuesChange={(_, values) => setSettings({ ...defaultSettings, ...values })}
        >
          <div className="oil-news-settings-grid">
            <Form.Item label="启用监测" name="enabled" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item label="飞书推送" name="feishu_notifications_enabled" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item label="首次启动推送" name="bootstrap_alerts_enabled" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item label="轮询间隔" name="poll_interval_seconds" rules={[{ required: true }]}>
              <InputNumber min={60} max={86400} step={60} suffix="s" className="wide-input" />
            </Form.Item>
            <Form.Item label="最低推送级别" name="alert_min_severity" rules={[{ required: true }]}>
              <Select options={severityOptions.filter((option) => option.value !== "")} />
            </Form.Item>
            <Form.Item label="新闻有效窗口" name="alert_max_age_minutes" rules={[{ required: true }]}>
              <InputNumber min={5} max={10080} step={5} suffix="min" className="wide-input" />
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>保存监测配置</Button>
        </Form>
      </section>

      <div className="toolbar">
        <Space className="toolbar-controls" wrap>
          <Typography.Title level={4}>新闻记录</Typography.Title>
          <Select aria-label="重大程度" value={severity} options={severityOptions} onChange={setSeverity} style={{ width: 128 }} />
          <Select aria-label="交易方向" value={direction} options={directionOptions} onChange={setDirection} style={{ width: 128 }} />
        </Space>
        <Space className="toolbar-actions" wrap>
          <Button icon={<SearchOutlined />} onClick={() => void loadRows()} loading={loading}>查询</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void runRefresh()} loading={refreshing}>抓取最新</Button>
        </Space>
      </div>

      <Table
        className="opportunity-table oil-news-table"
        columns={columns}
        dataSource={rows}
        rowKey="id"
        loading={loading}
        size="middle"
        tableLayout="fixed"
        scroll={{ x: 1380 }}
        expandable={{ expandedRowRender: details, rowExpandable: () => true }}
      />
    </div>
  );
}
