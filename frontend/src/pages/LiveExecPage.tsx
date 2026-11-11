import { Alert, Button, Card, Col, Descriptions, Empty, Row, Space, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import type { LiveAccount, LiveGroup, LivePeriod, LivePosition, LiveSummary, LiveTrade } from "../api/liveExec";
import { isPageHidden } from "../utils/pageVisibility";
import { ProfitHistogram } from "./liveExec/ProfitHistogram";
import { useLiveExec } from "./liveExec/useLiveExec";

const reasons: Record<string, string> = {
  converged: "收敛止盈", stop_loss: "止损", max_hold: "超时", pre_funding: "结算前平仓",
  stale: "行情中断", one_leg: "单腿处理", halt: "熔断",
};
const states = { opening: "开仓中", open: "持仓中", closing: "平仓中", one_leg: "单腿处理中" };
const failureLabels: Record<string, string> = {
  not_configured: "未配置", timeout: "请求超时", unreachable: "上游不可达", invalid_json: "数据格式异常",
  invalid_configuration: "配置无效", request_failed: "请求失败",
};
function failureLabel(reason: string): string {
  const status = /^upstream_http_(\d{3})$/.exec(reason);
  return failureLabels[reason] ?? (status ? `上游响应异常 ${status[1]}` : "上游服务异常");
}
const number = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
const percentage = (value: number | null | undefined) => value == null ? "—" : `${number(value * 100, 1)}%`;
const feeRate = (value: number | null | undefined) => value == null ? "—" : `${number(value * 100, 4)}%`;
const price = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN", { maximumSignificantDigits: 10 });
const side = (value: string | null) => value === "long" ? "多" : value === "short" ? "空" : "—";
const date = (value: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
function duration(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 3600)}时 ${Math.floor(value % 3600 / 60)}分 ${value % 60}秒`;
}
function countdown(value: string | null, now: number): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  const seconds = (Date.parse(value) - now) / 1000;
  return seconds <= 0 ? "已到期" : duration(seconds);
}
function Profit({ usd, bps }: { usd: number | null; bps?: number | null }) {
  return <Typography.Text type={usd == null ? undefined : usd < 0 ? "danger" : "success"}>
    {number(usd)} USD{bps !== undefined && ` / ${number(bps)} bp`}
  </Typography.Text>;
}

function Period({ title, data }: { title: string; data: LivePeriod | null }) {
  return <Card size="small" title={title}>
    <Descriptions size="small" column={2}>
      <Descriptions.Item label={title === "今日" ? "今日净利润" : "累计净利润"}><Profit usd={data?.net_usd ?? null} /></Descriptions.Item>
      <Descriptions.Item label="手续费">{number(data?.fees_usd)} USD</Descriptions.Item>
      <Descriptions.Item label="资金费">{number(data?.funding_usd)} USD</Descriptions.Item>
      <Descriptions.Item label="笔数">{number(data?.trades, 0)}</Descriptions.Item>
      <Descriptions.Item label="胜率">{percentage(data?.trades ? (data.wins == null ? null : data.wins / data.trades) : null)}</Descriptions.Item>
      <Descriptions.Item label="亏损上限">{number(data?.daily_loss_limit_usd ?? data?.total_loss_limit_usd)} USD</Descriptions.Item>
    </Descriptions>
  </Card>;
}
function Account({ name, data, dropMax }: { name: string; data: LiveAccount | null; dropMax: number | null }) {
  const danger = data?.drop_usd != null && dropMax != null && dropMax > 0 && data.drop_usd >= dropMax * 0.8;
  return <Card size="small" title={`${name} 账户`} data-testid={`live-account-${name}`}>
    {!data ? <Empty description="暂无账户数据" /> : <>
      <Descriptions size="small" column={2}>
        <Descriptions.Item label="权益">{number(data.equity_usd)} USD</Descriptions.Item>
        <Descriptions.Item label="基线">{number(data.baseline_usd)} USD</Descriptions.Item>
        <Descriptions.Item label="较基线跌幅"><Typography.Text type={danger ? "danger" : undefined}>{number(data.drop_usd)}</Typography.Text> / {number(dropMax)} USD</Descriptions.Item>
        <Descriptions.Item label="保证金倍数">{data.margin_multiple == null ? "无保证金占用" : `${number(data.margin_multiple)} 倍`}</Descriptions.Item>
      </Descriptions>
      {data.ok === false && <Tag color="red">账户异常</Tag>}
      {data.error && <Typography.Text type="danger">{data.error}</Typography.Text>}
    </>}
  </Card>;
}
function Summary({ data }: { data: LiveSummary | null }) {
  return <Card title="执行汇总">
    {!data ? <Empty description="暂无汇总数据" /> : <Space direction="vertical" style={{ width: "100%" }}>
      <Space wrap>
        <Tag color={data.mode === "live" ? "volcano" : "blue"}>{data.mode === "live" ? "实盘模式" : data.mode === "shadow" ? "影子模式" : "模式未知"}</Tag>
        <Tag color={data.halted ? "red" : undefined}>{data.halted ? "熔断" : data.halted === false ? "未熔断" : "熔断状态未知"}{data.halt_reason ? `：${data.halt_reason}` : ""}</Tag>
        <Tag color={data.stop_file || data.opening_paused_reason ? "orange" : undefined}>
          {data.stop_file || data.opening_paused_reason ? `暂停开仓${data.opening_paused_reason ? `：${data.opening_paused_reason}` : "（停止文件）"}` : data.stop_file === false ? "开仓未暂停" : "开仓状态未知"}
        </Tag>
        <Typography.Text type="secondary">数据时间：{date(data.generated_at)}</Typography.Text>
      </Space>
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}><Period title="今日" data={data.today} /></Col>
        <Col xs={24} xl={12}><Period title="累计" data={data.total} /></Col>
      </Row>
      <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
        <Descriptions.Item label="已开名义额">{number(data.caps?.total_open_usd)} / {number(data.caps?.total_max_usd)} USD</Descriptions.Item>
        <Descriptions.Item label="持仓对数">{number(data.caps?.open_pairs, 0)} / {number(data.caps?.max_pairs, 0)}</Descriptions.Item>
        <Descriptions.Item label="单腿名义额上限">{number(data.caps?.leg_max_usd)} USD</Descriptions.Item>
        <Descriptions.Item label="通道吃单费率">{feeRate(data.fees?.crossex_taker)} / {feeRate(data.fees?.bitget_taker)}（crossex / bitget）</Descriptions.Item>
      </Descriptions>
      <Row gutter={[12, 12]}>
        <Col xs={24} xl={12}><Account name="crossex" data={data.accounts?.crossex ?? null} dropMax={data.caps?.equity_drop_max_usd ?? null} /></Col>
        <Col xs={24} xl={12}><Account name="bitget" data={data.accounts?.bitget ?? null} dropMax={data.caps?.equity_drop_max_usd ?? null} /></Col>
      </Row>
      <Typography.Text type={data.last_error ? "danger" : "secondary"}>最后一个错误：{data.last_error ?? "无"}</Typography.Text>
    </Space>}
  </Card>;
}

const tradeColumns: ColumnsType<LiveTrade> = [
  { title: "币种 / 交易对", key: "symbol", render: (_, row) => <><div>{row.symbol ?? "—"}</div><small>{row.pair ?? "—"}</small></> },
  { title: "开仓 / 平仓时间", key: "time", render: (_, row) => <><div>{date(row.opened_at)}</div><div>{date(row.closed_at)}</div></> },
  { title: "持有时长", dataIndex: "held_seconds", render: duration },
  { title: "两腿均价（开 → 平）", key: "legs", render: (_, row) => row.legs?.map((leg, index) => <div key={index}>{leg.exchange ?? "—"} · {side(leg.side)} · {price(leg.qty)}：{price(leg.entry_vwap)} → {price(leg.exit_vwap)}</div>) ?? "—" },
  { title: "开仓价差（bp）", dataIndex: "entry_spread_bps", render: (v: number | null) => number(v) },
  { title: "手续费（USD）", dataIndex: "fees_usd", render: (v: number | null) => number(v) },
  { title: "资金费（USD）", dataIndex: "funding_usd", render: (v: number | null) => number(v) },
  { title: "净利润", key: "net", render: (_, row) => <Profit usd={row.net_usd} bps={row.net_bps} /> },
  { title: "平仓原因", dataIndex: "exit_reason", render: (value: string | null) => value ? reasons[value] ?? "未知原因" : "—" },
  { title: "模拟净收益（bp）", key: "sim", render: (_, row) => number(row.sim?.net_bps) },
  { title: "滑点（bp）", dataIndex: "slippage_bps", render: (v: number | null) => number(v) },
  { title: "两腿发送 / 成交时间差（毫秒）", key: "gap", render: (_, row) => `${number(row.leg_send_gap_ms, 0)} / ${number(row.leg_fill_gap_ms, 0)}` },
  { title: "单腿事件", dataIndex: "one_leg_incident", render: (value: boolean | null) => value == null ? "—" : <Tag color={value ? "red" : undefined}>{value ? "有" : "无"}</Tag> },
];

function GroupTable({ title, rows, exitReason = false }: { title: string; rows: LiveGroup[] | null | undefined; exitReason?: boolean }) {
  const columns: ColumnsType<LiveGroup> = [
    { title: "分组", dataIndex: "key", render: (key: string | null) => key ? exitReason ? reasons[key] ?? "未知原因" : key : "—" },
    { title: "笔数", dataIndex: "n", render: (v: number | null) => number(v, 0) },
    { title: "净利润（USD）", dataIndex: "net_usd", render: (v: number | null) => number(v) },
    { title: "平均净收益（bp）", dataIndex: "mean_bps", render: (v: number | null) => number(v) },
    { title: "胜率", dataIndex: "win_rate", render: percentage },
  ];
  return <Card size="small" title={title}><Table size="small" rowKey={row => row.key ?? "unknown"} columns={columns}
    dataSource={rows ?? []} pagination={false} scroll={{ x: 500 }} locale={{ emptyText: "暂无分组统计" }} /></Card>;
}

export function LiveExecPage() {
  const { summary, positions, trades, stats, error, loadingMore, canLoadMore, loadMore } = useLiveExec();
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => { if (!isPageHidden()) setNow(Date.now()); }, 1000);
    return () => window.clearInterval(timer);
  }, []);
  const positionColumns: ColumnsType<LivePosition> = [
    { title: "币种", dataIndex: "symbol", render: (value: string | null) => value ?? "—" },
    { title: "两腿（交易所 / 通道 / 方向 / 数量 / 开仓价 / 标记价）", key: "legs", render: (_, row) => row.legs?.map((leg, index) => <div key={index}>{leg.exchange ?? "—"} / {leg.route ?? "—"} / {side(leg.side)} / {price(leg.qty)} / {price(leg.entry_price)} / {price(leg.mark_price)}</div>) ?? "—" },
    { title: "开仓 / 当前价差（bp）", key: "spread", render: (_, row) => `${number(row.entry_spread_bps)} / ${number(row.current_spread_bps)}` },
    { title: "浮动盈亏", key: "pnl", render: (_, row) => <Profit usd={row.unrealized_usd} bps={row.unrealized_bps} /> },
    { title: "已持有时长", key: "held", render: (_, row) => duration(row.opened_at && Number.isFinite(Date.parse(row.opened_at)) ? (now - Date.parse(row.opened_at)) / 1000 : row.held_seconds) },
    { title: "距资金费结算", key: "funding", render: (_, row) => countdown(row.next_funding_at, now) },
    { title: "距止损（bp）", dataIndex: "stop_distance_bps", render: (v: number | null) => number(v) },
    { title: "距最长持仓到期", key: "expiry", render: (_, row) => countdown(row.max_hold_at, now) },
    { title: "状态", dataIndex: "status", render: (value: LivePosition["status"]) => value ? <Tag color={value === "one_leg" ? "red" : "blue"}>{states[value] ?? "未知状态"}</Tag> : "—" },
  ];
  const vs = stats?.vs_sim;
  return <Space direction="vertical" size="middle" style={{ width: "100%", padding: 16 }}>
    <Typography.Title level={3}>实盘</Typography.Title>
    {error && <Alert type="warning" showIcon message={`实盘服务未连接（${failureLabel(error)}）`} />}
    <Summary data={summary} />
    <Card title="当前持仓"><Table rowKey="id" size="small" columns={positionColumns} dataSource={positions}
      pagination={false} scroll={{ x: 1900 }} locale={{ emptyText: "暂无当前持仓" }} /></Card>
    <Card title="已平仓记录">
      <Table rowKey="id" size="small" columns={tradeColumns} dataSource={trades}
        pagination={false} scroll={{ x: 2500 }} locale={{ emptyText: "暂无已平仓记录" }} />
      <Button style={{ marginTop: 12 }} disabled={!canLoadMore} loading={loadingMore} onClick={loadMore}>加载更多</Button>
    </Card>
    <Card title={`利润分布（近 ${stats?.days ?? "—"} 天，共 ${stats?.n ?? "—"} 笔）`}>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={10}><Typography.Text>净收益（bp） / 笔数</Typography.Text><ProfitHistogram bins={stats?.histogram} /></Col>
        <Col xs={24} xl={14}><Row gutter={[12, 12]}>
          <Col xs={24} lg={12}><GroupTable title="按币种" rows={stats?.by_symbol} /></Col>
          <Col xs={24} lg={12}><GroupTable title="按交易对" rows={stats?.by_pair} /></Col>
          <Col xs={24} lg={12}><GroupTable title="按平仓原因" rows={stats?.by_exit_reason} exitReason /></Col>
          <Col xs={24} lg={12}><GroupTable title="按开仓价差" rows={stats?.by_spread_tier} /></Col>
        </Row></Col>
      </Row>
    </Card>
    <Card title="实盘与模拟对比">{!vs ? <Empty description="暂无模拟对比数据" /> : <Descriptions column={{ xs: 1, md: 2, xl: 3 }}>
      <Descriptions.Item label="对比笔数">{number(vs.n, 0)}</Descriptions.Item>
      <Descriptions.Item label="滑点中位数">{number(vs.slippage_bps_p50)} bp</Descriptions.Item>
      <Descriptions.Item label="滑点第 90 百分位">{number(vs.slippage_bps_p90)} bp</Descriptions.Item>
      <Descriptions.Item label="两腿成交间隔中位数">{number(vs.leg_fill_gap_ms_p50, 0)} 毫秒</Descriptions.Item>
      <Descriptions.Item label="两腿成交间隔第 90 百分位">{number(vs.leg_fill_gap_ms_p90, 0)} 毫秒</Descriptions.Item>
      <Descriptions.Item label="单腿事件率">{percentage(vs.one_leg_rate)}</Descriptions.Item>
    </Descriptions>}</Card>
  </Space>;
}
