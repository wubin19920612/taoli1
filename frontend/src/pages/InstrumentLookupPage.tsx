import {
  BellOutlined,
  CloseOutlined,
  DownOutlined,
  LineChartOutlined,
  PlusOutlined,
  PushpinOutlined,
  ReloadOutlined,
  RightOutlined,
  SaveOutlined,
  SearchOutlined,
  SwapOutlined
} from "@ant-design/icons";
import {
  Alert,
  AutoComplete,
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
  createTradeAvailabilityWatch,
  createInstrumentAstroCard,
  deleteTradeAvailabilityWatch,
  getTradeAvailability,
  listTradeAvailabilityWatches,
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
  InstrumentMarketCandidate,
  InstrumentSpreadComparison,
  MarketTradeAvailability,
  MarketSnapshot,
  MarketType,
  SymbolSpreadPoint,
  SymbolSpreadQueryResult,
  TradeActionStatus,
  TradeAvailabilityResult,
  TradeAvailabilityWatch
} from "../api/types";
import { resolveHistoryIntervalSeconds } from "../constants/queryLimits";
import { addFloatingWatchSymbol } from "../utils/floatingWatch";

dayjs.extend(utc);

const LAST_SYMBOL_KEY = "taoli1.instrumentLookup.lastSymbol.v1";
const SAVED_SYMBOLS_KEY = "taoli1.instrumentLookup.savedSymbols.v1";
const MAX_SAVED_SYMBOLS = 30;
const AUTO_REFRESH_MS = 10_000;
const exchangeLabels: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "Hyperliquid",
  lighter: "Lighter",
  "rh-lighter": "RH Lighter",
  okx: "OKX"
};
const chartExchanges: Record<MarketType, Set<string>> = {
  spot: new Set(["binance", "okx", "bybit", "gate", "bitget", "lighter", "rh-lighter"]),
  future: new Set(["binance", "okx", "bybit", "gate", "bitget", "aster", "hyperliquid", "lighter", "rh-lighter"])
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
  { label: "FF 合约-合约", value: "FF" },
  { label: "SF 现货-合约", value: "SF" },
  { label: "SS 现货-现货", value: "SS" },
  { label: "反向 SF 合约-现货", value: "reverse_sf" }
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

function readSavedSymbols(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const stored = JSON.parse(window.localStorage.getItem(SAVED_SYMBOLS_KEY) ?? "[]");
    if (!Array.isArray(stored)) return [];
    return [...new Set(stored
      .filter((value): value is string => typeof value === "string")
      .map(normalizeSymbol)
      .filter((value) => /^[A-Z0-9]+USDT$/.test(value) && value.length <= 50))].slice(0, MAX_SAVED_SYMBOLS);
  } catch {
    return [];
  }
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

function MarketTypeTag({ value }: { value: MarketType }) {
  const isSpot = value === "spot";
  return (
    <Tag className={`instrument-market-type-tag instrument-market-type-tag--${isSpot ? "spot" : "future"}`}>
      {isSpot ? "现货" : "永续合约"}
    </Tag>
  );
}

function SpreadTypeTag({ value }: { value: InstrumentSpreadComparison["opportunity_type"] }) {
  const presentation = value === "FF"
    ? { code: "FF", route: "合约 → 合约", tone: "ff" }
    : value === "SF"
      ? { code: "SF", route: "现货 → 合约", tone: "sf" }
      : value === "SS"
        ? { code: "SS", route: "现货 → 现货", tone: "ss" }
        : { code: "反向 SF", route: "合约 → 现货", tone: "reverse-sf" };
  return (
    <Tag className={`instrument-spread-type-tag instrument-spread-type-tag--${presentation.tone}`}>
      <strong>{presentation.code}</strong>
      <span>{presentation.route}</span>
    </Tag>
  );
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

function exactMarkets(result: InstrumentLookupResult | null): InstrumentMarketCandidate[] {
  if (!result) return [];
  if (Array.isArray(result.markets) && result.markets.length > 0) return result.markets;
  return result.exchanges.flatMap((snapshot) => [snapshot.spot, snapshot.future]
    .filter((market): market is MarketSnapshot => market !== null)
    .map((market) => ({
      ...market,
      data_status: "live" as const,
      age_seconds: Math.max(0, dayjs().diff(dayjs.utc(market.timestamp), "second")),
      stale_after_seconds: 30,
      error: snapshot.error
    })));
}

function marketDex(market: MarketSnapshot): string | null {
  if (market.dex) return market.dex;
  if (market.exchange !== "hyperliquid" || market.market_type !== "future") return null;
  const separator = market.raw_symbol.indexOf(":");
  return separator > 0 ? market.raw_symbol.slice(0, separator).toLowerCase() : "main";
}

function exactMarketKey(market: Pick<MarketSnapshot, "exchange" | "market_type" | "raw_symbol" | "dex">): string | null {
  const rawSymbol = market.raw_symbol?.trim();
  if (!rawSymbol || !market.exchange || !market.market_type) return null;
  const prefix = market.exchange.toLowerCase() === "hyperliquid" && market.market_type === "future"
    ? rawSymbol.includes(":") ? rawSymbol.split(":", 1)[0].toLowerCase() : "main"
    : null;
  const declaredDex = market.dex?.trim().toLowerCase() || null;
  if (prefix && declaredDex && prefix !== declaredDex) return null;
  return JSON.stringify([market.exchange.toLowerCase(), market.market_type, rawSymbol.toUpperCase(), declaredDex ?? prefix ?? ""]);
}

function associateMarkets(quotes: InstrumentMarketCandidate[], diagnostics: MarketTradeAvailability[]) {
  const counts = (keys: Array<string | null>) => keys.reduce((result, key) => {
    if (key) result.set(key, (result.get(key) ?? 0) + 1);
    return result;
  }, new Map<string, number>());
  const quoteCounts = counts(quotes.map(exactMarketKey));
  const diagnosticCounts = counts(diagnostics.map(exactMarketKey));
  const uniqueDiagnostics = new Map(diagnostics.map((market) => [exactMarketKey(market), market]));
  const rows: Array<{ quote: InstrumentMarketCandidate | null; diagnostic: MarketTradeAvailability | null }> = quotes.map((quote) => {
    const key = exactMarketKey(quote);
    return {
      quote,
      diagnostic: key && quoteCounts.get(key) === 1 && diagnosticCounts.get(key) === 1
        ? uniqueDiagnostics.get(key) ?? null : null
    };
  });
  for (const diagnostic of diagnostics) {
    const key = exactMarketKey(diagnostic);
    if (!key || quoteCounts.get(key) !== 1 || diagnosticCounts.get(key) !== 1) {
      rows.push({ quote: null, diagnostic });
    }
  }
  return rows;
}

function diagnosticFreshness(market: MarketTradeAvailability, quote: InstrumentMarketCandidate | null): string | null {
  const observed = Date.parse(market.observed_at);
  const quoteTime = quote ? Date.parse(quote.upstream_timestamp ?? quote.timestamp) : NaN;
  const cutoff = (quote?.stale_after_seconds ?? 30) * 1000;
  if (!Number.isFinite(observed)) return "诊断时间未知";
  if (Date.now() - observed > cutoff) return "诊断已过期";
  if (Number.isFinite(quoteTime) && quoteTime - observed > cutoff) return "诊断旧于行情";
  if (!market.orderbook_updated_at || !Number.isFinite(Date.parse(market.orderbook_updated_at)) || Date.now() - Date.parse(market.orderbook_updated_at) > cutoff) return "盘口过期或缺失";
  if (!Number.isFinite(Date.parse(market.market_data_updated_at)) || Date.now() - Date.parse(market.market_data_updated_at) > cutoff) return "诊断行情已过期";
  return null;
}

function pairSpreadSymbol(exchange: string, rawSymbol: string | null | undefined, fallback: string): string {
  const value = rawSymbol?.trim() || fallback;
  if (exchange !== "hyperliquid") return value;
  const separator = value.indexOf(":");
  return separator > 0 ? value.slice(separator + 1) : value;
}

function exactSpreadMarket(
  result: InstrumentLookupResult,
  exchange: string,
  marketType: MarketType,
  rawSymbol: string,
  dex: string | null
): InstrumentMarketCandidate | null {
  return exactMarkets(result).find((market) => (
    market.exchange === exchange
    && market.market_type === marketType
    && market.raw_symbol === rawSymbol
    && (marketDex(market) ?? "") === (dex ?? "")
  )) ?? null;
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

function instrumentMarket(
  result: InstrumentLookupResult,
  exchange: string,
  marketType: MarketType
): MarketSnapshot | null {
  const snapshot = result.exchanges.find((item) => item.exchange === exchange);
  return (marketType === "spot" ? snapshot?.spot : snapshot?.future) ?? null;
}

function directionalOpportunityType(
  buyMarketType: MarketType,
  sellMarketType: MarketType
): InstrumentSpreadComparison["opportunity_type"] {
  if (buyMarketType === "future" && sellMarketType === "future") return "FF";
  if (buyMarketType === "spot" && sellMarketType === "spot") return "SS";
  if (buyMarketType === "spot" && sellMarketType === "future") return "SF";
  return null;
}

function reverseInstrumentSpread(
  result: InstrumentLookupResult,
  spread: InstrumentSpreadComparison
): InstrumentSpreadComparison | null {
  const buyMarket = exactSpreadMarket(
    result,
    spread.sell_exchange,
    spread.sell_market_type,
    spread.sell_raw_symbol,
    spread.sell_dex
  ) ?? instrumentMarket(result, spread.sell_exchange, spread.sell_market_type);
  const sellMarket = exactSpreadMarket(
    result,
    spread.buy_exchange,
    spread.buy_market_type,
    spread.buy_raw_symbol,
    spread.buy_dex
  ) ?? instrumentMarket(result, spread.buy_exchange, spread.buy_market_type);
  if (!buyMarket || !sellMarket) return null;
  const buyMid = (buyMarket.bid + buyMarket.ask) / 2;
  const sellMid = (sellMarket.bid + sellMarket.ask) / 2;
  return {
    id: `${buyMarket.exchange}:${buyMarket.market_type}:${marketDex(buyMarket) ?? ""}:${buyMarket.raw_symbol}->${sellMarket.exchange}:${sellMarket.market_type}:${marketDex(sellMarket) ?? ""}:${sellMarket.raw_symbol}`,
    buy_exchange: buyMarket.exchange,
    buy_market_type: buyMarket.market_type,
    buy_raw_symbol: buyMarket.raw_symbol,
    buy_dex: marketDex(buyMarket),
    buy_price_multiplier: buyMarket.symbol_alias_price_multiplier ?? 1,
    buy_contract_size_multiplier: buyMarket.contract_size_multiplier ?? null,
    buy_ask: buyMarket.ask,
    buy_volume_24h_usdt: buyMarket.volume_24h_usdt ?? null,
    buy_funding_rate_pct: buyMarket.funding_rate_pct ?? null,
    buy_funding_interval_hours: buyMarket.funding_interval_hours ?? null,
    buy_timestamp: buyMarket.timestamp,
    buy_data_source: buyMarket.data_source ?? null,
    buy_is_estimated: buyMarket.is_estimated ?? false,
    sell_exchange: sellMarket.exchange,
    sell_market_type: sellMarket.market_type,
    sell_raw_symbol: sellMarket.raw_symbol,
    sell_dex: marketDex(sellMarket),
    sell_price_multiplier: sellMarket.symbol_alias_price_multiplier ?? 1,
    sell_contract_size_multiplier: sellMarket.contract_size_multiplier ?? null,
    sell_bid: sellMarket.bid,
    sell_volume_24h_usdt: sellMarket.volume_24h_usdt ?? null,
    sell_funding_rate_pct: sellMarket.funding_rate_pct ?? null,
    sell_funding_interval_hours: sellMarket.funding_interval_hours ?? null,
    sell_timestamp: sellMarket.timestamp,
    sell_data_source: sellMarket.data_source ?? null,
    sell_is_estimated: sellMarket.is_estimated ?? false,
    price_difference: sellMarket.bid - buyMarket.ask,
    executable_spread_pct: 2 * (sellMarket.bid - buyMarket.ask) / (buyMarket.ask + sellMarket.bid) * 100,
    mid_spread_pct: 2 * (sellMid - buyMid) / (buyMid + sellMid) * 100,
    opportunity_type: directionalOpportunityType(buyMarket.market_type, sellMarket.market_type),
    astro_supported: true,
    astro_blocker: null
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

function spotFutureBasis(row: InstrumentExchangeSnapshot): number | null {
  const spot = displayPrice(row.spot);
  const future = displayPrice(row.future);
  return spot && future ? ((future - spot) / spot) * 100 : null;
}

function fullTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("MM-DD HH:mm:ss") : "-";
}

function marketTime(value: string | null | undefined): string {
  return value ? dayjs.utc(value).utcOffset(8).format("YYYY-MM-DD HH:mm:ss") : "-";
}

function ageText(value: string | null | undefined): string {
  if (!value) return "-";
  const seconds = Math.max(0, dayjs().diff(dayjs.utc(value), "second"));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return fullTime(value);
}

function tone(value: number | null): string {
  if (value === null || Math.abs(value) < 0.000_001) return "neutral";
  return value > 0 ? "positive" : "negative";
}

function tradeActionMeta(action: TradeActionStatus) {
  const config = {
    available: { color: "green", label: "可用", tone: "available" },
    blocked: { color: "red", label: "受限", tone: "blocked" },
    conditional: { color: "gold", label: "未知", tone: "unknown" },
    unknown: { color: "default", label: "未知", tone: "unknown" },
    not_applicable: { color: "default", label: "不适用", tone: "unknown" }
  }[action.state];
  return config;
}

function TradeActionTile({
  action,
  label,
  hint
}: {
  action: TradeActionStatus;
  label: string;
  hint?: string;
}) {
  const config = tradeActionMeta(action);
  const detail = action.reason;
  return (
    <div className={`instrument-trade-action instrument-trade-action-${config.tone}`}>
      <span>{label}</span>
      <Tooltip title={detail}>
        <Tag color={config.color}>{config.label}</Tag>
      </Tooltip>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

function TradeEvidenceTag({ evidence }: { evidence: MarketTradeAvailability["diagnostics"][number] }) {
  const scopeLabel = {
    public_market: "公开市场",
    account: "账户",
    order_error: "真实订单",
    platform_capability: "平台能力",
    none: "其他"
  }[evidence.scope];
  const stateConfig = {
    confirmed: { color: "green", label: "已确认" },
    not_checked: { color: "default", label: "未核验" },
    not_provided: { color: "default", label: "未提供" },
    error: { color: "red", label: "错误" }
  }[evidence.state];
  const detail = evidence.raw_error
    ? `${evidence.message}\n${evidence.raw_error}`
    : evidence.message;
  const label = evidence.scope === "account" && evidence.state === "not_checked"
    ? "账户未接入"
    : `${scopeLabel} ${stateConfig.label}`;
  return (
    <Tooltip title={detail}>
      <Tag color={stateConfig.color}>{label}</Tag>
    </Tooltip>
  );
}

function TradeMetric({
  label,
  value,
  tone,
  tooltip
}: {
  label: string;
  value: string;
  tone?: "bid" | "ask" | "positive" | "negative";
  tooltip?: string | null;
}) {
  const content = (
    <span className={`instrument-trade-metric${tone ? ` instrument-trade-metric-${tone}` : ""}`}>
      <small>{label}</small>
      <strong>{value}</strong>
    </span>
  );
  return tooltip ? <Tooltip title={tooltip}>{content}</Tooltip> : content;
}

function TransferNetworkState({ value }: { value: boolean | null }) {
  const config = value === true
    ? { className: "enabled", label: "开启" }
    : value === false
      ? { className: "paused", label: "暂停" }
      : { className: "unknown", label: "未知" };
  return <span className={`instrument-transfer-state instrument-transfer-state-${config.className}`}>{config.label}</span>;
}

function relevantTradeActions(market: MarketTradeAvailability): TradeActionStatus[] {
  return market.market_type === "spot"
    ? [market.buy_open, market.sell_open]
    : [market.buy_open, market.sell_open, market.buy_reduce_only, market.sell_reduce_only];
}

function tradeMarketHasIssue(market: MarketTradeAvailability): boolean {
  return relevantTradeActions(market).some((action) => action.state !== "available");
}

function tradeMarketHasRestriction(market: MarketTradeAvailability): boolean {
  return relevantTradeActions(market).some((action) => action.state === "blocked");
}

function tradeMarketHasUnknown(market: MarketTradeAvailability): boolean {
  return relevantTradeActions(market).some(
    (action) => action.state === "unknown" || action.state === "conditional"
  );
}

function MarketDiagnosticDetails({
  market, quote, source, watch, watchSaving, refreshFailed, onToggleWatch
}: {
  market: MarketTradeAvailability;
  quote: InstrumentMarketCandidate | null;
  source: string;
  watch: TradeAvailabilityWatch | undefined;
  watchSaving: boolean;
  refreshFailed: boolean;
  onToggleWatch: () => void;
}) {
  const issue = tradeMarketHasIssue(market);
  const restriction = tradeMarketHasRestriction(market);
  const unknown = tradeMarketHasUnknown(market);
  const reason = market.public_restrictions.join("；")
    || (issue ? "公开接口未返回足够信息" : "未发现公开市场限制");
  const freshness = diagnosticFreshness(market, quote);
  return (
    <details className="instrument-market-diagnostics" role="cell">
      <summary>
        市场诊断 · {ageText(market.observed_at)}
        {refreshFailed ? <Tag color="orange">{freshness ? `诊断未更新 · ${freshness}` : "诊断未更新"}</Tag>
          : freshness ? <Tag color="orange">{freshness}</Tag> : <Tag color="green">诊断新鲜</Tag>}
        {restriction ? <Tag color="red">交易受限</Tag> : unknown ? <Tag color="gold">交易待核实</Tag> : null}
      </summary>
      <div className="instrument-market-diagnostic-meta">
        <span>诊断来源：{source} · 公开状态来源：{market.public_status_source}</span>
        <span>诊断时间：{marketTime(market.observed_at)} · 行情快照：{marketTime(market.market_data_updated_at)} · 盘口：{marketTime(market.orderbook_updated_at)}</span>
        {quote ? <span>与上方行情时间分别记录，不视为同步报价（行情：{marketTime(quote.upstream_timestamp ?? quote.timestamp)}）</span> : null}
      </div>
      <div className="instrument-market-diagnostic-content">
        <div className="instrument-market-data-group">
          <span className="instrument-market-data-group-name">盘口与流动性</span>
          <TradeMetric label="盘口实际买一 Bid" value={price(market.best_bid)} tone="bid" />
          <TradeMetric label="盘口实际卖一 Ask" value={price(market.best_ask)} tone="ask" />
          <TradeMetric label="0.1% 买深度" value={compactUsdt(market.bid_depth_01pct_usdt)} />
          <TradeMetric label="0.1% 卖深度" value={compactUsdt(market.ask_depth_01pct_usdt)} />
          <TradeMetric label="1% 买深度" value={compactUsdt(market.bid_depth_1pct_usdt)} />
          <TradeMetric label="1% 卖深度" value={compactUsdt(market.ask_depth_1pct_usdt)} />
          <TradeMetric label="诊断 24h 成交额" value={compactUsdt(market.volume_24h_usdt)} />
        </div>
        <div className="instrument-market-data-group">
          <span className="instrument-market-data-group-name">交易限制</span>
          <Tooltip title={market.public_status_source}><Tag color={restriction ? "red" : unknown ? "gold" : "green"}>公开 {market.public_status_code}</Tag></Tooltip>
          <span>{reason}</span>
          <div>
            {market.diagnostics.filter((evidence) => evidence.scope !== "public_market" && ["confirmed", "error"].includes(evidence.state))
              .map((evidence) => <TradeEvidenceTag key={evidence.scope + ":" + evidence.reason_code} evidence={evidence} />)}
          </div>
          <div className={market.market_type === "spot" ? "instrument-trade-action-grid-spot" : "instrument-trade-action-grid"}>
            <TradeActionTile action={market.buy_open} label={market.market_type === "spot" ? "买入" : "开多"} />
            <TradeActionTile action={market.sell_open} label={market.market_type === "spot" ? "卖出" : "开空"} />
            {market.market_type === "future" ? <>
              <TradeActionTile action={market.buy_reduce_only} label="平空" hint="Reduce Only Buy" />
              <TradeActionTile action={market.sell_reduce_only} label="平多" hint="Reduce Only Sell" />
            </> : null}
          </div>
          {issue || watch ? <Button
            size="small" type={watch ? "default" : "primary"}
            icon={watch ? <CloseOutlined /> : <BellOutlined />}
            aria-label={"市场 " + (exchangeLabels[market.exchange] ?? market.exchange) + " " + market.market_type + " " + (market.dex ? market.dex + " " : "") + market.raw_symbol + " " + (watch ? "取消恢复通知" : "订阅恢复通知")}
            loading={watchSaving} onClick={onToggleWatch}
          >{watch ? "已订阅" : "订阅恢复"}</Button> : null}
        </div>
        <div className="instrument-market-data-group">
          <span className="instrument-market-data-group-name">资金与费用（诊断口径）</span>
          <TradeMetric label="资金费率 / 周期" value={market.market_type === "future" ? signedPct(market.funding_rate_pct, 6) + " / " + (market.funding_interval_hours ? market.funding_interval_hours + "h" : "未返回") : "-"} />
          <TradeMetric label="预估资金费率" value={market.market_type === "future" ? signedPct(market.funding_next_rate_pct, 6) : "-"} />
          <TradeMetric label="Maker / Taker" value={(market.maker_fee_pct === null ? "未返回" : signedPct(market.maker_fee_pct, 4)) + " / " + (market.taker_fee_pct === null ? "未返回" : signedPct(market.taker_fee_pct, 4))} />
          <TradeMetric label="手续费计入情况" value={market.fees_included ? "已计入" : "未计入"} tooltip={market.fee_note} />
          <span>{market.fee_note}</span>
        </div>
        <div className="instrument-market-data-group">
          <span className="instrument-market-data-group-name">合约规格与数据质量</span>
          <TradeMetric label="诊断价格倍率" value={market.market_multiplier + "x"} />
          <TradeMetric label="诊断数量乘数" value={String(market.contract_size_multiplier)} />
          <TradeMetric label="盘口来源" value={market.orderbook_source} tooltip={market.orderbook_source} />
          <TradeMetric label="盘口更新时间" value={ageText(market.orderbook_updated_at)} tooltip={marketTime(market.orderbook_updated_at)} />
          <TradeMetric label="诊断行情更新时间" value={ageText(market.market_data_updated_at)} tooltip={marketTime(market.market_data_updated_at)} />
          <TradeMetric label="诊断更新时间" value={ageText(market.observed_at)} tooltip={marketTime(market.observed_at)} />
        </div>
      </div>
    </details>
  );
}

function weightPct(value: number | null): string {
  return value === null ? "未返回" : `${(value * 100).toFixed(2)}%`;
}

function resultPriceRange(result: InstrumentLookupResult | null): { min: number; max: number } | null {
  if (!result) return null;
  const values = exactMarkets(result)
    .map((market) => displayPrice(market))
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
  const [savedSymbols, setSavedSymbols] = useState(readSavedSymbols);
  const [result, setResult] = useState<InstrumentLookupResult | null>(null);
  const [tradeStatus, setTradeStatus] = useState<TradeAvailabilityResult | null>(null);
  const [tradeWatches, setTradeWatches] = useState<TradeAvailabilityWatch[]>([]);
  const [tradeStatusError, setTradeStatusError] = useState<{ symbol: string; message: string } | null>(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [tradeWatchSaving, setTradeWatchSaving] = useState("");
  const [tradeAvailabilityFilter, setTradeAvailabilityFilter] = useState<"all" | "blocked" | "unknown">("all");
  const [tradeOnlyIssues, setTradeOnlyIssues] = useState(false);
  const [tradeMarketTypeFilter, setTradeMarketTypeFilter] = useState<"all" | MarketType>("all");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [watchSaving, setWatchSaving] = useState(false);
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
  const [spreadExchangeSearch, setSpreadExchangeSearch] = useState("");
  const [astroSymbol, setAstroSymbol] = useState("");
  const [astroSpread, setAstroSpread] = useState<InstrumentSpreadComparison | null>(null);
  const [astroReversed, setAstroReversed] = useState(false);
  const [astroPlan, setAstroPlan] = useState<AstroPairPlan | null>(null);
  const [astroPreviewLoading, setAstroPreviewLoading] = useState(false);
  const [astroPreviewError, setAstroPreviewError] = useState("");
  const [astroSubmitLoading, setAstroSubmitLoading] = useState(false);
  const [astroSubmitResult, setAstroSubmitResult] = useState<AstroActionResult | null>(null);
  const [astroSubmitError, setAstroSubmitError] = useState("");
  const requestIdRef = useRef(0);
  const autoRefreshPendingRef = useRef(false);
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
      setTradeStatus((current) => current?.query === next.symbol ? current : null);
      setTradeStatusError((current) => current?.symbol === next.symbol ? current : null);
      setDiagnosticsLoading(next.market_count > 0);
      if (next.market_count > 0) {
        const [statusResult, watchesResult] = await Promise.allSettled([
          getTradeAvailability(next.symbol),
          listTradeAvailabilityWatches()
        ]);
        if (requestId !== requestIdRef.current) return;
        if (statusResult.status === "fulfilled") {
          setTradeStatus(statusResult.value);
          setTradeStatusError(null);
        } else {
          setTradeStatusError({
            symbol: next.symbol,
            message: statusResult.reason instanceof Error ? statusResult.reason.message : String(statusResult.reason)
          });
        }
        if (watchesResult.status === "fulfilled") setTradeWatches(watchesResult.value);
      } else {
        setTradeStatus(null);
        setTradeStatusError(null);
      }
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
        setDiagnosticsLoading(false);
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void runLookup(startingSymbol);
  }, [runLookup, startingSymbol]);

  useEffect(() => {
    const handleNavigation = () => {
      const next = new URLSearchParams(window.location.search).get("symbol");
      if (next && normalizeSymbol(next) !== activeSymbol) void runLookup(next);
    };
    window.addEventListener("taoli1:navigate", handleNavigation);
    return () => window.removeEventListener("taoli1:navigate", handleNavigation);
  }, [activeSymbol, runLookup]);

  useEffect(() => {
    if (!autoRefresh || !activeSymbol) return undefined;
    const refresh = () => {
      if (document.visibilityState === "hidden" || autoRefreshPendingRef.current) return;
      autoRefreshPendingRef.current = true;
      void runLookup(activeSymbol, true).finally(() => { autoRefreshPendingRef.current = false; });
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
  const instrumentMarkets = exactMarkets(result);
  const marketCounts = instrumentMarkets.reduce(
    (counts, market) => ({
      spot: counts.spot + Number(market.market_type === "spot"),
      future: counts.future + Number(market.market_type === "future")
    }),
    { spot: 0, future: 0 }
  );
  const instrumentSpreads = result?.spreads ?? [];
  const hiddenSpreadTypeSet = new Set(hiddenSpreadTypes);
  const spreadExchanges = [...new Set(instrumentSpreads.flatMap(
    (spread) => [spread.buy_exchange, spread.sell_exchange]
  ))].sort(exchangeNameOrder);
  const exchangeSearchTerm = spreadExchangeSearch.trim().toLowerCase();
  const exactExchange = spreadExchanges.find((exchange) => (
    exchange.toLowerCase() === exchangeSearchTerm
    || (exchangeLabels[exchange] ?? exchange).toLowerCase() === exchangeSearchTerm
  ));
  const exchangeMatchesSearch = (exchange: string) => (
    !exchangeSearchTerm
    || (exactExchange
      ? exchange === exactExchange
      : exchange.toLowerCase().includes(exchangeSearchTerm)
        || (exchangeLabels[exchange] ?? exchange).toLowerCase().includes(exchangeSearchTerm))
  );
  const spreadExchangeOptions = spreadExchanges
    .filter(exchangeMatchesSearch)
    .map((exchange) => ({ value: exchangeLabels[exchange] ?? exchange }));
  const visibleInstrumentSpreads = instrumentSpreads.filter(
    (spread) => !hiddenSpreadTypeSet.has(spreadTypeFilter(spread.opportunity_type))
      && (exchangeMatchesSearch(spread.buy_exchange) || exchangeMatchesSearch(spread.sell_exchange))
  );
  const currentTradeStatus = tradeStatus?.query === result?.symbol ? tradeStatus : null;
  const currentTradeStatusError = tradeStatusError && tradeStatusError.symbol === result?.symbol ? tradeStatusError.message : "";
  const marketRows = associateMarkets(instrumentMarkets, currentTradeStatus?.markets ?? []);
  const visibleMarketRows = marketRows.filter(({ quote, diagnostic }) => {
    const marketType = quote?.market_type ?? diagnostic?.market_type;
    if (tradeMarketTypeFilter !== "all" && marketType !== tradeMarketTypeFilter) return false;
    if (!diagnostic) return true;
    if (tradeOnlyIssues && !tradeMarketHasIssue(diagnostic)) return false;
    if (tradeAvailabilityFilter === "blocked" && !tradeMarketHasRestriction(diagnostic)) return false;
    if (tradeAvailabilityFilter === "unknown" && !tradeMarketHasUnknown(diagnostic)) return false;
    return true;
  });
  const spotTransferMarkets = currentTradeStatus?.markets.filter(
    (market) => market.market_type === "spot"
  ) ?? [];
  const marketRowKeys = new Map<string, number>();

  const tradeMarketKey = (market: MarketTradeAvailability) => (
    `${market.exchange}:${market.market_type}:${market.dex ?? ""}:${market.raw_symbol}`
  );

  const tradeWatchFor = (market: MarketTradeAvailability) => tradeWatches.find(
    (watch) => watch.exchange === market.exchange
      && watch.market_type === market.market_type
      && (watch.dex ?? "") === (market.dex ?? "")
      && watch.raw_symbol.toUpperCase() === market.raw_symbol.toUpperCase()
  );

  const toggleTradeWatch = async (market: MarketTradeAvailability) => {
    const existing = tradeWatchFor(market);
    setTradeWatchSaving(tradeMarketKey(market));
    try {
      if (existing) {
        await deleteTradeAvailabilityWatch(existing.id);
        setTradeWatches((current) => current.filter((watch) => watch.id !== existing.id));
        message.success(`${market.exchange} / ${market.raw_symbol} 已停止恢复监控`);
      } else {
        const saved = await createTradeAvailabilityWatch({
          symbol: market.symbol,
          exchange: market.exchange,
          market_type: market.market_type,
          raw_symbol: market.raw_symbol,
          dex: market.dex,
          monitor_buy: true,
          monitor_sell: true
        });
        setTradeWatches((current) => [
          saved,
          ...current.filter((watch) => watch.id !== saved.id)
        ]);
        message.success(`${market.exchange} / ${market.raw_symbol} 已监控普通交易恢复`);
      }
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setTradeWatchSaving("");
    }
  };

  const setSpreadTypeHidden = (type: SpreadTypeFilter, hidden: boolean) => {
    setHiddenSpreadTypes((current) => hidden
      ? current.includes(type) ? current : [...current, type]
      : current.filter((item) => item !== type));
  };

  const addCurrentSymbolToWatch = async () => {
    if (!result) return;
    setWatchSaving(true);
    try {
      await addFloatingWatchSymbol(result.symbol);
      message.success(`${result.base} 已加入关注浮窗`);
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setWatchSaving(false);
    }
  };

  const saveCurrentSymbol = () => {
    if (!result || normalizeSymbol(query) !== result.symbol || savedSymbols.includes(result.symbol)) return;
    const next = [result.symbol, ...savedSymbols].slice(0, MAX_SAVED_SYMBOLS);
    try {
      window.localStorage.setItem(SAVED_SYMBOLS_KEY, JSON.stringify(next));
      setSavedSymbols(next);
      message.success(`${result.symbol} 已保存`);
    } catch {
      message.error("保存失败，请检查浏览器存储权限");
    }
  };

  const removeSavedSymbol = (symbol: string) => {
    const next = savedSymbols.filter((item) => item !== symbol);
    try {
      window.localStorage.setItem(SAVED_SYMBOLS_KEY, JSON.stringify(next));
      setSavedSymbols(next);
    } catch {
      message.error("移除失败，请检查浏览器存储权限");
    }
  };

  const openPairSpread = (spread: InstrumentSpreadComparison) => {
    if (!result) return;
    const url = new URL(window.location.href);
    url.searchParams.set("page", "pair-monitor");
    url.searchParams.delete("symbol");
    const fallbackBuyMarket = instrumentMarket(result, spread.buy_exchange, spread.buy_market_type);
    const fallbackSellMarket = instrumentMarket(result, spread.sell_exchange, spread.sell_market_type);
    const legs = [
      {
        exchange: spread.buy_exchange,
        marketType: spread.buy_market_type,
        rawSymbol: spread.buy_raw_symbol || fallbackBuyMarket?.raw_symbol || result.symbol,
        dex: spread.buy_dex ?? (fallbackBuyMarket ? marketDex(fallbackBuyMarket) : null),
        priceMultiplier: spread.buy_price_multiplier ?? fallbackBuyMarket?.symbol_alias_price_multiplier ?? 1,
        contractSizeMultiplier: spread.buy_contract_size_multiplier ?? fallbackBuyMarket?.contract_size_multiplier ?? null
      },
      {
        exchange: spread.sell_exchange,
        marketType: spread.sell_market_type,
        rawSymbol: spread.sell_raw_symbol || fallbackSellMarket?.raw_symbol || result.symbol,
        dex: spread.sell_dex ?? (fallbackSellMarket ? marketDex(fallbackSellMarket) : null),
        priceMultiplier: spread.sell_price_multiplier ?? fallbackSellMarket?.symbol_alias_price_multiplier ?? 1,
        contractSizeMultiplier: spread.sell_contract_size_multiplier ?? fallbackSellMarket?.contract_size_multiplier ?? null
      }
    ];
    legs.forEach((leg, index) => {
      const key = index + 1;
      url.searchParams.set(`leg${key}_exchange`, leg.exchange);
      url.searchParams.set(`leg${key}_market_type`, leg.marketType);
      url.searchParams.set(`leg${key}_symbol`, pairSpreadSymbol(leg.exchange, leg.rawSymbol, result.symbol));
      url.searchParams.set(`leg${key}_raw_symbol`, leg.rawSymbol);
      url.searchParams.set(`leg${key}_price_multiplier`, String(leg.priceMultiplier));
      if (leg.contractSizeMultiplier !== null) {
        url.searchParams.set(`leg${key}_contract_size_multiplier`, String(leg.contractSizeMultiplier));
      } else {
        url.searchParams.delete(`leg${key}_contract_size_multiplier`);
      }
      if (leg.dex) {
        url.searchParams.set(`leg${key}_dex`, leg.dex);
      } else {
        url.searchParams.delete(`leg${key}_dex`);
      }
    });
    url.searchParams.set("leg2_multiplier", "1");
    url.searchParams.set("hours", "4");
    url.searchParams.set("interval_seconds", "60");
    url.searchParams.delete("interval_minutes");
    window.open(url.toString(), "_blank", "noopener,noreferrer");
  };

  const closeAstroPreview = () => {
    if (astroSubmitLoading) return;
    astroPreviewRequestIdRef.current += 1;
    astroSubmitRequestIdRef.current += 1;
    setAstroSymbol("");
    setAstroSpread(null);
    setAstroReversed(false);
    setAstroPlan(null);
    setAstroPreviewLoading(false);
    setAstroPreviewError("");
    setAstroSubmitLoading(false);
    setAstroSubmitResult(null);
    setAstroSubmitError("");
    astroSizingForm.resetFields();
  };

  const openAstroPreview = async (
    spread: InstrumentSpreadComparison,
    reversed = false
  ) => {
    if (!result || astroSubmitLoading) return;
    const selectedSpread = reversed ? reverseInstrumentSpread(result, spread) : spread;
    if (!selectedSpread) {
      message.error("无法读取反向路线的最新盘口，请刷新后重试");
      return;
    }
    const requestId = ++astroPreviewRequestIdRef.current;
    astroSubmitRequestIdRef.current += 1;
    const symbol = result.symbol;
    setAstroSymbol(symbol);
    setAstroSpread(selectedSpread);
    setAstroReversed(reversed);
    setAstroPlan(null);
    setAstroPreviewError("");
    setAstroSubmitResult(null);
    setAstroSubmitError("");
    setAstroPreviewLoading(true);
    try {
      const plan = await previewInstrumentAstroPair(astroRoute(symbol, selectedSpread));
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
      || !astroPlan?.pair
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

  const spreadColumns: ColumnsType<InstrumentSpreadComparison> = [
    {
      title: "差价类型",
      dataIndex: "opportunity_type",
      width: 132,
      sorter: (left, right) => (
        spreadTypeOrder(left.opportunity_type) - spreadTypeOrder(right.opportunity_type)
      ),
      render: (value: InstrumentSpreadComparison["opportunity_type"]) => (
        <SpreadTypeTag value={value} />
      )
    },
    {
      title: "买入市场",
      key: "buy_market",
      width: 170,
      sorter: (left, right) => exchangeNameOrder(left.buy_exchange, right.buy_exchange),
      render: (_, spread) => (
        <div className="instrument-market-cell">
          <Space size={6}>
            <Typography.Text strong>{exchangeLabels[spread.buy_exchange] ?? spread.buy_exchange}</Typography.Text>
            <MarketTypeTag value={spread.buy_market_type} />
          </Space>
          {spread.buy_raw_symbol ? <span>{spread.buy_dex ? `DEX ${spread.buy_dex} · ` : ""}{spread.buy_raw_symbol}</span> : null}
          {spread.buy_price_multiplier !== undefined || spread.buy_contract_size_multiplier !== undefined ? (
            <span>价格倍率 {spread.buy_price_multiplier ?? 1}x · 数量乘数 {spread.buy_contract_size_multiplier ?? "-"}</span>
          ) : null}
        </div>
      )
    },
    {
      title: "买入 Ask",
      dataIndex: "buy_ask",
      width: 130,
      align: "right",
      render: (value: number, spread) => (
        <div className="instrument-market-cell">
          <strong>{price(value)}</strong>
          <span>24h {compactUsdt(spread.buy_volume_24h_usdt)}</span>
          <span>资金 {signedPct(spread.buy_funding_rate_pct, 6)} / {spread.buy_funding_interval_hours ? `${spread.buy_funding_interval_hours}h` : "-"}</span>
          <span title={fullTime(spread.buy_timestamp)}>更新 {ageText(spread.buy_timestamp)}</span>
        </div>
      )
    },
    {
      title: "卖出市场",
      key: "sell_market",
      width: 170,
      sorter: (left, right) => exchangeNameOrder(left.sell_exchange, right.sell_exchange),
      render: (_, spread) => (
        <div className="instrument-market-cell">
          <Space size={6}>
            <Typography.Text strong>{exchangeLabels[spread.sell_exchange] ?? spread.sell_exchange}</Typography.Text>
            <MarketTypeTag value={spread.sell_market_type} />
          </Space>
          {spread.sell_raw_symbol ? <span>{spread.sell_dex ? `DEX ${spread.sell_dex} · ` : ""}{spread.sell_raw_symbol}</span> : null}
          {spread.sell_price_multiplier !== undefined || spread.sell_contract_size_multiplier !== undefined ? (
            <span>价格倍率 {spread.sell_price_multiplier ?? 1}x · 数量乘数 {spread.sell_contract_size_multiplier ?? "-"}</span>
          ) : null}
        </div>
      )
    },
    {
      title: "卖出 Bid",
      dataIndex: "sell_bid",
      width: 130,
      align: "right",
      render: (value: number, spread) => (
        <div className="instrument-market-cell">
          <strong>{price(value)}</strong>
          <span>24h {compactUsdt(spread.sell_volume_24h_usdt)}</span>
          <span>资金 {signedPct(spread.sell_funding_rate_pct, 6)} / {spread.sell_funding_interval_hours ? `${spread.sell_funding_interval_hours}h` : "-"}</span>
          <span title={fullTime(spread.sell_timestamp)}>更新 {ageText(spread.sell_timestamp)}</span>
        </div>
      )
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
      width: 218,
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
            <Tooltip title={spread.astro_blocker ? `${spread.astro_blocker}；人工建卡仅提示，不会拦截` : "按当前方向创建卡片"}>
              <Button
                size="small"
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => void openAstroPreview(spread)}
              >
                建卡
              </Button>
            </Tooltip>
            <Tooltip title="交换买卖方向，按反向盘口创建卡片">
              <Button
                size="small"
                icon={<SwapOutlined />}
                onClick={() => void openAstroPreview(spread, true)}
              >
                反向
              </Button>
            </Tooltip>
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
          <Button
            icon={<SaveOutlined />}
            aria-label="保存当前标的"
            disabled={!result || loading || normalizeSymbol(query) !== result.symbol || savedSymbols.includes(result.symbol)}
            onClick={saveCurrentSymbol}
          >
            保存
          </Button>
          <Button
            icon={<PushpinOutlined />}
            loading={watchSaving}
            disabled={!result || loading}
            onClick={() => void addCurrentSymbolToWatch()}
          >
            加入浮窗
          </Button>
          <Tooltip title="立即刷新"><Button aria-label="立即刷新" icon={<ReloadOutlined spin={refreshing} />} disabled={!activeSymbol || loading} onClick={() => void runLookup(activeSymbol, true)} /></Tooltip>
          <Space size={6}><Switch size="small" checked={autoRefresh} onChange={setAutoRefresh} /><Typography.Text type="secondary">自动刷新</Typography.Text></Space>
        </div>
      </div>

      <div className="instrument-diagnostic-scope" role="note">
        <strong>诊断口径</strong>
        <span>账户数据未接入 · 未发送探测订单 · 当前仅依据公开市场数据判断</span>
      </div>

      {savedSymbols.length > 0 ? (
        <div className="instrument-saved-symbols">
          <Typography.Text type="secondary">已保存</Typography.Text>
          <div className="instrument-saved-symbol-list">
            {savedSymbols.map((symbol) => (
              <div className="instrument-saved-symbol" key={symbol}>
                <Button size="small" type="text" disabled={loading} onClick={() => void runLookup(symbol)}>
                  {symbol}
                </Button>
                <Tooltip title={`移除 ${symbol}`}>
                  <Button
                    size="small"
                    type="text"
                    aria-label={`移除已保存标的 ${symbol}`}
                    icon={<CloseOutlined />}
                    onClick={() => removeSavedSymbol(symbol)}
                  />
                </Tooltip>
              </div>
            ))}
          </div>
        </div>
      ) : null}

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

      {marketRows.length > 0 ? (
        <section className="instrument-market-table instrument-exact-markets">
          <div className="instrument-section-head">
            <div>
              <Typography.Title level={4}>精确行情市场</Typography.Title>
              <Typography.Text type="secondary">逐个原始市场展示；盘口、公开限制及费用需展开查看，各自时间独立</Typography.Text>
            </div>
            <Space size={[8, 4]} wrap>
              <Segmented<"all" | MarketType> size="small" value={tradeMarketTypeFilter}
                options={[{ label: "全部", value: "all" }, { label: "现货", value: "spot" }, { label: "永续", value: "future" }]}
                onChange={setTradeMarketTypeFilter} />
              <Segmented<"all" | "blocked" | "unknown"> size="small" value={tradeAvailabilityFilter}
                options={[{ label: "全部状态", value: "all" }, { label: "有限制", value: "blocked" }, { label: "未知", value: "unknown" }]}
                onChange={setTradeAvailabilityFilter} />
              <Space size={5}><Switch size="small" checked={tradeOnlyIssues} onChange={setTradeOnlyIssues} /><Typography.Text>只看异常</Typography.Text></Space>
              <Tag>{instrumentMarkets.length} 个行情市场</Tag>
            </Space>
          </div>
          {currentTradeStatusError ? <Alert type="warning" showIcon
            message={currentTradeStatus ? "市场诊断刷新失败；显示上次诊断" : "市场诊断失败；行情仍可查看"}
            description={currentTradeStatusError} /> : null}
          {currentTradeStatus && Object.keys(currentTradeStatus.errors).length ? <Alert type="warning" showIcon
            message={Object.keys(currentTradeStatus.errors).length + " 个市场诊断降级"}
            description={Object.entries(currentTradeStatus.errors).map(([key, value]) => key + ": " + value).join(" | ")} /> : null}
          <div className="instrument-market-data-list" role="table" aria-label="精确行情市场">
            <div className="instrument-market-data-head" role="row">
              <span>市场身份</span><span>行情 Bid / Ask</span><span>成交额</span><span>资金费率</span><span>倍率</span><span>行情数据状态</span>
            </div>
            {visibleMarketRows.map(({ quote, diagnostic }) => {
              const market = quote ?? diagnostic!;
              const dex = quote ? marketDex(quote) : diagnostic?.dex;
              const priceMultiplier = quote?.symbol_alias_price_multiplier ?? 1;
              const qualityTimestamp = quote?.upstream_timestamp ?? quote?.timestamp;
              const diagnosticStaleness = diagnostic && quote ? diagnosticFreshness(diagnostic, quote) : null;
              const rowIdentity = `${quote ? "quote" : "diagnostic"}:${exactMarketKey(market) ?? JSON.stringify([market.exchange, market.market_type, market.raw_symbol, market.dex])}`;
              const occurrence = marketRowKeys.get(rowIdentity) ?? 0;
              marketRowKeys.set(rowIdentity, occurrence + 1);
              return (
                <article className={"instrument-market-data-row" + (quote?.data_status === "stale" ? " instrument-market-data-row-stale" : "")}
                  key={`${rowIdentity}:${occurrence}`} role="row">
                  <div className="instrument-market-data-identity" role="cell">
                    <strong>{exchangeLabels[market.exchange] ?? market.exchange}</strong>
                    <span>{marketTypeLabel(market.market_type)} · {dex ? "DEX " + dex + " · " : ""}{market.raw_symbol}</span>
                    <span>规范标的 {market.symbol}</span>
                    {quote ? <Tag color={quote.data_status === "live" ? "green" : "red"}>{quote.data_status === "live" ? "行情实时" : "行情已过期"}</Tag>
                      : <Tag color="orange">仅诊断，未可靠关联行情</Tag>}
                    {diagnostic && quote && (currentTradeStatusError || diagnosticStaleness) ?
                      <Tag color="orange">{currentTradeStatusError
                        ? `诊断未更新${diagnosticStaleness ? ` · ${diagnosticStaleness}` : ""}`
                        : diagnosticStaleness}</Tag> : null}
                  </div>
                  <div className="instrument-market-data-group" role="cell">
                    <span className="instrument-market-data-group-name">行情可成交价格</span>
                    <TradeMetric label="买一 Bid" value={price(quote?.bid)} tone="bid" />
                    <TradeMetric label="卖一 Ask" value={price(quote?.ask)} tone="ask" />
                    <TradeMetric label="标记 / 指数" value={quote ? price(quote.mark_price) + " / " + price(quote.index_price) : "-"} />
                  </div>
                  <div className="instrument-market-data-group" role="cell">
                    <span className="instrument-market-data-group-name">行情成交额</span>
                    <TradeMetric label="24h 成交额" value={compactUsdt(quote?.volume_24h_usdt)} />
                    <TradeMetric label="买一数量" value={quote?.bid_size == null ? "-" : String(quote.bid_size)} />
                    <TradeMetric label="卖一数量" value={quote?.ask_size == null ? "-" : String(quote.ask_size)} />
                  </div>
                  <div className="instrument-market-data-group" role="cell">
                    <span className="instrument-market-data-group-name">行情资金费率</span>
                    <TradeMetric label="当前 / 周期" value={!quote ? "-" : quote.market_type === "spot" ? "现货" : signedPct(quote.funding_rate_pct, 6) + " / " + (quote.funding_interval_hours ? quote.funding_interval_hours + "h" : "-")} />
                    <TradeMetric label="下期预估" value={quote?.market_type === "future" ? signedPct(quote.funding_next_rate_pct, 6) : "-"} />
                    <TradeMetric label="下次结算" value={quote?.market_type === "future" ? fullTime(quote.funding_next_time) : "-"} />
                  </div>
                  <div className="instrument-market-data-group" role="cell">
                    <span className="instrument-market-data-group-name">行情倍率</span>
                    <TradeMetric label="价格倍率" value={quote ? priceMultiplier + "x" : "-"} />
                    <TradeMetric label="合约数量乘数" value={quote?.contract_size_multiplier == null ? "-" : String(quote.contract_size_multiplier)} />
                    <TradeMetric label="价格口径" value={!quote ? "-" : priceMultiplier === 1 ? "原始 = 规范" : "原始 x " + priceMultiplier} />
                  </div>
                  <div className="instrument-market-data-group instrument-market-data-quality" role="cell">
                    <span className="instrument-market-data-group-name">行情数据状态</span>
                    <TradeMetric label="行情来源" value={quote?.data_source || "未标注"} tooltip={quote?.data_source} />
                    <TradeMetric label="行情更新时间" value={ageText(qualityTimestamp)} tooltip={fullTime(qualityTimestamp)} />
                    <TradeMetric label="行情时间戳" value={marketTime(qualityTimestamp)} />
                    <TradeMetric label="估算字段" value={quote ? quote.estimated_fields?.length ? quote.estimated_fields.join("、") : quote.is_estimated ? "含估算" : "无" : "-"} />
                    {quote?.error ? <TradeMetric label="上游错误" value={quote.error} tooltip={quote.error} tone="negative" /> : null}
                  </div>
                  {diagnostic ? <MarketDiagnosticDetails market={diagnostic} quote={quote} source={currentTradeStatus?.source ?? "公开诊断接口"}
                    watch={tradeWatchFor(diagnostic)} watchSaving={tradeWatchSaving === tradeMarketKey(diagnostic)}
                    refreshFailed={Boolean(currentTradeStatusError)}
                    onToggleWatch={() => void toggleTradeWatch(diagnostic)} />
                    : <div className="instrument-market-diagnostic-missing" role="cell">{diagnosticsLoading && !currentTradeStatus ? "诊断加载中" : currentTradeStatusError ? "诊断请求失败，未混用其他市场数据" : "无可靠关联的市场诊断"}</div>}
                </article>
              );
            })}
            {visibleMarketRows.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选下没有市场" /> : null}
          </div>
        </section>
      ) : null}
      {spotTransferMarkets.length ? (
        <section className="instrument-market-table instrument-transfer-status">
          <div className="instrument-section-head">
            <div>
              <Typography.Title level={4}>现货充提通道</Typography.Title>
              <Typography.Text type="secondary">每条网络独立展示，不合并为“部分开放”</Typography.Text>
            </div>
            <Tag>{spotTransferMarkets.length} 个现货市场</Tag>
          </div>
          <div className="instrument-transfer-grid" role="table" aria-label="现货逐链充提状态">
            <div className="instrument-transfer-grid-head" role="row">
              <span>交易所</span>
              <span>资产</span>
              <span>网络</span>
              <span>充币</span>
              <span>提币</span>
              <span>数据来源</span>
              <span>更新时间</span>
            </div>
            {spotTransferMarkets.flatMap((market) => {
              const transfer = market.spot_transfer;
              const networks = transfer?.networks.length
                ? transfer.networks
                : [{ network: "未返回链列表", deposit_enabled: null, withdraw_enabled: null }];
              const sourceDetail = transfer?.error || transfer?.note || transfer?.source || "没有可公开核验的逐链状态";
              return networks.map((network, index) => (
                <div
                  className={`instrument-transfer-grid-row${index === 0 ? " instrument-transfer-grid-row-start" : ""}`}
                  key={`${tradeMarketKey(market)}:${network.network}:${index}`}
                  role="row"
                >
                  <div data-label="交易所" role="cell">
                    <strong>{exchangeLabels[market.exchange] ?? market.exchange}</strong>
                    <span>{market.raw_symbol}</span>
                  </div>
                  <div data-label="资产" role="cell"><strong>{transfer?.asset ?? "-"}</strong></div>
                  <div data-label="网络" role="cell"><strong>{network.network}</strong></div>
                  <div data-label="充币" role="cell"><TransferNetworkState value={network.deposit_enabled} /></div>
                  <div data-label="提币" role="cell"><TransferNetworkState value={network.withdraw_enabled} /></div>
                  <div data-label="数据来源" role="cell">
                    <Tooltip title={sourceDetail}><span>{transfer?.source || sourceDetail}</span></Tooltip>
                  </div>
                  <div data-label="更新时间" role="cell">
                    <Tooltip title={fullTime(transfer?.observed_at)}><span>{ageText(transfer?.observed_at)}</span></Tooltip>
                  </div>
                </div>
              ));
            })}
          </div>
        </section>
      ) : null}

      {currentTradeStatus?.index_compositions?.length ? (
        <section className="instrument-market-table instrument-index-compositions">
          <div className="instrument-section-head">
            <div>
              <Typography.Title level={4}>合约指数成分</Typography.Title>
              <Typography.Text type="secondary">按合约交易所分组；仅展示官方接口真实返回</Typography.Text>
            </div>
            <Tag>
              {currentTradeStatus.index_compositions.length} 个合约 · {currentTradeStatus.index_compositions.reduce((total, item) => total + item.components.length, 0)} 个成分
            </Tag>
          </div>
          <div className="instrument-index-card-grid">
            {currentTradeStatus.index_compositions.map((composition) => (
              <article
                className="instrument-index-card"
                key={`${composition.exchange}:${composition.market_type}:${composition.dex ?? ""}:${composition.raw_symbol}`}
              >
                <div className="instrument-index-card-title">
                  <div>
                    <strong>{exchangeLabels[composition.exchange] ?? composition.exchange} · {composition.symbol.replace(/USDT$/, "")}</strong>
                    <span>{marketTypeLabel(composition.market_type)} · {composition.dex ? `DEX ${composition.dex} · ` : ""}{composition.market_type} / {composition.dex ? `${composition.dex} / ` : ""}{composition.raw_symbol}</span>
                  </div>
                  <div className="instrument-index-price">
                    <span>加权指数</span>
                    <b>{composition.index_price === null ? "未返回" : price(composition.index_price)}</b>
                  </div>
                </div>
                <div className="instrument-index-source">指数来源：{composition.source}</div>
                <div className="instrument-index-component-head">
                  <span>占比</span><span>价格</span><span>来源 / 原始符号</span>
                </div>
                {composition.components.length ? composition.components.map((component, index) => (
                  <div className="instrument-index-component-row" key={`${component.source_exchange}:${component.raw_symbol}:${index}`}>
                    <strong>{weightPct(component.weight)}</strong>
                    <strong>{component.price === null ? "未返回" : price(component.price)}</strong>
                    <span>
                      <b>{exchangeLabels[component.source_exchange] ?? component.source_exchange} · {component.market_type ? marketTypeLabel(component.market_type) : "未返回"}</b>
                      <small>{component.raw_symbol}</small>
                    </span>
                  </div>
                )) : (
                  <Tooltip title={composition.error || composition.note}>
                    <div className="instrument-index-empty">{composition.status === "error" ? "查询失败" : "未返回"}</div>
                  </Tooltip>
                )}
                <div className="instrument-index-card-foot">
                  <span>权重 <b>{weightPct(composition.weight_total)}</b></span>
                  <Tooltip title={fullTime(composition.observed_at)}><span>更新 {ageText(composition.observed_at)}</span></Tooltip>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {instrumentSpreads.length > 0 ? (
        <section className="instrument-market-table">
          <div className="instrument-section-head instrument-spread-head">
            <div>
              <Typography.Title level={4}>跨市场差价</Typography.Title>
              <Typography.Text type="secondary">按买入 Ask、卖出 Bid 计算，每组市场保留较优方向</Typography.Text>
            </div>
            <Space size={[10, 4]} wrap className="instrument-spread-filters">
              <AutoComplete
                className="instrument-spread-exchange-search"
                value={spreadExchangeSearch}
                options={spreadExchangeOptions}
                onChange={setSpreadExchangeSearch}
                allowClear
              >
                <Input
                  aria-label="搜索差价交易所"
                  prefix={<SearchOutlined />}
                  placeholder="搜索交易所（买入或卖出）"
                />
              </AutoComplete>
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
        title={astroReversed ? "反向创建 Astro 卡片" : "创建 Astro 卡片"}
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
            disabled={astroPreviewLoading || astroSubmitLoading || !astroPlan?.pair}
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
            {astroReversed ? (
              <Alert
                type="warning"
                showIcon
                message="当前为反向建卡"
                description={astroPlan?.pair?.type === "FS"
                  ? "将使用 FS 类型买入永续、卖出现货，并主动承受当前可成交价差；请确认现货侧有可卖余额或借币能力。"
                  : "将交换买卖方向并主动承受当前可成交价差；请核对开平仓阈值和资金费率。"}
              />
            ) : null}
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
