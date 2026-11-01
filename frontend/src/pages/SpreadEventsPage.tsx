import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Input, InputNumber, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useEffect, useState } from "react";

import { getSpreadEvents, spreadEventMultiplierUnknown, spreadEventPairUrl } from "../api/spreadEvents";
import type { SpreadEvent, SpreadEventLeg, SpreadEventsParams, SpreadEventsResponse } from "../api/spreadEvents";

dayjs.extend(utc);

const reasons = { below: "回落", gap: "行情中断", ongoing: "进行中" };
const reasonColors = { below: "default", gap: "orange", ongoing: "green" };

function legLabel(leg: SpreadEventLeg): string {
  return `${leg.exchange} · ${leg.market_type === "spot" ? "现货" : "永续"}${leg.raw_symbol ? ` · ${leg.raw_symbol}` : ""}${leg.dex ? ` (${leg.dex})` : ""}`;
}

function duration(value: number): string {
  if (value < 60) return `${value.toFixed(0)} 秒`;
  if (value < 3600) return `${(value / 60).toFixed(1)} 分钟`;
  return `${(value / 3600).toFixed(1)} 小时`;
}

const columns: ColumnsType<SpreadEvent> = [
  { title: "币种", dataIndex: "symbol", width: 140, render: (symbol, event) => {
    const url = spreadEventPairUrl(event);
    return url ? <a href={`${url.pathname}${url.search}${url.hash}`}>{symbol}</a> : symbol;
  } },
  { title: "两腿（买 → 卖）", width: 360, render: (_, event) => (
    <div><div>{legLabel(event.buy_leg)}</div><div>→ {legLabel(event.sell_leg)}</div>
      {spreadEventMultiplierUnknown(event) && <Tag color="orange">倍率未知</Tag>}
      {!spreadEventPairUrl(event) && <Typography.Text type="secondary">历史样本缺少原始市场身份，无法精确跳转</Typography.Text>}
    </div>
  ) },
  { title: "开始时间 (UTC+8)", dataIndex: "start_at", width: 175,
    render: (value: string) => dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") },
  { title: "持续", dataIndex: "duration_s", width: 115, render: duration },
  { title: "峰值", dataIndex: "peak", width: 110, render: (value: number) => `${value.toFixed(3)}%` },
  { title: "均值", dataIndex: "mean", width: 110, render: (value: number) => `${value.toFixed(3)}%` },
  { title: "结束原因", dataIndex: "end_reason", width: 115,
    render: (reason: SpreadEvent["end_reason"]) => <Tag color={reasonColors[reason]}>{reasons[reason]}</Tag> }
];

type Filters = Pick<SpreadEventsParams, "threshold" | "metric" | "min_duration_s" | "sort" | "symbol"> & { hours: number };

export function SpreadEventsPage() {
  const [filters, setFilters] = useState<Filters>({
    hours: 24, threshold: 0.3, metric: "fee_adjusted", min_duration_s: 0, sort: "peak", symbol: ""
  });
  const [refresh, setRefresh] = useState(0);
  const [result, setResult] = useState<SpreadEventsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (document.visibilityState === "hidden") return;
    const controller = new AbortController();
    const end = new Date();
    const { hours, ...params } = filters;
    setLoading(true);
    setError("");
    setResult(null);
    void getSpreadEvents({
      ...params, start_at: new Date(end.getTime() - hours * 3600_000).toISOString(),
      end_at: end.toISOString()
    }, controller.signal).then((data) => {
      if (!controller.signal.aborted) setResult(data);
    }).catch((exc: unknown) => {
      if (!controller.signal.aborted) setError(exc instanceof Error ? exc.message : String(exc));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [filters, refresh]);

  const navigate = (event: SpreadEvent) => {
    const url = spreadEventPairUrl(event);
    if (!url) return;
    window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
    window.dispatchEvent(new Event("taoli1:navigate"));
  };

  return (
    <div className="page">
      <div className="toolbar">
        <Typography.Title level={4}>价差事件榜</Typography.Title>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => setRefresh((value) => value + 1)}>刷新</Button>
      </div>
      <Space wrap style={{ marginBottom: 16 }}>
        <label>时间范围 <Select aria-label="时间范围" value={filters.hours} style={{ width: 100 }}
          options={[1, 6, 24, 168].map((hours) => ({ value: hours, label: hours === 168 ? "7d" : `${hours}h` }))}
          onChange={(hours) => setFilters((value) => ({ ...value, hours }))} /></label>
        <label>阈值 (%) <InputNumber aria-label="阈值 (%)" min={0} step={0.1} value={filters.threshold}
          onChange={(threshold) => { if (threshold !== null) setFilters((value) => ({ ...value, threshold })); }} /></label>
        <label>指标 <Select aria-label="指标" value={filters.metric} style={{ width: 135 }}
          options={[{ value: "fee_adjusted", label: "扣费后价差" }, { value: "raw", label: "原始价差" }]}
          onChange={(metric) => setFilters((value) => ({ ...value, metric }))} /></label>
        <label>最短持续 (秒) <InputNumber aria-label="最短持续 (秒)" min={0} value={filters.min_duration_s}
          onChange={(min_duration_s) => { if (min_duration_s !== null) setFilters((value) => ({ ...value, min_duration_s })); }} /></label>
        <label>排序 <Select aria-label="排序" value={filters.sort} style={{ width: 100 }}
          options={[{ value: "peak", label: "峰值" }, { value: "duration", label: "持续" }, { value: "start", label: "开始时间" }]}
          onChange={(sort) => setFilters((value) => ({ ...value, sort }))} /></label>
        <Input.Search aria-label="币种" placeholder="币种，如 BTCUSDT" allowClear style={{ width: 230 }}
          onSearch={(symbol) => setFilters((value) => ({ ...value, symbol: symbol.trim() }))} />
      </Space>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      {result?.truncated && <Alert type="warning" showIcon message="历史样本超过读取上限，结果已截断；请缩小时间范围或指定币种。" style={{ marginBottom: 12 }} />}
      <Typography.Paragraph type="secondary">
        仅在打开页面、修改筛选或手动刷新时查询。历史采样有筛选，事件仅代表已记录样本；持续时间截至最后可确认的样本。
        {result && ` 扫描 ${result.scanned_rows} 条样本，${result.series_count} 个序列，显示 ${result.events.length} 个事件。`}
      </Typography.Paragraph>
      <Table<SpreadEvent> columns={columns} dataSource={result?.events ?? []}
        rowKey={(event) => `${event.series_key}:${event.start_at}`} loading={loading}
        size="middle" scroll={{ x: 1120 }} pagination={{ pageSize: 25, showSizeChanger: false }}
        onRow={(event) => ({
          onClick: (click) => {
            if (click.target instanceof Element && click.target.closest("a")) {
              if (click.ctrlKey || click.metaKey || click.shiftKey || click.altKey) return;
              click.preventDefault();
            }
            navigate(event);
          },
          style: { cursor: spreadEventPairUrl(event) ? "pointer" : "default" }
        })} />
    </div>
  );
}
