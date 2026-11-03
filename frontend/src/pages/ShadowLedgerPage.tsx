import { Alert, Button, Card, Checkbox, DatePicker, Select, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import { useEffect, useState } from "react";

import { getShadowBuckets, getShadowStatus, getShadowTrades } from "../api/client";
import type { ShadowBucket, ShadowDimension, ShadowStatus, ShadowTrade } from "../api/shadow";

const labels: Record<ShadowDimension, string> = {
  variant: "策略变体", pair: "交易所对", spread_tier: "价差档位", liquidity: "流动性档",
  routes: "通道组合", fake_flag: "假机会", executable: "可执行", strategy_version: "策略版本"
};
const states = { INSUFFICIENT: "样本不足", POSITIVE: "期望值为正", NOT_POSITIVE: "未证实为正" };
const reasons: Record<string, string> = {
  stale: "行情过期", stop: "止损", converged: "收敛", timeout: "24 小时退出", timeout_hard: "48 小时退出"
};
const fakeReasons: Record<string, string> = {
  median_outlier: "中位价异常", frozen_leg: "价格冻结", spread_plateau: "价差平台", volume_unknown: "成交额未知", extreme_spread: "极端价差"
};
const number = (value: number | null | undefined) => value == null ? "—" : value.toFixed(2);
const percent = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

export function ShadowLedgerPage() {
  const [dims, setDims] = useState<ShadowDimension[]>(["variant", "pair"]);
  const [includeFake, setIncludeFake] = useState(false);
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [tradeStatus, setTradeStatus] = useState<"open" | "closed" | undefined>();
  const [status, setStatus] = useState<ShadowStatus | null>(null);
  const [buckets, setBuckets] = useState<ShadowBucket[]>([]);
  const [trades, setTrades] = useState<ShadowTrade[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    Promise.all([
      getShadowStatus(),
      getShadowBuckets({ dims: dims.join(","), include_fake: includeFake,
        since: range?.[0]?.toISOString(), until: range?.[1]?.toISOString() }),
      getShadowTrades(tradeStatus)
    ]).then(([nextStatus, nextBuckets, nextTrades]) => {
      if (active) { setStatus(nextStatus); setBuckets(nextBuckets); setTrades(nextTrades); }
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "加载影子账本失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [dims, includeFake, range, tradeStatus, refresh]);

  const columns: ColumnsType<ShadowBucket> = [
    { title: "分桶", key: "dimensions", render: (_, row) => Object.entries(row.dimensions).map(([key, value]) => `${labels[key as ShadowDimension]}：${typeof value === "boolean" ? value ? "是" : "否" : value}`).join("；") || "全部" },
    { title: "状态", dataIndex: "status", render: (value: ShadowBucket["status"]) => <Tag color={value === "POSITIVE" ? "green" : "default"}>{states[value]}</Tag> },
    { title: "事件 / 天 / 币", key: "counts", render: (_, row) => `${row.n_evt} / ${row.days} / ${row.coins}` },
    { title: "胜率（95% 区间）", key: "win", render: (_, row) => `${percent(row.win_rate)}（${percent(row.wilson_low)}～${percent(row.wilson_high)}）` },
    ...([ ["E", "期望 E"], ["lcb", "保守下界 lcb"], ["sigma", "标准差 σ"], ["spread_bps", "价差收益"],
      ["slippage_bps", "滑点成本"], ["fees_bps", "手续费成本"], ["funding_bps", "资金费"], ["cvar5", "最差 5% 均值"] ] as const)
      .map(([key, title]) => ({ title: `${title} (bp)`, dataIndex: key, render: number })),
    { title: "最小事件数", dataIndex: "n_min", render: (value: number | null) => value ?? "—" }
  ];
  const tradeColumns: ColumnsType<ShadowTrade> = [
    { title: "币 / 变体", key: "symbol", render: (_, row) => `${row.symbol} / ${row.variant}` },
    { title: "原始市场（买 → 卖）", key: "markets", render: (_, row) => `${row.buy_market_key.exchange}:${row.buy_market_key.raw_symbol} → ${row.sell_market_key.exchange}:${row.sell_market_key.raw_symbol}` },
    { title: "开仓时间", dataIndex: "opened_at", render: (v: string) => new Date(v).toLocaleString("zh-CN") },
    { title: "平仓 / 状态", key: "exit", render: (_, row) => row.closed_at ? `${new Date(row.closed_at).toLocaleString("zh-CN")} / ${reasons[row.exit_reason ?? ""] ?? row.exit_reason}` : "未平仓" },
    { title: "开仓 / 平仓 (%)", key: "spread", render: (_, row) => `${number(row.open_spread_pct)} / ${number(row.close_spread_pct)}` },
    { title: "净收益 (bp)", dataIndex: "net_bps", render: number },
    { title: "通道 / 可执行", key: "routes", render: (_, row) => `${row.routes} / ${row.executable ? "是" : "否"}` },
    { title: "假机会评分", key: "fake", render: (_, row) => `${row.fake_flag ? "假机会" : "未判假"} (${row.fake_score ?? 0}) ${row.fake_reasons.map(r => fakeReasons[r] ?? r).join("、")}` }
  ];
  return <Space direction="vertical" size="middle" style={{ width: "100%", padding: 16 }}>
    <Typography.Title level={3}>影子账本</Typography.Title>
    <Alert type="info" showIcon message="模拟观察，不代表实际成交" description="默认排除假机会；仅统计已平仓事件。资金费与滑点为估计，流动性暂按两腿较小的 24h 成交额分档。日期分组采用北京时间。" />
    {error && <Alert type="error" showIcon message={error} />}
    <Space wrap>
      <Tag>{status?.enabled ? "记录已开启" : "记录已关闭"}</Tag>
      <span>未平：{status?.open_count ?? "—"}；上限丢弃：{status?.dropped ?? "—"}；异常：{status?.errors ?? "—"}</span>
      <span>目录更新：{status?.catalog_updated_at ? new Date(status.catalog_updated_at).toLocaleString("zh-CN") : "尚未成功更新"}</span>
      <Button loading={loading} onClick={() => setRefresh(v => v + 1)}>刷新</Button>
    </Space>
    <Card title="分桶统计">
      <Space wrap style={{ marginBottom: 16 }}>
        <Select aria-label="统计维度" mode="multiple" style={{ minWidth: 280 }} value={dims} onChange={setDims}
          options={Object.entries(labels).map(([value, label]) => ({ value, label }))} />
        <DatePicker.RangePicker showTime value={range} onChange={setRange} />
        <Checkbox checked={includeFake} onChange={event => setIncludeFake(event.target.checked)}>包含假机会</Checkbox>
      </Space>
      <Table rowKey={row => JSON.stringify(row.dimensions)} columns={columns} dataSource={buckets} loading={loading}
        scroll={{ x: 2100 }} size="small" locale={{ emptyText: "暂无已平仓统计" }} />
    </Card>
    <Card title="最近影子单" extra={<Select aria-label="影子单状态" allowClear placeholder="全部状态" style={{ width: 130 }}
      value={tradeStatus} onChange={setTradeStatus} options={[{ value: "open", label: "未平仓" }, { value: "closed", label: "已平仓" }]} />}>
      <Table rowKey="id" columns={tradeColumns} dataSource={trades} loading={loading} scroll={{ x: 1800 }} size="small" locale={{ emptyText: "暂无影子单" }} />
    </Card>
  </Space>;
}
