import { ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Space, Statistic, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useState } from "react";

import { getSqueezeEvents, getSqueezeStatus, getSqueezeWatchlist } from "../api/client";
import type { SqueezeStatus, SqueezeWatchEvent } from "../api/types";
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextStatus, nextWatchlist, nextEvents] = await Promise.all([
        getSqueezeStatus(), getSqueezeWatchlist(), getSqueezeEvents()
      ]);
      setStatus(nextStatus);
      setWatchlist(nextWatchlist);
      setEvents(nextEvents);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
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
      </Space>
      {status?.last_error && <Alert type="warning" showIcon message={status.last_error} style={{ marginBottom: 16 }} />}
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
        }
      ]} />
    </div>
  );
}
