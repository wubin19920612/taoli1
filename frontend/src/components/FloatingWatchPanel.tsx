import {
  CloseOutlined,
  DeleteOutlined,
  DragOutlined,
  EyeInvisibleOutlined,
  ExportOutlined,
  FilterOutlined,
  LineChartOutlined,
  MinusOutlined,
  PushpinOutlined,
  ReloadOutlined,
  UndoOutlined
} from "@ant-design/icons";
import { Alert, Button, Empty, Segmented, Spin, Tag, Tooltip, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

import {
  getFloatingWatchSettings,
  listAccountPositions,
  listAstroPairs,
  listPairSpreadPresets,
  lookupInstrument,
  queryPairSpread,
  mutateFloatingWatchPosition
} from "../api/client";
import type {
  AccountPositionAccountStatus,
  AccountPositionIdentity,
  AccountPositionSnapshot,
  FloatingWatchSettings,
  AstroPairStatus,
  InstrumentLookupResult,
  MarketType,
  MarketSnapshot,
  PairSpreadPreset,
  PairSpreadQueryResult
} from "../api/types";
import {
  FLOATING_WATCH_NAVIGATE_MESSAGE,
  FLOATING_WATCH_UPDATED_EVENT,
  removeFloatingWatchPair,
  removeFloatingWatchSymbol
} from "../utils/floatingWatch";

const REFRESH_INTERVAL_MS = 10_000;
const POSITION_STORAGE_KEY = "taoli1:floating-watch-position.v1";
const COLLAPSED_STORAGE_KEY = "taoli1:floating-watch-collapsed.v1";
const SHOW_DUST_POSITIONS_STORAGE_KEY = "taoli1:floating-watch-show-dust-positions.v1";
const MIN_VISIBLE_POSITION_NOTIONAL_USDT = 1;
const STANDALONE_QUERY_PARAM = "floating_watch";
const STANDALONE_QUERY_VALUE = "standalone";
const STANDALONE_WINDOW_NAME = "taoli1-floating-watch";
const emptySettings: FloatingWatchSettings = { symbols: [], pair_ids: [], hidden_positions: [] };
const exchangeLabels: Record<string, string> = {
  aster: "Aster",
  binance: "Binance",
  binance_alpha: "Binance Alpha",
  bitget: "Bitget",
  bybit: "Bybit",
  gate: "Gate",
  hyperliquid: "Hyperliquid",
  lighter: "Lighter",
  okx: "OKX"
};

type WatchMode = "symbols" | "pairs" | "astro" | "positions";
type SavedPosition = { left: number; top: number };
type InstrumentState = { result: InstrumentLookupResult | null; error: string };
type PairState = { result: PairSpreadQueryResult | null; error: string };
type AstroLeg = {
  exchange: string;
  marketType: MarketType;
  symbol: string;
  dex: string;
};
type AstroMetrics = {
  legs: [AstroLeg, AstroLeg];
  markets: [MarketSnapshot, MarketSnapshot];
  openMetric: number;
  closeMetric: number;
  buyClose: number;
  sellClose: number;
};
type AstroPositionMetrics = { buyNotional: number; sellNotional: number };
type AstroProfitEstimate = { value: number; feeRate: number };
type AstroNavigationRoute = {
  legs: [AstroLeg, AstroLeg];
  markets: [MarketSnapshot, MarketSnapshot];
};

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T) => Promise<R>
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await worker(items[index]);
    }
  });
  await Promise.all(runners);
  return results;
}

function clampPosition(position: SavedPosition, width: number, height: number): SavedPosition {
  const maxLeft = Math.max(8, window.innerWidth - width - 8);
  const maxTop = Math.max(8, window.innerHeight - height - 8);
  return {
    left: Math.max(8, Math.min(maxLeft, position.left)),
    top: Math.max(8, Math.min(maxTop, position.top))
  };
}

function loadPosition(): SavedPosition | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(POSITION_STORAGE_KEY) ?? "null") as Partial<SavedPosition> | null;
    return parsed && Number.isFinite(parsed.left) && Number.isFinite(parsed.top)
      ? { left: Number(parsed.left), top: Number(parsed.top) }
      : null;
  } catch {
    return null;
  }
}

function marketPrice(market: MarketSnapshot | null): number | null {
  if (!market) return null;
  if (typeof market.bid === "number" && typeof market.ask === "number") {
    return (market.bid + market.ask) / 2;
  }
  return market.mark_price ?? market.bid ?? market.ask ?? null;
}

function instrumentPriceRange(result: InstrumentLookupResult | null): { min: number; max: number } | null {
  const prices = result?.exchanges.flatMap((item) => [marketPrice(item.spot), marketPrice(item.future)])
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value)) ?? [];
  return prices.length ? { min: Math.min(...prices), max: Math.max(...prices) } : null;
}

function price(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  const absolute = Math.abs(value);
  if (absolute >= 10_000) return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (absolute >= 100) return value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (absolute >= 1) return value.toFixed(5).replace(/0+$/, "").replace(/\.$/, "");
  return value.toPrecision(6).replace(/0+$/, "").replace(/\.$/, "");
}

