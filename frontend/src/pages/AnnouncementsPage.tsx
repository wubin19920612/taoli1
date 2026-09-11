import { ReloadOutlined, SearchOutlined, SaveOutlined } from "@ant-design/icons";
import { Alert, Button, Descriptions, Form, InputNumber, Select, Space, Switch, Table, Tag, Typography, message } from "antd";
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
  AnnouncementAssetResearch,
  AnnouncementKind,
  AnnouncementSettings,
  ExchangeAnnouncement
} from "../api/types";

dayjs.extend(utc);

const defaultAnnouncementSettings: AnnouncementSettings = {
  enabled: true,
  poll_interval_seconds: 300,
  alert_max_age_minutes: 30,
  record_exchanges: ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
  alert_exchanges: [],
  listing_delisting_alerts_enabled: true,
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
    alert_exchanges: values?.alert_exchanges ?? defaultAnnouncementSettings.alert_exchanges,
    listing_delisting_alerts_enabled:
      values?.listing_delisting_alerts_enabled ?? defaultAnnouncementSettings.listing_delisting_alerts_enabled
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
    not_applicable: "无具体时间"
  };
  const color = status === "sent" ? "green" : status === "failed" ? "red" : status === "pending" ? "orange" : "default";
  return <Tag color={color}>{labels[status] ?? status}</Tag>;
}

const marketTypeLabels: Record<string, string> = {
  spot: "现货",
  futures: "合约",
  "stock perpetual": "股票合约",
  "spot margin": "现货杠杆",
  margin: "杠杆",
  convert: "闪兑",
  "pre-market": "盘前",
  options: "期权",
  alpha: "Alpha",
  airdrop: "空投活动"
};

function marketTypeParts(value?: string | null): string[] {
  if (!value) {
    return [];
  }
  return value
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean);
}

function announcementIsStock(row: ExchangeAnnouncement): boolean {
  if (row.asset_research?.some((item) => item.asset_type === "stock" || item.asset_type === "index")) {
    return true;
  }
  const context = `${row.title} ${row.category ?? ""} ${row.market_type ?? ""}`.toLowerCase();
  if (
    [
      "stock",
      "stocks",
      "equity",
      "equities",
      "bstock",
      "tradfi",
      "cfd",
      "share",
      "股票",
      "指数",
      "index",
      "etf"
    ].some((token) => context.includes(token))
  ) {
    return true;
  }
  return false;
}

function announcementMarketTypeParts(row: ExchangeAnnouncement): string[] {
  const parts = marketTypeParts(row.market_type);
  const isStock = announcementIsStock(row);
  return Array.from(
    new Set(
      parts.map((part) => {
        if (part === "spot" && isStock) {
          return "股票现货";
        }
        if ((part === "futures" || part === "stock perpetual") && isStock) {
          return "股票合约";
        }
        return part;
      })
    )
  );
}

function marketTypeTag(row: ExchangeAnnouncement) {
  const parts = announcementMarketTypeParts(row);
  if (parts.length === 0) {
    return <Typography.Text type="secondary">未识别</Typography.Text>;
  }
  return (
    <Space size={[0, 4]} wrap>
      {parts.map((part) => (
        <Tag key={part} color="geekblue">
          {marketTypeLabels[part] ?? part}
        </Tag>
      ))}
    </Space>
  );
}

function marketTypeText(row: ExchangeAnnouncement): string {
  const parts = announcementMarketTypeParts(row);
  if (parts.length === 0) {
    return "未识别";
  }
  return parts.map((part) => marketTypeLabels[part] ?? part).join(" / ");
}

function categoryLabel(row: ExchangeAnnouncement): string {
  const value = (row.category || "").toLowerCase();
  if (value.includes("baseline")) {
    return "当前市场基线";
  }
  if (value.includes("delist") || row.kind === "delisting") {
    return "下架公告";
  }
  if (value.includes("newfutures") || value.includes("futures")) {
    return "合约上币";
  }
  if (value.includes("newspot") || value.includes("spot")) {
    return "现货上币";
  }
  if (value.includes("convert")) {
    return "闪兑上币";
  }
  if (value.includes("new_crypto") || value.includes("new listings") || value.includes("new cryptocurrency")) {
    return "新币上架";
  }
  if (value.includes("coin_listings")) {
    return "上币公告";
  }
  if (row.kind === "listing") {
    return "上币公告";
  }
  return row.category || "未分类";
}

function eventSchedule(row: ExchangeAnnouncement) {
  return row.event_schedule ?? [];
}

function eventScheduleTimeRange(row: ExchangeAnnouncement): { first: string; last: string; count: number } | null {
  const schedule = eventSchedule(row);
  if (schedule.length === 0) {
    return null;
  }
  const sorted = [...schedule].sort((a, b) => dayjs.utc(a.event_time).valueOf() - dayjs.utc(b.event_time).valueOf());
  return {
    first: sorted[0].event_time,
    last: sorted[sorted.length - 1].event_time,
    count: sorted.length
  };
}

