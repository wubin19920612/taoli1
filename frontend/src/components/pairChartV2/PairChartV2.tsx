import type { TemplateLines } from "../pairTemplate/recommend";
import type { AutoscaleInfo } from "lightweight-charts";
import { Button, InputNumber, Popover, Switch, Tag, Tooltip, Typography } from "antd";
import dayjs from "dayjs";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, LineType, TickMarkType, createChart,
  type IChartApi, type ISeriesApi, type Time
} from "lightweight-charts";
import type { PairSpreadPoint, PairSpreadQueryResult } from "../../api/types";
import { formatPriceRatio } from "../../utils/pairRatio";
import { chartData, isFiniteNumber, latestAt, MAX_CHART_HOURS, priceFormatFor, thresholdMetric, utcSeconds } from "./data";
import { readThresholds, writeThresholds, type Thresholds } from "./storage";
import { useRangeSelection } from "./useRangeSelection";
import "./pairChartV2.css";

export type PairChartV2Controls = {
  loading: boolean;
  onSwap: () => void;
  onLoadHours: (hours: number) => void;
};
type Props = PairChartV2Controls & {
  templateLines?: TemplateLines | null;
  result: PairSpreadQueryResult | null;
  points: PairSpreadPoint[];
  labels: [string, string];
  onRangeSelect: (start: number, end: number) => void;
  onRangeClear: () => void;
};
type Series = {
  spread: ISeriesApi<"Line">; left: ISeriesApi<"Line">; right: ISeriesApi<"Line">;
  leftFunding: ISeriesApi<"Line">; rightFunding: ISeriesApi<"Line">; settlements: ISeriesApi<"Histogram">;
};
const percent = (value: number | null | undefined) => isFiniteNumber(value) ? `${value.toFixed(4)}%` : "—";
const localDate = (time: Time) => typeof time === "number" ? dayjs.unix(time) : dayjs(typeof time === "string" ? time : `${time.year}-${time.month}-${time.day}`);
const localTime = (time: Time) => localDate(time).format("YYYY-MM-DD HH:mm:ss");
const tickTime = (time: Time, type: TickMarkType) => localDate(time).format(
  type === TickMarkType.Year ? "YYYY" : type === TickMarkType.Month ? "YYYY-MM" : type === TickMarkType.DayOfMonth ? "MM-DD" : type === TickMarkType.TimeWithSeconds ? "HH:mm:ss" : "HH:mm"
);
const colors = { spread: "#1D9E75", left: "#378ADD", right: "#D85A30", thresholds: "#D99520" };

function QuoteAge({ label, age }: { label: string; age?: number | null }) {
  return <Tag color={!isFiniteNumber(age) || age < 0 ? undefined : age <= 2000 ? "green" : age <= 10000 ? "gold" : "red"}>
    {label} {isFiniteNumber(age) && age >= 0 ? `${(age / 1000).toFixed(1)}s` : "时效未知"}
  </Tag>;
}

function FundingSummary({ result, data }: { result: Props["result"]; data: ReturnType<typeof chartData> }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);
  return <div className="pair-v2-funding-summary">
    <strong>资金费率 / 净结算（多左空右）</strong>
    {(["leg1", "leg2"] as const).map((leg, index) => {
      const current = result?.current?.[leg];
      const rates = index === 0 ? data.leftFunding : data.rightFunding;
      const latest = isFiniteNumber(current?.funding_rate_pct) ? current.funding_rate_pct : rates[rates.length - 1]?.value;
      const next = current?.funding_next_time ? utcSeconds(current.funding_next_time) : null;
      const remaining = next === null ? null : Math.ceil(next - now / 1000);
      const countdown = remaining !== null && remaining >= 0
        ? `${Math.floor(remaining / 3600)}:${String(Math.floor(remaining % 3600 / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}` : null;
      return <span key={leg} style={{ color: index === 0 ? colors.left : colors.right }}>
        {index === 0 ? "左" : "右"} {result?.[leg].exchange} {percent(latest)}
        {current?.funding_interval_hours ? ` / ${current.funding_interval_hours}h` : ""}
        {countdown !== null ? ` · 下次结算 ${countdown}` : ""}
      </span>;
    })}
    <span>柱：已结算净费率；线：实时费率，不足两点时接历史阶梯</span>
  </div>;
}