function signedPct(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}%`;
}

function tone(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "neutral";
  return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function positionMetric(value: number | null | undefined, suffix = ""): string {
  return typeof value === "number" && Number.isFinite(value) ? `${price(value)}${suffix}` : "--";
}

function positionPnl(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return `${value >= 0 ? "+" : ""}${price(value)} U`;
}

function positionPct(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? signedPct(value) : "--";
}

function positionUpdatedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "更新时间未知";
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
}

function positionMarketLabel(position: AccountPositionIdentity): string {
  const marketType = position.market_type === "spot" ? "现货" : "永续";
  const dex = position.exchange === "hyperliquid" ? ` · ${position.dex || "DEX 未知"}` : "";
  return `${marketType}${dex}`;
}

function positionIdentityLabel(position: AccountPositionIdentity): string {
  const exchange = exchangeLabels[position.exchange] || position.exchange;
  const side = position.side === "long" ? "多" : "空";
  return `${exchange} ${position.account_label} · ${position.raw_symbol} · ${positionMarketLabel(position)} · ${side}`;
}

function accountStatusLabel(status: AccountPositionAccountStatus): string {
  const exchange = exchangeLabels[status.exchange] || status.exchange;
  const marketType = status.market_type === "spot" ? "现货" : "永续";
  const dex = status.exchange === "hyperliquid" ? ` · ${status.dex || "DEX 未知"}` : "";
  return `${exchange} ${status.account_label} · ${marketType}${dex}：${status.message}`;
}

function astroHasPosition(pair: AstroPairStatus): boolean {
  return [pair.aExPosition, pair.bExPosition].some((value) => {
    const parsed = finiteNumber(value);
    return parsed !== null && Math.abs(parsed) > 0;
  });
}

function astroPositionMetrics(pair: AstroPairStatus): AstroPositionMetrics {
  const buyQuantity = Math.abs(finiteNumber(pair.aExPosition) ?? 0);
  const sellQuantity = Math.abs(finiteNumber(pair.bExPosition) ?? 0);
  const averageBuy = positivePrice(finiteNumber(pair.avgOpenAExPrice)) ?? 0;
  const averageSell = positivePrice(finiteNumber(pair.avgOpenBExPrice)) ?? 0;
  return {
    buyNotional: buyQuantity * averageBuy,
    sellNotional: sellQuantity * averageSell
  };
}

function astroRealizedProfit(pair: AstroPairStatus): number | null {
  const averageCloseBuy = positivePrice(finiteNumber(pair.avgCloseAExPrice));
  const averageCloseSell = positivePrice(finiteNumber(pair.avgCloseBExPrice));
  return averageCloseBuy !== null && averageCloseSell !== null
    ? finiteNumber(pair.realizedProfit)
    : null;
}

function astroRuntimeState(pair: AstroPairStatus): { label: string; tone: "positive" | "neutral" | "warning" } {
  if (astroHasPosition(pair) && pair.disableClose) return { label: "持仓·禁平", tone: "warning" };
  if (astroHasPosition(pair) && pair.disableOpen) return { label: "持仓·禁开", tone: "warning" };
  if (astroHasPosition(pair)) return { label: "持仓中", tone: "positive" };
  if (pair.disableOpen && pair.disableClose) return { label: "已锁定", tone: "warning" };
  if (pair.disableOpen) return { label: "仅平仓", tone: "warning" };
  if (pair.disableClose) return { label: "仅开仓", tone: "warning" };
  return { label: "监控中", tone: "neutral" };
}

function astroExchange(value: string | undefined): string {
  const normalized = (value || "").trim().toLowerCase().replace(/^gc-/, "");
  if (normalized === "hl") return "hyperliquid";
  if (normalized === "bitgetr") return "bitget";
  return normalized;
}

function astroSymbol(base: string): string {
  const normalized = base.trim().toUpperCase().replace(/[-_/]/g, "");
  return /(?:USDT|USDC|USD)$/.test(normalized) ? normalized : `${normalized}USDT`;
}

function astroPairBases(pair: AstroPairStatus): [string, string] | null {
  const name = pair.name?.trim();
  if (!name) return null;
  if (!pair.type?.toUpperCase().endsWith("R")) return [name, name];
  const separator = name.indexOf("-");
  if (separator <= 0 || separator >= name.length - 1) return null;
  return [name.slice(0, separator), name.slice(separator + 1)];
}

function astroLegs(pair: AstroPairStatus): [AstroLeg, AstroLeg] | null {
  const bases = astroPairBases(pair);
  if (!bases) return null;
  const type = pair.type?.toUpperCase() || "FF";
  const dexes = [
    pair.aEffectiveHlDex || pair.aHlDex || "",
    pair.bEffectiveHlDex || pair.bHlDex || ""
  ];
  return ([0, 1] as const).map((index) => ({
    exchange: astroExchange(index === 0 ? pair.buyEx : pair.sellEx),
    marketType: type[index] === "S" ? "spot" : "future",
    symbol: astroSymbol(bases[index]),
    dex: dexes[index]
  })) as [AstroLeg, AstroLeg];
}

function astroInstrumentKey(leg: AstroLeg): string {
  const dex = leg.exchange === "hyperliquid" ? leg.dex.trim().toLowerCase() : "";
  return `${leg.symbol}|${dex}`;
}

function astroMarket(
  leg: AstroLeg,
  states: Record<string, InstrumentState>
): MarketSnapshot | null {
  const result = states[astroInstrumentKey(leg)]?.result;
  const exact = result?.markets?.find((market) => {
    const rawDex = market.raw_symbol.includes(":") ? market.raw_symbol.split(":", 1)[0].toLowerCase() : "";
    const marketDex = market.dex?.toLowerCase() || (market.exchange === "hyperliquid" ? rawDex || "main" : "");
    const requestedDex = leg.exchange === "hyperliquid" ? leg.dex.trim().toLowerCase() || "main" : "";
    return market.exchange === leg.exchange
      && market.market_type === leg.marketType
      && marketDex === requestedDex
      && market.data_status === "live";
  });
  if (exact) return exact;
  if (result?.markets?.length) return null;
  const exchange = result?.exchanges.find((item) => item.exchange === leg.exchange);
  return leg.marketType === "spot" ? exchange?.spot ?? null : exchange?.future ?? null;
}

function astroNavigationRoute(
  pair: AstroPairStatus,
  states: Record<string, InstrumentState>
): AstroNavigationRoute | null {
  const legs = astroLegs(pair);
  if (!legs) return null;
  const markets = legs.map((leg) => astroMarket(leg, states));
  return markets.every((market): market is MarketSnapshot => market !== null)
    ? { legs, markets: markets as [MarketSnapshot, MarketSnapshot] }
    : null;
}

function positivePrice(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function executablePrice(market: MarketSnapshot, side: "bid" | "ask"): number | null {
  return positivePrice(market[side]) ?? positivePrice(marketPrice(market));
}

function spreadPct(left: number, right: number): number {
  return (right - left) / ((left + right) / 2) * 100;
}

function astroRatioReference(pair: AstroPairStatus): number {
  return positivePrice(finiteNumber(pair.regressionValue)) ?? 1;
}

function astroEffectiveRatioReference(
  pair: AstroPairStatus,
  markets: [MarketSnapshot, MarketSnapshot]
): number {
  const buyAliasMultiplier = positivePrice(markets[0].symbol_alias_price_multiplier) ?? 1;
  const sellAliasMultiplier = positivePrice(markets[1].symbol_alias_price_multiplier) ?? 1;
  return astroRatioReference(pair) * buyAliasMultiplier / sellAliasMultiplier;
}

function astroMetrics(
  pair: AstroPairStatus,
  states: Record<string, InstrumentState>
): { value: AstroMetrics | null; error: string } {
  const legs = astroLegs(pair);
  if (!legs) return { value: null, error: "无法从卡片名称识别两腿标的" };
  const markets = legs.map((leg) => astroMarket(leg, states)) as [MarketSnapshot | null, MarketSnapshot | null];
  const missingIndex = markets.findIndex((market) => !market);
  if (missingIndex >= 0) {
    const leg = legs[missingIndex];
    if (leg.exchange === "rh-lighter") {
      return {
        value: null,
        error: "RH-Lighter 仅为 Astro 路由；没有已验证的独立公开实时行情"
      };
    }
    const state = states[astroInstrumentKey(leg)];
    if (!state) return { value: null, error: "实时行情刷新中" };
    const venue = exchangeLabels[leg.exchange] ?? leg.exchange;
    return {
      value: null,
      error: state.error || `未找到 ${venue} ${marketLabel(leg.marketType)}实时行情`
    };
  }
  const completeMarkets = markets as [MarketSnapshot, MarketSnapshot];
  const buyOpen = executablePrice(completeMarkets[0], "ask");
  const sellOpen = executablePrice(completeMarkets[1], "bid");
  const buyClose = executablePrice(completeMarkets[0], "bid");
  const sellClose = executablePrice(completeMarkets[1], "ask");
  if ([buyOpen, sellOpen, buyClose, sellClose].some((value) => value === null)) {
    return { value: null, error: "实时行情缺少有效买卖价" };
  }
  const ratioMode = pair.type?.toUpperCase().endsWith("R") === true;
  const ratioReference = astroEffectiveRatioReference(pair, completeMarkets);
  return {
    value: {
      legs,
      markets: completeMarkets,
      openMetric: ratioMode
        ? spreadPct(buyOpen!, sellOpen! * ratioReference)
        : spreadPct(buyOpen!, sellOpen!),
      closeMetric: ratioMode
        ? spreadPct(buyClose!, sellClose! * ratioReference)
        : spreadPct(buyClose!, sellClose!),
      buyClose: buyClose!,
      sellClose: sellClose!
    },
    error: ""
  };
}

function astroProfitFeeRate(legs: [AstroLeg, AstroLeg]): number {
  const hyperliquidLegs = legs.filter((leg) => leg.exchange === "hyperliquid").length;
  if (hyperliquidLegs === 2) return 0.0005;
  if (hyperliquidLegs === 1) return 0.0013;
  return 0.002;
}

function astroLocalProfitEstimate(
  pair: AstroPairStatus,
  metrics: AstroMetrics,
  position: AstroPositionMetrics
): AstroProfitEstimate | null {
  const buyQuantity = Math.abs(finiteNumber(pair.aExPosition) ?? 0);
  const sellQuantity = Math.abs(finiteNumber(pair.bExPosition) ?? 0);
  if (buyQuantity === 0 && sellQuantity === 0) return null;

  const averageBuy = positivePrice(finiteNumber(pair.avgOpenAExPrice));
  const averageSell = positivePrice(finiteNumber(pair.avgOpenBExPrice));
  if ((buyQuantity > 0 && averageBuy === null) || (sellQuantity > 0 && averageSell === null)) return null;

  const buyProfit = buyQuantity === 0 ? 0 : (metrics.buyClose - averageBuy!) * buyQuantity;
  const sellProfit = sellQuantity === 0 ? 0 : (averageSell! - metrics.sellClose) * sellQuantity;
  const feeRate = astroProfitFeeRate(metrics.legs);
  const fee = (position.buyNotional + position.sellNotional) * feeRate;
  return { value: buyProfit + sellProfit - fee, feeRate };
}

function astroSpreadMetric(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "-";
  const displayValue = Math.abs(value) < 0.005 ? 0 : value;
  return `${displayValue.toFixed(2)}%`;
}

function compactHours(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null || parsed <= 0) return "?h";
  return `${parsed.toFixed(2).replace(/\.?0+$/, "")}h`;
}

function astroFundingMetric(market: MarketSnapshot): string {
  if (market.market_type === "spot") return "现货";
  const rate = finiteNumber(market.funding_rate_pct);
  const rateText = rate === null ? "--" : `${rate >= 0 ? "+" : ""}${rate.toFixed(4)}%`;
  return `${rateText}/${compactHours(market.funding_interval_hours)}`;
}

function astroThresholdMetric(pair: AstroPairStatus, value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return "-";
  const ratioMode = pair.type?.toUpperCase().endsWith("R") === true;
  if (ratioMode) {
    const ratioThreshold = positivePrice(parsed);
    if (ratioThreshold === null) return "-";
    return astroSpreadMetric(spreadPct(ratioThreshold, astroRatioReference(pair)));
  }
  return astroSpreadMetric(parsed * 100);
}

function usdt(value: number | null, signed = false): string {
  if (value === null || !Number.isFinite(value)) return "-";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} U`;
}

