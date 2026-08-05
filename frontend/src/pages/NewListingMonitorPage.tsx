import {
  DeleteOutlined,
  EditOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SaveOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
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
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  collectNewListingWatchItem,
  deleteNewListingWatchItem,
  getNewListingMonitorStatus,
  listNewListingMonitorExchanges,
  queryNewListingHistory,
  upsertNewListingWatchItem
} from "../api/client";
import type {
  MarketType,
  NewListingAlertEvent,
  NewListingAlertLevel,
  NewListingHistoryResult,
  NewListingMonitorStatus,
  NewListingSpreadSample,
  NewListingWatchItem
} from "../api/types";

dayjs.extend(utc);

const exchangeNames: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "HL",
  okx: "OKX"
};

const routeColors = ["#0f766e", "#2563eb", "#f97316", "#7c3aed", "#b42318", "#0891b2"];

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now()}${Math.random().toString(16).slice(2)}`;
}

function nowIso(): string {
  return new Date().toISOString();
}

function emptyWatch(): NewListingWatchItem {
  const now = nowIso();
  return {
    id: newId(),
    enabled: true,
    symbol: "UNITREEUSDT",
    market_type: "future",
    exchanges: ["bybit", "gate", "bitget", "okx", "binance"],
    interval_seconds: 1,
    retention_hours: 72,
    normal_threshold_pct: 3,
    strong_threshold_pct: 8,
    extreme_threshold_pct: 15,
    min_executable_notional_usdt: 100,
    depth_validation_notional_usdt: 300,
    allow_low_liquidity_alert: true,
    normal_consecutive_hits: 2,
    strong_consecutive_hits: 1,
    extreme_consecutive_hits: 1,
    cooldown_seconds: 60,
    buy_fee_pct: 0.05,
    sell_fee_pct: 0.05,
    slippage_buffer_pct: 0.1,
    note: "",
    created_at: now,
    updated_at: now
  };
}

function normalizeSymbol(value: string): string {
  const normalized = value.trim().toUpperCase().replace(/[-_/]/g, "");
  return normalized.endsWith("USDT") ? normalized : `${normalized}USDT`;
}

function shortSymbol(symbol: string): string {
  return symbol.replace(/(?:USDT|USDC|USD)$/iu, "");
}

function exchangeText(exchange: string): string {
  return exchangeNames[exchange] ?? exchange;
}

function pct(value: number | null | undefined, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%` : "-";
}

function money(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "深度未知";
  }
  if (value >= 10_000) {
    return `${(value / 1000).toFixed(1)}k USDT`;
  }
  return `${value.toFixed(2)} USDT`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const abs = Math.abs(value);
  if (abs >= 100) {
    return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (abs >= 1) {
    return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  }
  return value.toPrecision(6);
}

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function levelTag(level: NewListingAlertLevel) {
  const color = level === "extreme" ? "red" : level === "strong" ? "orange" : level === "normal" ? "blue" : "default";
  const text = level === "extreme" ? "极端" : level === "strong" ? "强提醒" : level === "normal" ? "普通" : "未触发";
  return <Tag color={color}>{text}</Tag>;
}

function marketText(value: MarketType): string {
  return value === "spot" ? "现货" : "合约";
}

function riskText(label: string): string {
  const labels: Record<string, string> = {
    NEW_LISTING: "新币",
    DEPTH_UNKNOWN: "深度未知",
    DEPTH_TOO_SMALL: "深度不足",
    DEPTH_BELOW_TARGET: "低于验证金额",
    BUY_SLOW_DATA: "买入侧慢",
    SELL_SLOW_DATA: "卖出侧慢",
    LOW_LIQUIDITY_ALLOWED: "允许低流动性"
  };
  return labels[label] ?? label;
}

function groupKey(sample: NewListingSpreadSample): string {
  return `${sample.buy_exchange}->${sample.sell_exchange}`;
}

