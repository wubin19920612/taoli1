import { Alert, Button, Card, Form, InputNumber, Modal, Space, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createInstrumentAstroCard, lookupInstrumentAstroCard, previewInstrumentAstroPair, updateInstrumentAstroCard } from "../../api/client";
import type { AstroActionResult, AstroCardLookup, AstroInstrumentRouteRequest, AstroPairPlan, PairSpreadQueryResult } from "../../api/types";
import { AstroCardCreateModal, type AstroSizingFormValues, type AstroSpreadDisplay } from "../astroCard/AstroCardCreateModal";
import { fundingMetrics } from "../pairSummary/metrics";
import { backtest, chartLines, recommend, type Backtest, type Direction, type TemplateLines, type Thresholds } from "./recommend";
import "./pairTemplate.css";

const feeKey = "taoli1.pairTemplate.roundTripFee.v1";
const pct = (n: number | null | undefined) => typeof n === "number" && Number.isFinite(n) ? `${n.toFixed(4)}%` : "—";
const directionLabel = (d: Direction) => d === "long1_short2" ? "左多右空" : "右多左空";
function readFee() {
  try {
    const stored = localStorage.getItem(feeKey);
    const value = stored === null ? 0.16 : Number(stored);
    return Number.isFinite(value) && value >= 0 && value <= 50 ? value : 0.16;
  } catch { return 0.16; }
}

export function templateRoute(result: PairSpreadQueryResult, direction: Direction): AstroInstrumentRouteRequest | null {
  if (!result.current?.leg1.raw_symbol || !result.current.leg2.raw_symbol) return null;
  const [buy, sell] = direction === "long1_short2" ? [result.current.leg1, result.current.leg2] : [result.current.leg2, result.current.leg1];
  return { symbol: buy.symbol, buy_exchange: buy.exchange, buy_market_type: buy.market_type,
    buy_raw_symbol: buy.raw_symbol, buy_dex: buy.dex, sell_exchange: sell.exchange,
    sell_market_type: sell.market_type, sell_raw_symbol: sell.raw_symbol, sell_dex: sell.dex,
    comparison_kind: result.leg1.symbol !== result.leg2.symbol || buy.symbol !== sell.symbol ? "custom_cross_asset" : "same_asset" };
}

function BacktestSummary({ label, value }: { label: string; value: Backtest }) {
  return <div className="pair-template-backtest"><strong>{label}</strong>
    <div>{value.trades} 笔 · 胜率 {pct(value.winRate)} · 总净 {pct(value.totalNetPct)}</div>
    <div>平均持仓 {value.averageHours.toFixed(2)}h · 最长 {value.longestHours.toFixed(2)}h</div>
    <div>{value.holding ? `末尾仍持仓 · 浮盈亏 ${pct(value.floatingNetPct)}` : "末尾空仓"}</div>
  </div>;
}