function astroPositionUsdt(value: number): string {
  return `${Math.trunc(value).toLocaleString("en-US")} U`;
}

function astroProfitUsdt(value: number | null): string {
  return value === null ? "--" : usdt(value, true);
}

function astroLegLabel(leg: AstroLeg): string {
  const venue = leg.exchange === "hyperliquid" && leg.dex
    ? `Hyperliquid ${leg.dex}`
    : exchangeLabels[leg.exchange] ?? leg.exchange;
  return `${venue} ${marketLabel(leg.marketType)}`;
}

function marketLabel(value: string): string {
  return value === "spot" ? "现货" : "永续";
}

function pairLegLabel(preset: PairSpreadPreset, side: 1 | 2): string {
  const exchange = preset[`leg${side}_exchange`];
  const dex = preset[`leg${side}_dex`];
  const venue = exchange === "hyperliquid" && dex && dex !== "main"
    ? `Hyperliquid ${dex}`
    : exchangeLabels[exchange] ?? exchange;
  return `${venue} ${marketLabel(preset[`leg${side}_market_type`])}`;
}

function navigateFromWatch(url: URL, standalone: boolean, preserveStandalone = false): void {
  url.searchParams.delete(STANDALONE_QUERY_PARAM);
  const destination = `${url.pathname}${url.search}${url.hash}`;
  if (standalone) {
    try {
      if (
        window.opener
        && !window.opener.closed
        && window.opener.location.origin === window.location.origin
      ) {
        window.opener.postMessage({
          type: FLOATING_WATCH_NAVIGATE_MESSAGE,
          destination
        }, window.location.origin);
        window.opener.focus();
        return;
      }
    } catch {
      // Fall through when the opener is no longer same-origin or accessible.
    }
    if (preserveStandalone) {
      window.open(destination, "_blank", "noopener,noreferrer");
      return;
    }
    const appWindow = window.open(destination, "_blank");
    if (appWindow) {
      appWindow.focus();
      return;
    }
    window.location.assign(destination);
    return;
  }
  window.history.pushState({}, "", destination);
  window.dispatchEvent(new Event("taoli1:navigate"));
}

function openInstrument(symbol: string, standalone: boolean): void {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "instrument");
  url.searchParams.set("symbol", symbol);
  navigateFromWatch(url, standalone);
}

function astroPairSpreadLegRoute(leg: AstroLeg, market: MarketSnapshot): { symbol: string; dex: string } {
  const rawSymbol = market.raw_symbol.trim();
  if (leg.exchange === "hyperliquid" && leg.marketType === "future") {
    const separatorIndex = rawSymbol.indexOf(":");
    if (separatorIndex > 0) {
      return {
        symbol: rawSymbol.slice(separatorIndex + 1).trim() || leg.symbol,
        dex: rawSymbol.slice(0, separatorIndex).trim().toLowerCase() || leg.dex || "main"
      };
    }
    return { symbol: rawSymbol || leg.symbol, dex: leg.dex || "main" };
  }
  return { symbol: rawSymbol || leg.symbol, dex: "" };
}

function astroPairSpreadMultiplier(pair: AstroPairStatus, markets: [MarketSnapshot, MarketSnapshot]): number {
  if (!pair.type?.toUpperCase().endsWith("R")) return 1;
  const regressionValue = astroRatioReference(pair);
  const buyAliasMultiplier = positivePrice(markets[0].symbol_alias_price_multiplier) ?? 1;
  const sellAliasMultiplier = positivePrice(markets[1].symbol_alias_price_multiplier) ?? 1;
  // Pair spread applies market aliases separately, then expresses Astro's ratio by dividing leg 2.
  return sellAliasMultiplier / (regressionValue * buyAliasMultiplier);
}