function SpreadHistoryChart({ samples }: { samples: NewListingSpreadSample[] }) {
  const activeSamples = samples.filter((sample) => Number.isFinite(sample.net_spread_pct));
  const width = 1120;
  const height = 300;
  const padding = { top: 20, right: 26, bottom: 36, left: 58 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;

  const series = useMemo(() => {
    const grouped = new Map<string, NewListingSpreadSample[]>();
    activeSamples.forEach((sample) => {
      const key = groupKey(sample);
      grouped.set(key, [...(grouped.get(key) ?? []), sample]);
    });
    return Array.from(grouped.entries())
      .map(([key, rows]) => ({
        key,
        rows: rows.sort((left, right) => dayjs.utc(left.observed_at).valueOf() - dayjs.utc(right.observed_at).valueOf()),
        peak: Math.max(...rows.map((row) => Math.abs(row.net_spread_pct)))
      }))
      .sort((left, right) => right.peak - left.peak)
      .slice(0, 6);
  }, [activeSamples]);

  if (!series.length) {
    return <div className="new-listing-chart-empty">暂无秒级价差记录</div>;
  }

  const allRows = series.flatMap((item) => item.rows);
  const startMs = Math.min(...allRows.map((sample) => dayjs.utc(sample.observed_at).valueOf()));
  const endMs = Math.max(...allRows.map((sample) => dayjs.utc(sample.observed_at).valueOf()));
  const values = allRows.map((sample) => sample.net_spread_pct);
  const minValue = Math.min(...values, 0);
  const maxValue = Math.max(...values, 0);
  const span = maxValue - minValue || Math.max(Math.abs(maxValue), 1);
  const min = minValue - span * 0.12;
  const max = maxValue + span * 0.12;
  const xAt = (value: string) =>
    padding.left +
    (startMs === endMs
      ? chartWidth / 2
      : ((dayjs.utc(value).valueOf() - startMs) / (endMs - startMs)) * chartWidth);
  const yAt = (value: number) => padding.top + ((max - value) / (max - min)) * chartHeight;
  const ticks = Array.from({ length: 6 }, (_, index) => {
    const ms = startMs + ((endMs - startMs) * index) / 5;
    return { ms, label: dayjs.utc(ms).utcOffset(8).format(endMs - startMs > 24 * 3600_000 ? "MM-DD HH:mm" : "HH:mm:ss") };
  });

  return (
    <div className="new-listing-chart-wrap">
      <svg className="new-listing-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="新币秒级净价差图">
        <rect className="new-listing-chart-bg" x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} rx="4" />
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const y = padding.top + chartHeight * ratio;
          const value = max - (max - min) * ratio;
          return (
            <g key={ratio}>
              <line className="new-listing-grid" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="new-listing-axis" x={padding.left - 10} y={y + 4} textAnchor="end">
                {pct(value)}
              </text>
            </g>
          );
        })}
        {ticks.map((tick, index) => {
          const x = padding.left + (startMs === endMs ? chartWidth / 2 : ((tick.ms - startMs) / (endMs - startMs)) * chartWidth);
          return (
            <g key={tick.ms}>
              <line className="new-listing-time-grid" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="new-listing-axis" x={x} y={height - 12} textAnchor={index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"}>
                {tick.label}
              </text>
            </g>
          );
        })}
        <line className="new-listing-zero" x1={padding.left} y1={yAt(0)} x2={padding.left + chartWidth} y2={yAt(0)} />
        {series.map((item, index) => {
          const color = routeColors[index % routeColors.length];
          const path = item.rows
            .map((sample, rowIndex) => `${rowIndex === 0 ? "M" : "L"} ${xAt(sample.observed_at).toFixed(2)} ${yAt(sample.net_spread_pct).toFixed(2)}`)
            .join(" ");
          return <path key={item.key} d={path} fill="none" stroke={color} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />;
        })}
        {allRows
          .filter((sample) => sample.alert_triggered)
          .map((sample) => (
            <circle key={`${sample.id ?? sample.observed_at}-${sample.buy_exchange}-${sample.sell_exchange}`} cx={xAt(sample.observed_at)} cy={yAt(sample.net_spread_pct)} r="4.5" className="new-listing-alert-dot" />
          ))}
      </svg>
      <div className="new-listing-chart-legend">
        {series.map((item, index) => (
          <Tag key={item.key} color={routeColors[index % routeColors.length]}>
            {item.key.split("->").map(exchangeText).join(" 买 / ")} 卖
          </Tag>
        ))}
      </div>
    </div>
  );
}

