import {
  DownOutlined,
  LineChartOutlined,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined,
  SearchOutlined,
  WarningOutlined
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Segmented,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message
} from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createInstrumentAstroCard,
  lookupInstrument,
  previewInstrumentAstroPair,
  querySymbolExchangeSpreads
} from "../api/client";
import type {
  AstroActionResult,
  AstroCardCreateRequest,
  AstroInstrumentRouteRequest,
  AstroPairPlan,
  InstrumentExchangeSnapshot,
  InstrumentLookupResult,
  InstrumentSpreadComparison,
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

type AstroSizingFormValues = Required<Omit<AstroCardCreateRequest, "save_as_default">> & {
  save_as_default: boolean;
};

type SpreadTypeFilter = "FF" | "SF" | "SS" | "reverse_sf";

const spreadTypeFilterOptions: Array<{ label: string; value: SpreadTypeFilter }> = [
  { label: "FF", value: "FF" },
  { label: "SF", value: "SF" },
  { label: "SS", value: "SS" },
  { label: "反向 SF", value: "reverse_sf" }
];

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

function marketTypeLabel(value: MarketType): string {
  return value === "spot" ? "现货" : "永续";
}

function spreadTypeLabel(value: InstrumentSpreadComparison["opportunity_type"]): string {
  return value ?? "反向 SF";
}

function spreadTypeOrder(value: InstrumentSpreadComparison["opportunity_type"]): number {
  if (value === "FF") return 0;
  if (value === "SF") return 1;
  if (value === "SS") return 2;
  return 3;
}

function spreadTypeFilter(value: InstrumentSpreadComparison["opportunity_type"]): SpreadTypeFilter {
  return value ?? "reverse_sf";
}

function exchangeNameOrder(left: string, right: string): number {
  const leftName = exchangeLabels[left] ?? left;
  const rightName = exchangeLabels[right] ?? right;
  return leftName.localeCompare(rightName, "en", { sensitivity: "base" });
}

type PairSpreadLegRoute = {
  symbol: string;
  dex: string | null;
};

function pairSpreadLegRoute(
  result: InstrumentLookupResult,
  exchange: string,
  marketType: MarketType
): PairSpreadLegRoute {
  const exchangeSnapshot = result.exchanges.find((item) => item.exchange === exchange);
  const market = marketType === "spot" ? exchangeSnapshot?.spot : exchangeSnapshot?.future;
  const aliasSymbol = market?.symbol_alias_original_symbol?.trim() || "";
  const rawSymbol = market?.raw_symbol.trim() || "";
  if (exchange === "hyperliquid" && marketType === "future") {
    const separatorIndex = rawSymbol.indexOf(":");
    if (separatorIndex > 0) {
      return {
        symbol: rawSymbol.slice(separatorIndex + 1).trim() || aliasSymbol || result.symbol,
        dex: rawSymbol.slice(0, separatorIndex).trim().toLowerCase() || "main"
      };
    }
    return {
      symbol: rawSymbol || aliasSymbol || result.symbol,
      dex: "main"
    };
  }
  return {
    symbol: aliasSymbol || rawSymbol || result.symbol,
    dex: null
  };
}

function pairSpreadBlocker(spread: InstrumentSpreadComparison): string | null {
  const unsupported = [
    { exchange: spread.buy_exchange, marketType: spread.buy_market_type },
    { exchange: spread.sell_exchange, marketType: spread.sell_market_type }
  ].find((leg) => !chartExchanges[leg.marketType].has(leg.exchange));
  if (!unsupported) return null;
  const exchange = exchangeLabels[unsupported.exchange] ?? unsupported.exchange;
  return `价差查询暂不支持 ${exchange} ${marketTypeLabel(unsupported.marketType)}`;
}

function astroRoute(symbol: string, spread: InstrumentSpreadComparison): AstroInstrumentRouteRequest {
  return {
    symbol,
    buy_exchange: spread.buy_exchange,
    buy_market_type: spread.buy_market_type,
    sell_exchange: spread.sell_exchange,
    sell_market_type: spread.sell_market_type
  };
}

function planNumber(plan: AstroPairPlan, field: string, fallback: number): number {
  const value = Number(plan.pair?.[field]);
  return Number.isFinite(value) ? value : fallback;
}

function mid(market: MarketSnapshot | null | undefined): number | null {
  return market ? (market.bid + market.ask) / 2 : null;
}

function displayPrice(market: MarketSnapshot | null | undefined): number | null {
  return mid(market);
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
      <div className="instrument-market-price">
        <span>盘口中价</span>
        <Typography.Text strong>{price(displayPrice(market))}</Typography.Text>
      </div>
      <span>买 {price(market.bid)} · 卖 {price(market.ask)}</span>
      {market.market_type === "future" ? <span>标记价 {price(market.mark_price)}</span> : null}
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
  const [astroSizingForm] = Form.useForm<AstroSizingFormValues>();
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
  const [hiddenSpreadTypes, setHiddenSpreadTypes] = useState<SpreadTypeFilter[]>([
    "SS",
    "reverse_sf"
  ]);
  const [astroSymbol, setAstroSymbol] = useState("");
  const [astroSpread, setAstroSpread] = useState<InstrumentSpreadComparison | null>(null);
  const [astroPlan, setAstroPlan] = useState<AstroPairPlan | null>(null);
  const [astroPreviewLoading, setAstroPreviewLoading] = useState(false);
  const [astroPreviewError, setAstroPreviewError] = useState("");
  const [astroSubmitLoading, setAstroSubmitLoading] = useState(false);
  const [astroSubmitResult, setAstroSubmitResult] = useState<AstroActionResult | null>(null);
  const [astroSubmitError, setAstroSubmitError] = useState("");
  const requestIdRef = useRef(0);
  const astroPreviewRequestIdRef = useRef(0);
  const astroSubmitRequestIdRef = useRef(0);

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
  const instrumentSpreads = result?.spreads ?? [];
  const hiddenSpreadTypeSet = new Set(hiddenSpreadTypes);
  const visibleInstrumentSpreads = instrumentSpreads.filter(
    (spread) => !hiddenSpreadTypeSet.has(spreadTypeFilter(spread.opportunity_type))
  );

  const setSpreadTypeHidden = (type: SpreadTypeFilter, hidden: boolean) => {
    setHiddenSpreadTypes((current) => hidden
      ? current.includes(type) ? current : [...current, type]
      : current.filter((item) => item !== type));
  };

  const openPairSpread = (spread: InstrumentSpreadComparison) => {
    if (!result) return;
    const url = new URL(window.location.href);
    url.searchParams.set("page", "pair-monitor");
    url.searchParams.delete("symbol");
    const legs = [
      pairSpreadLegRoute(result, spread.buy_exchange, spread.buy_market_type),
      pairSpreadLegRoute(result, spread.sell_exchange, spread.sell_market_type)
    ];
    [
      { exchange: spread.buy_exchange, marketType: spread.buy_market_type },
      { exchange: spread.sell_exchange, marketType: spread.sell_market_type }
    ].forEach((leg, index) => {
      const key = index + 1;
      const route = legs[index];
      url.searchParams.set(`leg${key}_exchange`, leg.exchange);
      url.searchParams.set(`leg${key}_market_type`, leg.marketType);
      url.searchParams.set(`leg${key}_symbol`, route.symbol);
      if (route.dex) {
        url.searchParams.set(`leg${key}_dex`, route.dex);
      } else {
        url.searchParams.delete(`leg${key}_dex`);
      }
    });
    url.searchParams.set("leg2_multiplier", "1");
    url.searchParams.set("hours", "4");
    url.searchParams.set("interval_seconds", "60");
    url.searchParams.delete("interval_minutes");
    window.history.pushState({}, "", `${url.pathname}${url.search}${url.hash}`);
    window.dispatchEvent(new Event("taoli1:navigate"));
  };

  const closeAstroPreview = () => {
    if (astroSubmitLoading) return;
    astroPreviewRequestIdRef.current += 1;
    astroSubmitRequestIdRef.current += 1;
    setAstroSymbol("");
    setAstroSpread(null);
    setAstroPlan(null);
    setAstroPreviewLoading(false);
    setAstroPreviewError("");
    setAstroSubmitLoading(false);
    setAstroSubmitResult(null);
    setAstroSubmitError("");
    astroSizingForm.resetFields();
  };

  const openAstroPreview = async (spread: InstrumentSpreadComparison) => {
    if (!result || astroSubmitLoading) return;
    const requestId = ++astroPreviewRequestIdRef.current;
    astroSubmitRequestIdRef.current += 1;
    const symbol = result.symbol;
    setAstroSymbol(symbol);
    setAstroSpread(spread);
    setAstroPlan(null);
    setAstroPreviewError("");
    setAstroSubmitResult(null);
    setAstroSubmitError("");
    setAstroPreviewLoading(true);
    try {
      const plan = await previewInstrumentAstroPair(astroRoute(symbol, spread));
      if (requestId !== astroPreviewRequestIdRef.current) return;
      setAstroPlan(plan);
      if (plan.pair) {
        astroSizingForm.setFieldsValue({
          max_trade_usdt: planNumber(plan, "maxTradeUSDT", 10),
          leverage: planNumber(plan, "leverage", 1),
          min_notional: planNumber(plan, "minNotional", 10),
          max_notional: planNumber(plan, "maxNotional", 10),
          open_enabled: plan.pair.status === true && plan.pair.disableOpen !== true,
          save_as_default: false
        });
      }
    } catch (exc) {
      if (requestId !== astroPreviewRequestIdRef.current) return;
      setAstroPreviewError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (requestId === astroPreviewRequestIdRef.current) {
        setAstroPreviewLoading(false);
      }
    }
  };

  const submitAstroCard = async () => {
    if (
      !astroSymbol
      || !astroSpread
      || !astroPlan?.can_submit
      || astroPlan.source_open_spread_pct === null
    ) return;
    const requestId = ++astroSubmitRequestIdRef.current;
    setAstroSubmitLoading(true);
    setAstroSubmitResult(null);
    setAstroSubmitError("");
    try {
      const sizing = await astroSizingForm.validateFields();
      const next = await createInstrumentAstroCard(
        astroRoute(astroSymbol, astroSpread),
        astroPlan.source_open_spread_pct,
        sizing
      );
      if (requestId !== astroSubmitRequestIdRef.current) return;
      setAstroSubmitResult(next);
      if (next.status === "created" || next.status === "updated") {
        message.success(next.message);
      } else if (next.status === "failed") {
        message.error(next.message);
      } else {
        message.warning(next.message);
      }
    } catch (exc) {
      if (requestId !== astroSubmitRequestIdRef.current) return;
      const text = exc instanceof Error ? exc.message : String(exc);
      setAstroSubmitError(text);
      message.error(text);
    } finally {
      if (requestId === astroSubmitRequestIdRef.current) {
        setAstroSubmitLoading(false);
      }
    }
  };

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

  const spreadColumns: ColumnsType<InstrumentSpreadComparison> = [
    {
      title: "差价类型",
      dataIndex: "opportunity_type",
      width: 108,
      sorter: (left, right) => (
        spreadTypeOrder(left.opportunity_type) - spreadTypeOrder(right.opportunity_type)
      ),
      render: (value: InstrumentSpreadComparison["opportunity_type"]) => (
        <Tag>{spreadTypeLabel(value)}</Tag>
      )
    },
    {
      title: "买入市场",
      key: "buy_market",
      width: 170,
      sorter: (left, right) => exchangeNameOrder(left.buy_exchange, right.buy_exchange),
      render: (_, spread) => (
        <Space size={6}>
          <Typography.Text strong>{exchangeLabels[spread.buy_exchange] ?? spread.buy_exchange}</Typography.Text>
          <Tag>{marketTypeLabel(spread.buy_market_type)}</Tag>
        </Space>
      )
    },
    {
      title: "买入 Ask",
      dataIndex: "buy_ask",
      width: 130,
      align: "right",
      render: (value: number) => price(value)
    },
    {
      title: "卖出市场",
      key: "sell_market",
      width: 170,
      sorter: (left, right) => exchangeNameOrder(left.sell_exchange, right.sell_exchange),
      render: (_, spread) => (
        <Space size={6}>
          <Typography.Text strong>{exchangeLabels[spread.sell_exchange] ?? spread.sell_exchange}</Typography.Text>
          <Tag>{marketTypeLabel(spread.sell_market_type)}</Tag>
        </Space>
      )
    },
    {
      title: "卖出 Bid",
      dataIndex: "sell_bid",
      width: 130,
      align: "right",
      render: (value: number) => price(value)
    },
    {
      title: "可成交差价",
      dataIndex: "executable_spread_pct",
      width: 126,
      align: "right",
      sorter: (left, right) => left.executable_spread_pct - right.executable_spread_pct,
      defaultSortOrder: "descend",
      render: (value: number) => (
        <Typography.Text strong className={`instrument-rate instrument-rate-${tone(value)}`}>
          {signedPct(value)}
        </Typography.Text>
      )
    },
    {
      title: "中价差",
      dataIndex: "mid_spread_pct",
      width: 110,
      align: "right",
      render: (value: number) => signedPct(value)
    },
    {
      title: "价差额",
      dataIndex: "price_difference",
      width: 110,
      align: "right",
      render: (value: number) => price(value)
    },
    {
      title: "操作",
      key: "action",
      fixed: "right",
      width: 132,
      render: (_, spread) => {
        const blocker = pairSpreadBlocker(spread);
        return (
          <Space size={4}>
            <Tooltip title={blocker ?? "在价差查询查看走势图"}>
              <span>
                <Button
                  aria-label={`价差查询 ${result?.symbol ?? ""} ${spread.id}`}
                  size="small"
                  icon={<LineChartOutlined />}
                  disabled={Boolean(blocker)}
                  onClick={() => openPairSpread(spread)}
                />
              </span>
            </Tooltip>
            {spread.astro_supported ? (
              <Button
                size="small"
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => void openAstroPreview(spread)}
              >
                建卡
              </Button>
            ) : (
              <Tooltip title={spread.astro_blocker}>
                <span><Button size="small" icon={<PlusOutlined />} disabled>建卡</Button></span>
              </Tooltip>
            )}
          </Space>
        );
      }
    }
  ];

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

      {instrumentSpreads.length > 0 ? (
        <section className="instrument-market-table">
          <div className="instrument-section-head instrument-spread-head">
            <div>
              <Typography.Title level={4}>跨市场差价</Typography.Title>
              <Typography.Text type="secondary">按买入 Ask、卖出 Bid 计算，每组市场保留较优方向</Typography.Text>
            </div>
            <Space size={[10, 4]} wrap className="instrument-spread-filters">
              <Typography.Text type="secondary">屏蔽类型</Typography.Text>
              {spreadTypeFilterOptions.map((option) => (
                <Checkbox
                  key={option.value}
                  checked={hiddenSpreadTypeSet.has(option.value)}
                  onChange={(event) => setSpreadTypeHidden(option.value, event.target.checked)}
                >
                  {option.label}
                </Checkbox>
              ))}
              <Tag>{visibleInstrumentSpreads.length} / {instrumentSpreads.length} 组</Tag>
            </Space>
          </div>
          <Table<InstrumentSpreadComparison>
            rowKey="id"
            columns={spreadColumns}
            dataSource={visibleInstrumentSpreads}
            pagination={false}
            size="small"
            scroll={{ x: 1160 }}
          />
        </section>
      ) : null}

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

      <Modal
        open={astroSpread !== null}
        title="创建 Astro 卡片"
        width={760}
        onCancel={closeAstroPreview}
        closable={!astroSubmitLoading}
        keyboard={!astroSubmitLoading}
        maskClosable={!astroSubmitLoading}
        footer={[
          <Button key="close" disabled={astroSubmitLoading} onClick={closeAstroPreview}>关闭</Button>,
          <Button
            key="submit"
            type="primary"
            loading={astroSubmitLoading}
            disabled={astroPreviewLoading || astroSubmitLoading || !astroPlan?.can_submit}
            onClick={() => void submitAstroCard()}
          >
            确认创建
          </Button>
        ]}
        destroyOnHidden
      >
        {astroSpread && astroSymbol ? (
          <Space direction="vertical" size={12} className="astro-preview-panel">
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="标的">{astroSymbol}</Descriptions.Item>
              <Descriptions.Item label="卡片名称">{String(astroPlan?.pair?.name ?? "-")}</Descriptions.Item>
              <Descriptions.Item label="卡片类型">{String(astroPlan?.pair?.type ?? astroSpread.opportunity_type ?? "-")}</Descriptions.Item>
              <Descriptions.Item label="Astro 路线">
                {String(astroPlan?.pair?.buyEx ?? "-")} → {String(astroPlan?.pair?.sellEx ?? "-")}
              </Descriptions.Item>
              <Descriptions.Item label="买入">
                {exchangeLabels[astroSpread.buy_exchange] ?? astroSpread.buy_exchange} · {marketTypeLabel(astroSpread.buy_market_type)} · Ask {price(astroSpread.buy_ask)}
              </Descriptions.Item>
              <Descriptions.Item label="卖出">
                {exchangeLabels[astroSpread.sell_exchange] ?? astroSpread.sell_exchange} · {marketTypeLabel(astroSpread.sell_market_type)} · Bid {price(astroSpread.sell_bid)}
              </Descriptions.Item>
              <Descriptions.Item label="预览可成交差价">{signedPct(astroPlan?.source_open_spread_pct)}</Descriptions.Item>
              <Descriptions.Item label="Astro 开仓阈值">{String(astroPlan?.pair?.openPosition ?? "-")}</Descriptions.Item>
              <Descriptions.Item label="允许提交">{astroPlan?.can_submit ? "是" : "否"}</Descriptions.Item>
            </Descriptions>
            {astroPreviewLoading ? <Alert type="info" showIcon message="正在按最新行情生成卡片预览" /> : null}
            {astroPreviewError ? <Alert type="error" showIcon message={astroPreviewError} /> : null}
            {astroSubmitError ? <Alert type="error" showIcon message={astroSubmitError} /> : null}
            {astroSubmitResult ? (
              <Alert
                type={astroSubmitResult.status === "created" || astroSubmitResult.status === "updated" ? "success" : astroSubmitResult.status === "failed" ? "error" : "warning"}
                showIcon
                message={astroSubmitResult.message}
              />
            ) : null}
            {astroSubmitResult?.warnings?.length ? (
              <Alert type="warning" showIcon message={astroSubmitResult.warnings.join("；")} />
            ) : null}
            {astroPlan?.blockers.length ? <Alert type="error" showIcon message="当前不能创建" description={astroPlan.blockers.join("；")} /> : null}
            {astroPlan?.warnings.length ? <Alert type="warning" showIcon message={astroPlan.warnings.join("；")} /> : null}
            {astroPlan?.pair ? (
              <Form form={astroSizingForm} layout="vertical" className="astro-sizing-form">
                <div className="form-grid">
                  <Form.Item label="单笔金额 USDT" name="max_trade_usdt" rules={[{ required: true }]}>
                    <InputNumber min={0.01} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="杠杆" name="leverage" rules={[{ required: true }]}>
                    <InputNumber min={1} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="最小名义金额 USDT" name="min_notional" rules={[{ required: true }]}>
                    <InputNumber min={0} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="最大名义金额 USDT" name="max_notional" rules={[{ required: true }]}>
                    <InputNumber min={0.01} step={1} className="wide-input" />
                  </Form.Item>
                  <Form.Item label="创建后允许开仓" name="open_enabled" valuePropName="checked">
                    <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                  </Form.Item>
                </div>
                <Form.Item name="save_as_default" valuePropName="checked">
                  <Checkbox>保存为全局建卡默认值</Checkbox>
                </Form.Item>
              </Form>
            ) : null}
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