function openAstroPair(
  pair: AstroPairStatus,
  route: AstroNavigationRoute,
  standalone: boolean
): void {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "pair-monitor");
  url.searchParams.delete("symbol");
  route.legs.forEach((leg, index) => {
    const key = index + 1;
    const marketRoute = astroPairSpreadLegRoute(leg, route.markets[index]);
    url.searchParams.set(`leg${key}_exchange`, leg.exchange);
    url.searchParams.set(`leg${key}_market_type`, leg.marketType);
    url.searchParams.set(`leg${key}_symbol`, marketRoute.symbol);
    url.searchParams.set(`leg${key}_raw_symbol`, route.markets[index].raw_symbol);
    url.searchParams.set(
      `leg${key}_price_multiplier`,
      String(route.markets[index].symbol_alias_price_multiplier ?? 1)
    );
    if (route.markets[index].contract_size_multiplier !== null && route.markets[index].contract_size_multiplier !== undefined) {
      url.searchParams.set(
        `leg${key}_contract_size_multiplier`,
        String(route.markets[index].contract_size_multiplier)
      );
    } else {
      url.searchParams.delete(`leg${key}_contract_size_multiplier`);
    }
    if (marketRoute.dex) url.searchParams.set(`leg${key}_dex`, marketRoute.dex);
    else url.searchParams.delete(`leg${key}_dex`);
  });
  url.searchParams.set("leg2_multiplier", String(astroPairSpreadMultiplier(pair, route.markets)));
  url.searchParams.set("hours", "4");
  url.searchParams.set("interval_seconds", "60");
  url.searchParams.delete("interval_minutes");
  navigateFromWatch(url, standalone, true);
}

function openPair(preset: PairSpreadPreset, standalone: boolean): void {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "pair-monitor");
  url.searchParams.delete("symbol");
  ([1, 2] as const).forEach((side) => {
    url.searchParams.set(`leg${side}_exchange`, preset[`leg${side}_exchange`]);
    url.searchParams.set(`leg${side}_market_type`, preset[`leg${side}_market_type`]);
    url.searchParams.set(`leg${side}_symbol`, preset[`leg${side}_symbol`]);
    const dex = preset[`leg${side}_dex`];
    if (dex) url.searchParams.set(`leg${side}_dex`, dex);
    else url.searchParams.delete(`leg${side}_dex`);
    const rawSymbol = preset[`leg${side}_raw_symbol`];
    if (rawSymbol) url.searchParams.set(`leg${side}_raw_symbol`, rawSymbol);
    else url.searchParams.delete(`leg${side}_raw_symbol`);
    const priceMultiplier = preset[`leg${side}_price_multiplier`];
    if (typeof priceMultiplier === "number") {
      url.searchParams.set(`leg${side}_price_multiplier`, String(priceMultiplier));
    } else {
      url.searchParams.delete(`leg${side}_price_multiplier`);
    }
    const contractMultiplier = preset[`leg${side}_contract_size_multiplier`];
    if (typeof contractMultiplier === "number") {
      url.searchParams.set(`leg${side}_contract_size_multiplier`, String(contractMultiplier));
    } else {
      url.searchParams.delete(`leg${side}_contract_size_multiplier`);
    }
  });
  url.searchParams.set("leg2_multiplier", String(preset.leg2_multiplier));
  url.searchParams.set("hours", String(preset.hours));
  url.searchParams.set("interval_seconds", String(preset.intervalSeconds));
  url.searchParams.delete("interval_minutes");
  navigateFromWatch(url, standalone);
}

type FloatingWatchPanelProps = {
  visible: boolean;
  onClose: () => void;
  standalone?: boolean;
};

