import { Empty, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";

import type {
  SqueezeRouteCapacity, SqueezeRouteEvent, SqueezeRouteRow
} from "../api/types";

dayjs.extend(utc);

interface CapacityRow {
  key: string;
  route: SqueezeRouteRow;
  capacity: SqueezeRouteCapacity;
}

const phaseLabels: Record<string, string> = {
  watching: "观察", blocked: "阻断", dislocation: "异常价差",
  confirming: "收敛确认中", confirmed: "研究候选"
};

const reasonLabels: Record<string, string> = {
  normal_reference_missing: "正常价差基线不足",
  no_capacity_has_valid_depth: "各档盘口深度不足",
  source_skew: "两腿源时间差过大",
  duplicate_or_regressed_sequence: "盘口序列重复或回退",
  sampling_gap: "采样间隔中断",
  funding_unknown: "资金费或结算周期未知",
  net_space_below_minimum: "预计净空间不足",
  recent_trade_missing: "最近 60 秒无真实成交",
  recent_trade_zero: "最近成交额为零",
  turnover_unknown: "24 小时成交额未知",
  borrow_unknown: "借币能力与成本未知",
  source_stale: "交易所源时间陈旧",
  received_stale: "本地接收时间陈旧",
  source_time_missing: "交易所源时间缺失",
  sequence_missing: "盘口序列缺失",
  book_crossed: "盘口交叉",
  book_unsorted: "盘口顺序异常",
  book_invalid_level: "盘口数量或价格异常",
  book_empty: "盘口为空",
  metadata_stale: "市场规则核验超时",
  market_identity_mismatch: "原始市场身份不符",
  depth_insufficient: "目标数量深度不足",
  impact_excess: "盘口冲击超过上限",
  minimum_quantity: "低于最小数量",
  minimum_notional: "低于最小名义额",
  quantity_step_mismatch: "数量不符合当前步长"
};

function reasons(items: string[]): string {
  if (!items.length) return "-";
  return items.map((item) => {
    const side = item.startsWith("expensive_") ? "贵腿 " :
      item.startsWith("cheap_") ? "便宜腿 " : "";
    const key = side ? item.replace(/^(expensive|cheap)_/, "") : item;
    const phase = key.startsWith("open_") ? "开仓 " :
      key.startsWith("close_") ? "平仓 " : "";
    const reason = phase ? key.replace(/^(open|close)_/, "") : key;
    return side + phase + (reasonLabels[reason] ?? item);
  }).join("；");
}

function price(value: string | null | undefined): string {
  return value == null ? "-" : Number(value).toFixed(6);
}

function money(value: string | null | undefined): string {
  return value == null ? "-" : Number(value).toFixed(2);
}

function age(evaluatedAt: string, sourceAt: string | null): string {
  if (!sourceAt) return "-";
  return Math.max(0, (new Date(evaluatedAt).getTime() -
    new Date(sourceAt).getTime()) / 1000).toFixed(1) + "s";
}

function signedRate(
  value: string | null, interval: number | null, next: string | null, kind: string
): string {
  if (value == null || interval == null) return "-";
  const source = kind === "bybit_current_public_estimate" ? "公开预估" :
    kind === "binance_last_public_rate_proxy" ? "上期代理" : "来源未知";
  return (Number(value) * 100).toFixed(4) + "% " + source + " / " + interval + "h / " +
    (next ? dayjs.utc(next).utcOffset(8).format("MM-DD HH:mm") : "-");
}

const columns: ColumnsType<CapacityRow> = [
  {
    title: "精确路线", key: "route", width: 290,
    render: (_, row) => (
      <>
        <Typography.Text strong>空 {row.route.evaluation.expensive_key}</Typography.Text><br />
        <Typography.Text>多 {row.route.evaluation.cheap_key}</Typography.Text><br />
        <Typography.Text type="secondary">{row.route.evaluation.asset_id} / {row.route.evaluation.quote_asset}</Typography.Text>
      </>
    )
  },
  {
    title: "状态", key: "status", width: 125,
    render: (_, row) => (
      <>
        <Tag color={row.route.state?.phase === "confirmed" ? "green" : "default"}>
          {phaseLabels[row.route.state?.phase ?? ""] ?? row.route.state?.phase ?? "-"}
        </Tag><br />
        <Tag color={row.route.evaluation.quality === "blocked" ? "red" : "orange"}>
          {row.route.evaluation.quality === "blocked" ? "阻断" : "仅研究"}
        </Tag>
      </>
    )
  },
  {
    title: "目标 / 同一基础币数量", key: "quantity", width: 145,
    render: (_, row) => (
      <>
        <div>{money(row.capacity.target_notional)} USDT / {row.capacity.base_quantity}</div>
        <Typography.Text type="secondary">
          合约单位 {row.route.evaluation.expensive_contract_base_qty} / {row.route.evaluation.cheap_contract_base_qty}
        </Typography.Text>
      </>
    )
  },
  {
    title: "四向 VWAP", key: "vwap", width: 190,
    render: (_, row) => (
      <>
        <div>贵卖 {price(row.capacity.expensive_open_sell?.unit_price)}</div>
        <div>贵买 {price(row.capacity.expensive_close_buy?.unit_price)}</div>
        <div>便宜买 {price(row.capacity.cheap_open_buy?.unit_price)}</div>
        <div>便宜卖 {price(row.capacity.cheap_close_sell?.unit_price)}</div>
      </>
    )
  },
  {
    title: "开仓差 / 退出目标", key: "difference", width: 145,
    render: (_, row) => price(row.capacity.open_difference) + " / " +
      price(row.capacity.target_residual)
  },
  {
    title: "估算成本与净空间 (USDT)", key: "cost", width: 210,
    render: (_, row) => (
      <>
        <div>进/出费 {money(row.capacity.entry_fee)} / {money(row.capacity.estimated_exit_fee)}</div>
        <div>资金费 {money(row.capacity.estimated_funding)} · 缓冲 {money(row.capacity.latency_buffer)}</div>
        <div>借币 {money(row.capacity.estimated_borrow)}</div>
        <Typography.Text strong>净空间 {money(row.capacity.estimated_net)}</Typography.Text>
      </>
    )
  },
  {
    title: "资金费率 / 周期 / 下次结算", key: "funding", width: 230,
    render: (_, row) => (
      <>
        <div>空 {signedRate(row.route.evaluation.expensive_funding_rate,
          row.route.evaluation.expensive_funding_interval_hours,
          row.route.evaluation.expensive_next_funding_at,
          row.route.evaluation.expensive_funding_kind)}</div>
        <div>多 {signedRate(row.route.evaluation.cheap_funding_rate,
          row.route.evaluation.cheap_funding_interval_hours,
          row.route.evaluation.cheap_next_funding_at,
          row.route.evaluation.cheap_funding_kind)}</div>
      </>
    )
  },
  {
    title: "源时间年龄 / 原因", key: "quality", width: 300,
    render: (_, row) => (
      <>
        <div>贵/便宜 {age(row.route.evaluated_at,
          row.route.evaluation.expensive_source_at)} /
          {age(row.route.evaluated_at, row.route.evaluation.cheap_source_at)}</div>
        <Typography.Text type="secondary">
          {reasons([...row.route.evaluation.blockers, ...row.capacity.blockers])}
        </Typography.Text>
      </>
    )
  },
  {
    title: "双方成交额 / 费率假设", key: "turnover", width: 220,
    render: (_, row) => (
      <>
        <div>24h 贵/便宜 {money(row.route.evaluation.expensive_turnover_24h)} /
          {money(row.route.evaluation.cheap_turnover_24h)} USDT</div>
        <div>最新成交 {money(row.route.evaluation.expensive_recent_trade_notional)} /
          {money(row.route.evaluation.cheap_recent_trade_notional)} USDT</div>
        <div>taker {(Number(row.route.evaluation.expensive_taker_fee_rate) * 100).toFixed(3)}% /
          {(Number(row.route.evaluation.cheap_taker_fee_rate) * 100).toFixed(3)}%</div>
        <Typography.Text type="secondary">保守公开假设，非账户档位</Typography.Text>
      </>
    )
  }
];

export function SqueezeRoutesView({
  routes, events, loading
}: {
  routes: SqueezeRouteRow[];
  events: SqueezeRouteEvent[];
  loading: boolean;
}) {
  const data = routes.flatMap((route) => route.evaluation.capacities.map((capacity) => ({
    key: route.route_id + ":" + capacity.target_notional,
    route, capacity
  })));
  return (
    <>
      <Table
        rowKey="key" columns={columns} dataSource={data} loading={loading}
        size="small" scroll={{ x: 1880 }} pagination={false}
        locale={{ emptyText: <Empty description="暂无路线采样" /> }}
      />
      <Typography.Title level={5} style={{ marginTop: 20 }}>路线事件</Typography.Title>
      <Table
        rowKey="id" size="small" loading={loading} dataSource={events}
        pagination={{ pageSize: 10 }} scroll={{ x: 750 }}
        columns={[
          { title: "时间", dataIndex: "occurred_at", width: 145,
            render: (value: string) => dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") },
          { title: "路线", dataIndex: "route_id", width: 310 },
          { title: "阶段", dataIndex: "phase", width: 135,
            render: (value: string) => phaseLabels[value] ?? value },
          { title: "质量", key: "quality",
            render: (_, row) => row.evaluation.quality === "blocked" ? "阻断" : "仅研究" }
        ]}
        locale={{ emptyText: <Empty description="暂无路线事件" /> }}
      />
    </>
  );
}
