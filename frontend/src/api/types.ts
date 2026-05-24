export type MarketType = "spot" | "future";
export type OpportunityType = "SF" | "FF" | "SS";
export type AlertSeverity = "info" | "warning" | "critical";

export interface Opportunity {
  id: string;
  type: OpportunityType;
  symbol: string;
  buy_exchange: string;
  buy_market_type: MarketType;
  sell_exchange: string;
  sell_market_type: MarketType;
  open_spread_pct: number;
  close_spread_pct: number;
  fee_adjusted_open_pct: number;
  spread_width_pct: number;
  buy_bid: number;
  buy_ask: number;
  sell_bid: number;
  sell_ask: number;
  buy_bid_depth_usdt?: number | null;
  buy_ask_depth_usdt?: number | null;
  sell_bid_depth_usdt?: number | null;
  sell_ask_depth_usdt?: number | null;
  min_open_depth_usdt?: number | null;
  buy_volume_24h_usdt: number | null;
  sell_volume_24h_usdt: number | null;
  funding_rate_buy_pct: number | null;
  funding_rate_sell_pct: number | null;
  funding_next_rate_buy_pct: number | null;
  funding_next_rate_sell_pct: number | null;
  funding_next_time_buy: string | null;
  funding_next_time_sell: string | null;
  net_funding_pct: number | null;
  net_funding_next_pct: number | null;
  buy_funding_interval_hours: number | null;
  sell_funding_interval_hours: number | null;
  net_funding_hourly_pct: number | null;
  net_funding_daily_pct: number | null;
  net_funding_next_hourly_pct: number | null;
  net_funding_next_daily_pct: number | null;
  mark_index_diff_buy_pct: number | null;
  mark_index_diff_sell_pct: number | null;
  risk_labels: string[];
  last_seen_at: string;
}

export interface ExchangePollState {
  status: "healthy" | "degraded" | "cooling_down";
  last_success_at: string | null;
  last_error_at: string | null;
  consecutive_failures: number;
  cooldown_until: string | null;
  next_due_at: string | null;
  in_flight: boolean;
}

export interface HealthStatus {
  status: string;
  markets: number;
  opportunities: number;
  exchange_errors: Record<string, string>;
  exchange_states: Record<string, ExchangePollState>;
}

export interface RiskSettings {
  min_volume_24h_usdt: number;
  min_volume_24h_k?: number;
  stale_after_seconds: number;
  huge_spread_pct: number;
  wide_spread_pct: number;
  mark_index_deviation_pct: number;
  funding_against_pct: number;
  signal_slippage_buffer_pct: number;
  min_effective_open_pct: number;
  max_open_spread_decay_pct: number;
  signal_validation_notional_usdt: number;
  orderbook_depth_safety_multiple: number;
  min_top_of_book_depth_usdt: number;
  signal_strategy_notes: string;
  ticker_collision_symbols: string[];
  excluded_symbols: string[];
  ignored_exchanges: string[];
}

export interface AlertMessageTemplateSettings {
  include_trigger_summary: boolean;
  include_rule_details: boolean;
  include_pair: boolean;
  include_spread: boolean;
  include_funding: boolean;
  include_volume: boolean;
  include_risk: boolean;
  include_observations: boolean;
  include_dashboard_link: boolean;
  observation_limit: number;
}

export interface AlertRule {
  id?: string;
  name: string;
  enabled: boolean;
  types: OpportunityType[];
  include_exchanges: string[];
  exclude_exchanges: string[];
  include_symbols: string[];
  exclude_symbols: string[];
  min_open_spread_pct: number;
  min_fee_adjusted_open_pct: number;
  min_volume_24h_usdt: number;
  max_data_age_seconds: number;
  excluded_risk_labels: string[];
  consecutive_hits: number;
  cooldown_seconds: number;
  severity: AlertSeverity;
}

export interface AlertEvent {
  id: string;
  rule_id: string;
  opportunity_id: string;
  symbol: string;
  status: string;
  message: string;
  created_at: string;
}

export interface AstroCardSettings {
  max_trade_usdt: number;
  leverage: number;
  min_notional: number;
  max_notional: number;
  close_position_buffer_pct: number;
  unfavorable_funding_weight: number;
  close_position_floor_pct: number;
}

export interface AstroCardCreateRequest {
  max_trade_usdt?: number;
  leverage?: number;
  min_notional?: number;
  max_notional?: number;
  save_as_default?: boolean;
}

export interface AstroFieldAssumption {
  field: string;
  source: string;
  assumed_value: string;
  note: string;
  needs_verification: boolean;
}

export interface AstroPairPlan {
  opportunity_id: string;
  symbol: string;
  mode: "dry_run";
  can_submit: boolean;
  pair: Record<string, unknown> | null;
  sdk_payload: Record<string, unknown> | null;
  blockers: string[];
  warnings: string[];
  assumptions: AstroFieldAssumption[];
}

export interface AstroActionResult {
  enabled: boolean;
  status: "disabled" | "skipped" | "created" | "updated" | "failed";
  action: string;
  message: string;
  pair_name: string | null;
  pair_type: string | null;
}

export interface AstroSdkStatus {
  configured: boolean;
  dry_run_only: boolean;
  base_url: string;
  admin_prefix: string;
  api_key_configured: boolean;
  list_path: string;
  pair_path: string;
  message_path: string;
  message: string | null;
}

export interface ServiceControlDetail {
  name: string;
  available: boolean;
  container_id: string | null;
  container_name: string | null;
  state: string | null;
  status: string | null;
}

export interface ServiceControlStatus {
  enabled: boolean;
  environment: string;
  services: string[];
  details: ServiceControlDetail[];
  message: string | null;
}

export interface ServiceRestartResult {
  service: string;
  status: string;
  message: string | null;
}

export interface OpportunityFilters {
  type?: OpportunityType;
  exclude_types?: OpportunityType[];
  symbol?: string;
  exchange?: string;
  min_open_spread_pct?: number;
  include_risky?: boolean;
  hidden_risk_labels?: string[];
  min_volume_24h_k?: number;
}
