import { Alert, Empty, Space, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import type {
  SqueezePaperReport, SqueezePaperStatus, SqueezePaperTrade
} from "../api/types";

dayjs.extend(utc);

type Props = {
  status: SqueezePaperStatus | null | undefined;
  positions: SqueezePaperTrade[];
  trades: SqueezePaperTrade[];
  report: SqueezePaperReport | null;
  error: string | null;
  loading: boolean;
};

function time(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function amount(value: string | null | undefined): string {
  if (value == null) return "-";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(2) : "-";
}

function market(value: string): string {
  return value.split("|").join(" / ");
}

const stateLabels: Record<string, { label: string; color: string }> = {
  pending_entry: { label: "等待开仓", color: "blue" },
  recovering: { label: "裸腿恢复", color: "orange" },
  open: { label: "模拟持仓", color: "cyan" },
  pending_exit: { label: "等待平仓", color: "blue" },
  unresolved: { label: "未决敞口", color: "red" },
  closed: { label: "已平仓", color: "green" },
  unfilled: { label: "未成交", color: "default" },
  blocked: { label: "已阻断", color: "default" }
};

const columns: ColumnsType<SqueezePaperTrade> = [
  { title: "事件 / 路线", key: "identity", width: 235, render: (_, row) => <>
    <Typography.Text strong>{row.asset_id}</Typography.Text><br />
    <Typography.Text type="secondary" copyable={{ text: row.event_id }}>{row.event_id.slice(0, 18)}</Typography.Text>
  </> },
  { title: "状态", dataIndex: "status", width: 115,
    render: (value: string) => <Tag color={stateLabels[value]?.color ?? "default"}>{stateLabels[value]?.label ?? value}</Tag> },
  { title: "贵腿卖 / 便宜腿买", key: "legs", width: 245, render: (_, row) => <>
    <Typography.Text>{market(row.expensive_key)}</Typography.Text><br />
    <Typography.Text>{market(row.cheap_key)}</Typography.Text>
  </> },
  { title: "目标 / 剩余数量", key: "quantity", width: 155,
    render: (_, row) => `${row.target_quantity} / ${row.expensive_open_quantity} : ${row.cheap_open_quantity}` },
  { title: "价格损益 / 资金费", key: "pnl", width: 155,
    render: (_, row) => `${amount(row.price_pnl)} / ${amount(row.funding_total)}` },
  { title: "开平手续费", key: "fees", width: 110,
    render: (_, row) => amount(String(Number(row.entry_fees) + Number(row.exit_fees))) },
  { title: "信号 / 开仓 / 平仓", key: "time", width: 165,
    render: (_, row) => `${time(row.signal_at)} / ${time(row.opened_at)} / ${time(row.closed_at)}` }
];

function details(trade: SqueezePaperTrade) {
  return <div className="squeeze-paper-details">
    <Typography.Paragraph type="secondary">
      事件 {trade.event_id}；退出原因 {trade.exit_reason ?? "-"}；风险 {trade.risk_labels.join("、") || "-"}；
      资金费缺口 {time(trade.funding_gap_at)}；最后错误 {trade.last_error ?? "-"}。
      两腿最低剩余模拟资金 {amount(trade.minimum_expensive_free_balance)} / {amount(trade.minimum_cheap_free_balance)} USDT。
    </Typography.Paragraph>
    <Typography.Text strong>模拟成交</Typography.Text>
    <Table size="small" rowKey="id" pagination={false} dataSource={trade.fills}
      scroll={{ x: 740 }} locale={{ emptyText: <Empty description="无成交" /> }}
      columns={[
        { title: "时间", dataIndex: "filled_at", render: time },
        { title: "阶段", dataIndex: "role" },
        { title: "市场", dataIndex: "leg_key", render: market },
        { title: "方向", dataIndex: "side" },
        { title: "数量", dataIndex: "quantity" },
        { title: "价格", dataIndex: "unit_price" },
        { title: "手续费", dataIndex: "fee" }
      ]} />
    <Typography.Text strong>现金流水</Typography.Text>
    <Table size="small" rowKey="id" pagination={false} dataSource={trade.cashflows}
      scroll={{ x: 750 }} locale={{ emptyText: <Empty description="无现金流水" /> }}
      columns={[
        { title: "时间", dataIndex: "occurred_at", render: time },
        { title: "市场", dataIndex: "leg_key", render: market },
        { title: "类型", dataIndex: "kind" },
        { title: "金额 USDT", dataIndex: "amount" },
        { title: "结算价格来源", dataIndex: "mark_kind" },
        { title: "数据来源", dataIndex: "source" }
      ]} />
  </div>;
}

export function SqueezePaperView({ status, positions, trades, report, error, loading }: Props) {
  const empty = status?.enabled ? "尚无路线确认事件" : "模拟观察未启用";
  const capacities = Object.entries(report?.capacity_by_target_notional ?? {}).map(([target, row]) => ({ target, ...row }));
  return <div className="squeeze-paper-view">
    {error && <Alert type="warning" showIcon message={`模拟账本读取失败：${error}`} style={{ marginBottom: 16 }} />}
    <Space size="large" wrap style={{ marginBottom: 16 }}>
      <Statistic title="模拟观察" value={status?.enabled ? "运行中" : "已关闭"} />
      <Statistic title="独立事件" value={report?.independent_events ?? 0} />
      <Statistic title="未决敞口" value={status?.unresolved_exposure_count ?? 0} />
      <Statistic title="最近处理 (北京时间)" value={time(status?.last_processed_at)} />
    </Space>
    {status?.last_error && <Alert type="warning" showIcon message={status.last_error} style={{ marginBottom: 16 }} />}
    {!!status?.unresolved_exposure_count && <Alert type="error" showIcon
      message={`${status.unresolved_exposure_count} 笔模拟敞口未决，不能视为已平仓`}
      style={{ marginBottom: 16 }} />}
    <Typography.Paragraph type="secondary">
      公开 REST 盘口的研究模拟；IOC 使用延迟后的新盘口与保守 taker 费。两所模拟账户资金隔离，维持保证金分层尚未建模。
    </Typography.Paragraph>
    <Typography.Title level={5}>前瞻报告</Typography.Title>
    <Alert type={report?.sample_status === "ready_for_review" ? "info" : "warning"} showIcon
      message={report?.sample_status === "ready_for_review" ? "样本已达到复核门槛" : "样本不足"}
      description={report ? `连续观察 ${report.elapsed_days} / ${report.minimum_days} 天，本窗口 ${report.independent_events} / ${report.minimum_independent_events} 个独立事件；覆盖中断 ${report.coverage_gap_count} 次${report.coverage_gap_open ? "，当前仍中断" : ""}。统计仅供研究复核。` : "尚未建立固定参数观察。"}
      style={{ marginBottom: 16 }} />
    {report && <>
      <Space size="large" wrap style={{ marginBottom: 16 }}>
        <Statistic title="已平仓净收益 USDT" value={amount(report.closed_net_pnl)} />
        <Statistic title="已平仓资金收益率" value={report.closed_trade_capital_return == null ? "-" : `${(Number(report.closed_trade_capital_return) * 100).toFixed(2)}%`} />
        <Statistic title="已平仓最大回撤 USDT" value={amount(report.max_drawdown_closed_trade_only)} />
        <Statistic title="单腿失败率" value={report.single_leg_failure_rate == null ? "-" : `${(report.single_leg_failure_rate * 100).toFixed(1)}%`} />
      </Space>
      <Typography.Paragraph type="secondary">
        已结清资金费的平仓 {report.closed_trades} 笔，待补资金费的平仓 {report.closed_with_funding_gap} 笔；每笔净收益 {amount(report.net_per_closed_trade)} USDT；平均持仓 {report.average_holding_seconds == null ? "-" : `${Math.round(report.average_holding_seconds / 60)} 分钟`}；
        最大不利价差扩大 {report.maximum_adverse_spread_expansion ?? "-"} USDT/基础币；
        资金费现金流（实际标记价 / 一分钟代理价）{amount(report.funding_cashflow_actual_mark)} / {amount(report.funding_cashflow_proxy_mark)} USDT；
        借币费用 {amount(report.borrow_cost)} USDT（当前双永续路线不借币）；资金费缺口 {report.funding_gap_trades} 笔。
        {report.drawdown_excludes_open_positions && " 回撤仅统计已平仓交易，不含未平仓估值。"}
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        每腿最低剩余模拟资金：{Object.entries(report.minimum_free_balance_by_leg).map(([leg, value]) => `${market(leg)} ${amount(value)} USDT`).join("；") || "-"}。
        维持保证金模型不完整，不代表清算风险通过验收。
      </Typography.Paragraph>
      <Table size="small" rowKey="target" pagination={false} dataSource={capacities}
        locale={{ emptyText: <Empty description="暂无容量样本" /> }} scroll={{ x: 480 }}
        columns={[
          { title: "目标单腿名义 USDT", dataIndex: "target" },
          { title: "事件", dataIndex: "events" },
          { title: "深度合格", dataIndex: "depth_qualified" },
          { title: "成本合格", dataIndex: "cost_qualified" }
        ]} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
        路线分组：{Object.entries(report.by_route).map(([route, row]) => `${route}: ${row.events} 事件、${row.closed} 平仓、${row.failed_or_unfilled} 失败或未成交、净收益 ${amount(row.closed_net)} USDT`).join("；") || "-"}。
      </Typography.Paragraph>
    </>}
    <Typography.Title level={5}>模拟持仓与未决</Typography.Title>
    <Table rowKey="id" columns={columns} dataSource={positions} loading={loading} size="small"
      expandable={{ expandedRowRender: details }} scroll={{ x: 1170 }} pagination={{ pageSize: 20 }}
      locale={{ emptyText: <Empty description={empty} /> }} />
    <Typography.Title level={5}>事件交易账本</Typography.Title>
    <Table rowKey="id" columns={columns} dataSource={trades} loading={loading} size="small"
      expandable={{ expandedRowRender: details }} scroll={{ x: 1170 }} pagination={{ pageSize: 20 }}
      locale={{ emptyText: <Empty description={empty} /> }} />
    <Typography.Title level={5}>模拟账户</Typography.Title>
    <Table rowKey="exchange" size="small" pagination={false} dataSource={status?.accounts ?? []}
      scroll={{ x: 650 }} locale={{ emptyText: <Empty description={empty} /> }}
      columns={[
        { title: "交易所", dataIndex: "exchange" },
        { title: "初始 USDT", dataIndex: "initial_balance", render: amount },
        { title: "现金 USDT", dataIndex: "cash_balance", render: amount },
        { title: "预留保证金 USDT", dataIndex: "reserved_margin", render: amount },
        { title: "资金费 USDT", dataIndex: "funding_cashflow", render: amount }
      ]} />
  </div>;
}
