import { sessionFetch } from "./auth";

type Num = number | null;
type Text = string | null;
export type LiveExecResult<T> = { available: true; data: T } | { available: false; error: string };
export interface LivePeriod {
  net_usd: Num; fees_usd: Num; funding_usd: Num; trades: Num; wins: Num;
  daily_loss_limit_usd?: Num; total_loss_limit_usd?: Num;
}
export interface LiveAccount {
  equity_usd: Num; baseline_usd: Num; drop_usd: Num; margin_multiple: Num; ok: boolean | null; error: Text;
}
export interface LiveSummary {
  generated_at: Text; mode: "shadow" | "live" | null; halted: boolean | null; halt_reason: Text;
  stop_file: boolean | null; opening_paused_reason: Text;
  caps: { leg_max_usd: Num; total_max_usd: Num; total_open_usd: Num; equity_drop_max_usd: Num; max_pairs: Num; open_pairs: Num } | null;
  accounts: { crossex: LiveAccount | null; bitget: LiveAccount | null } | null;
  today: LivePeriod | null; total: LivePeriod | null;
  fees: { crossex_taker: Num; bitget_taker: Num } | null; last_error: Text;
}
interface Leg {
  exchange: Text; side: "long" | "short" | null; qty: Num;
}
export interface LivePosition {
  id: string; symbol: Text; opened_at: Text; held_seconds: Num;
  legs: (Leg & { route: Text; entry_price: Num; notional_usd: Num; mark_price: Num })[] | null;
  entry_spread_bps: Num; current_spread_bps: Num; unrealized_usd: Num; unrealized_bps: Num;
  next_funding_at: Text; stop_distance_bps: Num; max_hold_at: Text;
  status: "opening" | "open" | "closing" | "one_leg" | null;
}
export interface LiveTrade {
  id: string; symbol: Text; pair: Text; opened_at: Text; closed_at: Text; held_seconds: Num;
  legs: (Leg & { entry_vwap: Num; exit_vwap: Num })[] | null;
  entry_spread_bps: Num; fees_usd: Num; funding_usd: Num; net_usd: Num; net_bps: Num; exit_reason: Text;
  sim: { net_bps: Num; gross_bps: Num } | null; slippage_bps: Num;
  leg_send_gap_ms: Num; leg_fill_gap_ms: Num; one_leg_incident: boolean | null;
}
export interface LiveGroup { key: Text; n: Num; net_usd: Num; mean_bps: Num; win_rate: Num }
export interface LiveStats {
  days: Num; n: Num; histogram: { lo: number; hi: number; count: number }[] | null;
  by_symbol: LiveGroup[] | null; by_pair: LiveGroup[] | null;
  by_exit_reason: LiveGroup[] | null; by_spread_tier: LiveGroup[] | null;
  vs_sim: { n: Num; slippage_bps_p50: Num; slippage_bps_p90: Num;
    leg_fill_gap_ms_p50: Num; leg_fill_gap_ms_p90: Num; one_leg_rate: Num } | null;
}
export interface LiveResources { summary: LiveSummary | null; positions: LivePosition[] | null; trades: LiveTrade[] | null; stats: LiveStats | null }
export type LiveResource = keyof LiveResources;

export async function getLiveExec<K extends LiveResource>(
  resource: K, signal: AbortSignal, before?: string
): Promise<LiveExecResult<LiveResources[K]>> {
  const params = new URLSearchParams();
  if (resource === "trades") { params.set("limit", "200"); if (before) params.set("before", before); }
  if (resource === "stats") params.set("days", "7");
  try {
    const response = await sessionFetch(`/api/live-exec/${resource}${params.size ? `?${params}` : ""}`, { signal });
    if (!response.ok) return { available: false, error: "request_failed" };
    const result = await response.json();
    if (result?.available === false) return { available: false, error: String(result.error ?? "request_failed") };
    if (result?.available !== true || !("data" in result)) return { available: false, error: "invalid_json" };
    return result;
  } catch {
    return { available: false, error: "request_failed" };
  }
}
