import { Alert, Descriptions, InputNumber, Select, Spin, Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useEffect, useState } from "react";

import { ApiError, rangeBacktest } from "../api/client";
import type {
  RangeBacktestDirection,
  RangeBacktestRange,
  RangeBacktestRequest,
  RangeBacktestResult,
  RangeSettlement
} from "../api/rangeBacktest";
import type { PairSpreadLegQuery } from "../api/types";

type Props = {
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  range: RangeBacktestRange;
  defaultDirection?: RangeBacktestDirection;
};

const legNames = { leg1: "左腿", leg2: "右腿" };
const gapNames = { entry: "入场端点", exit: "退出端点", funding: "资金费结算" };
const sourceNames: Record<string, string> = {
  kline_close: "历史K线收盘价",
  realtime_mid: "实时盘口中间价",
  mid_price: "盘口中间价"
};
const money = (value: number) => `${value >= 0 ? "+" : ""}${value.toFixed(4)} 美元`;
const dateTime = (value: string) => dayjs(value).format("YYYY-MM-DD HH:mm:ss Z");
const marketName = (leg: Pick<RangeSettlement, "exchange" | "dex" | "symbol">) =>
  `${leg.exchange} · ${leg.dex ? `市场分区 ${leg.dex} · ` : ""}${leg.symbol}`;

function warningText(value: string): string {
  return value.replace(/leg1/g, "左腿").replace(/leg2/g, "右腿")
    .replace(/funding coverage|coverage/g, "资金费覆盖率")
    .replace(/spread_cost_est/g, "点差成本估算").replace(/total/g, "总收益")
    .replace(/entry/g, "入场").replace(/exit/g, "退出").replace(/null/g, "未知");
}