function eventTimeText(row: ExchangeAnnouncement): string {
  const range = eventScheduleTimeRange(row);
  if (range && range.count > 1) {
    const first = formatUtcPlus8(range.first);
    const last = formatUtcPlus8(range.last);
    return first === last ? `分批 ${first} UTC+8 (${range.count}项)` : `分批 ${first} - ${last} UTC+8 (${range.count}项)`;
  }
  if (range) {
    return `${formatUtcPlus8(range.first)} UTC+8`;
  }
  if (row.event_time) {
    return `${formatUtcPlus8(row.event_time)} UTC+8`;
  }
  if (row.kind === "listing") {
    return "公告未给出具体上币时间";
  }
  if (row.kind === "delisting") {
    return "公告未给出具体下币时间";
  }
  return "公告未给出具体时间";
}

function eventScheduleList(row: ExchangeAnnouncement) {
  const schedule = eventSchedule(row);
  if (schedule.length === 0) {
    return "-";
  }
  return (
    <div className="announcement-schedule-list">
      {schedule.map((item) => (
        <div key={`${item.symbol}-${item.event_time}`} className="announcement-schedule-item">
          <Tag color="purple">{item.symbol}</Tag>
          <Typography.Text>{formatUtcPlus8(item.event_time)} UTC+8</Typography.Text>
          {item.note ? <Typography.Text type="secondary">{item.note}</Typography.Text> : null}
        </div>
      ))}
    </div>
  );
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

const researchAssetTypeLabels: Record<string, string> = {
  crypto: "加密货币",
  stock: "股票",
  index: "指数",
  unknown: "未确认"
};

function researchStatusTag(status: string) {
  const labels: Record<string, string> = {
    found: "已找到",
    partial: "部分资料",
    not_found: "待核实"
  };
  const colors: Record<string, string> = {
    found: "green",
    partial: "orange",
    not_found: "default"
  };
  return <Tag color={colors[status] ?? "default"}>{labels[status] ?? status}</Tag>;
}

function researchTypeLabel(value: string): string {
  return researchAssetTypeLabels[value] ?? (value || "未确认");
}

function researchList(row: ExchangeAnnouncement): AnnouncementAssetResearch[] {
  return row.asset_research ?? [];
}

function researchSummary(row: ExchangeAnnouncement) {
  const items = researchList(row);
  if (items.length === 0) {
    return <Typography.Text type="secondary">未检索</Typography.Text>;
  }
  const found = items.filter((item) => item.status === "found").length;
  const pending = items.length - found;
  return (
    <Space size={[0, 4]} wrap>
      <Tag color={found > 0 ? "green" : "default"}>{found}/{items.length} 已找到</Tag>
      {pending > 0 ? <Tag color="orange">{pending} 待核实</Tag> : null}
    </Space>
  );
}

function researchDetails(row: ExchangeAnnouncement) {
  const items = researchList(row);
  if (items.length === 0) {
    return <Typography.Text type="secondary">暂无自动检索资料</Typography.Text>;
  }
  return (
    <div className="announcement-research-list">
      {items.map((item) => (
        <div key={`${item.symbol}-${item.canonical_symbol ?? ""}`} className="announcement-research-item">
          <div className="announcement-research-head">
            <Space size={6} wrap>
              <Tag color="purple">{item.symbol}</Tag>
              {item.name ? <Typography.Text strong>{item.name}</Typography.Text> : null}
              {item.canonical_symbol && item.canonical_symbol !== item.symbol ? (
                <Typography.Text type="secondary">标准标的：{item.canonical_symbol}</Typography.Text>
              ) : null}
            </Space>
            <Space size={4} wrap>
              <Tag>{researchTypeLabel(item.asset_type)}</Tag>
              {researchStatusTag(item.status)}
            </Space>
          </div>
          {item.summary ? <Typography.Paragraph>{item.summary}</Typography.Paragraph> : null}
          {item.business ? (
            <Typography.Paragraph>
              <Typography.Text strong>具体业务：</Typography.Text> {item.business}
            </Typography.Paragraph>
          ) : null}
          {item.status === "not_found" ? (
            <Typography.Text type="secondary">
              暂未检索到足够可靠的公开资料，建议人工核实。
            </Typography.Text>
          ) : null}
          {item.status === "partial" ? (
            <Typography.Text type="secondary">
              资料不完整或来自公开搜索摘要，建议人工核实。
            </Typography.Text>
          ) : null}
          {item.sources.length > 0 ? (
            <div className="announcement-research-sources">
              <Typography.Text type="secondary">来源：</Typography.Text>
              {item.sources.map((source) => (
                <a key={`${source.title}-${source.url}`} href={source.url} target="_blank" rel="noreferrer">
                  {source.title}
                </a>
              ))}
            </div>
          ) : null}
        </div>
      ))}
      <Typography.Text type="secondary">
        以上为公开资料自动整理，仅供参考，请自行核实，不构成投资建议。
      </Typography.Text>
    </div>
  );
}

function rowSummary(row: ExchangeAnnouncement): string {
  const pieces: string[] = [];
  if (row.symbols.length > 0) {
    pieces.push(`${announcementIsStock(row) ? "标的" : "币种"} ${row.symbols.slice(0, 8).join(", ")}`);
  }
  if (row.market_type) {
    pieces.push(`市场 ${marketTypeText(row)}`);
  }
  if (row.event_time) {
    const range = eventScheduleTimeRange(row);
    if (range && range.count > 1) {
      pieces.push(`分批 ${formatUtcPlus8(range.first)} - ${formatUtcPlus8(range.last)} UTC+8`);
    } else {
      pieces.push(`事件时间 ${formatUtcPlus8(row.event_time)} UTC+8`);
    }
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
  { title: "标的/币种", dataIndex: "symbols", width: 180, render: symbolTags },
  { title: "市场", dataIndex: "market_type", width: 132, render: (_value, row) => marketTypeTag(row) },
  { title: "公开资料", dataIndex: "asset_research", width: 142, render: (_value, row) => researchSummary(row) },
  { title: "上/下币时间(UTC+8)", dataIndex: "event_time", width: 168, render: (_value, row) => eventTimeText(row) },
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
  { title: "分类", dataIndex: "category", width: 128, ellipsis: true, render: (_value, row) => categoryLabel(row) },
  { title: "新公告告警", dataIndex: "alert_status", width: 106, render: alertStatusTag },
  { title: "到点提醒", dataIndex: "event_reminder_status", width: 112, render: reminderStatusTag }
];

function announcementDetails(row: ExchangeAnnouncement) {
  return (
    <div className="announcement-detail">
      <Descriptions size="small" column={1} bordered>
        <Descriptions.Item label="摘要">
          {rowSummary(row)}
        </Descriptions.Item>
        <Descriptions.Item label="完整标题">
          <a href={row.url} target="_blank" rel="noreferrer">
            {row.title}
          </a>
        </Descriptions.Item>
        <Descriptions.Item label={announcementIsStock(row) ? "标的" : "币种"}>
          {row.symbols.length > 0 ? row.symbols.join(", ") : "-"}
        </Descriptions.Item>
        <Descriptions.Item label="市场">{marketTypeText(row)}</Descriptions.Item>
        <Descriptions.Item label="类型">{kindTag(row.kind)}</Descriptions.Item>
        <Descriptions.Item label="公告时间">{formatUtcPlus8(row.published_at)} UTC+8</Descriptions.Item>
        <Descriptions.Item label="上/下币时间">{eventTimeText(row)}</Descriptions.Item>
        {eventSchedule(row).length > 0 ? (
          <Descriptions.Item label="逐项时间">{eventScheduleList(row)}</Descriptions.Item>
        ) : null}
        <Descriptions.Item label="抓取时间">{formatUtcPlus8(row.fetched_at)} UTC+8</Descriptions.Item>
        <Descriptions.Item label="交易所">{row.exchange.toUpperCase()}</Descriptions.Item>
        <Descriptions.Item label="分类">{categoryLabel(row)}</Descriptions.Item>
        <Descriptions.Item label="原始分类">{row.category || "-"}</Descriptions.Item>
        <Descriptions.Item label="来源">{row.source}</Descriptions.Item>
        <Descriptions.Item label="新公告告警">{alertStatusTag(row.alert_status)}</Descriptions.Item>
        <Descriptions.Item label="到点提醒">{reminderStatusTag(row.event_reminder_status)}</Descriptions.Item>
        <Descriptions.Item label="公开资料">{researchDetails(row)}</Descriptions.Item>
        <Descriptions.Item label="提醒发送时间">
          {row.event_reminder_sent_at ? `${formatUtcPlus8(row.event_reminder_sent_at)} UTC+8` : "-"}
        </Descriptions.Item>
        {row.summary ? (
          <Descriptions.Item label="结构化摘要">
            {row.summary}
          </Descriptions.Item>
        ) : null}
      </Descriptions>
    </div>
  );
}

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
  const listingDelistingAlertEnabled = settingsPreview.listing_delisting_alerts_enabled;

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
            <Tag color={listingDelistingAlertEnabled ? "green" : "default"}>
              上/下币告警 {listingDelistingAlertEnabled ? "开启" : "关闭"}
            </Tag>
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
          description="record_exchanges 控制哪些交易所会写入公告记录，alert_exchanges 控制哪些交易所的新公告和事件到点提醒会发飞书。上/下币公告默认也会同步飞书，可通过上面的开关单独关闭；只有能识别出明确上/下币时间的公告才会触发到点提醒。"
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
            <Form.Item label="上/下币公告飞书提醒" name="listing_delisting_alerts_enabled" valuePropName="checked">
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
            <Form.Item label="新公告通知窗口" name="alert_max_age_minutes" rules={[{ required: true }]}>
              <InputNumber min={1} max={10080} step={5} suffix="min" className="wide-input" />
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
        scroll={{ x: 1280 }}
        expandable={{
          expandedRowRender: announcementDetails,
          rowExpandable: () => true
        }}
      />
    </div>
  );
}
