import { useEffect, useRef, useState } from "react";
import { Alert, Button, Collapse, Descriptions, Form, Grid, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { getFsReport, getFsSettings, updateFsSettings, refreshFsMonitor, getFsHistory, getFsEvents, listAccountConnections, saveDashboardPassword } from "../api/client";
import type { FsCandidate, FsEvent, FsReport, FsSettings } from "../api/fsTypes";
import type { AccountConnection } from "../api/types";
import "./FsMonitorPage.css";

const stages: Record<string, string> = {
  discovered: "候选观察", quota_sufficient: "额度满足", cost_pass: "成本通过（预估）",
  blocked: "条件不满足", stale: "快照已过期", left_pool: "离开本轮观察池"
};
const reasons: Record<string, string> = {
  account_not_configured: "未选择借币账户", account_unavailable: "账户已禁用或读取范围不符",
  account_account_unavailable: "账户已禁用或读取范围不符", account_storage_unavailable: "账户加密存储未就绪",
  account_account_storage_unavailable: "账户加密存储未就绪", account_account_credentials_unavailable: "账户凭据无法读取",
  account_permission_denied: "账户查询权限不足", permission_denied: "查询权限或地区访问受限",
  account_account_margin_disabled: "账户现货杠杆未开启", account_account_borrow_unavailable: "账户借贷币种或利率不可用",
  account_stale_inventory: "库存数据过期", account_account_fee_unknown: "账户手续费未知",
  account_account_currency_mismatch: "账户查询币种不一致", account_account_credentials_missing: "账户凭据缺失",
  account_query_timeout: "账户查询超时", account_query_failed: "账户查询失败",
  account_exchange_query_failed: "交易所未返回账户数据", account_rate_limited: "账户查询限流",
  account_stale_response: "账户响应过期", account_capacity_unknown: "账户额度或库存未知",
  insufficient_borrow_capacity: "可借量不足目标仓位", fees_unverified: "手续费采用配置假设",
  identity_unverified: "资产身份或单位尚未核验", identity_conflict: "资产身份冲突",
  unsupported_multiplier: "该市场倍率不在首版支持范围", borrow_not_supported: "不支持现货杠杆借币",
  borrow_support_unknown: "借币支持状态未知", low_or_unknown_volume: "成交额不足或未知",
  borrow_rate_unknown: "借币利率未知", cost_threshold_not_met: "压力情景未达到净收益门槛",
  observation_expired: "观察已过期或监测已暂停", account_changed: "账户已变更，等待重新核验",
  spot_market_missing: "未找到同平台对应现货", unsupported_contract: "市场状态或合约规格不支持",
  insufficient_depth: "目标金额的进出场深度不足", stale_orderbook: "订单簿过期",
  invalid_orderbook: "订单簿无效", leg_time_skew: "双腿采样时间差过大", stale_input: "输入数据过期",
  quantity_step_unknown: "数量步长未知", quantity_below_minimum: "目标数量低于交易下限",
  quantity_above_maximum: "目标数量超过市价单上限", order_limits_unknown: "交易数量限制未知",
  funding_schedule_unknown: "结算时间或周期无效", negative_funding_missing: "当前不再满足负费率",
  invalid_market_data: "行情数据无效", orderbook_symbol_mismatch: "订单簿市场不一致",
  candidate_query_failed: "候选数据查询失败", query_timeout: "查询超时", query_failed: "查询失败",
  exchange_query_failed: "交易所查询失败", rate_limited: "交易所限流", scan_failed: "本轮扫描失败，将自动重试",
  stale_response: "交易所返回过期数据", monitor_unavailable: "监测暂不可用，将自动重试",
  no_longer_in_selected_candidates: "费率或排序变化，已离开本轮候选范围",
  metadata_pagination_incomplete: "市场目录未完整读取", missing_source_time: "缺少来源时间",
  invalid_source_time: "来源时间无效",
  market_metadata_missing: "市场规格查询缺失"
};
const reason = (code: string) => reasons[code] ?? "数据未通过校验";
const num = (value: number | null | undefined, digits = 4) => value == null ? "未知" : value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
const pct = (value: number | null | undefined) => value == null ? "未知" : `${num(value, 6)}%`;
const time = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "尚无";
const scenarioLabel: Record<string, string> = { next: "下一次结算", holding: "设定持有期", "24h": "未来24小时" };

export function candidateStage(row: FsCandidate): string {
  return row.valid_until && Date.parse(row.valid_until) <= Date.now() ? "stale" : row.stage;
}

function Details({ row }: { row: FsCandidate }) {
  return <div className="fs-details">
    <Descriptions size="small" bordered column={{ xs: 1, sm: 2, lg: 3 }} items={[
      { key: "route", label: "交易方向", children: `Bybit ${row.symbol}：多永续 / 空现货` },
      { key: "unit", label: "基础币 / 报价 / 倍率", children: `${row.base} / ${row.quote} / ${row.multiplier}` },
      { key: "identity", label: "身份核验", children: row.identity_state === "verified" ? `已核验 ${row.asset_id}` : "未通过核验" },
      { key: "q", label: "双腿基础币数量", children: num(row.quantity, 8) },
      { key: "borrow", label: "所需借币（含首期余量）", children: num(row.borrow_required, 8) },
      { key: "quota", label: "账户最大可借 / 库存", children: `${num(row.account_max_loan)} / ${num(row.available_inventory)}` },
      { key: "capacity", label: "借贷容量参考（USDT）", children: num(row.capacity_usdt, 2) },
      { key: "public", label: "公开档位上限（不是库存）", children: num(row.public_vip_limit) },
      { key: "fee", label: "单笔现货 / 永续费率", children: `${pct(row.spot_fee_pct)} / ${pct(row.future_fee_pct)}（${row.fee_source === "account" ? "账户查询" : "配置假设"}）` },
      ...Object.entries({ spot_sell: "现货开仓卖出均价", future_buy: "永续开仓买入均价", spot_buy: "现货退出买回均价", future_sell: "永续退出卖出均价" }).map(([key, label]) => ({ key, label, children: num(row.prices[key], 10) })),
      { key: "inventory_at", label: "库存来源时间（北京时间）", children: time(row.inventory_at) },
      { key: "expires", label: "核验有效至（北京时间）", children: time(row.valid_until) }
    ]} />
    <Table size="small" rowKey="label" dataSource={row.scenarios} pagination={false} scroll={{ x: 1050 }} columns={[
      { title: "估算情景", dataIndex: "label", render: (v: string) => scenarioLabel[v] },
      { title: "持有 / 预算计息小时", render: (_, r) => `${num(r.hours, 2)} / ${r.budget_interest_hours}` },
      { title: "结算次数", dataIndex: "settlements" },
      ...([ ["funding_usdt", "预估资金费"], ["borrow_usdt", "借币成本"], ["fees_usdt", "四笔手续费"], ["round_trip_price_pnl_usdt", "当前盘口往返损益"], ["buffer_usdt", "执行缓冲"], ["net_usdt", "预估净收益"], ["stressed_net_usdt", "压力净收益"] ] as const).map(([dataIndex, title]) => ({ title: `${title} U`, dataIndex, render: (v: number) => num(v, 3) }))
    ]} />
    <Typography.Paragraph type="secondary">按当前费率、利率、标记价及退出盘口不变估算；利息向上取整小时作为保守预算，不是交易所实际账单。压力情景额外扣除设定的基差恶化。借贷容量不含两腿下单保证金约束，库存查询不会锁定借款。</Typography.Paragraph>
    <Typography.Text type="secondary">来源时间：{Object.entries(row.source_times).map(([key, value]) => `${({ spot_specs: "现货规格", future_specs: "永续规格", spot_ticker: "现货行情", future_ticker: "永续行情", public_borrow: "公开借贷", spot_book: "现货盘口", future_book: "永续盘口", account: "账户额度", account_interest: "账户利率", inventory: "库存" } as Record<string, string>)[key] ?? "查询"} ${time(value)}`).join("；")}</Typography.Text>
  </div>;
}

export function FsMonitorPage() {
  const screens = Grid.useBreakpoint();
  const [form] = Form.useForm<FsSettings>();
  const [report, setReport] = useState<FsReport | null>(null);
  const [accounts, setAccounts] = useState<AccountConnection[]>([]);
  const [events, setEvents] = useState<FsEvent[]>([]);
  const [history, setHistory] = useState<FsReport[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState("");
  const [loginVersion, setLoginVersion] = useState(0);
  const alive = useRef(false);
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    let loading = false;
    async function load(initial = false) {
      if (loading) return;
      loading = true;
      try {
        const data = await getFsReport();
        if (cancelled) return;
        setReport(data); setError("");
        if (initial) {
          const [settings, overview, eventRows, historyRows] = await Promise.all([getFsSettings(), listAccountConnections(), getFsEvents(), getFsHistory()]);
          if (cancelled) return;
          form.setFieldsValue(settings);
          setAccounts(overview.connections.filter(x => x.exchange === "bybit" && x.enabled && x.include_spot && x.include_futures));
          setEvents(eventRows); setHistory(historyRows);
        }
      } catch (e) { if (!cancelled) setError(e instanceof Error ? e.message : "读取失败"); }
      finally { loading = false; }
    }
    void load(true);
    const timer = window.setInterval(() => void load(), 5000);
    return () => { cancelled = true; alive.current = false; window.clearInterval(timer); };
  }, [form, loginVersion]);

  async function save() {
    setBusy(true);
    try {
      const values = await form.validateFields();
      const settings = await updateFsSettings({ ...values, account_id: values.account_id || null });
      const data = await getFsReport();
      if (!alive.current) return;
      form.setFieldsValue(settings); setReport(data); message.success("配置已保存，等待新一轮扫描");
    } catch (e) { if (alive.current && e instanceof Error) message.error(e.message); }
    finally { if (alive.current) setBusy(false); }
  }
  const columns: ColumnsType<FsCandidate> = [
    { title: "Bybit 原始市场", dataIndex: "symbol", width: 150 },
    { title: "状态", width: 170, render: (_, r) => { const s = candidateStage(r); return <Tag color={s === "cost_pass" ? "green" : s === "quota_sufficient" ? "blue" : "default"}>{stages[s]}</Tag>; } },
    { title: "永续费率 / 周期", width: 175, render: (_, r) => <>{pct(r.funding_pct)} / {num(r.interval_hours)}h<br /><small>下次 {time(r.next_funding_at)}</small></> },
    { title: "小时借币利率", width: 145, render: (_, r) => <>{pct(r.hourly_borrow_pct)}<br /><small>{r.borrow_rate_source === "account" ? "账户利率" : "公开普通档位"}</small></> },
    { title: "借币 / 额度 / 库存", width: 180, render: (_, r) => <>{r.product_borrowable == null ? "支持状态未知" : r.product_borrowable ? "产品支持" : "不支持"}<br />{num(r.account_max_loan)} / {num(r.available_inventory)}</> },
    { title: "24h成交额 U（现货 / 永续）", width: 185, render: (_, r) => `${num(r.spot_volume_usdt, 0)} / ${num(r.future_volume_usdt, 0)}` },
    { title: "持有期预估净收益 / 压力 U", width: 180, render: (_, r) => { const s = r.scenarios.find(x => x.label === "holding"); return s ? `${num(s.net_usdt, 2)} / ${num(s.stressed_net_usdt, 2)}` : "无法估算"; } },
    { title: "待满足条件", width: 250, render: (_, r) => r.blockers.length ? r.blockers.map(reason).join("；") : "估算条件通过，执行前仍需重新校验" }
  ];
  return <section className="fs-page">
    <Typography.Title level={3}>负费率 FS 观察池</Typography.Title>
    <Typography.Paragraph>Bybit 做多 USDT 永续、借币卖出现货。持续筛选借贷与成本条件；不借币、不下单。</Typography.Paragraph>
    {error && <Alert type="error" showIcon message={error} description={<Space wrap><Input.Password aria-label="仪表盘密码" placeholder="仪表盘密码" value={password} onChange={e => setPassword(e.target.value)} /><Button onClick={() => { saveDashboardPassword(password); setPassword(""); setLoginVersion(v => v + 1); }}>验证并重新读取</Button></Space>} />}
    {report && <>
      <Alert type={report.error ? "error" : "info"} showIcon message={report.error ? reason(report.error) : "资金费率与借币利率会变化，所有收益均为情景估算"} description={`本轮负费率市场 ${report.negative_count} 个，展示 ${report.candidates.length} 条。观察时间（北京时间）：${time(report.observed_at)}。${report.stale ? "尚无有效快照，或上一轮已过期。" : ""}`} />
      <Collapse items={[{ key: "settings", forceRender: true, label: "监测配置与借币账户", children: <Form form={form} layout="vertical" className="fs-form">
        <Form.Item name="enabled" label="持续扫描" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="account_id" label="Bybit 账户（可不选，仅公开观察）"><Select allowClear placeholder="只查公开数据" options={accounts.map(a => ({ value: a.id, label: a.account_label }))} /></Form.Item>
        {([
          ["target_usdt", "目标现货规模 USDT", 10, 100000], ["holding_hours", "持有小时", 0.1, 72],
          ["scan_interval_seconds", "扫描间隔秒", 30, 600], ["max_candidates", "每轮深查数量", 1, 30],
          ["min_volume_usdt", "双侧最低24h成交额 USDT", 0, 1e12], ["min_net_pct", "压力净收益门槛 %", 0, 100],
          ["spot_fee_pct", "假设现货单笔手续费 %", 0, 5], ["future_fee_pct", "假设永续单笔手续费 %", 0, 5],
          ["execution_buffer_pct", "额外执行缓冲 %", 0, 10], ["basis_stress_pct", "压力基差恶化 %", 0, 50]
        ] as const).map(([name, label, min, max]) => <Form.Item key={name} name={name} label={label} rules={[{ required: true }]}><InputNumber min={min} max={max} /></Form.Item>)}
        <Button type="primary" loading={busy} onClick={() => void save()}>保存配置</Button>
        <Typography.Text type="secondary">需在“账户连接”保存启用现货与永续读取的 Bybit 账户。页面不会替你开启交易所杠杆。</Typography.Text>
      </Form> }]} />
      <Space wrap><Button disabled={!report.settings.enabled || report.scanning} onClick={() => void refreshFsMonitor().then(() => message.info("已请求刷新，结果将在扫描完成后更新")).catch(e => message.error(e.message))}>{report.scanning ? "正在扫描" : "请求刷新"}</Button><Typography.Text type="secondary">展开行查看同币数进出场均价、来源时间及三种持有情景。</Typography.Text></Space>
      {screens.md === false ? <div className="fs-mobile-candidates">{report.candidates.map(row => {
        const s = candidateStage(row);
        const holding = row.scenarios.find(x => x.label === "holding");
        return <article key={row.symbol} className="fs-candidate-card">
          <Space wrap><strong>{row.symbol}</strong><Tag color={s === "cost_pass" ? "green" : s === "quota_sufficient" ? "blue" : "default"}>{stages[s]}</Tag></Space>
          <div className="fs-mobile-metrics">
            <div><small>永续资金费率</small><div>{pct(row.funding_pct)} / {num(row.interval_hours)}h</div></div>
            <div><small>小时借币利率</small><div>{pct(row.hourly_borrow_pct)}</div></div>
            <div><small>持有期预估净收益 U</small><div>{num(holding?.net_usdt, 2)}</div></div>
            <div><small>压力净收益 U</small><div>{num(holding?.stressed_net_usdt, 2)}</div></div>
          </div>
          <div>{row.product_borrowable == null ? "借币支持未知" : row.product_borrowable ? "产品支持借币" : "产品不支持借币"} · 账户额度 {num(row.account_max_loan)} · 库存 {num(row.available_inventory)}</div>
          <small>下次结算 {time(row.next_funding_at)}（北京时间）</small>
          <Typography.Paragraph type="secondary">{row.blockers.map(reason).join("；") || "估算条件通过，执行前仍需重新校验"}</Typography.Paragraph>
          <Collapse size="small" items={[{ key: "detail", label: "查看盘口、成交额与成本明细", children: <>
            <Typography.Paragraph>24h成交额 USDT：现货 {num(row.spot_volume_usdt, 0)} / 永续 {num(row.future_volume_usdt, 0)}</Typography.Paragraph>
            <Details row={row} />
          </> }]} />
        </article>;
      })}</div> : <Table<FsCandidate> size="small" rowKey="symbol" columns={columns} dataSource={report.candidates} scroll={{ x: 1435 }} pagination={{ pageSize: 15, hideOnSinglePage: true }} expandable={{ expandedRowRender: row => <Details row={row} /> }} />}
      <Collapse items={[{ key: "history", label: "历史观察与站内事件", children: <>
        <Button onClick={() => void Promise.all([getFsEvents(), getFsHistory()]).then(([e, h]) => { if (alive.current) { setEvents(e); setHistory(h); } }).catch(e => message.error(e.message))}>刷新历史</Button>
        <Typography.Paragraph type="secondary">每5分钟留存一份，最多288份。历史状态不代表当前可借；站内事件不发送外部通知。</Typography.Paragraph>
        <Table<FsEvent> size="small" rowKey="id" dataSource={events} pagination={{ pageSize: 5 }} scroll={{ x: 700 }} columns={[
          { title: "北京时间", dataIndex: "observed_at", render: time }, { title: "市场", dataIndex: "symbol" },
          { title: "变化", dataIndex: "stage", render: (v: string) => stages[v] ?? "观察变化" },
          { title: "原因", dataIndex: "reasons", render: (v: string[]) => v.map(reason).join("；") || "本轮估算条件通过" }
        ]} />
        <Table<FsReport> size="small" rowKey={r => r.observed_at ?? "empty"} dataSource={history} pagination={{ pageSize: 5 }} columns={[
          { title: "快照时间（北京时间）", dataIndex: "observed_at", render: time },
          { title: "负费率市场", dataIndex: "negative_count" },
          { title: "观察条数", render: (_, r) => r.candidates.length }
        ]} expandable={{ expandedRowRender: r => <Table rowKey="symbol" size="small" dataSource={r.candidates} columns={columns} scroll={{ x: 1435 }} expandable={{ expandedRowRender: row => <Details row={row} /> }} /> }} />
      </> }]} />
    </>}
  </section>;
}
