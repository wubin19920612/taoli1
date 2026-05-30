import { ReloadOutlined, SearchOutlined, SaveOutlined } from "@ant-design/icons";
import { Alert, Button, Form, InputNumber, Select, Space, Switch, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useState } from "react";

import {
  getAnnouncementSettings,
  listAnnouncementExchanges,
  listAnnouncements,
  updateAnnouncementSettings
} from "../api/client";
import type {
  AnnouncementExchangeOption,
  AnnouncementKind,
  AnnouncementSettings,
  ExchangeAnnouncement
} from "../api/types";

dayjs.extend(utc);

const defaultAnnouncementSettings: AnnouncementSettings = {
  enabled: true,
  poll_interval_seconds: 300,
  record_exchanges: ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
  alert_exchanges: [],
  bootstrap_alerts_enabled: false,
  event_reminders_enabled: true,
  event_reminder_minutes_before: 30
};

const fallbackExchangeOptions: AnnouncementExchangeOption[] = [
  { label: "Binance", value: "binance" },
  { label: "OKX", value: "okx" },
  { label: "Bybit", value: "bybit" },
  { label: "Gate", value: "gate" },
  { label: "Bitget", value: "bitget" },
  { label: "Hyperliquid", value: "hyperliquid" }
];

const kindOptions: Array<{ label: string; value: "" | AnnouncementKind }> = [
  { label: "全部类型", value: "" },
  { label: "上币", value: "listing" },
  { label: "下币", value: "delisting" },
  { label: "其他", value: "other" }
];

function normalizeAnnouncementSettings(values?: Partial<AnnouncementSettings>): AnnouncementSettings {
  return {
    ...defaultAnnouncementSettings,
    ...(values ?? {}),
    record_exchanges: values?.record_exchanges ?? defaultAnnouncementSettings.record_exchanges,
    alert_exchanges: values?.alert_exchanges ?? defaultAnnouncementSettings.alert_exchanges
  };
}

function friendlyError(exc: unknown): string {
  const raw = exc instanceof Error ? exc.message : String(exc);
  try {
    const parsed = JSON.parse(raw) as { detail?: unknown };
    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail;
    }
  } catch {
    // Use the raw error text.
  }
  return raw;
}

function formatUtcPlus8(value: string): string {
  return dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss");
}

function kindTag(kind: AnnouncementKind) {
  const labels: Record<AnnouncementKind, string> = {
    listing: "上币",
    delisting: "下币",
    other: "其他"
  };
  const colors: Record<AnnouncementKind, string> = {
    listing: "green",
    delisting: "red",
    other: "default"
  };
  return <Tag color={colors[kind]}>{labels[kind]}</Tag>;
}

function alertStatusTag(status: string) {
  const color = status === "sent" ? "green" : status === "failed" ? "red" : status === "muted" ? "default" : "blue";
  return <Tag color={color}>{status}</Tag>;
}

function reminderStatusTag(status: string) {
  const labels: Record<string, string> = {
    pending: "待提醒",
    sent: "已提醒",
    failed: "失败",
    skipped: "跳过",
    not_applicable: "无事件时间"
  };
  const color = status === "sent" ? "green" : status === "failed" ? "red" : status === "pending" ? "orange" : "default";
  return <Tag color={color}>{labels[status] ?? status}</Tag>;
}

function marketTypeTag(value?: string | null) {
  return value ? <Tag color="geekblue">{value}</Tag> : "-";
}

function symbolTags(values?: string[]) {
  if (!values || values.length === 0) {
    return "-";
  }
  return (
    <Space size={[0, 4]} wrap>
      {values.slice(0, 8).map((symbol) => (
        <Tag key={symbol} color="purple">
          {symbol}
        </Tag>
      ))}
      {values.length > 8 ? <Tag>+{values.length - 8}</Tag> : null}
    </Space>
  );
}

