export interface FsSettings {
  enabled: boolean;
  astro_auto_create?: boolean;
  exchanges?: string[];
  account_id: string | null;
  target_usdt: number;
  holding_hours: number;
  scan_interval_seconds: number;
  max_candidates: number;
  min_volume_usdt: number;
  min_net_pct: number;
  spot_fee_pct: number;
  future_fee_pct: number;
  execution_buffer_pct: number;
  basis_stress_pct: number;
}
export interface FsScenario {
  label: string; hours: number; settlements: number; budget_interest_hours: number;
  funding_usdt: number; borrow_usdt: number; fees_usdt: number;
  round_trip_price_pnl_usdt: number; buffer_usdt: number;
  net_usdt: number; net_pct: number; stressed_net_usdt: number;
}
export interface FsCandidate {
  symbol: string; base: string; exchange: string; quote: string; multiplier: number;
  spot_raw_symbol?: string | null; future_raw_symbol?: string | null; contract_multiplier?: number;
  observed_at: string; valid_until: string | null;
  funding_pct: number | null; interval_hours: number | null; next_funding_at: string | null;
  hourly_borrow_pct: number | null; borrow_rate_source: string;
  product_borrowable: boolean | null; public_vip_limit: number | null;
  account_max_loan: number | null; available_inventory: number | null; inventory_at: string | null;
  account_state: string; quantity: number | null; borrow_required: number | null; capacity_usdt: number | null;
  spot_volume_usdt: number | null; future_volume_usdt: number | null;
  prices: Record<string, number>; source_times: Record<string, string>;
  fee_source: string; spot_fee_pct: number | null; future_fee_pct: number | null;
  identity_state: string; asset_id: string | null;
  identity_details?: Array<Record<string, string | number | null>>;
  signal_ready?: boolean; signal_blockers?: string[]; astro_state?: string;
  stage: "discovered" | "quota_sufficient" | "cost_pass" | "blocked" | "stale";
  blockers: string[]; scenarios: FsScenario[];
}
export interface FsReport {
  exchange_status?: Record<string, string>;
  scan_duration_seconds?: number | null; next_refresh_at?: string | null;
  observed_at: string | null; scanning: boolean; stale: boolean; error: string | null;
  negative_count: number; settings: FsSettings; candidates: FsCandidate[]; sources: string[];
}
export interface FsEvent { id: number; observed_at: string; symbol: string; stage: string; reasons: string[] }