export function PairChartV2({ templateLines, result, points, labels, loading, onSwap, onLoadHours, onRangeSelect, onRangeClear }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Series | null>(null);
  const fitted = useRef(false);
  const [thresholds, setThresholds] = useState(readThresholds);
  const [visible, setVisible] = useState({ spread: true, left: true, right: true, thresholds: true });
  const [rangeMode, setRangeMode] = useState(false);
  const [hoverTime, setHoverTime] = useState<number | null>(null);
  const data = useMemo(() => chartData(result, points), [result, points]);
  const ratioMode = data.ratioMode;
  const metric = ratioMode ? formatPriceRatio : percent;
  const title = ratioMode ? "价格比值曲线（左 ÷ 右）" : "价差曲线";
  const atHistoryCap = (result?.hours ?? 0) >= MAX_CHART_HOURS;

  useEffect(() => {
    if (!host.current) return;
    const chart = createChart(host.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#64748b", panes: { enableResize: false } },
      grid: { vertLines: { color: "#f1f5f9" }, horzLines: { color: "#e2e8f0" } },
      leftPriceScale: { visible: true, borderColor: "#e2e8f0" },
      rightPriceScale: { visible: true, borderColor: "#e2e8f0" },
      timeScale: { timeVisible: true, secondsVisible: true, tickMarkFormatter: tickTime, borderColor: "#e2e8f0" },
      localization: { timeFormatter: localTime }, crosshair: { mode: CrosshairMode.Normal }
    });
    chartRef.current = chart;
    const line = (color: string, priceScaleId: string, paneIndex = 0) => chart.addSeries(LineSeries, {
      color, priceScaleId, lineWidth: 1, lastValueVisible: true, priceLineVisible: false
    }, paneIndex);
    const spread = line(colors.spread, "right");
    spread.applyOptions({ lineWidth: 3 });
    const left = line(colors.left, "left"), right = line(colors.right, "left");
    const leftFunding = line(colors.left, "right", 1), rightFunding = line(colors.right, "right", 1);
    const settlements = chart.addSeries(HistogramSeries, { priceScaleId: "right", priceLineVisible: false, lastValueVisible: false, title: "净结算" }, 1);
    const fundingFormat = { type: "custom" as const, formatter: percent, minMove: 0.0001 };
    leftFunding.applyOptions({ lineType: LineType.WithSteps, priceFormat: fundingFormat });
    rightFunding.applyOptions({ lineType: LineType.WithSteps, priceFormat: fundingFormat });
    settlements.applyOptions({ priceFormat: fundingFormat });
    chart.panes()[0].setStretchFactor(0.7);
    chart.panes()[1].setStretchFactor(0.3);
    seriesRef.current = { spread, left, right, leftFunding, rightFunding, settlements };
    const hover = (event: { time?: Time; point?: { x: number; y: number } }) => setHoverTime(event.point && typeof event.time === "number" ? event.time : null);
    chart.subscribeCrosshairMove(hover);
    return () => {
      chart.unsubscribeCrosshairMove(hover);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      fitted.current = false;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current, chart = chartRef.current;
    if (!series || !chart) return;
    series.spread.applyOptions({ title: ratioMode ? "价格比值" : "价差", priceFormat: { type: "custom", formatter: ratioMode ? formatPriceRatio : percent, minMove: 0.000001 } });
    series.left.applyOptions({ title: labels[0], priceFormat: priceFormatFor(data.left[data.left.length - 1]?.value, ratioMode) });
    series.right.applyOptions({ title: labels[1], priceFormat: priceFormatFor(data.right[data.right.length - 1]?.value, ratioMode) });
    series.leftFunding.applyOptions({ title: `${result?.leg1.exchange ?? "左"} 费率` });
    series.rightFunding.applyOptions({ title: `${result?.leg2.exchange ?? "右"} 费率` });
    for (const key of ["spread", "left", "right", "leftFunding", "rightFunding", "settlements"] as const) series[key].setData(data[key]);
    if (!fitted.current && data.spread.length) { chart.timeScale().fitContent(); fitted.current = true; }
  }, [data, labels[0], labels[1], ratioMode, result?.leg1.exchange, result?.leg2.exchange]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    for (const key of ["spread", "left", "right"] as const) series[key].applyOptions({ visible: visible[key] });
    const lines = (["open", "close"] as const).flatMap((key) => {
      const value = thresholds[key];
      const price = value === null ? null : thresholdMetric(value, ratioMode);
      return price === null ? [] : [series.spread.createPriceLine({
        price, color: colors.thresholds, lineStyle: LineStyle.Dashed, lineWidth: 1,
        title: key === "open" ? "开仓阈值" : "平仓阈值", lineVisible: visible.thresholds, axisLabelVisible: visible.thresholds
      })];
    });
    return () => { if (seriesRef.current === series) lines.forEach((line) => series.spread.removePriceLine(line)); };
  }, [visible, thresholds, ratioMode]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const templatePrices = templateLines ? Object.values(templateLines)
      .map((value) => thresholdMetric(value, ratioMode)).filter((value): value is number => value !== null) : [];
    series.spread.applyOptions({ autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
      const info = original();
      if (!info?.priceRange || !templatePrices.length) return info;
      return { ...info, priceRange: {
        minValue: Math.min(info.priceRange.minValue, ...templatePrices),
        maxValue: Math.max(info.priceRange.maxValue, ...templatePrices)
      } };
    } });
    if (!templateLines) return;
    const lines = Object.entries(templateLines).flatMap(([key, value]) => {
      const price = thresholdMetric(value, ratioMode);
      return price === null ? [] : [series.spread.createPriceLine({ price,
        color: key === "open" ? "#1677ff" : "#d97706", lineStyle: LineStyle.Dashed, lineWidth: 2,
        title: key === "open" ? "模板开仓线" : "模板平仓线", axisLabelVisible: true, lineVisible: true })];
    });
    return () => { if (seriesRef.current === series) lines.forEach((line) => series.spread.removePriceLine(line)); };
  }, [templateLines, ratioMode]);

  const { box, clear } = useRangeSelection(host, chartRef, rangeMode, onRangeSelect, onRangeClear);
  const updateThreshold = (key: keyof Thresholds, value: number | null) => {
    const next = { ...thresholds, [key]: value };
    setThresholds(next); writeThresholds(next);
  };
  const hovered = hoverTime === null ? undefined : data.prices.find((point) => point.time === hoverTime);
  const hoveredSpread = hoverTime === null ? undefined : data.spread.find((point) => point.time === hoverTime);
  const settlement = hoverTime === null ? undefined : latestAt(data.settlements, hoverTime);

  return <div className="pair-chart-card pair-chart-v2" data-testid="pair-chart-v2">
    <div className="pair-v2-toolbar">
      <Typography.Title level={5}>{title}</Typography.Title>
      <span>{result?.leg1.exchange ?? "左腿"} 多 / {result?.leg2.exchange ?? "右腿"} 空</span>
      <Button size="small" disabled={!result || loading} onClick={onSwap}>⇆ 切换</Button>
      <QuoteAge label={result?.leg1.exchange ?? "左腿"} age={result?.current?.leg1.age_ms} />
      <span>·</span><QuoteAge label={result?.leg2.exchange ?? "右腿"} age={result?.current?.leg2.age_ms} />
      <Tooltip title={atHistoryCap ? "超过 7 天后端会改为小时周期" : undefined}>
        <span><Button size="small" loading={loading} disabled={!result || loading || atHistoryCap}
          onClick={() => onLoadHours(Math.min(MAX_CHART_HOURS, (result?.hours ?? 0) + 24))}>再加载 1 天</Button></span>
      </Tooltip>
      <Button size="small" loading={loading} disabled={!result || loading || atHistoryCap} onClick={() => onLoadHours(MAX_CHART_HOURS)}>加载至 7 天</Button>
      <Switch size="small" checked={rangeMode} onChange={setRangeMode} aria-label="区间" /><span>区间</span>
      <Button size="small" aria-label="清除" onClick={clear}>清除</Button>
      <Popover trigger="click" title="阈值设置" content={<div className="pair-v2-settings">
        {(["open", "close"] as const).map((key) => <label key={key}>
          {key === "open" ? "开仓阈值 (%)" : "平仓阈值 (%)"}
          <InputNumber aria-label={key === "open" ? "开仓阈值 (%)" : "平仓阈值 (%)"} value={thresholds[key]} onChange={(value) => updateThreshold(key, value)} />
        </label>)}
        <span>留空不画。{ratioMode ? "比值模式按原价差率换算；有效范围 (-200%, 200%)。" : ""}</span>
      </div>}><Button size="small" aria-label="设置">设置</Button></Popover>
    </div>
    <div className="pair-v2-legend">
      {(["spread", "left", "right", "thresholds"] as const).map((key) => <Button key={key} size="small" type="text"
        aria-pressed={visible[key]} style={{ color: colors[key], opacity: visible[key] ? 1 : 0.4 }}
        onClick={() => setVisible((prev) => ({ ...prev, [key]: !prev[key] }))}>
        {key === "spread" ? ratioMode ? "价格比值" : "价差" : key === "left" ? `价格 A · ${labels[0]}` : key === "right" ? `价格 B · ${labels[1]}` : "阈值"}
      </Button>)}
      {ratioMode && <span>价格 A/B 指数化 · 各自首点=100</span>}
      <span>按住 Shift 拖动，或开启「区间」后拖动；Esc / 清除取消</span>
    </div>
    <FundingSummary result={result} data={data} />
    <div className="pair-v2-stage">
      <div ref={host} className="pair-v2-canvas" data-testid="pair-chart-v2-canvas" role="img" aria-label={title} />
      {box && <div className="pair-v2-selection" data-testid="pair-v2-selection" style={box} />}
      {hoverTime !== null && <div className="pair-v2-tooltip" role="status">
        <strong>{localTime(hoverTime as Time)}</strong>
        <span>{ratioMode ? "价格比值" : "价差"}：{metric(hoveredSpread?.value)}</span>
        <span>左价：{hovered?.leg1_close ?? "—"} · 右价：{hovered?.leg2_close ?? "—"}</span>
        <span>最近净结算：{percent(settlement?.value)}{settlement ? ` (${localTime(settlement.time)})` : ""}</span>
      </div>}
      {!data.spread.length && <div className="pair-v2-empty">{loading ? "正在加载图表" : "暂无价差数据，请查询标的对"}</div>}
    </div>
  </div>;
}