function rowSummary(row: ExchangeAnnouncement): string {
  const pieces: string[] = [];
  if (row.symbols.length > 0) {
    pieces.push(`币种 ${row.symbols.slice(0, 8).join(", ")}`);
  }
  if (row.market_type) {
    pieces.push(`市场 ${row.market_type}`);
  }
  if (row.event_time) {
    pieces.push(`事件时间 ${formatUtcPlus8(row.event_time)} UTC+8`);
  }
  if (pieces.length === 0) {
    return row.title;
  }
  const action = row.kind === "listing" ? "上币" : row.kind === "delisting" ? "下币" : "公告";
  return `${action}: ${pieces.join("；")}`;
}

const columns: ColumnsType<ExchangeAnnouncement> = [
  { title: "公告时间(UTC+8)", dataIndex: "published_at", width: 142, render: formatUtcPlus8 },
  { title: "交易所", dataIndex: "exchange", width: 96, render: (value: string) => value.toUpperCase() },
  { title: "类型", dataIndex: "kind", width: 86, render: kindTag },
  { title: "币种", dataIndex: "symbols", width: 180, render: symbolTags },
  { title: "市场", dataIndex: "market_type", width: 120, render: marketTypeTag },
  { title: "事件时间(UTC+8)", dataIndex: "event_time", width: 142, render: (value?: string | null) => (value ? formatUtcPlus8(value) : "-") },
  {
    title: "公告摘要",
    dataIndex: "title",
    ellipsis: true,
    render: (value: string, row) => {
      const display = rowSummary(row);
      return (
        <Space direction="vertical" size={2} className="announcement-title-cell">
          <a href={row.url} target="_blank" rel="noreferrer">
            {display}
          </a>
          {display !== value ? <Typography.Text type="secondary">{value}</Typography.Text> : null}
        </Space>
      );
    }
  },
  { title: "分类", dataIndex: "category", width: 180, ellipsis: true, render: (value?: string | null) => value || "-" },
  { title: "新公告告警", dataIndex: "alert_status", width: 106, render: alertStatusTag },
  { title: "到点提醒", dataIndex: "event_reminder_status", width: 112, render: reminderStatusTag }
];

