import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useState } from "react";

import {
  getSqueezeEvents, getSqueezeRouteEvents, getSqueezeRoutes,
  getSqueezeStatus, getSqueezeWatchlist,
  getSqueezePaperPositions, getSqueezePaperTrades, getSqueezePaperReport
} from "../api/client";
import type {
  SqueezeRouteEvent, SqueezeRouteRow, SqueezeStatus, SqueezeWatchEvent,
  SqueezePaperTrade, SqueezePaperReport
} from "../api/types";
import { SqueezeRoutesView } from "./SqueezeRoutesView";
import { SqueezePaperView } from "./SqueezePaperView";
import "./SqueezeArbitragePage.css";

dayjs.extend(utc);

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm") : "-";
}

function pct(value: number | null | undefined): string {
  return value == null ? "-" : `${(value * 100).toFixed(2)}%`;
}

function number(value: number | null | undefined): string {
  return value == null ? "-" : value.toFixed(2);
}

const stageLabels: Record<string, { label: string; color: string }> = {
  building: { label: "建仓观察", color: "blue" },
  squeeze_pending: { label: "逼空待确认", color: "orange" },
  tail_risk: { label: "尾部风险", color: "red" }
};

const screeningReasons: Record<string, string> = {
  hourly_candle_gap_or_unavailable: "K 线不足或缺口",
  positioning_endpoint_missing: "OI 样本缺失",
  positioning_endpoint_stale: "OI 样本过期",
  open_interest_window_gap: "OI 样本有缺口",
  account_ratio_missing: "账户比缺失",
  account_ratio_stale: "账户比过期",
  four_hour_move_outside_early_range: "4h 涨幅不在早期区间",
  day_move_outside_early_range: "24h 涨幅不在早期区间",
  volume_not_accelerating: "量能未放大",
  raw_oi_not_growing: "原始 OI 未增长",
  short_crowding_not_evident: "账户比未见空头拥挤"
};

const columns: ColumnsType<SqueezeWatchEvent> = [
  {
    title: "原始市场",
    dataIndex: "market_key",
    key: "market_key",
    width: 190,
    render: (value: string) => <Typography.Text strong>{value.split("|").join(" / ")}</Typography.Text>
  },
  {
    title: "阶段",
    dataIndex: "stage",
    key: "stage",
    width: 125,
    render: (value: string) => <Tag color={stageLabels[value]?.color ?? "default"}>{stageLabels[value]?.label ?? value}</Tag>
  },
  {
    title: "4h / 24h 涨幅",
    key: "return",
    width: 150,
    render: (_, row) => `${pct(row.features.return_4h)} / ${pct(row.features.return_24h)}`
  },
  {
    title: "成交额倍数",
    key: "volume",
    width: 110,
    render: (_, row) => `${number(row.features.volume_ratio)}x`
  },
  {
    title: "账户多空比",
    key: "ratio",
    width: 110,
    render: (_, row) => number(row.features.account_ratio)
  },
  {
    title: "原始 OI 峰值增长 / 回撤",
    key: "oi",
    width: 185,
    render: (_, row) => `${pct(row.features.oi_peak_growth)} / ${pct(row.features.oi_drawdown)}`
  },
  {
    title: "数据小时 / 到期",
    key: "time",
    width: 145,
    render: (_, row) => `${time(row.last_bucket_at)} / ${time(row.expires_at)}`
  }
];