export function NewListingMonitorPage() {
  const [form] = Form.useForm<NewListingWatchItem>();
  const [status, setStatus] = useState<NewListingMonitorStatus | null>(null);
  const [exchangeOptions, setExchangeOptions] = useState<string[]>([]);
  const [draft, setDraft] = useState<NewListingWatchItem>(() => emptyWatch());
  const [selectedWatchId, setSelectedWatchId] = useState<string | undefined>();
  const [history, setHistory] = useState<NewListingHistoryResult | null>(null);
  const [range, setRange] = useState<[dayjs.Dayjs, dayjs.Dayjs] | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, exchanges] = await Promise.all([
        getNewListingMonitorStatus(),
        listNewListingMonitorExchanges()
      ]);
      setStatus(nextStatus);
      setExchangeOptions(exchanges);
      if (!selectedWatchId && nextStatus.watchlist.length > 0) {
        setSelectedWatchId(nextStatus.watchlist[0].id);
      }
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [selectedWatchId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    form.setFieldsValue(draft);
  }, [draft, form]);

  const selectedWatch = useMemo(
    () => status?.watchlist.find((item) => item.id === selectedWatchId),
    [selectedWatchId, status?.watchlist]
  );

  const latestSamples = useMemo(
    () =>
      (status?.latest_samples ?? [])
        .filter((sample) => !selectedWatchId || sample.watch_id === selectedWatchId)
        .slice(0, 80),
    [selectedWatchId, status?.latest_samples]
  );

  const saveWatch = async () => {
    setSaving(true);
    try {
      const values = await form.validateFields();
      const payload: NewListingWatchItem = {
        ...draft,
        ...values,
        symbol: normalizeSymbol(values.symbol),
        updated_at: nowIso()
      };
      const saved = await upsertNewListingWatchItem(payload);
      setDraft(saved);
      setSelectedWatchId(saved.id);
      message.success("新币极速监控已保存");
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const editWatch = (item: NewListingWatchItem) => {
    setDraft(item);
    setSelectedWatchId(item.id);
  };

  const createWatch = () => {
    const item = emptyWatch();
    setDraft(item);
    setSelectedWatchId(undefined);
    setHistory(null);
  };

  const toggleWatch = async (item: NewListingWatchItem, enabled: boolean) => {
    try {
      await upsertNewListingWatchItem({ ...item, enabled, updated_at: nowIso() });
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const collectNow = async (item: NewListingWatchItem) => {
    setLoading(true);
    try {
      await collectNewListingWatchItem(item.id);
      message.success("已完成一次即时采样");
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  };

  const deleteWatch = async (item: NewListingWatchItem) => {
    try {
      await deleteNewListingWatchItem(item.id);
      message.success("已删除监控标的");
      if (selectedWatchId === item.id) {
        createWatch();
      }
      await refresh();
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const loadHistory = async () => {
    const symbol = selectedWatch?.symbol ?? normalizeSymbol(form.getFieldValue("symbol") ?? "");
    setHistoryLoading(true);
    try {
      const result = await queryNewListingHistory({
        watch_id: selectedWatch?.id,
        symbol,
        hours: 6,
        start_at: range?.[0]?.toISOString(),
        end_at: range?.[1]?.toISOString(),
        limit: 8000
      });
      setHistory(result);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setHistoryLoading(false);
    }
  };

  const watchColumns: ColumnsType<NewListingWatchItem> = [
    {
      title: "标的",
      dataIndex: "symbol",
      width: 110,
      render: (_, item) => (
        <Space size={4} direction="vertical">
          <Typography.Text strong>{shortSymbol(item.symbol)}</Typography.Text>
          <Typography.Text type="secondary">{marketText(item.market_type)}</Typography.Text>
        </Space>
      )
    },
    {
      title: "状态",
      dataIndex: "enabled",
      width: 84,
      render: (_, item) => <Switch size="small" checked={item.enabled} onChange={(checked) => void toggleWatch(item, checked)} />
    },
    {
      title: "交易所",
      dataIndex: "exchanges",
      render: (exchanges: string[]) => (
        <Space size={[4, 4]} wrap>
          {exchanges.map((exchange) => (
            <Tag key={exchange}>{exchangeText(exchange)}</Tag>
          ))}
        </Space>
      )
    },
    {
      title: "阈值",
      width: 150,
      render: (_, item) => (
        <Typography.Text>
          {item.normal_threshold_pct}% / {item.strong_threshold_pct}% / {item.extreme_threshold_pct}%
        </Typography.Text>
      )
    },
    {
      title: "周期",
      width: 120,
      render: (_, item) => `${item.interval_seconds}s / ${item.retention_hours}h`
    },
    {
      title: "操作",
      width: 190,
      render: (_, item) => (
        <Space size={4}>
          <Button icon={<EditOutlined />} size="small" onClick={() => editWatch(item)} />
          <Button icon={<PlayCircleOutlined />} size="small" onClick={() => void collectNow(item)} />
          <Popconfirm title="删除这个新币监控？" onConfirm={() => void deleteWatch(item)}>
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      )
    }
  ];

  const sampleColumns: ColumnsType<NewListingSpreadSample> = [
    {
      title: "时间",
      dataIndex: "observed_at",
      width: 120,
      render: time
    },
    {
      title: "方向",
      width: 170,
      render: (_, item) => (
        <Typography.Text strong>
          {exchangeText(item.buy_exchange)} 买 / {exchangeText(item.sell_exchange)} 卖
        </Typography.Text>
      )
    },
    {
      title: "净价差",
      dataIndex: "net_spread_pct",
      width: 110,
      render: (value: number, item) => <Tag color={item.alert_triggered ? "red" : value >= 3 ? "orange" : "default"}>{pct(value, 3)}</Tag>
    },
    {
      title: "原始价差",
      dataIndex: "raw_spread_pct",
      width: 110,
      render: (value: number) => pct(value, 3)
    },
    {
      title: "可成交",
      dataIndex: "executable_notional_usdt",
      width: 130,
      render: money
    },
    {
      title: "价格",
      width: 190,
      render: (_, item) => `${price(item.buy_price)} -> ${price(item.sell_price)}`
    },
    {
      title: "提醒",
      dataIndex: "alert_level",
      width: 90,
      render: levelTag
    },
    {
      title: "风险",
      dataIndex: "risk_labels",
      render: (labels: string[]) => (
        <Space size={[4, 4]} wrap>
          {labels.map((label) => (
            <Tag key={label}>{riskText(label)}</Tag>
          ))}
        </Space>
      )
    },
    {
      title: "未提醒原因",
      dataIndex: "no_alert_reason",
      ellipsis: true,
      render: (value: string | null) => value ?? "-"
    }
  ];

  const eventColumns: ColumnsType<NewListingAlertEvent> = [
    { title: "时间", dataIndex: "created_at", width: 130, render: time },
    {
      title: "标的",
      dataIndex: "symbol",
      width: 100,
      render: (value: string) => shortSymbol(value)
    },
    {
      title: "方向",
      width: 160,
      render: (_, item) => `${exchangeText(item.buy_exchange)} 买 / ${exchangeText(item.sell_exchange)} 卖`
    },
    { title: "级别", dataIndex: "level", width: 90, render: levelTag },
    { title: "净价差", dataIndex: "net_spread_pct", width: 100, render: (value: number) => pct(value, 3) },
    { title: "可成交", dataIndex: "executable_notional_usdt", width: 130, render: money },
    { title: "消息", dataIndex: "message", ellipsis: true }
  ];

  return (
    <div className="page new-listing-page">
      <div className="toolbar">
        <div>
          <Typography.Title level={4}>新币极速监控</Typography.Title>
          <Typography.Text type="secondary">秒级记录跨所可成交价差，低流动性不隐藏，只作为风险提示。</Typography.Text>
        </div>
        <Space>
          <Button icon={<PlusOutlined />} onClick={createWatch}>新增标的</Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void refresh()}>刷新</Button>
        </Space>
      </div>

      {status?.latest_error ? <Alert type="warning" showIcon message={status.latest_error} /> : null}

      <div className="new-listing-metrics">
        <Statistic title="后台状态" value={status?.running ? "运行中" : "未运行"} />
        <Statistic title="启用标的" value={status?.enabled_watch_count ?? 0} suffix={`/ ${status?.watch_count ?? 0}`} />
        <Statistic title="样本数" value={status?.sample_count ?? 0} />
        <Statistic title="提醒事件" value={status?.event_count ?? 0} />
      </div>

      <div className="new-listing-layout">
        <Card size="small" title="监控参数" className="new-listing-config-card">
          <Form form={form} layout="vertical" initialValues={draft}>
            <div className="new-listing-form-grid">
              <Form.Item label="启用" name="enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="标的" name="symbol" rules={[{ required: true, message: "请输入标的" }]}>
                <Input placeholder="UNITREE" />
              </Form.Item>
              <Form.Item label="市场" name="market_type">
                <Select
                  options={[
                    { label: "合约", value: "future" },
                    { label: "现货", value: "spot" }
                  ]}
                />
              </Form.Item>
              <Form.Item label="交易所" name="exchanges" rules={[{ required: true, message: "请选择交易所" }]}>
                <Select
                  mode="multiple"
                  options={exchangeOptions.map((exchange) => ({ label: exchangeText(exchange), value: exchange }))}
                />
              </Form.Item>
              <Form.Item label="采样周期(秒)" name="interval_seconds">
                <InputNumber min={1} max={60} step={1} />
              </Form.Item>
              <Form.Item label="保留小时" name="retention_hours">
                <InputNumber min={1} max={720} step={1} />
              </Form.Item>
              <Form.Item label="普通阈值%" name="normal_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="强提醒阈值%" name="strong_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="极端阈值%" name="extreme_threshold_pct">
                <InputNumber min={0} step={0.1} />
              </Form.Item>
              <Form.Item label="最低可成交USDT" name="min_executable_notional_usdt">
                <InputNumber min={0} step={10} />
              </Form.Item>
              <Form.Item label="验证金额USDT" name="depth_validation_notional_usdt">
                <InputNumber min={0} step={10} />
              </Form.Item>
              <Form.Item label="允许低流动性" name="allow_low_liquidity_alert" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="普通连续次数" name="normal_consecutive_hits">
                <InputNumber min={1} max={20} step={1} />
              </Form.Item>
              <Form.Item label="强提醒次数" name="strong_consecutive_hits">
                <InputNumber min={1} max={20} step={1} />
              </Form.Item>
              <Form.Item label="极端次数" name="extreme_consecutive_hits">
                <InputNumber min={1} max={20} step={1} />
              </Form.Item>
              <Form.Item label="冷却秒数" name="cooldown_seconds">
                <InputNumber min={0} max={86400} step={5} />
              </Form.Item>
              <Form.Item label="买入手续费%" name="buy_fee_pct">
                <InputNumber min={0} max={10} step={0.01} />
              </Form.Item>
              <Form.Item label="卖出手续费%" name="sell_fee_pct">
                <InputNumber min={0} max={10} step={0.01} />
              </Form.Item>
              <Form.Item label="滑点缓冲%" name="slippage_buffer_pct">
                <InputNumber min={0} max={50} step={0.01} />
              </Form.Item>
            </div>
            <Form.Item label="备注" name="note">
              <Input.TextArea rows={2} placeholder="例如：新上市前 24 小时重点观察" />
            </Form.Item>
            <Space>
              <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void saveWatch()}>
                保存参数
              </Button>
              {selectedWatch ? (
                <Button icon={<PlayCircleOutlined />} onClick={() => void collectNow(selectedWatch)}>
                  立即采样
                </Button>
              ) : null}
            </Space>
          </Form>
        </Card>

        <Card size="small" title="已保存标的" className="new-listing-watch-card">
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            columns={watchColumns}
            dataSource={status?.watchlist ?? []}
            rowClassName={(item) => (item.id === selectedWatchId ? "new-listing-row-active" : "")}
            onRow={(item) => ({
              onClick: () => {
                setSelectedWatchId(item.id);
                setDraft(item);
              }
            })}
          />
        </Card>
      </div>

      <Card size="small" title="实时极速机会">
        <Table rowKey={(row) => `${row.id ?? row.observed_at}-${row.buy_exchange}-${row.sell_exchange}`} size="small" columns={sampleColumns} dataSource={latestSamples} loading={loading} pagination={{ pageSize: 12 }} />
      </Card>

      <Card
        size="small"
        title="历史复盘"
        extra={
          <Space wrap>
            <DatePicker.RangePicker
              showTime
              value={range}
              onChange={(value) => setRange(value as [dayjs.Dayjs, dayjs.Dayjs] | null)}
            />
            <Button icon={<ReloadOutlined />} loading={historyLoading} onClick={() => void loadHistory()}>
              查询
            </Button>
          </Space>
        }
      >
        {history?.warnings.map((warning) => (
          <Alert key={warning} type="warning" showIcon message={warning} className="new-listing-history-alert" />
        ))}
        {history ? (
          <>
            <div className="new-listing-history-summary">
              <Statistic title="样本" value={history.sample_count} />
              <Statistic title="事件" value={history.event_count} />
              <Statistic title="最大净价差" value={history.max_net_spread_pct ?? 0} precision={3} suffix="%" />
              <Statistic title="最大原始价差" value={history.max_raw_spread_pct ?? 0} precision={3} suffix="%" />
            </div>
            <SpreadHistoryChart samples={history.samples} />
            <Table
              rowKey="id"
              size="small"
              columns={eventColumns}
              dataSource={history.events}
              pagination={{ pageSize: 6 }}
              locale={{ emptyText: <Empty description="该时间段没有提醒事件" /> }}
            />
          </>
        ) : (
          <Empty description="选择一个标的后查询历史复盘" />
        )}
      </Card>
    </div>
  );
}