export function AnnouncementsPage() {
  const [form] = Form.useForm<AnnouncementSettings>();
  const [settingsPreview, setSettingsPreview] = useState<AnnouncementSettings>(defaultAnnouncementSettings);
  const [exchangeOptions, setExchangeOptions] = useState<AnnouncementExchangeOption[]>(fallbackExchangeOptions);
  const [rows, setRows] = useState<ExchangeAnnouncement[]>([]);
  const [loading, setLoading] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [exchange, setExchange] = useState("");
  const [kind, setKind] = useState<"" | AnnouncementKind>("");
  const [error, setError] = useState("");

  const loadAnnouncements = async () => {
    setLoading(true);
    setError("");
    try {
      setRows(
        await listAnnouncements({
          exchange: exchange.trim().toLowerCase(),
          kind: kind || undefined,
          limit: 200
        })
      );
    } catch (exc) {
      const text = friendlyError(exc);
      setError(text);
      message.error(text);
    } finally {
      setLoading(false);
    }
  };

  const loadSettings = async () => {
    setSettingsLoading(true);
    try {
      const [nextSettings, nextExchanges] = await Promise.all([
        getAnnouncementSettings(),
        listAnnouncementExchanges().catch(() => fallbackExchangeOptions)
      ]);
      const normalized = normalizeAnnouncementSettings(nextSettings);
      setExchangeOptions(nextExchanges.length > 0 ? nextExchanges : fallbackExchangeOptions);
      setSettingsPreview(normalized);
      form.setFieldsValue(normalized);
    } catch (exc) {
      const text = friendlyError(exc);
      setError(text);
      message.error(text);
    } finally {
      setSettingsLoading(false);
    }
  };

  const saveSettings = async () => {
    const values = normalizeAnnouncementSettings(await form.validateFields());
    const saved = normalizeAnnouncementSettings(await updateAnnouncementSettings(values));
    form.setFieldsValue(saved);
    setSettingsPreview(saved);
    message.success("公告监控配置已保存");
  };

  useEffect(() => {
    form.setFieldsValue(defaultAnnouncementSettings);
    void loadSettings();
    void loadAnnouncements();
    // Initial load only; filters use the query button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const tableExchangeOptions = [{ label: "全部交易所", value: "" }, ...exchangeOptions];
  const alertExchangeSet = new Set(settingsPreview.alert_exchanges);
  const recordExchangeSet = new Set(settingsPreview.record_exchanges);

  return (
    <div className="page announcements-page">
      {error ? <Alert type="error" message={error} showIcon /> : null}
      <section className="panel panel-wide announcements-settings-panel">
        <div className="announcements-settings-head">
          <div>
            <Typography.Title level={4}>上币/下币公告监控</Typography.Title>
            <Typography.Text type="secondary">
              记录交易所公告并按配置发送飞书告警，当前支持 Binance、OKX、Bybit、Gate、Bitget、Hyperliquid 的公开数据源。
            </Typography.Text>
          </div>
          <Space wrap>
            {exchangeOptions.map((item) => (
              <Tag
                key={item.value}
                color={alertExchangeSet.has(item.value) ? "green" : recordExchangeSet.has(item.value) ? "blue" : "default"}
              >
                {item.label}
                {alertExchangeSet.has(item.value) ? " 告警" : recordExchangeSet.has(item.value) ? " 记录" : " 关闭"}
              </Tag>
            ))}
          </Space>
        </div>
        <Alert
          className="rule-guide"
          type={settingsPreview.enabled ? "info" : "warning"}
          showIcon
          message={settingsPreview.enabled ? "公告轮询已启用" : "公告轮询已关闭"}
          description="record_exchanges 控制哪些交易所会写入公告记录，alert_exchanges 控制哪些交易所的新公告和事件到点提醒会发飞书。只有能识别出明确上币/下架时间的公告才会触发到点提醒。"
        />
        <Form
          form={form}
          layout="vertical"
          disabled={settingsLoading}
          onFinish={saveSettings}
          onValuesChange={(_, values) => setSettingsPreview(normalizeAnnouncementSettings(values))}
        >
          <div className="announcements-settings-grid">
            <Form.Item label="启用公告轮询" name="enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="首次启动也告警" name="bootstrap_alerts_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="事件到点提醒" name="event_reminders_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="轮询间隔" name="poll_interval_seconds" rules={[{ required: true }]}>
              <InputNumber min={30} max={86400} step={30} suffix="s" className="wide-input" />
            </Form.Item>
            <Form.Item label="提前提醒" name="event_reminder_minutes_before" rules={[{ required: true }]}>
              <InputNumber min={1} max={10080} step={5} suffix="min" className="wide-input" />
            </Form.Item>
            <Form.Item label="记录交易所" name="record_exchanges">
              <Select mode="multiple" allowClear options={exchangeOptions} />
            </Form.Item>
            <Form.Item label="告警交易所" name="alert_exchanges">
              <Select mode="multiple" allowClear options={exchangeOptions} />
            </Form.Item>
          </div>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />}>
            保存公告监控
          </Button>
        </Form>
      </section>

      <div className="toolbar">
        <Space className="toolbar-controls" wrap>
          <Typography.Title level={4}>公告记录</Typography.Title>
          <Select
            value={exchange}
            options={tableExchangeOptions}
            onChange={setExchange}
            popupMatchSelectWidth={false}
            style={{ width: 150 }}
            aria-label="交易所"
          />
          <Select
            value={kind}
            options={kindOptions}
            onChange={setKind}
            popupMatchSelectWidth={false}
            style={{ width: 128 }}
            aria-label="公告类型"
          />
        </Space>
        <Space className="toolbar-actions">
          <Button icon={<SearchOutlined />} onClick={() => void loadAnnouncements()} loading={loading}>
            查询
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void loadAnnouncements()} loading={loading} />
        </Space>
      </div>
      <Table
        className="opportunity-table announcements-table"
        columns={columns}
        dataSource={rows}
        rowKey="id"
        loading={loading}
        size="middle"
        tableLayout="fixed"
      />
    </div>
  );
}
