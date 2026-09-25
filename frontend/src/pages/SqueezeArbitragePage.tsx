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

function pct(value: number | null): string {
  return value == null ? "-" : `${(value * 100).toFixed(2)}%`;
}

function number(value: number | null): string {
  return value == null ? "-" : value.toFixed(2);
}

const stageLabels: Record<string, { label: string; color: string }> = {
  building: { label: "建仓观察", color: "blue" },
  squeeze_pending: { label: "逼空待确认", color: "orange" },
  tail_risk: { label: "尾部风险", color: "red" }
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
        <Statistic title="观察中" value={status?.active_watch_count ?? 0} />
        <Statistic title="最近完整小时 (北京时间)" value={time(status?.last_bucket_at)} />
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
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        已核验市场：{status?.verified_symbols.join("、") || "-"}。公开强平流仅为节流观测，缺失不能视为零；结构观察不代表可成交机会。
      </Typography.Paragraph>
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
          { title: "质量", dataIndex: "result_status", width: 125, render: (value: string) => <Tag color={value === "ready" ? "green" : "orange"}>{value}</Tag> },
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
