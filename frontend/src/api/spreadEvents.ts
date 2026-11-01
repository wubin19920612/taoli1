import { ApiError } from "./client";
import type { MarketType } from "./types";

export interface SpreadEventsParams {
  start_at: string;
  end_at: string;
  threshold: number;
  metric: "fee_adjusted" | "raw";
  min_duration_s: number;
  sort: "peak" | "duration" | "start";
  limit?: number;
  symbol?: string;
}

export interface SpreadEventLeg {
  exchange: string;
  market_type: MarketType;
  raw_symbol: string | null;
  dex: string | null;
}

export interface SpreadEvent {
  series_key: string;
  symbol: string;
  buy_leg: SpreadEventLeg;
  sell_leg: SpreadEventLeg;
  start_at: string;
  end_at: string;
  duration_s: number;
  peak: number;
  peak_at: string;
  mean: number;
  samples: number;
  end_reason: "below" | "gap" | "ongoing";
}

export interface SpreadEventsResponse {
  events: SpreadEvent[];
  scanned_rows: number;
  series_count: number;
  truncated: boolean;
  params: SpreadEventsParams;
}

export async function getSpreadEvents(
  params: SpreadEventsParams, signal?: AbortSignal
): Promise<SpreadEventsResponse> {
  const base = import.meta.env.VITE_API_BASE_URL ?? "/api";
  const url = new URL(`${base}/spread-events`, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  });
  const password = window.localStorage.getItem("dashboard_password");
  const response = await fetch(url, {
    headers: password ? { "X-Dashboard-Password": password } : {}, signal
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `价差事件查询失败 (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<SpreadEventsResponse>;
}

// Same URL protocol as OpportunityTable.openPairSpread, including exact HL identity.
export function spreadEventPairUrl(event: SpreadEvent): URL | null {
  const url = new URL(window.location.href);
  url.searchParams.set("page", "pair-monitor");
  for (const [index, leg] of [[1, event.buy_leg], [2, event.sell_leg]] as const) {
    if (leg.exchange === "hyperliquid" && !leg.raw_symbol) return null;
    const raw = leg.raw_symbol || event.symbol;
    const separator = leg.exchange === "hyperliquid" ? raw.indexOf(":") : -1;
    // Remove identity left over from a previous pair before setting this leg.
    for (const name of [...url.searchParams.keys()]) {
      if (name.startsWith(`leg${index}_`)) url.searchParams.delete(name);
    }
    url.searchParams.set(`leg${index}_exchange`, leg.exchange);
    url.searchParams.set(`leg${index}_market_type`, leg.market_type);
    url.searchParams.set(`leg${index}_symbol`, (separator > 0 ? raw.slice(separator + 1) : raw).toUpperCase());
    url.searchParams.set(`leg${index}_raw_symbol`, raw);
    if (leg.dex) url.searchParams.set(`leg${index}_dex`, leg.dex);
  }
  url.searchParams.set("leg2_multiplier", "1");
  url.searchParams.set("hours", "4");
  url.searchParams.set("interval_minutes", "5");
  return url;
}
