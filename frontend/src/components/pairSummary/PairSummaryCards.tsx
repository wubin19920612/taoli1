import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { PairSpreadCurrentLeg, PairSpreadCurrentSnapshot, PairSpreadQueryResult } from "../../api/types";
import { formatPriceRatio, priceRatio, ratioChartPoints } from "../../utils/pairRatio";
import { countdown, fundingMetrics, nextSettlementTime, roundTripMetrics, spreadPosition } from "./metrics";
import "./pairSummary.css";

type Value = number | null | undefined;
type Tone = "positive" | "negative" | "neutral";
type PercentFormatter = (value: Value, digits?: number) => string;

interface Props {
  result: PairSpreadQueryResult | null;
  hours: number;
  ratioMode: boolean;
  spreadTone: Tone;
  closeSpreadTone: Tone;
  price: (value: Value) => string;
  signedPct: PercentFormatter;
  exchangeLabels: Record<string, string>;
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return <section className="pair-metric-card pair-summary-card" aria-label={title}>
    <h3 className="pair-metric-label">{title}</h3>
    {children}
  </section>;
}

function signedRatio(value: Value): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : "-"}${value === 0 ? "0" : formatPriceRatio(Math.abs(value))}`;
}

function FundingSummaryCard({ current, signedPct, exchangeLabels }: {
  current: PairSpreadCurrentSnapshot | null | undefined;
  signedPct: PercentFormatter;
  exchangeLabels: Record<string, string>;
}) {
  const funding = fundingMetrics(current?.leg1, current?.leg2);
  const settlement = nextSettlementTime(current?.leg1, current?.leg2);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (settlement === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [settlement]);
  const tone = funding.eightHourly === null || funding.eightHourly === 0 ? "neutral"
    : funding.eightHourly > 0 ? "positive" : "negative";
  const legText = (leg: PairSpreadCurrentLeg | undefined, side: string) => {
    if (!leg) return `${side} -`;
    const venue = `${exchangeLabels[leg.exchange] ?? leg.exchange}${leg.dex ? ` (${leg.dex})` : ""}`;
    return `${side} ${venue} ${leg.market_type === "spot" ? "现货 · 无资金费"
      : `${signedPct(leg.funding_rate_pct, 4)} · ${leg.funding_interval_hours ?? 8}h`}`;
  };
  return <Card title="资金费（左多 / 右空）">
    <div className="pair-summary-row">
      <span>净费率 / 8h</span>
      <strong className={`pair-summary-main pair-summary-funding-${tone}`}>{signedPct(funding.eightHourly, 4)}</strong>
    </div>
    <div className="pair-summary-small">日化 {signedPct(funding.daily, 4)} · 年化 {signedPct(funding.annual, 2)}</div>
    <div className="pair-summary-small" title={current?.leg1.raw_symbol}>{legText(current?.leg1, "左")}</div>
    <div className="pair-summary-small" title={current?.leg2.raw_symbol}>{legText(current?.leg2, "右")}</div>
    <div className="pair-summary-footer">
      <span>下次结算 {countdown(settlement, now)}</span>
      {funding.nextEightHourly !== null && <span>下期净 {signedPct(funding.nextEightHourly, 4)} / 8h</span>}
    </div>
  </Card>;
}

export function PairSummaryCards({ result, hours, ratioMode, spreadTone, closeSpreadTone, price, signedPct, exchangeLabels }: Props) {
  const current = result?.current;
  const open = ratioMode ? priceRatio(current?.leg1.ask_price, current?.leg2.bid_price) : current?.open_spread_pct;
  const close = ratioMode ? priceRatio(current?.leg1.bid_price, current?.leg2.ask_price) : current?.close_spread_pct;
  const metric = ratioMode ? formatPriceRatio : signedPct;
  const delta = ratioMode ? signedRatio : signedPct;
  const cost = roundTripMetrics(open, close);
  const position = useMemo(() => {
    const currentValue = ratioMode
      ? priceRatio(current?.leg1.mid_price, current?.leg2.mid_price) ?? priceRatio(current?.leg1.price, current?.leg2.price)
      : current?.spread_pct;
    return spreadPosition(ratioChartPoints(result?.points ?? [], ratioMode), currentValue, current?.observed_at);
  }, [result, current, ratioMode]);
  const queryHours = result?.hours ?? hours;
  const range = queryHours >= 24 ? `${Number((queryHours / 24).toFixed(2))} 天` : `${queryHours} 小时`;

  return <>
    <Card title={ratioMode ? "可成交比值" : "可成交价差"}>
      <div className={`pair-summary-row pair-metric-${ratioMode ? "neutral" : spreadTone}`}>
        <span>开仓</span><strong className="pair-metric-value pair-summary-main">{metric(open)}</strong>
      </div>
      <div className="pair-summary-small">左 ask {price(current?.leg1.ask_price)} · 右 bid {price(current?.leg2.bid_price)}</div>
      <div className={`pair-summary-row pair-metric-${ratioMode ? "neutral" : closeSpreadTone}`}>
        <span>平仓</span><strong className="pair-metric-value pair-summary-main">{metric(close)}</strong>
      </div>
      <div className="pair-summary-small">左 bid {price(current?.leg1.bid_price)} · 右 ask {price(current?.leg2.ask_price)}</div>
      <div className="pair-summary-footer">
        <span>开平差（来回成本）</span>
        <strong>{delta(cost.difference)}{ratioMode && cost.relativePct !== null ? `（${signedPct(cost.relativePct)}）` : ""}</strong>
      </div>
    </Card>
    <FundingSummaryCard current={current} signedPct={signedPct} exchangeLabels={exchangeLabels} />
    <Card title={`价差位置（查询区间 ${range}）`}>
      <div className="pair-summary-row">
        <span>当前分位</span><strong className="pair-summary-main pair-summary-percentile">{position.percentile === null ? "-" : `P${position.percentile}`}</strong>
      </div>
      <div className="pair-summary-range" role="img" aria-label={`区间位置：当前 ${metric(position.current)}，均值 ${metric(position.mean)}`}>
        {position.meanPosition !== null && <span className="pair-summary-marker pair-summary-mean" style={{ left: `${position.meanPosition}%` }} title={`均值 ${metric(position.mean)}`} />}
        {position.currentPosition !== null && <span className="pair-summary-marker pair-summary-current" style={{ left: `${position.currentPosition}%` }} title={`当前 ${metric(position.current)}`} />}
      </div>
      <div className="pair-summary-stats">
        <span>低 <strong>{metric(position.min)}</strong></span>
        <span>均 <strong>{metric(position.mean)}</strong></span>
        <span>高 <strong>{metric(position.max)}</strong></span>
      </div>
      <div className="pair-summary-small">偏离均值 {delta(position.deviation)}{ratioMode && position.relativeDeviationPct !== null ? `（${signedPct(position.relativeDeviationPct)}）` : ""}</div>
      <div className="pair-summary-footer">
        <span>近 1h 变化 {delta(position.change1h)}</span><span>波动 σ {ratioMode ? (position.sigma === 0 ? "0" : formatPriceRatio(position.sigma)) : signedPct(position.sigma)}</span>
      </div>
    </Card>
  </>;
}