export function RangeBacktestPanel({ leg1, leg2, range, defaultDirection = "long1_short2" }: Props) {
  const [notional, setNotional] = useState<number | null>(1000);
  const [direction, setDirection] = useState(defaultDirection);
  const [state, setState] = useState<{
    key: string;
    result?: RangeBacktestResult;
    error?: string;
  } | null>(null);
  const validNotional = notional !== null && Number.isFinite(notional) && notional > 0 && notional <= 10_000_000;
  // Use values rather than object identity: refreshing the chart must not repeat a backtest.
  const requestKey = validNotional ? JSON.stringify({
    leg1, leg2, ...range, direction, notional_usd: notional
  } satisfies RangeBacktestRequest) : null;

  useEffect(() => {
    if (!requestKey) return;
    const controller = new AbortController();
    let active = true;
    setState({ key: requestKey });
    void rangeBacktest(JSON.parse(requestKey) as RangeBacktestRequest, controller.signal).then(
      (result) => { if (active) setState({ key: requestKey, result }); },
      (error: unknown) => {
        if (!active) return;
        const message = error instanceof ApiError && error.status === 422
          ? "区间或参数无法回测（422）：请确认起止时间有效、跨度不超过30天、每腿名义金额大于0且不超过1000万美元；缺少端点价格时请调整区间后重试。"
          : error instanceof Error && /[\u3400-\u9fff]/.test(error.message)
            ? `区间回测失败：${error.message}`
            : "区间回测失败，请检查网络或稍后重试。";
        setState({ key: requestKey, error: message });
      }
    );
    return () => { active = false; controller.abort(); };
  }, [requestKey]);

  const current = state?.key === requestKey ? state : null;
  const result = current?.result;
  const incompleteFunding = result && Object.values(result.funding_pnl.coverage).some(
    (coverage) => coverage !== null && coverage < 1
  );

  return (
    <section className="pair-chart-card pair-range-panel" aria-label="区间回测结果">
      <div className="pair-chart-head">
        <Typography.Title level={5}>区间回测结果</Typography.Title>
        <Tag color="orange">估算</Tag>
      </div>
      <div className="pair-query-options">
        <label htmlFor="range-backtest-notional">每腿名义金额（美元）</label>
        <InputNumber id="range-backtest-notional" value={notional} min={0} max={10_000_000} onChange={setNotional} />
        <label htmlFor="range-backtest-direction">方向</label>
        <Select
          id="range-backtest-direction"
          value={direction}
          onChange={setDirection}
          options={[
            { label: "做多左腿、做空右腿", value: "long1_short2" },
            { label: "做空左腿、做多右腿", value: "short1_long2" }
          ]}
        />
      </div>
      <Typography.Text>起始时间：{dateTime(range.start_at)}；结束时间：{dateTime(range.end_at)}</Typography.Text>
      {!validNotional ? <Alert type="warning" showIcon message="请输入大于0且不超过1000万美元的每腿名义金额。" /> : null}
      {requestKey && !result && !current?.error ? <div role="status"><Spin size="small" /> 正在计算区间估算结果…</div> : null}
      {current?.error ? <Alert type="error" showIcon message={current.error} /> : null}
      {result ? <>
        <Alert type="info" showIcon message="估算结果：每腿使用相同名义金额，历史价格盈亏与当前盘口点差成本估算合并核算。收益率以单腿名义金额为分母。" />
        <Descriptions size="small" bordered column={{ xs: 1, md: 2 }} items={[
          { key: "leg1", label: "左腿价格盈亏", children: money(result.price_pnl.leg1) },
          { key: "leg2", label: "右腿价格盈亏", children: money(result.price_pnl.leg2) },
          { key: "price", label: "价格盈亏合计", children: money(result.price_pnl.total) },
          { key: "funding", label: "资金费合计", children: money(result.funding_pnl.total) },
          { key: "fees", label: "手续费（扣除）", children: `${result.fees.toFixed(4)} 美元` },
          { key: "spread", label: "点差成本估算（扣除）", children: result.spread_cost_est === null ? "未知（总收益未扣除点差成本）" : `${result.spread_cost_est.toFixed(4)} 美元` },
          { key: "total", label: "总收益（估算）", children: <strong>{money(result.total)}</strong> },
          { key: "return", label: "收益率（单腿名义金额）", children: `${result.total_pct_of_notional.toFixed(4)}%` }
        ]} />
        <Typography.Text strong>数据质量（估算）</Typography.Text>
        {(["leg1", "leg2"] as const).map((name) => {
          const coverage = result.funding_pnl.coverage[name];
          const leg = name === "leg1" ? leg1 : leg2;
          return <div key={name}>
            <Typography.Text>{legNames[name]}：{marketName({ ...leg, dex: leg.dex ?? null })}；来源：{sourceNames[result.data_quality.source[name]] ?? "未知来源"}；粒度：{result.data_quality.granularity_seconds[name] ?? "未知"} 秒；资金费覆盖率：{coverage === null ? (leg.market_type === "spot" ? "不适用（现货）" : "未知（未补零）") : `${(coverage * 100).toFixed(2)}%`}</Typography.Text>
            <div>入场价 {result.entry[name].price}（实际时间 {dateTime(result.entry[name].timestamp)}，偏差 {result.entry[name].deviation_seconds} 秒）；退出价 {result.exit[name].price}（实际时间 {dateTime(result.exit[name].timestamp)}，偏差 {result.exit[name].deviation_seconds} 秒）</div>
          </div>;
        })}
        {incompleteFunding ? <Alert type="warning" showIcon message="资金费覆盖率不足，缺失记录未补零，估算结果可能不完整。" /> : null}
        {result.data_quality.gaps.length ? <Alert type="warning" showIcon message="数据缺口" description={
          <ul>{result.data_quality.gaps.map((gap, index) => <li key={index}>
            {legNames[gap.leg]}{gapNames[gap.kind]}：{gap.kind === "funding" ? `实际 ${gap.actual ?? "未知"} / 预计 ${gap.expected ?? "未知"} 次结算` : `端点偏差 ${gap.deviation_seconds ?? "未知"} 秒`}
          </li>)}</ul>
        } /> : <Typography.Text type="secondary">端点与资金费检查未报告缺口；区间内部价格缺口未核验。</Typography.Text>}
        {result.warnings.length ? <Alert type="warning" showIcon message="回测提示" description={
          <ul>{result.warnings.map((warning, index) => <li key={index}>{warningText(warning)}</li>)}</ul>
        } /> : null}
        <details>
          <summary>资金费逐笔明细（{result.funding_pnl.settlements.length} 笔）</summary>
          <Table<RangeSettlement>
            size="small"
            pagination={false}
            scroll={{ x: 660 }}
            locale={{ emptyText: "区间内没有可用结算记录，请结合资金费覆盖率判断是否缺失。" }}
            rowKey={(row) => `${row.leg}-${row.funding_time}`}
            dataSource={result.funding_pnl.settlements}
            columns={[
              { title: "结算时间", dataIndex: "funding_time", render: dateTime },
              { title: "腿", dataIndex: "leg", render: (value: RangeSettlement["leg"]) => legNames[value] },
              { title: "市场", render: (_, row) => marketName(row) },
              { title: "结算费率", dataIndex: "rate_pct", render: (value: number) => `${value}%` },
              { title: "资金费收支", dataIndex: "pnl", render: money }
            ]}
          />
        </details>
      </> : null}
    </section>
  );
}