type CreateSnapshot = { route: AstroInstrumentRouteRequest; thresholds: Thresholds; display: AstroSpreadDisplay; direction: Direction };
function TemplateCreateModal({ snapshot, onClose, onCreated }: {
  snapshot: CreateSnapshot; onClose: () => void; onCreated: () => void;
}) {
  const [form] = Form.useForm<AstroSizingFormValues>();
  const [plan, setPlan] = useState<AstroPairPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<AstroActionResult | null>(null);
  const variant = Form.useWatch("card_variant", form) ?? plan?.card_variant ?? "both";
  useEffect(() => {
    let active = true;
    previewInstrumentAstroPair(snapshot.route, "astro", { open_spread_pct: snapshot.thresholds.open, close_spread_pct: snapshot.thresholds.close })
      .then((next) => {
        if (!active) return;
        setPlan(next);
        if (next.pair) form.setFieldsValue({
          max_trade_usdt: Number(next.pair.maxTradeUSDT ?? 10), leverage: Number(next.pair.leverage ?? 1),
          min_notional: Number(next.pair.minNotional ?? 10), max_notional: Number(next.pair.maxNotional ?? 10),
          open_enabled: next.pair.status === true && next.pair.disableOpen !== true,
          card_variant: next.card_variant ?? "both", save_as_default: false
        });
      }).catch((e: unknown) => { if (active) setError(String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [snapshot, form]);
  const submit = async () => {
    if (busyRef.current || loading || !plan?.can_submit || plan.source_open_spread_pct == null || response?.status === "created") return;
    busyRef.current = true; setBusy(true); setError("");
    try {
      const values = await form.validateFields();
      const next = await createInstrumentAstroCard(snapshot.route, plan.source_open_spread_pct,
        { ...values, target: "astro", open_spread_pct: snapshot.thresholds.open, close_spread_pct: snapshot.thresholds.close });
      setResponse(next);
      if (next.status === "created" || next.status === "updated") { setPlan({ ...plan, can_submit: false }); onCreated(); }
    } catch (e) { setError(String(e)); }
    finally { busyRef.current = false; setBusy(false); }
  };
  return <AstroCardCreateModal astroOnly templateThresholds={snapshot.thresholds} astroSpread={snapshot.display} astroSymbol={snapshot.route.symbol}
    astroReversed={snapshot.direction === "long2_short1"} astroPlan={plan} astroPreviewLoading={loading}
    astroPreviewError={error} astroSubmitLoading={busy} astroSubmitResult={response} astroSubmitError=""
    astroSizingForm={form} cardTarget="astro" arbStatus={null} readyPreviewTarget={plan ? "astro" : null}
    selectedCardVariant={variant} closeAstroPreview={() => { if (!busyRef.current) onClose(); }}
    submitAstroCard={() => void submit()} changeCardTarget={() => {}} />;
}

export function PairTemplateCard({ result, onLinesChange }: {
  result: PairSpreadQueryResult; onLinesChange: (lines: TemplateLines | null) => void;
}) {
  const [fee, setFee] = useState(readFee);
  const [profit, setProfit] = useState(0.15);
  const [selected, setSelected] = useState("balanced");
  const metrics = fundingMetrics(result.current?.leg1, result.current?.leg2);
  const recommendation = useMemo(() => recommend(result.points, result.current, fee, profit, metrics.hourly), [result.points, result.current, fee, profit, metrics.hourly]);
  const editContext = JSON.stringify([result.leg1, result.leg2, result.leg2_multiplier, result.hours,
    result.interval_minutes, result.interval_seconds, result.first_seen_at, result.last_seen_at, recommendation.direction]);
  const [manual, setManual] = useState<{ context: string; values: Record<string, Thresholds> }>({ context: editContext, values: {} });
  // Discard old thresholds before committing chart lines or actions for a new context.
  if (manual.context !== editContext) setManual({ context: editContext, values: {} });
  const edits = manual.context === editContext ? manual.values : {};
  const setEdits = (values: Record<string, Thresholds>) => setManual({ context: editContext, values });
  const template = recommendation.templates.find((t) => t.id === selected);
  const thresholds = template ? edits[selected] ?? template : null;
  const routeJson = JSON.stringify(templateRoute(result, recommendation.direction));
  const route = useMemo(() => JSON.parse(routeJson) as AstroInstrumentRouteRequest | null, [routeJson]);
  const [lookup, setLookup] = useState<AstroCardLookup | null>(null);
  const [lookupError, setLookupError] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [create, setCreate] = useState<CreateSnapshot | null>(null);
  const [update, setUpdate] = useState<{ route: AstroInstrumentRouteRequest; before: AstroCardLookup; after: Thresholds } | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState("");
  const refresh = useCallback(() => setRevision((n) => n + 1), []);
  const multiplied = result.leg2_multiplier !== 1;
  useEffect(() => {
    let active = true;
    setLookup(null); setLookupError("");
    if (!route || multiplied) return;
    setLookupLoading(true);
    lookupInstrumentAstroCard(route).then((value) => { if (active) setLookup(value); })
      .catch((e: unknown) => { if (active) setLookupError(String(e)); })
      .finally(() => { if (active) setLookupLoading(false); });
    return () => { active = false; };
  }, [route, multiplied, revision]);
  const open = thresholds?.open, close = thresholds?.close;
  const valid = open !== undefined && close !== undefined && Number.isFinite(open) && Number.isFinite(close) && Math.abs(open) <= 50 && Math.abs(close) <= 50 && open > close;
  useEffect(() => {
    onLinesChange(valid ? chartLines({ open: open!, close: close! }, recommendation.h, recommendation.direction) : null);
    return () => onLinesChange(null);
  }, [open, close, valid, recommendation.h, recommendation.direction, onLinesChange]);
  const disabled = multiplied || !route || !valid || busy;
  const submitUpdate = async () => {
    if (!update || busyRef.current) return;
    busyRef.current = true; setBusy(true); setNotice("");
    try {
      const response = await updateInstrumentAstroCard({ route: update.route, open_spread_pct: update.after.open,
        close_spread_pct: update.after.close, expected_open_position: update.before.open_position ?? undefined,
        expected_close_position: update.before.close_position ?? undefined });
      setNotice([response.message, ...(response.warnings ?? [])].join("；")); setUpdate(null); refresh();
    } catch (e) { setNotice(String(e)); setUpdate(null); refresh(); }
    finally { busyRef.current = false; setBusy(false); }
  };
  return <section className="pair-template-panel" aria-label="推荐开平仓模板">
    <Typography.Title level={5}>推荐开平仓模板 · {directionLabel(recommendation.direction)}</Typography.Title>
    <Space wrap>
      <label>往返手续费 % <InputNumber aria-label="模板往返手续费" value={fee} min={0} max={50} step={0.01} onChange={(v) => {
        if (v !== null) { setFee(v); try { localStorage.setItem(feeKey, String(v)); } catch { /* storage optional */ } }
      }} /></label>
      <label>最小净利 % <InputNumber aria-label="模板最小净利" value={profit} min={0} max={50} step={0.01} onChange={(v) => { if (v !== null) setProfit(v); }} /></label>
      <span>半边成本 h={pct(recommendation.h)} · 净资金费率 {pct(recommendation.netHourly)}/小时（按当前费率估算）</span>
    </Space>
    <p>基于历史回测，不代表未来。分钟收盘价近似中间价；样本内使用整个查询区间。样本外仅用前 2/3 拟合方向、分位和持仓时长，在后 1/3 从空仓验证。盘口成本固定为当前 h；净收益扣手续费，不计资金费实收实付。</p>
    {recommendation.netHourly === null ? <Alert type="warning" message="资金费数据不足，资金费调整按 0 估算" /> : null}
    {recommendation.insufficient ? <Alert type="info" message="数据不足：至少需要 120 个有效点且覆盖 6 小时，请扩大查询区间" /> : <div className="pair-template-grid">
      {recommendation.templates.map((item) => {
        const values = edits[item.id] ?? item;
        const stats = edits[item.id] ? backtest(recommendation.directed, values, recommendation.h, fee) : item.inSample;
        return <Card key={item.id} size="small" title={item.label} className={selected === item.id ? "pair-template-selected" : ""}
          extra={<Button size="small" type={selected === item.id ? "primary" : "default"} onClick={() => setSelected(item.id)}>选用</Button>}>
          <label>开仓≥ <InputNumber aria-label={`${item.label}开仓`} value={values.open} min={-50} max={50} step={0.01} onChange={(v) => { if (v !== null) setEdits({ ...edits, [item.id]: { ...values, open: v } }); }} /> %</label>
          <label>平仓≤ <InputNumber aria-label={`${item.label}平仓`} value={values.close} min={-50} max={50} step={0.01} onChange={(v) => { if (v !== null) setEdits({ ...edits, [item.id]: { ...values, close: v } }); }} /> %</label>
          <p>间距 {pct(values.open - values.close)} · 资金费调整 {pct(item.fundingAdjustment)}</p>
          <p>距开仓线 {recommendation.openExec === null ? "盘口不可用" : recommendation.openExec >= values.open ? "已达到" : pct(values.open - recommendation.openExec)}</p>
          <BacktestSummary label={edits[item.id] ? "样本内（手调后）" : "样本内"} value={stats} />
          <BacktestSummary label="样本外（训练模板，未使用手调值）" value={item.outOfSample} />
          <small>验证方向 {directionLabel(item.validationDirection)} · 开 {pct(item.validationThresholds.open)} / 平 {pct(item.validationThresholds.close)}</small>
        </Card>;
      })}
    </div>}
    {multiplied ? <Alert type="warning" message="带倍数的价差暂不支持建卡" /> : !route ? <Alert type="warning" message="缺少两腿原始市场信息，请刷新盘口后建卡" /> : null}
    {!recommendation.insufficient && !valid ? <Alert type="error" message="开仓阈值必须大于平仓阈值，且均在 -50% 至 50% 内" /> : null}
    <p>{lookupLoading ? "正在查找已有卡片…" : lookup?.count === 1 ? `已有卡片 ${lookup.pair_name}：开 ${pct(lookup.open_spread_pct)} → ${pct(open)} / 平 ${pct(lookup.close_spread_pct)} → ${pct(close)}` : lookup?.count ? "找到多条对应卡片，请在 Astro 确认路线，暂不支持修改" : "未找到对应卡片，修改不可用"}</p>
    {lookupError ? <Alert type="error" message={`查卡失败：${lookupError}`} /> : null}
    {notice ? <Alert type="info" message={notice} /> : null}
    <Space wrap>
      <Button disabled={disabled} onClick={() => {
        if (!route || !thresholds || !result.current) return;
        const [buy, sell] = recommendation.direction === "long1_short2" ? [result.current.leg1, result.current.leg2] : [result.current.leg2, result.current.leg1];
        setCreate({ route, thresholds: { ...thresholds }, direction: recommendation.direction,
          display: { ...route, buy_ask: buy.ask_price ?? 0, sell_bid: sell.bid_price ?? 0, opportunity_type: null } });
      }}>快捷建卡</Button>
      <Button disabled={disabled || lookupLoading || lookup?.count !== 1} onClick={() => {
        if (route && thresholds && lookup?.count === 1) setUpdate({ route, before: lookup, after: { ...thresholds } });
      }}>修改卡片</Button>
      <Button disabled={!route || multiplied || busy} loading={lookupLoading} onClick={refresh}>刷新卡片</Button>
      <Button onClick={() => setEdits({})}>恢复推荐值</Button>
    </Space>
    {create ? <TemplateCreateModal snapshot={create} onClose={() => setCreate(null)} onCreated={refresh} /> : null}
    <Modal open={update !== null} title="确认修改卡片开平阈值" okText="确认修改" cancelText="取消" confirmLoading={busy}
      closable={!busy} maskClosable={!busy} keyboard={!busy} cancelButtonProps={{ disabled: busy }}
      onCancel={() => { if (!busyRef.current) setUpdate(null); }} onOk={() => void submitUpdate()}>
      <p>开仓 {pct(update?.before.open_spread_pct)} → {pct(update?.after.open)}</p>
      <p>平仓 {pct(update?.before.close_spread_pct)} → {pct(update?.after.close)}</p>
      <p>仅修改 openPosition / closePosition，其他参数保持原样。</p>
    </Modal>
  </section>;
}