export function SqueezeArbitragePage() {
  const [status, setStatus] = useState<SqueezeStatus | null>(null);
  const [watchlist, setWatchlist] = useState<SqueezeWatchEvent[]>([]);
  const [events, setEvents] = useState<SqueezeWatchEvent[]>([]);
  const [routes, setRoutes] = useState<SqueezeRouteRow[]>([]);
  const [routeEvents, setRouteEvents] = useState<SqueezeRouteEvent[]>([]);
  const [paperPositions, setPaperPositions] = useState<SqueezePaperTrade[]>([]);
  const [paperTrades, setPaperTrades] = useState<SqueezePaperTrade[]>([]);
  const [paperReport, setPaperReport] = useState<SqueezePaperReport | null>(null);
  const [paperError, setPaperError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const [nextStatus, nextWatchlist, nextEvents, nextRoutes, nextRouteEvents] = await Promise.all([
        getSqueezeStatus(), getSqueezeWatchlist(), getSqueezeEvents(),
        getSqueezeRoutes(), getSqueezeRouteEvents()
      ]);
      setStatus(nextStatus);
      setWatchlist(nextWatchlist);
      setEvents(nextEvents);
      setRoutes(nextRoutes);
      setRouteEvents(nextRouteEvents);
      setError(null);
      try {
        const [nextPositions, nextTrades, nextReport] = await Promise.all([
          getSqueezePaperPositions(), getSqueezePaperTrades(), getSqueezePaperReport()
        ]);
        setPaperPositions(nextPositions);
        setPaperTrades(nextTrades);
        setPaperReport(nextReport);
        setPaperError(null);
      } catch (cause) {
        setPaperError(cause instanceof Error ? cause.message : "读取失败");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(false), 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return (
    <div className="squeeze-arbitrage-page">
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>挤仓结构观察</Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => void refresh()} loading={loading} title="刷新数据" aria-label="刷新数据" />
      </Space>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Space size="large" wrap style={{ marginBottom: 16 }}>
        <Statistic title="公开数据采集" value={status?.enabled ? "运行中" : "已关闭"} />
        <Statistic title="当前候选" value={status?.discovery?.selected.length ?? 0} />
        <Statistic title="观察中" value={status?.active_watch_count ?? 0} />
        <Statistic title="候选采样小时 (北京时间)" value={time(status?.discovery?.bucket_at)} />
        <Statistic title="强平公开流" value={status?.liquidation_coverage.state === "throttled_public_stream" ? "节流观测" : "未连接"} />
        <Statistic title="路线采集" value={status?.routes?.enabled ? "运行中" : "已关闭"} />
      </Space>
      {status?.last_error && <Alert type="warning" showIcon message={status.last_error} style={{ marginBottom: 16 }} />}
      {status?.routes?.last_error && <Alert type="warning" showIcon message={status.routes.last_error} style={{ marginBottom: 16 }} />}
      {!!status?.routes?.queue_depth && <Alert type="info" showIcon
        message={`路线写入积压 ${status.routes.queue_depth} 条；最新采集 ${time(status.routes.last_collected_at)}`}
        style={{ marginBottom: 16 }} />}
      {!!status?.routes?.dropped_scan_count && <Alert type="warning" showIcon
        message={`已丢弃 ${status.routes.dropped_scan_count} 条路线采样，连续确认已重置；最近 ${time(status.routes.last_drop_at)}`}
        style={{ marginBottom: 16 }} />}
      {!!status?.routes?.storage_failure_count && <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        路线存储忙锁 {status.routes.storage_failure_count} 次，最近 {time(status.routes.last_storage_error_at)}。
      </Typography.Paragraph>}
      {status?.discovery?.stale && <Alert type="warning" showIcon
        message="候选筛选未完成或结果已过期，当前没有有效观察市场"
        style={{ marginBottom: 16 }} />}
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        {status?.discovery?.mode === "fixed" ? "人工指定市场" : "自动初筛"}：
        {time(status?.discovery?.selected_at)}；24 小时行情初筛 {status?.discovery?.eligible_count ?? 0} 个，
        深入核验 {status?.discovery?.screened_count ?? 0} 个。
        公开强平流仅为节流观测，缺失不能视为零；结构观察不代表可成交机会。
      </Typography.Paragraph>
      <Typography.Title level={5}>当前异动候选</Typography.Title>
      <Table
        rowKey="raw_symbol"
        size="small"
        pagination={false}
        dataSource={status?.discovery?.selected ?? []}
        scroll={{ x: 870 }}
        columns={[
          { title: "原始市场", dataIndex: "market_key", width: 205,
            render: (value: string | undefined, row) => value || `binance|future|${row.raw_symbol}|` },
          { title: "类型", dataIndex: "selection_kind", width: 110,
            render: (value: string) => value === "structure_event" ? <Tag color="orange">结构触发</Tag>
              : value === "manual_configured" ? <Tag>人工指定</Tag> : <Tag color="blue">早期异动</Tag> },
          { title: "4h / 24h", key: "returns", width: 135,
            render: (_, row) => `${pct(row.return_4h)} / ${pct(row.return_24h)}` },
          { title: "量能", dataIndex: "volume_ratio", width: 90,
            render: (value: number | null | undefined) => value == null ? "-" : `${number(value)}x` },
          { title: "原始 OI 24h", dataIndex: "oi_current_growth", width: 115,
            render: (value: number | null | undefined) => pct(value) },
          { title: "账户多空比", dataIndex: "account_ratio", width: 105,
            render: (value: number | null | undefined) => number(value) },
          { title: "24h 成交额", dataIndex: "quote_volume_24h", width: 105,
            render: (value: number | undefined) => value == null ? "-" : `${(value / 1_000_000).toFixed(1)}M` }
        ]}
        locale={{ emptyText: <Empty description={status?.discovery?.mode === "not_started"
          ? "候选初筛尚未运行" : "当前无符合早期量价与仓位条件的市场"} /> }}
        style={{ marginBottom: 16 }}
      />
      {status?.discovery?.selected.length === 0 && !!status.discovery.screened.length && <>
        <Typography.Title level={5}>本轮初筛（未入选）</Typography.Title>
        <Table
          rowKey="raw_symbol"
          size="small"
          pagination={false}
          dataSource={status.discovery.screened}
          scroll={{ x: 760 }}
          columns={[
            { title: "原始市场", dataIndex: "market_key", width: 205 },
            { title: "24h 涨幅", dataIndex: "ticker_change_24h", width: 105,
              render: (value: number | undefined) => pct(value) },
            { title: "4h 涨幅", dataIndex: "return_4h", width: 105,
              render: (value: number | null | undefined) => pct(value) },
            { title: "量能", dataIndex: "volume_ratio", width: 85,
              render: (value: number | null | undefined) => value == null ? "-" : `${number(value)}x` },
            { title: "原始 OI 24h", dataIndex: "oi_current_growth", width: 110,
              render: (value: number | null | undefined) => pct(value) },
            { title: "未入选原因", dataIndex: "reasons",
              render: (value: string[] | undefined) =>
                (value ?? []).map((reason) => screeningReasons[reason] ?? reason).join("、") || "-" }
          ]}
          style={{ marginBottom: 16 }}
        />
      </>}
      <Typography.Title level={5}>数据覆盖</Typography.Title>
      <Table
        rowKey="market_key"
        size="small"
        pagination={false}
        dataSource={status?.latest_market_scans ?? []}
        scroll={{ x: 700 }}
        columns={[
          { title: "原始市场", dataIndex: "market_key", width: 190 },
          { title: "小时", dataIndex: "bucket_at", width: 100, render: (value: string) => time(value) },
          { title: "K线 / OI样本", key: "counts", width: 115, render: (_, row) => `${row.candle_count} / ${row.positioning_count}` },
          { title: "数据质量", dataIndex: "result_status", width: 125, render: (value: string) => <Tag color={value === "ready" ? "green" : "orange"}>{value === "ready" ? "完整" : value}</Tag> },
          { title: "缺口", dataIndex: "error", render: (value: string | null) => value || "-" }
        ]}
        locale={{ emptyText: <Empty description="暂无采集记录" /> }}
        style={{ marginBottom: 16 }}
      />
      <Tabs items={[
        {
          key: "watchlist", label: `观察池 (${watchlist.length})`,
          children: <Table rowKey="id" columns={columns} dataSource={watchlist} loading={loading} size="small" scroll={{ x: 1080 }} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无结构观察事件" /> }} />
        },
        {
          key: "events", label: "事件记录",
          children: <Table rowKey="id" columns={columns} dataSource={events} loading={loading} size="small" scroll={{ x: 1080 }} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description="暂无事件记录" /> }} />
        },
        {
          key: "routes", label: "路线研究",
          children: <SqueezeRoutesView routes={routes} events={routeEvents} loading={loading} />
        },
        {
          key: "paper", label: "模拟账本",
          children: <SqueezePaperView status={status?.paper} positions={paperPositions}
            trades={paperTrades} report={paperReport} error={paperError} loading={loading} />
        }
      ]} />
    </div>
  );
}
