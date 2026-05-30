export type MarketType = "spot" | "future";
export type OpportunityType = "SF" | "FF" | "SS";
export type AlertSeverity = "info" | "warning" | "critical";
export type PhonePriceAlertCondition = "above" | "below";
export type PhonePriceAlertPriceField = "mark_price" | "index_price" | "mid_price" | "bid" | "ask";
export type AnnouncementKind = "listing" | "delisting" | "other";

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

export interface MarketSnapshot {
  symbol: string;
  base: string;
  quote: string;
  exchange: string;
  market_type: MarketType;
  bid: number;
  ask: number;
  bid_size?: number | null;
  ask_size?: number | null;
  volume_24h_usdt?: number | null;
  funding_rate_pct?: number | null;
  funding_next_rate_pct?: number | null;
  funding_interval_hours?: number | null;
  funding_next_time?: string | null;
  mark_price?: number | null;
  index_price?: number | null;
  timestamp: string;
  raw_symbol: string;
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
  suppress_when_card_conditions_fail: boolean;
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

export interface PhonePriceAlertRule {
  id?: string;
  name: string;
  enabled: boolean;
  symbol: string;
  exchange?: string | null;
  market_type: MarketType;
  price_field: PhonePriceAlertPriceField;
  condition: PhonePriceAlertCondition;
  target_price: number;
  cooldown_seconds: number;
}

export interface PhonePriceAlertEvent {
  id: string;
  rule_id: string;
  symbol: string;
  exchange: string;
  market_type: MarketType;
  price_field: PhonePriceAlertPriceField;
  condition: PhonePriceAlertCondition;
  target_price: number;
  observed_price: number;
  status: string;
  message: string;
  created_at: string;
}

export interface PhonePriceAlertDiagnostic {
  rule_id: string;
  rule_name: string;
  symbol: string;
  exchange?: string | null;
  market_type: MarketType;
  price_field: PhonePriceAlertPriceField;
  resolved_price_field?: PhonePriceAlertPriceField | null;
  condition: PhonePriceAlertCondition;
  target_price: number;
  market_found: boolean;
  observed_price?: number | null;
  triggered: boolean;
  exchange_error?: string | null;
  reason: string;
}

export interface PhonePriceAlertDiagnostics {
  phone_enabled: boolean;
  items: PhonePriceAlertDiagnostic[];
}

export interface IndexComponent {
  source: string;
  symbol: string;
  weight?: number | null;
  price?: number | null;
  extra?: Record<string, unknown>;
}

export interface IndexComponentChange {
  id: string;
  exchange: string;
  symbol: string;
  old_hash: string;
  new_hash: string;
  old_components: IndexComponent[];
  new_components: IndexComponent[];
  added_components: IndexComponent[];
  removed_components: IndexComponent[];
  changed_components: IndexComponent[];
  source: string;
  alert_status: string;
  created_at: string;
}

export interface IndexComponentSnapshot {
  exchange: string;
  symbol: string;
  components: IndexComponent[];
  component_hash: string;
  source: string;
  observed_at: string;
}

export interface IndexComponentWatchItem {
  id: string;
  symbol: string;
  note?: string | null;
  created_at: string;
}

export interface IndexComponentChangeFilters {
  symbol?: string;
  exchange?: string;
  limit?: number;
}

export interface IndexComponentSnapshotFilters {
  symbol?: string;
  exchange?: string;
  limit?: number;
}

export interface ExchangeAnnouncement {
  id: string;
  exchange: string;
  announcement_id: string;
  kind: AnnouncementKind;
  title: string;
  url: string;
  source: string;
  category?: string | null;
  symbols: string[];
  market_type?: string | null;
  event_time?: string | null;
  summary?: string | null;
  published_at: string;
  fetched_at: string;
  alert_status: string;
  event_reminder_status: string;
  event_reminder_sent_at?: string | null;
}

export interface AnnouncementSettings {
  enabled: boolean;
  poll_interval_seconds: number;
  record_exchanges: string[];
  alert_exchanges: string[];
  bootstrap_alerts_enabled: boolean;
  event_reminders_enabled: boolean;
  event_reminder_minutes_before: number;
}

export interface AnnouncementFilters {
  exchange?: string;
  kind?: AnnouncementKind;
  limit?: number;
}

export interface AnnouncementExchangeOption {
  label: string;
  value: string;
}

export interface MarketFilters {
  symbol?: string;
  exchange?: string;
  market_type?: MarketType;
}

export interface OpportunityHistoryRow {
  observed_at: string;
  opportunity_id: string;
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
  buy_volume_24h_usdt: number | null;
  sell_volume_24h_usdt: number | null;
  risk_labels: string[];
}

export interface OpportunitySpreadStats {
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  p05: number | null;
  p95: number | null;
  current: number | null;
  z_score: number | null;
}

export interface OpportunityHistoryPoint {
  observed_at: string;
  open_spread_pct: number;
  close_spread_pct: number;
  fee_adjusted_open_pct: number;
  funding_rate_buy_pct: number | null;
  funding_rate_sell_pct: number | null;
  funding_next_rate_buy_pct: number | null;
  funding_next_rate_sell_pct: number | null;
  funding_next_time_buy: string | null;
  funding_next_time_sell: string | null;
  net_funding_pct: number | null;
  net_funding_next_pct: number | null;
}

export interface OpportunityHistoryStats {
  symbol: string | null;
  opportunity_id: string | null;
  type: OpportunityType | null;
  count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  latest: OpportunityHistoryRow | null;
  open_spread_pct: OpportunitySpreadStats;
  close_spread_pct: OpportunitySpreadStats;
  fee_adjusted_open_pct: OpportunitySpreadStats;
  net_funding_pct: OpportunitySpreadStats;
  net_funding_next_pct: OpportunitySpreadStats;
  points: OpportunityHistoryPoint[];
}

export interface OpportunityHistoryStatsQuery {
  symbol?: string;
  opportunity_id?: string;
  type?: OpportunityType;
  hours?: number;
  point_limit?: number;
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

export interface LivePilotSettings {
  enabled: boolean;
  max_symbols: number;
  notional_per_symbol_usdt: number;
  min_next_funding_edge_pct: number;
  prefer_hyperliquid: boolean;
  exclude_ss: boolean;
  create_cards_enabled: boolean;
}

export interface LivePilotPreviewItem {
  opportunity_id: string;
  symbol: string;
  type: OpportunityType;
  route: string;
  buy_exchange: string;
  sell_exchange: string;
  uses_hyperliquid: boolean;
  open_spread_pct: number;
  fee_adjusted_open_pct: number;
  next_funding_edge_pct: number;
  combined_open_edge_pct: number;
  volume_24h_usdt: number | null;
  notional_usdt: number;
  risk_labels: string[];
}

export interface LivePilotPreview {
  settings: LivePilotSettings;
  total_opportunities: number;
  eligible_symbols: number;
  selected_symbols: number;
  skipped_negative_funding: number;
  skipped_type: number;
  skipped_risk: number;
  budget_usdt: number;
  items: LivePilotPreviewItem[];
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

export type FundingArbitrageDecision = "ENTER" | "HOLD" | "EXIT_SOON" | "EXIT_NOW" | "BLOCKED";
export type FundingSource = "predicted" | "fallback_current" | "missing";
export type AdlRiskLevel = "LOW" | "MEDIUM" | "HIGH" | "BLOCKED";

export interface FundingArbitrageSettings {
  enabled: boolean;
  max_candidates: number;
  min_entry_edge_pct: number;
  min_hold_edge_pct: number;
  min_exit_edge_pct: number;
  min_funding_edge_pct: number;
  min_volume_24h_usdt: number;
  max_mark_index_deviation_pct: number;
  max_basis_width_pct: number;
  slippage_buffer_pct: number;
  basis_risk_weight: number;
  confidence_penalty_pct: number;
  min_minutes_to_settlement: number;
  max_minutes_to_settlement: number;
  adl_block_score: number;
  leverage: number;
  notional_per_symbol_usdt: number;
  prefer_hyperliquid: boolean;
}

export interface FundingArbitrageCandidate {
  id: string;
  symbol: string;
  type: "SF" | "FF";
  long_exchange: string;
  long_market_type: MarketType;
  short_exchange: string;
  short_market_type: MarketType;
  funding_source: FundingSource;
  long_current_funding_pct: number | null;
  short_current_funding_pct: number | null;
  long_next_funding_pct: number | null;
  short_next_funding_pct: number | null;
  current_funding_edge_pct: number | null;
  next_funding_edge_pct: number | null;
  long_next_settlement_time: string | null;
  short_next_settlement_time: string | null;
  next_settlement_time: string | null;
  minutes_to_settlement: number | null;
  entry_basis_pct: number;
  exit_basis_pct: number;
  basis_width_pct: number;
  basis_risk_penalty_pct: number;
  estimated_open_cost_pct: number;
  estimated_close_cost_pct: number;
  slippage_buffer_pct: number;
  confidence_penalty_pct: number;
  adl_risk_penalty_pct: number;
  expected_cycle_pnl_pct: number;
  adl_risk_score: number;
  adl_risk_level: AdlRiskLevel;
  decision: FundingArbitrageDecision;
  decision_reasons: string[];
  risk_labels: string[];
  volume_24h_usdt: number | null;
  depth_usdt: number | null;
  uses_hyperliquid: boolean;
}

export interface FundingArbitragePreview {
  settings: FundingArbitrageSettings;
  total_pairs_evaluated: number;
  displayed_candidates: number;
  blocked_missing_funding: number;
  blocked_liquidity: number;
  blocked_adl_risk: number;
  blocked_expected_pnl: number;
  enter_count: number;
  hold_count: number;
  exit_count: number;
  blocked_count: number;
  candidates: FundingArbitrageCandidate[];
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