export function FloatingWatchPanel({ visible, onClose, standalone = false }: FloatingWatchPanelProps) {
  const [mode, setMode] = useState<WatchMode>("symbols");
  const [collapsed, setCollapsed] = useState(
    () => !standalone && window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1"
  );
  const [position, setPosition] = useState<SavedPosition | null>(loadPosition);
  const [settings, setSettings] = useState<FloatingWatchSettings>(emptySettings);
  const [presets, setPresets] = useState<PairSpreadPreset[]>([]);
  const [instruments, setInstruments] = useState<Record<string, InstrumentState>>({});
  const [pairs, setPairs] = useState<Record<string, PairState>>({});
  const [astroPairs, setAstroPairs] = useState<AstroPairStatus[]>([]);
  const [astroInstruments, setAstroInstruments] = useState<Record<string, InstrumentState>>({});
  const [accountPositions, setAccountPositions] = useState<AccountPositionSnapshot | null>(null);
  const [accountPositionError, setAccountPositionError] = useState("");
  const [managingHiddenPositions, setManagingHiddenPositions] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [astroError, setAstroError] = useState("");
  const [removing, setRemoving] = useState("");
  const [positionMutation, setPositionMutation] = useState("");
  const [showDustPositions, setShowDustPositions] = useState(
    () => window.localStorage.getItem(SHOW_DUST_POSITIONS_STORAGE_KEY) === "1"
  );
  const panelRef = useRef<HTMLElement | null>(null);
  const refreshQueue = useRef<Promise<void>>(Promise.resolve());

  const refresh = useCallback((requestedMode: WatchMode): Promise<void> => {
    const run = async () => {
      setLoading(true);
      try {
        const astroResultPromise = listAstroPairs()
          .then((items) => ({ items, error: "" }))
          .catch((caught) => ({
            items: null,
            error: caught instanceof Error ? caught.message : String(caught)
          }));
        const nextSettings = await getFloatingWatchSettings();
        setSettings(nextSettings);
        if (requestedMode === "symbols") {
          const instrumentEntries = await mapWithConcurrency(
            nextSettings.symbols,
            3,
            async (symbol): Promise<[string, InstrumentState]> => {
              try {
                return [symbol, { result: await lookupInstrument(symbol), error: "" }];
              } catch (caught) {
                return [symbol, { result: null, error: caught instanceof Error ? caught.message : String(caught) }];
              }
            }
          );
          setInstruments(Object.fromEntries(instrumentEntries));
        } else if (requestedMode === "pairs") {
          const allPresets = await listPairSpreadPresets();
          const watchedPresets = nextSettings.pair_ids
            .map((id) => allPresets.find((preset) => preset.id === id))
            .filter((preset): preset is PairSpreadPreset => Boolean(preset));
          const pairEntries = await mapWithConcurrency(
            watchedPresets,
            3,
            async (preset): Promise<[string, PairState]> => {
              try {
                const result = await queryPairSpread({
                  leg1_exchange: preset.leg1_exchange,
                  leg1_market_type: preset.leg1_market_type,
                  leg1_dex: preset.leg1_dex || undefined,
                  leg1_symbol: preset.leg1_symbol,
                  leg2_exchange: preset.leg2_exchange,
                  leg2_market_type: preset.leg2_market_type,
                  leg2_dex: preset.leg2_dex || undefined,
                  leg2_symbol: preset.leg2_symbol,
                  leg2_multiplier: preset.leg2_multiplier,
                  hours: 1,
                  interval_seconds: 5,
                  include_current: true
                });
                return [preset.id, { result, error: "" }];
              } catch (caught) {
                return [preset.id, { result: null, error: caught instanceof Error ? caught.message : String(caught) }];
              }
            }
          );
          setPresets(watchedPresets);
          setPairs(Object.fromEntries(pairEntries));
        } else if (requestedMode === "positions") {
          try {
            setAccountPositions(await listAccountPositions());
            setAccountPositionError("");
          } catch (caught) {
            setAccountPositionError(caught instanceof Error ? caught.message : String(caught));
          }
        }
        const astroResult = await astroResultPromise;
        if (astroResult.items) {
          setAstroPairs(astroResult.items);
          if (requestedMode === "astro") {
            const legs = Array.from(new Map(
              astroResult.items
                .filter((pair) => pair.status === true)
                .flatMap((pair) => astroLegs(pair) ?? [])
                .map((leg): [string, AstroLeg] => [astroInstrumentKey(leg), leg])
            ).values());
            const instrumentEntries = await mapWithConcurrency(
              legs,
              3,
              async (leg): Promise<[string, InstrumentState]> => {
                const key = astroInstrumentKey(leg);
                try {
                  return [key, {
                    result: await lookupInstrument(
                      leg.symbol,
                      leg.exchange === "hyperliquid" ? (leg.dex || undefined) : undefined
                    ),
                    error: ""
                  }];
                } catch (caught) {
                  return [key, {
                    result: null,
                    error: caught instanceof Error ? caught.message : String(caught)
                  }];
                }
              }
            );
            setAstroInstruments(Object.fromEntries(instrumentEntries));
          }
        }
        setAstroError(astroResult.error);
        setError("");
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setLoading(false);
      }
    };
    const queued = refreshQueue.current.catch(() => undefined).then(run);
    refreshQueue.current = queued;
    return queued;
  }, []);

  useEffect(() => {
    if (!visible || collapsed) return undefined;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      await refresh(mode);
      if (!stopped) timer = window.setTimeout(() => void poll(), REFRESH_INTERVAL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [collapsed, mode, refresh, visible]);

  useEffect(() => {
    const handleUpdate = (event: Event) => {
      const updated = (event as CustomEvent<FloatingWatchSettings>).detail;
      if (updated) {
        setSettings({
          ...updated,
          hidden_positions: Array.isArray(updated.hidden_positions) ? updated.hidden_positions : []
        });
      }
      if (!standalone) {
        setCollapsed(false);
        window.localStorage.setItem(COLLAPSED_STORAGE_KEY, "0");
      }
      if (visible) void refresh(mode);
    };
    window.addEventListener(FLOATING_WATCH_UPDATED_EVENT, handleUpdate);
    return () => window.removeEventListener(FLOATING_WATCH_UPDATED_EVENT, handleUpdate);
  }, [mode, refresh, standalone, visible]);

  useEffect(() => {
    if (!visible || standalone || !position) return undefined;
    const keepPanelInViewport = () => {
      if (window.innerWidth <= 600) return;
      const panel = panelRef.current;
      if (!panel) return;
      setPosition((current) => {
        if (!current) return current;
        const bounds = panel.getBoundingClientRect();
        const next = clampPosition(current, bounds.width, bounds.height);
        if (next.left === current.left && next.top === current.top) return current;
        window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(next));
        return next;
      });
    };
    keepPanelInViewport();
    window.addEventListener("resize", keepPanelInViewport);
    return () => window.removeEventListener("resize", keepPanelInViewport);
  }, [collapsed, position, standalone, visible]);

  const missingPairIds = useMemo(
    () => settings.pair_ids.filter((id) => !presets.some((preset) => preset.id === id)),
    [presets, settings.pair_ids]
  );
  const runningAstroPairs = useMemo(
    () => astroPairs.filter((pair) => pair.status === true),
    [astroPairs]
  );
  const tradingAstroPairs = useMemo(
    () => runningAstroPairs.filter(astroHasPosition),
    [runningAstroPairs]
  );
  const tradingSymbols = useMemo(
    () => new Set(
      tradingAstroPairs.flatMap((pair) => astroLegs(pair)?.map((leg) => leg.symbol) ?? [])
    ),
    [tradingAstroPairs]
  );
  const hiddenPositions = settings.hidden_positions ?? [];
  const hiddenPositionIds = useMemo(
    () => new Set(hiddenPositions.map((accountPosition) => accountPosition.id)),
    [hiddenPositions]
  );
  const unhiddenAccountPositions = useMemo(
    () => (accountPositions?.positions ?? []).filter((item) => !hiddenPositionIds.has(item.id)),
    [accountPositions, hiddenPositionIds]
  );
  const dustAccountPositions = useMemo(
    () => unhiddenAccountPositions.filter(
      (item) => item.notional_usdt !== null
        && item.notional_usdt < MIN_VISIBLE_POSITION_NOTIONAL_USDT
    ),
    [unhiddenAccountPositions]
  );
  const visibleAccountPositions = useMemo(
    () => showDustPositions
      ? unhiddenAccountPositions
      : unhiddenAccountPositions.filter(
        (item) => item.notional_usdt === null
          || item.notional_usdt >= MIN_VISIBLE_POSITION_NOTIONAL_USDT
      ),
    [showDustPositions, unhiddenAccountPositions]
  );
  const positionIssueAccounts = useMemo(
    () => (accountPositions?.accounts ?? []).filter(
      (account) => ["permission_denied", "error", "stale"].includes(account.state)
    ),
    [accountPositions]
  );
  const configuredPositionAccounts = useMemo(
    () => (accountPositions?.accounts ?? []).filter((account) => account.configured),
    [accountPositions]
  );
  const configuredPositionAccountCount = useMemo(
    () => new Set(configuredPositionAccounts.map((account) => account.account_id)).size,
    [configuredPositionAccounts]
  );
  const positionAccountCount = useMemo(
    () => new Set((accountPositions?.accounts ?? []).map((account) => account.account_id)).size,
    [accountPositions]
  );

  if (!visible) return null;

  const panelStyle: CSSProperties | undefined = !standalone && position
    ? { left: position.left, top: position.top, right: "auto", bottom: "auto" }
    : undefined;

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (standalone) return;
    if ((event.target as HTMLElement).closest("button")) return;
    if (window.innerWidth <= 600) return;
    const panel = event.currentTarget.closest<HTMLElement>(".floating-watch-panel");
    if (!panel) return;
    const bounds = panel.getBoundingClientRect();
    const offsetX = event.clientX - bounds.left;
    const offsetY = event.clientY - bounds.top;
    const move = (pointerEvent: PointerEvent) => {
      setPosition(clampPosition(
        { left: pointerEvent.clientX - offsetX, top: pointerEvent.clientY - offsetY },
        bounds.width,
        bounds.height
      ));
    };
    const stop = (pointerEvent: PointerEvent) => {
      move(pointerEvent);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      const next = clampPosition(
        { left: pointerEvent.clientX - offsetX, top: pointerEvent.clientY - offsetY },
        bounds.width,
        bounds.height
      );
      window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(next));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  };

  const setPanelCollapsed = (next: boolean) => {
    setCollapsed(next);
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
  };

  const toggleDustPositions = () => {
    setShowDustPositions((current) => {
      const next = !current;
      window.localStorage.setItem(SHOW_DUST_POSITIONS_STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  };

  const openStandaloneWindow = () => {
    const url = new URL(window.location.href);
    url.search = "";
    url.hash = "";
    url.searchParams.set(STANDALONE_QUERY_PARAM, STANDALONE_QUERY_VALUE);
    const width = 420;
    const height = Math.max(420, Math.min(720, window.screen.availHeight - 80));
    const left = Math.max(0, window.screen.availWidth - width - 24);
    const top = Math.max(0, Math.min(64, window.screen.availHeight - height));
    const popup = window.open(
      url.toString(),
      STANDALONE_WINDOW_NAME,
      `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=no`
    );
    if (!popup) {
      setError("浏览器拦截了独立窗口，请允许本站打开弹出窗口。");
      return;
    }
    popup.focus();
    onClose();
  };

  const removeSymbol = async (symbol: string) => {
    setRemoving(`symbol:${symbol}`);
    try {
      const next = await removeFloatingWatchSymbol(symbol);
      setSettings((current) => ({
        ...next,
        hidden_positions: Array.isArray(next.hidden_positions)
          ? next.hidden_positions
          : current.hidden_positions ?? []
      }));
      setInstruments((current) => {
        const copy = { ...current };
        delete copy[symbol];
        return copy;
      });
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving("");
    }
  };

  const removePair = async (pairId: string) => {
    setRemoving(`pair:${pairId}`);
    try {
      const next = await removeFloatingWatchPair(pairId);
      setSettings((current) => ({
        ...next,
        hidden_positions: Array.isArray(next.hidden_positions)
          ? next.hidden_positions
          : current.hidden_positions ?? []
      }));
      setPresets((current) => current.filter((preset) => preset.id !== pairId));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRemoving("");
    }
  };

  const changePositionVisibility = async (
    action: "add" | "remove",
    accountPosition: AccountPositionIdentity
  ) => {
    setPositionMutation(accountPosition.id);
    try {
      const next = await mutateFloatingWatchPosition(action, accountPosition);
      setSettings({
        ...next,
        hidden_positions: Array.isArray(next.hidden_positions) ? next.hidden_positions : []
      });
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPositionMutation("");
    }
  };

  return (
    <aside
      ref={panelRef}
      className={`floating-watch-panel${collapsed ? " floating-watch-panel-collapsed" : ""}${standalone ? " floating-watch-panel-standalone" : ""}`}
      style={panelStyle}
      aria-label={standalone ? "独立关注窗口" : "关注浮窗"}
    >
      <div className="floating-watch-header" onPointerDown={startDrag}>
        {!standalone ? <span className="floating-watch-drag" aria-hidden="true"><DragOutlined /></span> : null}
        <PushpinOutlined />
        <Typography.Text strong>关注行情</Typography.Text>
        <Tag>{settings.symbols.length + settings.pair_ids.length}</Tag>
        <span className="floating-watch-header-actions">
          {!collapsed ? (
            <Tooltip title="立即刷新">
              <Button aria-label="刷新关注行情" type="text" size="small" icon={<ReloadOutlined spin={loading} />} onClick={() => void refresh(mode)} />
            </Tooltip>
          ) : null}
          {!standalone ? (
            <Tooltip title="打开独立窗口">
              <Button
                aria-label="打开独立关注窗口"
                type="text"
                size="small"
                icon={<ExportOutlined />}
                onClick={openStandaloneWindow}
              />
            </Tooltip>
          ) : null}
          {!standalone ? (
            <Tooltip title={collapsed ? "展开" : "收起"}>
              <Button
                aria-label={collapsed ? "展开关注浮窗" : "收起关注浮窗"}
                type="text"
                size="small"
                icon={collapsed ? <LineChartOutlined /> : <MinusOutlined />}
                onClick={() => setPanelCollapsed(!collapsed)}
              />
            </Tooltip>
          ) : null}
          <Tooltip title={standalone ? "关闭窗口" : "隐藏"}>
            <Button
              aria-label={standalone ? "关闭独立关注窗口" : "隐藏关注浮窗"}
              type="text"
              size="small"
              icon={<CloseOutlined />}
              onClick={onClose}
            />
          </Tooltip>
        </span>
      </div>
      {!collapsed ? (
        <div className="floating-watch-body">
          <Segmented
            block
            size="small"
            value={mode}
            options={[
              { label: `标的 ${settings.symbols.length}`, value: "symbols" },
              { label: `交易对 ${settings.pair_ids.length}`, value: "pairs" },
              {
                label: `Astro ${runningAstroPairs.length}${tradingAstroPairs.length ? ` · ${tradingAstroPairs.length} 持仓` : ""}`,
                value: "astro"
              },
              { label: `持仓 ${visibleAccountPositions.length}`, value: "positions" }
            ]}
            onChange={(value) => setMode(value as WatchMode)}
          />
          {error ? <Alert type="warning" showIcon message={error} /> : null}
          {mode === "astro" && astroError ? <Alert type="warning" showIcon message={`Astro 卡片读取失败：${astroError}`} /> : null}
          {mode === "positions" && accountPositionError ? (
            <Alert type="warning" showIcon message={`账户持仓读取失败：${accountPositionError}`} />
          ) : null}
          {loading && (
            (mode === "symbols" && settings.symbols.length === 0)
            || (mode === "pairs" && settings.pair_ids.length === 0)
            || (mode === "astro" && runningAstroPairs.length === 0)
            || (mode === "positions" && accountPositions === null)
          ) ? <Spin className="floating-watch-loading" /> : null}
          {mode === "symbols" ? (
            <div className="floating-watch-list">
              {settings.symbols.map((symbol) => {
                const state = instruments[symbol];
                const range = instrumentPriceRange(state?.result ?? null);
                const isTrading = tradingSymbols.has(astroSymbol(symbol));
                const bestSpread = state?.result?.spreads.length
                  ? Math.max(...state.result.spreads.map((spread) => spread.executable_spread_pct))
                  : null;
                return (
                  <div className={`floating-watch-row${isTrading ? " floating-watch-row-trading" : ""}`} key={symbol}>
                    <button className="floating-watch-row-main" type="button" onClick={() => openInstrument(symbol, standalone)}>
                      <span className="floating-watch-row-title floating-watch-symbol-title">
                        <span>{symbol.replace(/USDT$/, "")}</span>
                        {isTrading ? (
                          <span className="floating-watch-trading-status">
                            <span className="floating-watch-trading-dot" aria-hidden="true" />
                            交易中
                          </span>
                        ) : null}
                      </span>
                      <span className="floating-watch-row-sub">{range ? `${price(range.min)} - ${price(range.max)}` : state?.error || "等待刷新"}</span>
                      <span className={`floating-watch-value floating-watch-value-${tone(bestSpread)}`}>{signedPct(bestSpread)}</span>
                    </button>
                    <Tooltip title="取消关注">
                      <Button
                        aria-label={`取消关注标的 ${symbol}`}
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removing === `symbol:${symbol}`}
                        onClick={() => void removeSymbol(symbol)}
                      />
                    </Tooltip>
                  </div>
                );
              })}
              {!loading && settings.symbols.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有关注标的" /> : null}
            </div>
          ) : mode === "pairs" ? (
            <div className="floating-watch-list">
              {presets.map((preset) => {
                const state = pairs[preset.id];
                const current = state?.result?.current;
                const spread = current?.open_spread_pct ?? current?.spread_pct ?? null;
                return (
                  <div className="floating-watch-row floating-watch-pair-row" key={preset.id}>
                    <button className="floating-watch-row-main" type="button" onClick={() => openPair(preset, standalone)}>
                      <span className="floating-watch-row-title">{preset.leg1_symbol.replace(/USDT$/, "")} / {preset.leg2_symbol.replace(/USDT$/, "")}</span>
                      <span className="floating-watch-row-sub">{pairLegLabel(preset, 1)} {price(current?.leg1.price)} → {pairLegLabel(preset, 2)} {price(current?.leg2.price)}</span>
                      <span className={`floating-watch-value floating-watch-value-${tone(spread)}`}>{state?.error ? "异常" : signedPct(spread)}</span>
                    </button>
                    <Tooltip title="取消关注">
                      <Button
                        aria-label={`取消关注交易对 ${preset.id}`}
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removing === `pair:${preset.id}`}
                        onClick={() => void removePair(preset.id)}
                      />
                    </Tooltip>
                  </div>
                );
              })}
              {missingPairIds.map((pairId) => (
                <div className="floating-watch-row floating-watch-missing-row" key={pairId}>
                  <div className="floating-watch-row-main">
                    <span className="floating-watch-row-title">已删除的交易对</span>
                    <span className="floating-watch-row-sub">保存配置不存在，请移出浮窗</span>
                  </div>
                  <Button aria-label={`取消关注交易对 ${pairId}`} type="text" size="small" danger icon={<DeleteOutlined />} onClick={() => void removePair(pairId)} />
                </div>
              ))}
              {!loading && settings.pair_ids.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有关注交易对" /> : null}
            </div>
          ) : mode === "astro" ? (
            <div className="floating-watch-list">
              {runningAstroPairs.map((pair, index) => {
                const runtime = astroRuntimeState(pair);
                const astroProfit = finiteNumber(pair.profit);
                const realizedProfit = astroRealizedProfit(pair);
                const hasPosition = astroHasPosition(pair);
                const position = astroPositionMetrics(pair);
                const live = astroMetrics(pair, astroInstruments);
                const localProfit = astroProfit === null && live.value
                  ? astroLocalProfitEstimate(pair, live.value, position)
                  : null;
                const estimatedProfit = astroProfit ?? localProfit?.value ?? null;
                const profitTone = tone(estimatedProfit);
                const ratioMode = pair.type?.toUpperCase().endsWith("R") === true;
                const navigationRoute = astroNavigationRoute(pair, astroInstruments);
                return (
                  <div className="floating-watch-row floating-watch-astro-row" key={pair.id || `${pair.name || "astro"}-${index}`}>
                    <div className="floating-watch-row-main floating-watch-astro-row-main">
                      <span className="floating-watch-row-title floating-watch-astro-title" title={pair.name || "未命名卡片"}>
                        <button
                          className="floating-watch-astro-link"
                          type="button"
                          aria-label={`打开 Astro 交易对 ${pair.name || "未命名卡片"} 的价差查询`}
                          title={navigationRoute ? "打开价差查询" : "等待双方市场信息后可打开价差查询"}
                          disabled={!navigationRoute}
                          onClick={() => navigationRoute && openAstroPair(pair, navigationRoute, standalone)}
                        >
                          <span>{pair.name || "未命名卡片"}</span>
                          <LineChartOutlined aria-hidden="true" />
                        </button>
                        <span className="floating-watch-astro-type">{pair.type || "-"}</span>
                      </span>
                      <span className={`floating-watch-value floating-watch-astro-state floating-watch-astro-state-${runtime.tone}`}>
                        {runtime.label}
                      </span>
                      {live.value ? (
                        <>
                          <span className="floating-watch-row-sub floating-watch-astro-route">
                            {astroLegLabel(live.value.legs[0])} {price(marketPrice(live.value.markets[0]))}
                            <span aria-hidden="true"> → </span>
                            {astroLegLabel(live.value.legs[1])} {price(marketPrice(live.value.markets[1]))}
                          </span>
                          <span className="floating-watch-row-sub floating-watch-astro-spread">
                            <span>当前价差</span>
                            <strong className={ratioMode ? undefined : `floating-watch-value-${tone(live.value.openMetric)}`}>
                              开 {astroSpreadMetric(live.value.openMetric)} / 平 {astroSpreadMetric(live.value.closeMetric)}
                            </strong>
                          </span>
                          <span
                            className="floating-watch-row-sub floating-watch-astro-spread floating-watch-astro-funding"
                            title="当前单次结算资金费率 / 结算周期"
                          >
                            <span>资金费率</span>
                            <strong>
                              买 {astroFundingMetric(live.value.markets[0])} · 卖 {astroFundingMetric(live.value.markets[1])}
                            </strong>
                          </span>
                        </>
                      ) : (
                        <span className="floating-watch-row-sub floating-watch-astro-error">{live.error || "实时行情刷新中"}</span>
                      )}
                      <span className="floating-watch-row-sub floating-watch-astro-spread">
                        <span>
                          开清条件 {ratioMode ? <span className="floating-watch-astro-type">1:{price(astroRatioReference(pair))}</span> : null}
                        </span>
                        <strong>开 {astroThresholdMetric(pair, pair.openPosition)} / 平 {astroThresholdMetric(pair, pair.closePosition)}</strong>
                      </span>
                      {hasPosition ? (
                        <>
                          <div className="floating-watch-astro-metrics">
                            <span><span>买腿仓位</span><strong>{astroPositionUsdt(position.buyNotional)}</strong></span>
                            <span><span>卖腿仓位</span><strong>{astroPositionUsdt(position.sellNotional)}</strong></span>
                            <span className={`floating-watch-astro-profit floating-watch-value-${profitTone}`}>
                              <span>{astroProfit !== null ? "Astro 预估" : "本地预估"}</span>
                              <strong>{astroProfitUsdt(estimatedProfit)}</strong>
                            </span>
                            <span><span>已实现</span><strong>{astroProfitUsdt(realizedProfit)}</strong></span>
                          </div>
                          {astroProfit === null ? (
                            <span className="floating-watch-row-sub floating-watch-astro-profit-note">
                              {localProfit
                                ? `本地估算 · 已按双腿仓位扣 ${(localProfit.feeRate * 100).toFixed(2)}% 手续费`
                                : "Astro 未返回，等待实时行情后本地估算"}
                            </span>
                          ) : null}
                        </>
                      ) : (
                        <span className="floating-watch-row-sub floating-watch-astro-position">
                          当前无持仓 · 已实现 {astroProfitUsdt(realizedProfit)}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
              {!loading && !astroError && runningAstroPairs.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有正在运行的 Astro 卡片" />
              ) : null}
            </div>
          ) : (
            <div className="floating-watch-list floating-watch-position-list">
              <div className="floating-watch-position-toolbar">
                <Typography.Text type="secondary">
                  已配置账户 {configuredPositionAccountCount} / {positionAccountCount}
                </Typography.Text>
                <span className="floating-watch-position-actions">
                  <Tooltip title={showDustPositions ? "隐藏小于 1 USDT 的持仓" : "显示小于 1 USDT 的持仓"}>
                    <Button
                      aria-label={`${showDustPositions ? "隐藏" : "显示"}小于 1 USDT 的持仓（${dustAccountPositions.length}）`}
                      type={showDustPositions ? "default" : "text"}
                      size="small"
                      icon={<FilterOutlined />}
                      onClick={toggleDustPositions}
                    >
                      {dustAccountPositions.length}
                    </Button>
                  </Tooltip>
                  <Tooltip title={managingHiddenPositions ? "返回当前持仓" : "管理已屏蔽持仓"}>
                    <Button
                      aria-label={managingHiddenPositions ? "返回当前持仓" : "管理已屏蔽持仓"}
                      type={managingHiddenPositions ? "default" : "text"}
                      size="small"
                      icon={<EyeInvisibleOutlined />}
                      onClick={() => setManagingHiddenPositions((current) => !current)}
                    >
                      {hiddenPositions.length}
                    </Button>
                  </Tooltip>
                </span>
              </div>
              {managingHiddenPositions ? (
                <div className="floating-watch-hidden-positions" aria-label="已屏蔽持仓">
                  {hiddenPositions.map((hiddenPosition) => (
                    <div className="floating-watch-hidden-position" key={hiddenPosition.id}>
                      <span>
                        <strong>{hiddenPosition.raw_symbol}</strong>
                        <small>{positionIdentityLabel(hiddenPosition)}</small>
                      </span>
                      <Tooltip title="恢复到浮窗">
                        <Button
                          aria-label={`恢复持仓 ${positionIdentityLabel(hiddenPosition)}`}
                          type="text"
                          size="small"
                          icon={<UndoOutlined />}
                          loading={positionMutation === hiddenPosition.id}
                          onClick={() => void changePositionVisibility("remove", hiddenPosition)}
                        />
                      </Tooltip>
                    </div>
                  ))}
                  {hiddenPositions.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有已屏蔽持仓" />
                  ) : null}
                </div>
              ) : null}
              {positionIssueAccounts.map((account) => (
                <Alert
                  className="floating-watch-position-alert"
                  key={`${account.exchange}:${account.account_id}:${account.market_type}:${account.dex ?? ""}`}
                  type="warning"
                  showIcon
                  message={accountStatusLabel(account)}
                />
              ))}
              {!managingHiddenPositions ? visibleAccountPositions.map((accountPosition) => {
                const sideLabel = accountPosition.side === "long" ? "多" : "空";
                const estimatedRoi = accountPosition.estimated_fields.includes("roi_pct");
                const estimatedMark = accountPosition.estimated_fields.includes("mark_price");
                const estimatedNotional = accountPosition.estimated_fields.includes("notional_usdt");
                const estimatedPnl = accountPosition.estimated_fields.includes("unrealized_pnl_usdt");
                const multiplier = accountPosition.contract_multiplier === null
                  ? "--"
                  : `${price(accountPosition.contract_multiplier)} ${accountPosition.quantity_unit}/张`;
                return (
                  <article
                    className={`floating-watch-account-position floating-watch-account-position-${accountPosition.freshness}`}
                    key={accountPosition.id}
                  >
                    <div className="floating-watch-account-position-head">
                      <span className="floating-watch-account-position-title">
                        <strong title={accountPosition.raw_symbol}>{accountPosition.raw_symbol}</strong>
                        <small>{accountPosition.symbol}</small>
                      </span>
                      <Tag color={accountPosition.side === "long" ? "green" : "red"}>{sideLabel}</Tag>
                      <Tooltip title="仅在关注浮窗中屏蔽，不会修改交易所仓位">
                        <Button
                          aria-label={`在浮窗中屏蔽持仓 ${positionIdentityLabel(accountPosition)}`}
                          type="text"
                          size="small"
                          icon={<EyeInvisibleOutlined />}
                          loading={positionMutation === accountPosition.id}
                          onClick={() => void changePositionVisibility("add", accountPosition)}
                        />
                      </Tooltip>
                    </div>
                    <div className="floating-watch-account-position-route">
                      <span>{exchangeLabels[accountPosition.exchange] || accountPosition.exchange}</span>
                      <span>{accountPosition.account_label}</span>
                      <span>{positionMarketLabel(accountPosition)}</span>
                    </div>
                    <div className="floating-watch-account-position-metrics">
                      <span><small>数量</small><strong>{positionMetric(accountPosition.quantity)} {accountPosition.quantity_unit}</strong></span>
                      <span><small>开仓均价</small><strong>{positionMetric(accountPosition.entry_price)}</strong></span>
                      <span><small>标记价{estimatedMark ? "（估）" : ""}</small><strong>{positionMetric(accountPosition.mark_price)}</strong></span>
                      <span><small>名义价值{estimatedNotional ? "（估）" : ""}</small><strong>{positionMetric(accountPosition.notional_usdt, " U")}</strong></span>
                      <span className={`floating-watch-value-${tone(accountPosition.unrealized_pnl_usdt)}`}>
                        <small>未实现盈亏{estimatedPnl ? "（估）" : ""}</small><strong>{positionPnl(accountPosition.unrealized_pnl_usdt)}</strong>
                      </span>
                      <span className={`floating-watch-value-${tone(accountPosition.roi_pct)}`}>
                        <small>收益率{estimatedRoi ? "（估）" : ""}</small><strong>{positionPct(accountPosition.roi_pct)}</strong>
                      </span>
                      <span><small>杠杆</small><strong>{positionMetric(accountPosition.leverage, "x")}</strong></span>
                      <span><small>市场倍率</small><strong>{multiplier}</strong></span>
                    </div>
                    <div className="floating-watch-account-position-basis">
                      <span>{accountPosition.price_basis}</span>
                      {accountPosition.contract_quantity !== null ? (
                        <span>{positionMetric(accountPosition.contract_quantity)} 张</span>
                      ) : null}
                    </div>
                    <div className="floating-watch-account-position-update">
                      <span>{positionUpdatedAt(accountPosition.updated_at)}</span>
                      <Tag color={accountPosition.freshness === "fresh" ? "green" : "orange"}>
                        {accountPosition.freshness === "fresh"
                          ? `新鲜 · ${Math.round(accountPosition.age_seconds)}s`
                          : `过期 · ${Math.round(accountPosition.age_seconds)}s`}
                      </Tag>
                    </div>
                  </article>
                );
              }) : null}
              {!loading && !managingHiddenPositions && configuredPositionAccounts.length === 0 ? (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={(
                    <span className="floating-watch-position-empty-copy">
                      <span>未配置交易所账户持仓读取凭据</span>
                      <small>Astro 卡片状态不作为账户仓位来源</small>
                    </span>
                  )}
                />
              ) : null}
              {!loading
                && !managingHiddenPositions
                && configuredPositionAccounts.length > 0
                && (accountPositions?.positions.length ?? 0) === 0
                && positionIssueAccounts.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前无持仓" />
                ) : null}
              {!loading
                && !managingHiddenPositions
                && configuredPositionAccounts.length > 0
                && (accountPositions?.positions.length ?? 0) === 0
                && positionIssueAccounts.length > 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="账户状态未确认，不能判定为无持仓" />
                ) : null}
              {!loading
                && !managingHiddenPositions
                && (accountPositions?.positions.length ?? 0) > 0
                && visibleAccountPositions.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={unhiddenAccountPositions.length > 0
                      ? "当前持仓均小于 1 USDT，已默认隐藏"
                      : "当前持仓均已在浮窗中屏蔽"}
                  />
                ) : null}
            </div>
          )}
          <div className="floating-watch-footer">
            <Typography.Text type="secondary">
              {mode === "astro"
                ? "每 10 秒同步 Astro 运行状态"
                : mode === "positions"
                  ? "每 10 秒查询已配置账户 · 屏蔽仅影响本浮窗"
                  : "每 10 秒刷新 · 点击行查看详情"}
            </Typography.Text>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
