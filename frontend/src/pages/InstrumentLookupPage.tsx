import {
  DownOutlined,
  LineChartOutlined,
  ReloadOutlined,
  RightOutlined,
  SearchOutlined,
  WarningOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Empty,
  Input,
  Segmented,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { lookupInstrument, querySymbolExchangeSpreads } from "../api/client";
import type {
  InstrumentExchangeSnapshot,
  InstrumentLookupResult,
  MarketSnapshot,
  MarketType,
  SymbolSpreadPoint,
  SymbolSpreadQueryResult
} from "../api/types";
import { resolveHistoryIntervalSeconds } from "../constants/queryLimits";

dayjs.extend(utc);

const LAST_SYMBOL_KEY = "taoli1.instrumentLookup.lastSymbol.v1";
const AUTO_REFRESH_MS = 10_000;
const exchangeLabels: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  htx: "HTX",
  hyperliquid: "Hyperliquid",
  okx: "OKX"
};
const chartExchanges: Record<MarketType, Set<string>> = {
  spot: new Set(["binance", "okx", "bybit", "gate", "bitget"]),
  future: new Set(["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid"])
};
const seriesColors = ["#0f766e", "#2563eb", "#d97706", "#b42318", "#7c3aed", "#0891b2", "#475569"];

type TrendState = {
  loading: boolean;
  error: string;
  result: SymbolSpreadQueryResult | null;
};

type PriceSeries = {
  exchange: string;
  points: Array<{ bucketAt: string; price: number }>;
};

function initialSymbol(): string {
  if (typeof window === "undefined") {
    return "BTC";
  }
  const fromUrl = new URLSearchParams(window.location.search).get("symbol");
  return fromUrl || window.localStorage.getItem(LAST_SYMBOL_KEY) || "BTC";
}

function normalizeSymbol(value: string): string {
  const compact = value.trim().toUpperCase().replace(/[-_/\s]/g, "");
  if (!compact) {
    return "";
  }
  return compact.endsWith("USDT") ? compact : `${compact}USDT`;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const abs = Math.abs(value);
  if (abs >= 10_000) return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (abs >= 100) return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (abs >= 1) return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  return value.toPrecision(6);
}

