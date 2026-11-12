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
export type LiveOpeningSkipReason = "book_skew" | "book_stale" | "funding_diff" | "baseline_not_flat"
  | "not_single_venue_outlier" | "too_few_venues" | "size_too_small" | "oi_missing"
  | "mapping_ratio_mismatch" | "volume_too_low" | "volume_unknown" | "category_unconfirmed";
export interface LiveSummary {
  generated_at: Text; mode: "shadow" | "live" | null; halted: boolean | null; halt_reason: Text;
  stop_file: boolean | null; opening_paused_reason: Text;
  caps: { leg_max_usd: Num; total_max_usd: Num; total_open_usd: Num; equity_drop_max_usd: Num; max_pairs: Num; open_pairs: Num } | null;
  accounts: { crossex: LiveAccount | null; bitget: LiveAccount | null } | null;
  today: LivePeriod | null; total: LivePeriod | null;
  fees: { crossex_taker: Num; bitget_taker: Num } | null; last_error: Text;
  baseline_ready_pairs?: Num; structural_spread_pairs?: Num; cooldown_pairs?: Num;
  opening_skip_counts?: Partial<Record<LiveOpeningSkipReason, Num>> | null;
  config_version?: Num;
  effective_risk_limits?: { max_equity_drop_usd: Num; daily_loss_stop_usd: Num; total_loss_stop_usd: Num } | null;
}
interface Leg {
  exchange: Text; side: "long" | "short" | null; qty: Num;
}
interface LiveOpeningSnapshot {
  baseline_bps?: Num; excess_bps?: Num; funding_cost_bps?: Num;
  open_book_skew_ms?: Num; close_book_skew_ms?: Num;
}
export interface LivePosition extends LiveOpeningSnapshot {
  id: string; symbol: Text; opened_at: Text; held_seconds: Num;
  legs: (Leg & { route: Text; entry_price: Num; notional_usd: Num; mark_price: Num })[] | null;
  entry_spread_bps: Num; current_spread_bps: Num; unrealized_usd: Num; unrealized_bps: Num;
  next_funding_at: Text; stop_distance_bps: Num; max_hold_at: Text;
  status: "opening" | "open" | "closing" | "one_leg" | null;
}
export interface LiveTrade extends LiveOpeningSnapshot {
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

export interface LiveConfigRule { default: number; min: number; max: number | null; integer: boolean }
export interface LiveConfig {
  version: number; values: Record<string, number>; fallback: boolean; schema: Record<string, LiveConfigRule>;
}
export interface LiveConfigAudit {
  version: number; at: string; source: string; old: Record<string, number>; new: Record<string, number>;
  error?: "version_conflict"; expected_version?: number; current_version?: number;
}
export type LiveCategory = "crypto" | "listed_equity" | "pre_ipo" | "commodity" | "unconfirmed";
export interface LiveUniverseMarket {
  market_key: string; raw_symbol: string; exchange: string; category: LiveCategory;
  effective_category: LiveCategory; underlying_market: "US" | "KRX" | null;
  volume_usd: Num; volume_basis: Text; at: Text; selected: boolean; eligible: boolean; needs_classification: boolean;
}
export interface LiveUniverse {
  base: string; status: string; category: LiveCategory; excluded: boolean; underlying_market: "US" | "KRX" | null;
  markets: LiveUniverseMarket[];
}
export interface LiveMapping {
  id: string; base: string; status: "pending" | "confirmed" | "rejected"; multiplier: string; at: Text; ratio: Num;
  legs: { market_key: string; raw_base: string; quantity_multiplier: Num }[];
}
export interface LiveExternalIdentity {
  route: string; exchange_type: string; business: string; symbol: string; quote: string; dex: string;
}
export interface LiveExternalContract {
  identity: LiveExternalIdentity; raw_base: string; registered: boolean; forbidden: boolean;
  revision: number; registered_at: number; updated_at: number;
}
export interface LiveFlattenStatus {
  program_flat?: boolean | null; account_flat?: boolean | null; captured_at?: number | null;
}
export interface LiveExternalPositions {
  status?: string; contracts: LiveExternalContract[];
  income?: { verified?: boolean; reason?: string; at?: number } | null;
  flatten_status?: LiveFlattenStatus | null;
}
export interface LiveRemotePosition {
  position_key: string; route: string; symbol: string; side: string;
  exchange_type?: Text; business?: Text; quote?: Text; dex?: Text; identity_verified?: boolean | null;
  quantity?: Text; entry_price?: Text; mark_price?: Text;
  unrealized_pnl_native?: Text; unrealized_pnl_usd?: Text;
  valuation?: Text; valuation_verified?: boolean | null; valuation_error?: Text; ownership?: Text;
  snapshot_at?: Text; snapshot_version?: Num; stale?: boolean | null;
}
export interface LiveRemotePositions {
  available: boolean; positions: LiveRemotePosition[];
  snapshot_at?: Text; snapshot_version?: Num; stale?: boolean | null;
}
export interface LiveAdminResources {
  config: LiveConfig; "config/audit": LiveConfigAudit[]; universe: LiveUniverse[]; mappings: LiveMapping[];
  "external-positions": LiveExternalPositions;
  "remote-positions": LiveRemotePositions;
}
export type LiveAdminResource = keyof LiveAdminResources;

const readErrors: Record<string, string> = {
  not_configured: "未配置", timeout: "请求超时", unreachable: "上游不可达", invalid_json: "数据格式异常",
  invalid_configuration: "配置无效", request_failed: "请求失败",
};
export function liveAdminReadError(reason: string): string {
  const status = /^upstream_http_(\d{3})$/.exec(reason);
  return readErrors[reason] ?? (status ? `上游响应异常 ${status[1]}` : "服务不可用");
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function optionalFields(value: Record<string, unknown>, fields: string[], kind: "string" | "number" | "boolean"): boolean {
  return fields.every(key => value[key] == null || typeof value[key] === kind);
}
function remoteShape(value: unknown): boolean {
  const metadata = (row: Record<string, unknown>) => optionalFields(row, ["snapshot_at"], "string")
    && optionalFields(row, ["snapshot_version"], "number") && optionalFields(row, ["stale"], "boolean");
  return isObject(value) && typeof value.available === "boolean" && metadata(value)
    && Array.isArray(value.positions) && value.positions.every(row => isObject(row)
      && ["position_key", "route", "symbol", "side"].every(key => typeof row[key] === "string" && row[key] !== "")
      && metadata(row) && optionalFields(row, ["identity_verified", "valuation_verified"], "boolean")
      && optionalFields(row, ["exchange_type", "business", "quote", "dex", "quantity", "entry_price", "mark_price",
        "unrealized_pnl_native", "unrealized_pnl_usd", "valuation", "valuation_error", "ownership"], "string"));
}

export async function getLiveExecAdmin<K extends LiveAdminResource>(
  resource: K, signal: AbortSignal,
): Promise<LiveExecResult<LiveAdminResources[K]>> {
  try {
    const response = await sessionFetch(`/api/live-exec/${resource}`, { signal });
    if (!response.ok) return { available: false, error: "request_failed" };
    const result = await response.json();
    if (result?.available === false) return { available: false, error: String(result.error ?? "request_failed") };
    const data = result?.data;
    const shapeOk = resource === "config"
      ? data && Number.isInteger(data.version) && data.values && data.schema && typeof data.fallback === "boolean"
        && Object.entries(data.schema).every(([key, raw]) => {
          const rule = raw as LiveConfigRule | null;
          return rule && Number.isFinite(data.values[key]) && Number.isFinite(rule.default)
            && Number.isFinite(rule.min) && (rule.max === null || Number.isFinite(rule.max))
            && typeof rule.integer === "boolean";
        })
      : resource === "remote-positions" ? remoteShape(data)
      : resource === "external-positions" ? data && !Array.isArray(data) && Array.isArray(data.contracts)
        && data.contracts.every((row: LiveExternalContract | null) => row && typeof row.registered === "boolean"
          && typeof row.forbidden === "boolean" && row.identity
          && (["route", "exchange_type", "business", "symbol", "quote", "dex"] as const)
            .every(key => typeof row.identity[key] === "string"))
        : Array.isArray(data);
    if (result?.available !== true || !shapeOk) return { available: false, error: "invalid_json" };
    return result;
  } catch {
    return { available: false, error: "request_failed" };
  }
}

export type LiveAdminWrite =
  | { resource: "config"; changes: Record<string, number>; expected_version: number }
  | { resource: "universe"; base: string; action: "exclude" | "unexclude"; expected_status: string }
  | { resource: "universe"; base: string; action: "classify"; expected_status: string; classification: {
    category: Exclude<LiveCategory, "unconfirmed">; markets: string[]; underlying_market?: "US" | "KRX";
  } }
  | { resource: "mappings"; id: string; action: "confirm" | "reject";
    expected_status: LiveMapping["status"]; expected_multiplier: string }
  | { resource: "external-positions"; action: "register" | "unregister";
    identity: LiveExternalIdentity; expected_status: string; reason: string };

export function hasUniversePrecondition(row: LiveUniverse): boolean {
  return typeof row.status === "string" && row.status.length === 67 && /^u1:[0-9a-f]{64}$/.test(row.status);
}

export function hasMappingPrecondition(row: LiveMapping): boolean {
  return ["pending", "confirmed", "rejected"].includes(row.status) && typeof row.multiplier === "string"
    && row.multiplier.length > 0 && row.multiplier.length <= 80 && !/[^0-9.eE+\-]/.test(row.multiplier);
}

export class LiveAdminConflictError extends Error {
  constructor() { super("数据已被其他人修改，请刷新后重新确认"); }
}

export async function writeLiveExecAdmin(update: LiveAdminWrite): Promise<void> {
  const path = update.resource === "config" ? "config" : update.resource === "universe"
    ? `universe/${encodeURIComponent(update.base)}/${update.action}`
    : update.resource === "external-positions" ? `external-positions/${update.action}`
      : `mappings/${encodeURIComponent(update.id)}/${update.action}`;
  const body = update.resource === "config" ? { ...update.changes, expected_version: update.expected_version }
    : update.resource === "universe" ? {
      ...(update.action === "classify" ? update.classification : {}), expected_status: update.expected_status,
    } : update.resource === "external-positions"
      ? { identity: update.identity, expected_status: update.expected_status, reason: update.reason }
      : { expected_status: update.expected_status, expected_multiplier: update.expected_multiplier };
  let response: Response;
  try {
    // Browsers supply Origin; never attach either server token. Writes are never retried.
    response = await sessionFetch(`/api/live-exec/${path}`, {
      method: update.resource === "config" ? "PUT" : "POST",
      headers: { "Content-Type": "application/json", "X-Live-Exec-CSRF": "1" }, body: JSON.stringify(body),
    });
  } catch {
    throw new Error("请求未完成，结果可能已生效，请刷新核实后再操作");
  }
  if (response.status === 409) throw new LiveAdminConflictError();
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.available !== true) {
    const message = typeof result?.detail === "string" && /[\u4e00-\u9fff]/.test(result.detail) ? result.detail
      : response.status === 401 ? "登录已失效，请重新登录"
        : "操作失败，请刷新核实操作结果";
    throw new Error(message);
  }
}

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