function compactUsdt(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 2
  }).format(value)} USDT`;
}

function signedPct(value: number | null | undefined, digits = 3): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function mid(market: MarketSnapshot | null | undefined): number | null {
  return market ? (market.bid + market.ask) / 2 : null;
}

function displayPrice(market: MarketSnapshot | null | undefined): number | null {
  if (!market) return null;
  return market.market_type === "future" ? market.mark_price ?? mid(market) : mid(market);
}

function bookSpread(market: MarketSnapshot | null | undefined): number | null {
  const midpoint = mid(market);
  return market && midpoint ? ((market.ask - market.bid) / midpoint) * 100 : null;
}

function markIndexDeviation(market: MarketSnapshot | null | undefined): number | null {
  if (!market?.mark_price || !market.index_price) return null;
  return ((market.mark_price - market.index_price) / market.index_price) * 100;
}

function spotFutureBasis(row: InstrumentExchangeSnapshot): number | null {
  const spot = displayPrice(row.spot);
  const future = displayPrice(row.future);
  return spot && future ? ((future - spot) / spot) * 100 : null;
}

function fullTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function ageText(value: string | null | undefined): string {
  if (!value) return "-";
  const seconds = Math.max(0, dayjs().diff(dayjs.utc(value), "second"));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return fullTime(value);
}

function latestTimestamp(row: InstrumentExchangeSnapshot): string | null {
  const values = [row.spot?.timestamp, row.future?.timestamp].filter((value): value is string => Boolean(value));
  return values.sort((left, right) => dayjs.utc(right).valueOf() - dayjs.utc(left).valueOf())[0] ?? null;
}

function tone(value: number | null): string {
  if (value === null || Math.abs(value) < 0.000_001) return "neutral";
  return value > 0 ? "positive" : "negative";
}

function MarketCell({ market }: { market: MarketSnapshot | null }) {
  if (!market) {
    return <Typography.Text type="secondary">暂无数据</Typography.Text>;
  }
  return (
    <div className="instrument-market-cell">
      <Typography.Text strong>{price(displayPrice(market))}</Typography.Text>
      <span>买 {price(market.bid)} · 卖 {price(market.ask)}</span>
      <span>24h {compactUsdt(market.volume_24h_usdt)}</span>
    </div>
  );
}

function resultPriceRange(result: InstrumentLookupResult | null): { min: number; max: number } | null {
  if (!result) return null;
  const values = result.exchanges
    .flatMap((item) => [displayPrice(item.spot), displayPrice(item.future)])
    .filter((value): value is number => value !== null && Number.isFinite(value));
  return values.length ? { min: Math.min(...values), max: Math.max(...values) } : null;
}

function maxBasis(result: InstrumentLookupResult | null): { exchange: string; value: number } | null {
  if (!result) return null;
  return result.exchanges.reduce<{ exchange: string; value: number } | null>((best, item) => {
    const value = spotFutureBasis(item);
    if (value === null || (best && Math.abs(best.value) >= Math.abs(value))) return best;
    return { exchange: item.exchange, value };
  }, null);
}

function trendPriceSeries(result: SymbolSpreadQueryResult | null): PriceSeries[] {
  const first = result?.series[0];
  if (!result || !first) return [];
  const base: PriceSeries = {
    exchange: result.base_exchange,
    points: first.points.map((point) => ({ bucketAt: point.bucket_at, price: point.base_close }))
  };
  const compared = result.series.map((series) => ({
    exchange: series.exchange,
    points: series.points.map((point) => ({ bucketAt: point.bucket_at, price: point.exchange_close }))
  }));
  return [base, ...compared];
}

function nearestPoint(series: PriceSeries, targetMs: number) {
  return series.points.reduce<(typeof series.points)[number] | null>((best, point) => {
    if (!best) return point;
    return Math.abs(dayjs.utc(point.bucketAt).valueOf() - targetMs) < Math.abs(dayjs.utc(best.bucketAt).valueOf() - targetMs)
      ? point
      : best;
  }, null);
}

function InstrumentPriceChart({ result }: { result: SymbolSpreadQueryResult | null }) {
  const [hoverRatio, setHoverRatio] = useState<number | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const series = useMemo(() => trendPriceSeries(result), [result]);
  if (!series.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可对齐的价格走势" />;
  }

  const width = 1120;
  const height = 320;
  const padding = { left: 78, right: 24, top: 22, bottom: 42 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const allPoints = series.flatMap((item) => item.points);
  const startMs = Math.min(...allPoints.map((point) => dayjs.utc(point.bucketAt).valueOf()));
  const endMs = Math.max(...allPoints.map((point) => dayjs.utc(point.bucketAt).valueOf()));
  const rawMin = Math.min(...allPoints.map((point) => point.price));
  const rawMax = Math.max(...allPoints.map((point) => point.price));
  const pricePadding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.0001, Number.EPSILON);
  const minPrice = rawMin - pricePadding;
  const maxPrice = rawMax + pricePadding;
  const xAt = (value: string) => {
    const timestamp = dayjs.utc(value).valueOf();
    return padding.left + (startMs === endMs ? chartWidth / 2 : ((timestamp - startMs) / (endMs - startMs)) * chartWidth);
  };
  const yAt = (value: number) => padding.top + ((maxPrice - value) / (maxPrice - minPrice)) * chartHeight;
  const yTicks = Array.from({ length: 5 }, (_, index) => index / 4);
  const xTicks = Array.from({ length: 6 }, (_, index) => index / 5);
  const hoverMs = hoverRatio === null ? null : startMs + (endMs - startMs) * hoverRatio;
  const hovered = hoverMs === null
    ? []
    : series.map((item) => ({ exchange: item.exchange, point: nearestPoint(item, hoverMs) })).filter((item) => item.point);

  return (
    <div
      className="instrument-chart-wrap"
      ref={wrapperRef}
      onMouseMove={(event) => {
        const bounds = wrapperRef.current?.getBoundingClientRect();
        if (!bounds) return;
        const leftRatio = padding.left / width;
        const rightRatio = (width - padding.right) / width;
        const position = (event.clientX - bounds.left) / bounds.width;
        setHoverRatio(Math.max(0, Math.min(1, (position - leftRatio) / (rightRatio - leftRatio))));
      }}
      onMouseLeave={() => setHoverRatio(null)}
    >
      <svg className="instrument-price-chart" role="img" aria-label="多交易所价格走势" viewBox={`0 0 ${width} ${height}`}>
        <rect className="instrument-chart-plot" x={padding.left} y={padding.top} width={chartWidth} height={chartHeight} />
        {yTicks.map((tick) => {
          const y = padding.top + chartHeight * tick;
          const value = maxPrice - (maxPrice - minPrice) * tick;
          return (
            <g key={`y-${tick}`}>
              <line className="instrument-chart-grid" x1={padding.left} y1={y} x2={padding.left + chartWidth} y2={y} />
              <text className="instrument-chart-label" x={padding.left - 10} y={y + 4} textAnchor="end">{price(value)}</text>
            </g>
          );
        })}
        {xTicks.map((tick) => {
          const timestamp = startMs + (endMs - startMs) * tick;
          const x = padding.left + chartWidth * tick;
          return (
            <g key={`x-${tick}`}>
              <line className="instrument-chart-grid" x1={x} y1={padding.top} x2={x} y2={padding.top + chartHeight} />
              <text className="instrument-chart-label" x={x} y={height - 14} textAnchor={tick === 0 ? "start" : tick === 1 ? "end" : "middle"}>
                {dayjs.utc(timestamp).utcOffset(8).format(result && result.hours > 24 ? "MM-DD HH:mm" : "HH:mm")}
              </text>
            </g>
          );
        })}
        {series.map((item, index) => (
          <path
            key={item.exchange}
            className="instrument-chart-line"
            stroke={seriesColors[index % seriesColors.length]}
            d={item.points.map((point, pointIndex) => `${pointIndex ? "L" : "M"}${xAt(point.bucketAt).toFixed(2)},${yAt(point.price).toFixed(2)}`).join(" ")}
          />
        ))}
        {hoverRatio !== null ? (
          <line
            className="instrument-chart-crosshair"
            x1={padding.left + chartWidth * hoverRatio}
            x2={padding.left + chartWidth * hoverRatio}
            y1={padding.top}
            y2={padding.top + chartHeight}
          />
        ) : null}
      </svg>
      <div className="instrument-chart-legend">
        {series.map((item, index) => (
          <span key={item.exchange}><i style={{ backgroundColor: seriesColors[index % seriesColors.length] }} />{exchangeLabels[item.exchange] ?? item.exchange}</span>
        ))}
      </div>
      {hoverRatio !== null && hovered.length ? (
        <div className="instrument-chart-tooltip" style={{ left: `${Math.min(82, Math.max(12, hoverRatio * 100))}%` }}>
          <strong>{fullTime(hovered[0].point?.bucketAt)}</strong>
          {hovered.map((item) => <span key={item.exchange}>{exchangeLabels[item.exchange] ?? item.exchange} {price(item.point?.price)}</span>)}
        </div>
      ) : null}
    </div>
  );
}

function latestSpreadPoint(points: SymbolSpreadPoint[]): SymbolSpreadPoint | null {
  return points[points.length - 1] ?? null;
}

export function InstrumentLookupPage() {
  const startingSymbol = useMemo(initialSymbol, []);
  const [query, setQuery] = useState(startingSymbol);
  const [activeSymbol, setActiveSymbol] = useState("");
  const [result, setResult] = useState<InstrumentLookupResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [trendOpen, setTrendOpen] = useState(false);
  const [trendType, setTrendType] = useState<MarketType>("future");
  const [trendHours, setTrendHours] = useState(24);
  const [trendCache, setTrendCache] = useState<Record<string, TrendState>>({});
  const requestIdRef = useRef(0);

  const runLookup = useCallback(async (value: string, background = false) => {
    const normalized = normalizeSymbol(value);
    if (!normalized) {
      setError("请输入有效标的");
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    background ? setRefreshing(true) : setLoading(true);
    if (!background) setError("");
    try {
      const next = await lookupInstrument(normalized);
      if (requestId !== requestIdRef.current) return;
      setResult(next);
      setQuery(next.symbol);
      setActiveSymbol(next.symbol);
      if (!background) {
        setTrendOpen(false);
        setTrendCache({});
        const futureCount = next.exchanges.filter((item) => item.future).length;
        const spotCount = next.exchanges.filter((item) => item.spot).length;
        setTrendType(futureCount >= 2 || futureCount >= spotCount ? "future" : "spot");
      }
      window.localStorage.setItem(LAST_SYMBOL_KEY, next.symbol);
      const url = new URL(window.location.href);
      url.searchParams.set("symbol", next.symbol);
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    } catch (exc) {
      if (requestId === requestIdRef.current) setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void runLookup(startingSymbol);
  }, [runLookup, startingSymbol]);

  useEffect(() => {
    if (!autoRefresh || !activeSymbol) return undefined;
    const refresh = () => {
      if (document.visibilityState !== "hidden") void runLookup(activeSymbol, true);
    };
    const timer = window.setInterval(refresh, AUTO_REFRESH_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [activeSymbol, autoRefresh, runLookup]);

  const availableTrendExchanges = useMemo(
    () => result?.exchanges
      .filter((item) => Boolean(item[trendType]) && chartExchanges[trendType].has(item.exchange))
      .map((item) => item.exchange) ?? [],
    [result, trendType]
  );
  const trendKey = result ? `${result.symbol}:${trendType}:${trendHours}` : "";
  const trendState = trendCache[trendKey] ?? { loading: false, error: "", result: null };

  useEffect(() => {
    if (
      !trendOpen
      || !result
      || !trendKey
      || trendCache[trendKey]?.loading
      || trendCache[trendKey]?.result
      || trendCache[trendKey]?.error
    ) return;
    if (availableTrendExchanges.length < 2) {
      setTrendCache((current) => ({
        ...current,
        [trendKey]: { loading: false, error: "至少需要两个支持历史查询的交易所有该市场数据", result: null }
      }));
      return;
    }
    const baseExchange = availableTrendExchanges.includes("binance") ? "binance" : availableTrendExchanges[0];
    setTrendCache((current) => ({
      ...current,
      [trendKey]: { loading: true, error: "", result: null }
    }));
    void querySymbolExchangeSpreads({
      symbol: result.symbol,
      market_type: trendType,
      base_exchange: baseExchange,
      exchanges: availableTrendExchanges,
      hours: trendHours,
      interval_seconds: resolveHistoryIntervalSeconds(trendHours, trendHours <= 24 ? 60 : trendHours <= 168 ? 300 : 900),
      include_current: true
    }).then((next) => {
      setTrendCache((current) => ({
        ...current,
        [trendKey]: { loading: false, error: "", result: next }
      }));
    }).catch((exc) => {
      setTrendCache((current) => ({
        ...current,
        [trendKey]: { loading: false, error: exc instanceof Error ? exc.message : String(exc), result: null }
      }));
    });
  }, [availableTrendExchanges, result, trendCache, trendHours, trendKey, trendOpen, trendType]);

  const priceRange = resultPriceRange(result);
  const strongestBasis = maxBasis(result);
  const marketCounts = result?.exchanges.reduce(
    (counts, item) => ({ spot: counts.spot + Number(Boolean(item.spot)), future: counts.future + Number(Boolean(item.future)) }),
    { spot: 0, future: 0 }
  ) ?? { spot: 0, future: 0 };

  const columns = useMemo<ColumnsType<InstrumentExchangeSnapshot>>(() => [
    {
      title: "交易所",
      dataIndex: "exchange",
      fixed: "left",
      width: 132,
      render: (value: string, row) => (
        <Space size={6}>
          <Typography.Text strong>{exchangeLabels[value] ?? value}</Typography.Text>
          {row.error ? <Tooltip title={row.error}><WarningOutlined className="instrument-exchange-warning" /></Tooltip> : null}
        </Space>
      )
    },
    {
      title: "市场",
      key: "markets",
      width: 126,
      render: (_, row) => row.spot || row.future ? (
        <Space size={[4, 4]} wrap>
          {row.spot ? <Tag color="green">现货</Tag> : null}
          {row.future ? <Tag color="blue">永续</Tag> : null}
        </Space>
      ) : <Tag>暂无</Tag>
    },
    { title: "现货", key: "spot", width: 230, render: (_, row) => <MarketCell market={row.spot} /> },
    { title: "永续", key: "future", width: 230, render: (_, row) => <MarketCell market={row.future} /> },
    {
      title: "现永基差",
      key: "basis",
      width: 116,
      align: "right",
      render: (_, row) => {
        const value = spotFutureBasis(row);
        return <span className={`instrument-rate instrument-rate-${tone(value)}`}>{signedPct(value)}</span>;
      }
    },
    {
      title: "资金费率",
      key: "funding",
      width: 155,
      align: "right",
      render: (_, row) => row.future ? (
        <div className="instrument-funding-cell">
          <span className={`instrument-rate instrument-rate-${tone(row.future.funding_rate_pct ?? null)}`}>{signedPct(row.future.funding_rate_pct, 4)}</span>
          <span>预测 {signedPct(row.future.funding_next_rate_pct, 4)}</span>
          <span>{row.future.funding_interval_hours ? `${row.future.funding_interval_hours}h 一次` : "周期 -"}</span>
        </div>
      ) : "-"
    },
    {
      title: "盘口 / 标记偏离",
      key: "quality",
      width: 160,
      align: "right",
      render: (_, row) => (
        <div className="instrument-funding-cell">
          <span>现货 {signedPct(bookSpread(row.spot), 4)}</span>
          <span>永续 {signedPct(bookSpread(row.future), 4)}</span>
          <span>标记 {signedPct(markIndexDeviation(row.future), 4)}</span>
        </div>
      )
    },
    {
      title: "原始代码",
      key: "raw",
      width: 145,
      render: (_, row) => (
        <div className="instrument-funding-cell">
          <span>{row.spot ? `现 ${row.spot.raw_symbol}` : "现 -"}</span>
          <span>{row.future ? `永 ${row.future.raw_symbol}` : "永 -"}</span>
        </div>
      )
    },
    {
      title: "更新",
      key: "updated",
      width: 110,
      render: (_, row) => <span>{ageText(latestTimestamp(row))}</span>
    }
  ], []);

  const trendColumns = useMemo<ColumnsType<SymbolSpreadQueryResult["series"][number]>>(() => [
    { title: "交易所", dataIndex: "exchange", render: (value: string) => exchangeLabels[value] ?? value },
    {
      title: "最新价差",
      key: "current",
      align: "right",
      render: (_, series) => signedPct(series.current?.spread_pct ?? latestSpreadPoint(series.points)?.spread_pct)
    },
    { title: "最高", dataIndex: ["spread_pct", "max"], align: "right", render: (value) => signedPct(value) },
    { title: "最低", dataIndex: ["spread_pct", "min"], align: "right", render: (value) => signedPct(value) },
    { title: "均值", dataIndex: ["spread_pct", "mean"], align: "right", render: (value) => signedPct(value) },
    { title: "点数", dataIndex: "point_count", align: "right", width: 90 }
  ], []);

  return (
    <div className="page instrument-lookup-page">
      <div className="instrument-heading">
        <div>
          <Typography.Title level={2}>标的查询</Typography.Title>
          <Typography.Text type="secondary">{result ? `${result.base} / ${result.quote}` : "跨交易所市场总览"}</Typography.Text>
        </div>
        <div className="instrument-search-tools">
          <Input
            aria-label="查询标的"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onPressEnter={() => void runLookup(query)}
            prefix={<SearchOutlined />}
            placeholder="BTC 或 BTCUSDT"
            disabled={loading}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => void runLookup(query)}>查询</Button>
          <Tooltip title="立即刷新"><Button aria-label="立即刷新" icon={<ReloadOutlined spin={refreshing} />} disabled={!activeSymbol || loading} onClick={() => void runLookup(activeSymbol, true)} /></Tooltip>
          <Space size={6}><Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} /><Typography.Text type="secondary">自动刷新</Typography.Text></Space>
        </div>
      </div>

      {error ? <Alert type="error" showIcon message={error} /> : null}
      {result && result.exchange_count === 0 ? <Alert type="warning" showIcon message={`当前聚合行情中没有 ${result.symbol} 的精确匹配数据`} /> : null}

      <section className="instrument-summary-band">
        <div><span>规范标的</span><strong>{result?.symbol ?? "-"}</strong></div>
        <div><span>覆盖交易所</span><strong>{result ? `${result.exchange_count} / ${result.exchanges.length}` : "-"}</strong></div>
        <div><span>市场</span><strong>{result ? `${marketCounts.spot} 现货 · ${marketCounts.future} 永续` : "-"}</strong></div>
        <div><span>价格区间</span><strong>{priceRange ? `${price(priceRange.min)} - ${price(priceRange.max)}` : "-"}</strong></div>
        <div><span>最大现永基差</span><strong className={`instrument-rate-${tone(strongestBasis?.value ?? null)}`}>{strongestBasis ? `${signedPct(strongestBasis.value)} · ${exchangeLabels[strongestBasis.exchange]}` : "-"}</strong></div>
        <div><span>快照时间</span><strong>{fullTime(result?.observed_at)}</strong></div>
      </section>

      <section className="instrument-market-table">
        <div className="instrument-section-head">
          <Typography.Title level={4}>交易所市场</Typography.Title>
          <Tag>{result?.market_count ?? 0} 个市场</Tag>
        </div>
        <Table<InstrumentExchangeSnapshot>
          rowKey="exchange"
          columns={columns}
          dataSource={result?.exchanges ?? []}
          loading={loading}
          pagination={false}
          size="small"
          scroll={{ x: 1500 }}
        />
      </section>

      {result && result.exchange_count > 0 ? (
        <section className={`instrument-trend-section ${trendOpen ? "instrument-trend-open" : ""}`}>
          <div className="instrument-section-head">
            <Space size={10}>
              <LineChartOutlined />
              <Typography.Title level={4}>走势与价差</Typography.Title>
            </Space>
            <Button
              icon={trendOpen ? <DownOutlined /> : <RightOutlined />}
              onClick={() => setTrendOpen((value) => !value)}
            >
              {trendOpen ? "收起" : "展开"}
            </Button>
          </div>
          {trendOpen ? (
            <div className="instrument-trend-content">
              <div className="instrument-trend-toolbar">
                <Segmented<MarketType>
                  value={trendType}
                  options={[
                    { label: `现货 ${marketCounts.spot}`, value: "spot", disabled: marketCounts.spot === 0 },
                    { label: `永续 ${marketCounts.future}`, value: "future", disabled: marketCounts.future === 0 }
                  ]}
                  onChange={setTrendType}
                />
                <Segmented<number>
                  value={trendHours}
                  options={[{ label: "24 小时", value: 24 }, { label: "7 天", value: 168 }, { label: "30 天", value: 720 }]}
                  onChange={setTrendHours}
                />
                <Tooltip title="刷新走势">
                  <Button
                    aria-label="刷新走势"
                    icon={<ReloadOutlined />}
                    disabled={trendState.loading}
                    onClick={() => setTrendCache((current) => {
                      const next = { ...current };
                      delete next[trendKey];
                      return next;
                    })}
                  />
                </Tooltip>
              </div>
              {trendState.error ? <Alert type="warning" showIcon message={trendState.error} /> : null}
              {trendState.result?.warnings.length ? <Alert type="warning" showIcon message={trendState.result.warnings.join("；")} /> : null}
              {trendState.loading ? <div className="instrument-trend-loading"><Spin /></div> : <InstrumentPriceChart result={trendState.result} />}
              {trendState.result ? (
                <Table
                  rowKey="exchange"
                  className="instrument-spread-table"
                  columns={trendColumns}
                  dataSource={trendState.result.series}
                  pagination={false}
                  size="small"
                  scroll={{ x: 680 }}
                />
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
