export type MarketType = "spot" | "future";
export type OpportunityType = "SF" | "FF" | "SS";
export type AlertSeverity = "info" | "warning" | "critical";
export type PhonePriceAlertCondition = "above" | "below";
export type PhonePriceAlertPriceField = "mark_price" | "index_price" | "mid_price" | "bid" | "ask";
export type PairSpreadPriceField = "mark_price" | "mid_price" | "index_price" | "last_price";
export type AnnouncementKind = "listing" | "delisting" | "other";

export interface Opportunity {
  id: string;
  type: OpportunityType;
  symbol: string;
  buy_exchange: string;
  buy_market_type: MarketType;
  buy_raw_symbol?: string | null;
  sell_exchange: string;
  sell_market_type: MarketType;
  sell_raw_symbol?: string | null;
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


export interface SymbolAlias {
  exchange: string;
  symbol: string;
  canonical_symbol: string;
  market_type?: MarketType | null;
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
  orderbook_depth_band_pct: number;
  min_top_of_book_depth_usdt: number;
  signal_strategy_notes: string;
  ticker_collision_symbols: string[];
  excluded_symbols: string[];
  ignored_exchanges: string[];
  symbol_aliases: SymbolAlias[];
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

export interface AstroAutomationSettings {
  alert_auto_create: boolean;
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

export interface AnnouncementEventScheduleItem {
  symbol: string;
  event_time: string;
  note?: string | null;
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
  event_schedule?: AnnouncementEventScheduleItem[];
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

export interface PairSpreadLegQuery {
  exchange: string;
  symbol: string;
}

export interface PairSpreadPoint {
  bucket_at: string;
  leg1_close: number;
  leg2_close: number;
  spread_abs: number;
  spread_pct: number;
}

export interface PairSpreadFundingPoint {
  exchange: string;
  symbol: string;
  funding_time: string;
  funding_rate_pct: number;
}

export interface PairSpreadCurrentLeg {
  exchange: string;
  symbol: string;
  raw_symbol: string;
  price: number;
  price_field: PairSpreadPriceField;
  mark_price: number | null;
  index_price: number | null;
  mid_price: number | null;
  last_price: number | null;
  funding_rate_pct: number | null;
  funding_next_rate_pct: number | null;
  funding_next_time: string | null;
  timestamp: string;
}

export interface PairSpreadCurrentSnapshot {
  observed_at: string;
  leg1: PairSpreadCurrentLeg;
  leg2: PairSpreadCurrentLeg;
  spread_abs: number;
  spread_pct: number;
}

export interface PairSpreadValueStats {
  min: number | null;
  max: number | null;
  mean: number | null;
  current: number | null;
}

export interface PairSpreadQueryResult {
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  hours: number;
  interval_minutes: number;
  leg2_multiplier: number;
  observed_at: string;
  point_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  spread_abs: PairSpreadValueStats;
  spread_pct: PairSpreadValueStats;
  current: PairSpreadCurrentSnapshot | null;
  points: PairSpreadPoint[];
  funding_history: PairSpreadFundingPoint[];
  warnings: string[];
}

export interface PremiumIndexPoint {
  bucket_at: string;
  premium_pct: number;
  mark_price: number | null;
  index_price: number | null;
  source: string;
}

export interface PremiumIndexCurrentSnapshot {
  observed_at: string;
  exchange: string;
  symbol: string;
  raw_symbol: string;
  mark_price: number | null;
  index_price: number | null;
  mid_price: number | null;
  last_price: number | null;
  premium_pct: number | null;
  mid_premium_pct: number | null;
  funding_rate_pct: number | null;
  funding_next_rate_pct: number | null;
  funding_next_time: string | null;
  source: string;
}

export interface PremiumIndexValueStats {
  min: number | null;
  max: number | null;
  mean: number | null;
  current: number | null;
}

export interface PremiumIndexQueryResult {
  exchange: string;
  symbol: string;
  hours: number;
  interval_minutes: number;
  observed_at: string;
  point_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  premium_pct: PremiumIndexValueStats;
  current: PremiumIndexCurrentSnapshot | null;
  points: PremiumIndexPoint[];
  warnings: string[];
}

export interface AstroCardSettings {
  max_trade_usdt: number;
  leverage: number;
  min_notional: number;
  max_notional: number;
  open_enabled: boolean;
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
  open_enabled?: boolean;
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
  verify_tls: boolean;
  ca_bundle_configured: boolean;
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
  strong_funding_pct: number;
  near_settlement_minutes: number;
  small_basis_threshold_pct: number;
  interval_mismatch_min_hours: number;
  formula_divergence_min_funding_pct: number;
  conflicted_basis_min_check_pct: number;
  min_conflicted_reward_risk_ratio: number;
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
  long_funding_interval_hours: number | null;
  short_funding_interval_hours: number | null;
  funding_comparison_interval_hours: number | null;
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
  adverse_entry_basis_pct: number;
  conflicted_reward_risk_ratio: number | null;
  adl_risk_score: number;
  adl_risk_level: AdlRiskLevel;
  decision: FundingArbitrageDecision;
  decision_reasons: string[];
  risk_labels: string[];
  primary_opportunity_type: FundingOpportunityType;
  opportunity_types: FundingOpportunityType[];
  opportunity_reasons: string[];
  volume_24h_usdt: number | null;
  depth_usdt: number | null;
  uses_gate: boolean;
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

export type OpportunityRadarPremiumDirection = "negative" | "positive" | "both";
export type OpportunityRadarDirection = "LONG_ANCHOR_SHORT_PEER" | "LONG_PEER_SHORT_ANCHOR";
export type OpportunityRadarSignalLevel = "HIGH" | "MEDIUM" | "WATCH";

export interface OpportunityRadarSettings {
  enabled: boolean;
  feishu_notifications_enabled: boolean;
  min_alert_score: number;
  alert_consecutive_hits: number;
  alert_cooldown_seconds: number;
  anchor_exchange: string;
  peer_exchanges: string[];
  premium_direction: OpportunityRadarPremiumDirection;
  min_abs_premium_pct: number;
  min_relative_premium_gap_pct: number;
  max_abs_entry_spread_pct: number;
  require_funding_alignment: boolean;
  min_hourly_funding_edge_pct: number;
  min_volume_24h_usdt: number;
  notional_per_symbol_usdt: number;
  min_depth_multiple: number;
  max_data_age_seconds: number;
  max_candidates: number;
}

export interface OpportunityRadarCandidate {
  id: string;
  symbol: string;
  signal_level: OpportunityRadarSignalLevel;
  score: number;
  direction: OpportunityRadarDirection;
  long_exchange: string;
  short_exchange: string;
  anchor_exchange: string;
  peer_exchange: string;
  anchor_premium_pct: number;
  peer_premium_pct: number;
  peer_median_premium_pct: number;
  relative_premium_gap_pct: number;
  entry_spread_pct: number;
  long_entry_price: number;
  short_entry_price: number;
  long_funding_pct: number | null;
  short_funding_pct: number | null;
  long_funding_interval_hours: number | null;
  short_funding_interval_hours: number | null;
  hourly_funding_edge_pct: number | null;
  volume_24h_usdt: number | null;
  depth_usdt: number | null;
  data_age_seconds: number;
  reasons: string[];
  risk_labels: string[];
}

export interface OpportunityRadarPreview {
  observed_at: string;
  settings: OpportunityRadarSettings;
  anchor_markets: number;
  total_pairs_evaluated: number;
  displayed_candidates: number;
  high_count: number;
  medium_count: number;
  watch_count: number;
  candidates: OpportunityRadarCandidate[];
}

export type TradfiPerpDirection = "LONG_HL_SHORT_BINANCE" | "LONG_BINANCE_SHORT_HL";

export interface TradfiPerpLeg {
  exchange: string;
  symbol: string;
  raw_symbol: string;
  base_asset: string;
  dex?: string | null;
  bid?: number | null;
  ask?: number | null;
  mid?: number | null;
  mark_price?: number | null;
  index_price?: number | null;
  funding_rate_pct?: number | null;
  funding_rate_hourly_pct?: number | null;
  funding_interval_hours?: number | null;
  funding_next_time?: string | null;
  volume_24h_usdt?: number | null;
  open_interest?: number | null;
  timestamp: string;
}

export interface TradfiPerpMonitorRow {
  id: string;
  asset: string;
  binance_base_asset: string;
  binance_symbol: string;
  hl_dex: string;
  hl_symbol: string;
  hl_raw_symbol: string;
  hl: TradfiPerpLeg;
  binance: TradfiPerpLeg;
  mid_spread_pct: number | null;
  mark_spread_pct: number | null;
  index_spread_pct: number | null;
  open_long_hl_short_binance_pct: number | null;
  open_long_binance_short_hl_pct: number | null;
  best_price_direction: TradfiPerpDirection | null;
  best_open_edge_pct: number | null;
  funding_edge_long_hl_short_binance_hourly_pct: number | null;
  funding_edge_long_binance_short_hl_hourly_pct: number | null;
  best_funding_direction: TradfiPerpDirection | null;
  best_funding_edge_hourly_pct: number | null;
  best_funding_edge_daily_pct: number | null;
  min_volume_24h_usdt: number | null;
  risk_labels: string[];
  observed_at: string;
}

export interface TradfiPerpUnmatchedAsset {
  source: "hyperliquid" | "binance";
  asset: string;
  raw_symbol?: string | null;
  dex?: string | null;
  suggested_alias?: string | null;
}

export interface TradfiPerpMonitorPreview {
  observed_at: string;
  matched_count: number;
  hyperliquid_asset_count: number;
  binance_symbol_count: number;
  rows: TradfiPerpMonitorRow[];
  unmatched_hyperliquid: TradfiPerpUnmatchedAsset[];
  unmatched_binance: TradfiPerpUnmatchedAsset[];
}

export type FundingResearchDecision = "TRADE" | "SMALL_TRADE" | "WATCH" | "NO_TRADE";
export type FundingResearchBasisAlignment = "aligned" | "neutral" | "conflicted";
export type FundingOpportunityType =
  | "BASIS_AND_FUNDING_ALIGNED"
  | "STRONG_FUNDING_NEAR_SETTLEMENT"
  | "INTERVAL_MISMATCH"
  | "FORMULA_DIVERGENCE"
  | "BASIS_CARRY_CONFLICTED"
  | "BASIS_MEAN_REVERSION"
  | "PURE_FUNDING_SPREAD";
export type FundingResearchFormulaConfidence =
  | "formula"
  | "predicted"
  | "fallback_current"
  | "missing"
  | "uncertain";
export type FundingResearchPaperTradeStatus = "OPEN" | "CLOSED";

export interface FundingResearchDepthStats {
  source: string;
  levels: number;
  long_entry_depth_usdt: number | null;
  short_entry_depth_usdt: number | null;
  min_entry_depth_usdt: number | null;
  target_notional_usdt: number;
  long_entry_vwap: number | null;
  short_entry_vwap: number | null;
  executable_basis_diff_pct: number | null;
  slippage_loss_pct: number | null;
}

export interface FundingResearchCandidate {
  id: string;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  long_formula_family: string;
  short_formula_family: string;
  long_funding_pct: number | null;
  short_funding_pct: number | null;
  long_funding_interval_hours: number | null;
  short_funding_interval_hours: number | null;
  long_next_settlement_time: string | null;
  short_next_settlement_time: string | null;
  expected_net_funding_pct: number | null;
  expected_basis_change_pct: number;
  estimated_cost_pct: number;
  risk_buffer_pct: number;
  ev_pct: number | null;
  adverse_basis_pct: number;
  conflicted_reward_risk_ratio: number | null;
  score: number;
  decision: FundingResearchDecision;
  basis_alignment: FundingResearchBasisAlignment;
  basis_diff_pct: number | null;
  long_basis_pct: number | null;
  short_basis_pct: number | null;
  funding_window_hours: number;
  next_settlement_time: string | null;
  minutes_to_settlement: number | null;
  funding_source: FundingResearchFormulaConfidence;
  primary_opportunity_type: FundingOpportunityType;
  opportunity_types: FundingOpportunityType[];
  opportunity_reasons: string[];
  uses_gate: boolean;
  uses_hyperliquid: boolean;
  depth_stats: FundingResearchDepthStats | null;
  risk_labels: string[];
  reasons: string[];
}

export interface FundingResearchCandidateSnapshot {
  observed_at: string;
  candidate: FundingResearchCandidate;
}

export interface FundingResearchRunResult {
  observed_at: string;
  market_snapshot_count: number;
  candidate_snapshot_count: number;
  pruned_snapshot_count: number;
  candidate_count: number;
  opened_paper_trade_count: number;
  closed_paper_trade_count: number;
  top_candidates: FundingResearchCandidate[];
}

export interface FundingResearchPaperTrade {
  id: string;
  status: FundingResearchPaperTradeStatus;
  symbol: string;
  long_exchange: string;
  short_exchange: string;
  primary_opportunity_type: FundingOpportunityType;
  opportunity_types: FundingOpportunityType[];
  opened_at: string;
  closed_at: string | null;
  last_observed_at: string | null;
  open_long_basis_pct: number | null;
  open_short_basis_pct: number | null;
  open_basis_diff_pct: number | null;
  close_long_basis_pct: number | null;
  close_short_basis_pct: number | null;
  close_basis_diff_pct: number | null;
  unrealized_basis_change_pct: number | null;
  unrealized_pnl_pct: number | null;
  expected_net_funding_pct: number | null;
  expected_basis_change_pct: number;
  expected_ev_pct: number | null;
  score: number;
  decision: FundingResearchDecision;
  realized_funding_pct: number;
  realized_basis_change_pct: number;
  estimated_cost_pct: number;
  realized_pnl_pct: number | null;
  max_adverse_ev_pct: number | null;
  exit_reason: string | null;
  source_candidate: FundingResearchCandidate;
}

export interface FundingResearchOpportunityTypeSummary {
  opportunity_type: FundingOpportunityType;
  total_trades: number;
  closed_trades: number;
  winners: number;
  losers: number;
  win_rate_pct: number | null;
  total_realized_pnl_pct: number;
  average_realized_pnl_pct: number | null;
}

export interface FundingResearchPaperTradeSummary {
  total_trades: number;
  open_trades: number;
  closed_trades: number;
  winners: number;
  losers: number;
  win_rate_pct: number | null;
  total_realized_pnl_pct: number;
  average_realized_pnl_pct: number | null;
  average_expected_ev_pct: number | null;
  average_realized_funding_pct: number | null;
  average_realized_basis_change_pct: number | null;
  max_win_pct: number | null;
  max_loss_pct: number | null;
  average_score: number | null;
  by_opportunity_type: FundingResearchOpportunityTypeSummary[];
}

export interface FundingResearchLegacyBacktestSummary {
  rows_seen: number;
  trades: number;
  winners: number;
  losers: number;
  win_rate_pct: number | null;
  total_pnl_pct: number;
  average_pnl_pct: number | null;
  average_entry_edge_pct: number | null;
  max_win_pct: number | null;
  max_loss_pct: number | null;
  notes: string[];
}

export interface FundingResearchLegacyBacktestQuery {
  symbol?: string;
  hours?: number;
  limit?: number;
  min_entry_edge_pct?: number;
  min_next_funding_pct?: number;
  cost_pct?: number;
  max_hold_observations?: number;
}

export type GateTwapSide = "sell" | "buy";
export type GateTwapSliceMode = "initial" | "remaining";
export type GateTwapActionMode = "ACK" | "RESULT" | "FULL";
export type GateTwapJobState = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface GateTwapRequest {
  contract: string;
  settle: string;
  side: GateTwapSide;
  start_at?: string | null;
  interval_seconds: number;
  duration_seconds: number;
  percent: number;
  slice_mode: GateTwapSliceMode;
  initial_size?: number | null;
  last_order_all: boolean;
  slip_ratio?: number | null;
  client_prefix: string;
  action_mode: GateTwapActionMode;
}

export interface GateTwapRunRequest extends GateTwapRequest {
  live: boolean;
  confirm_live: boolean;
}

export interface GateTwapContractRules {
  order_size_min: number;
  order_size_step: number;
  enable_decimal: boolean;
  market_order_slip_ratio: number | null;
  market_order_size_max: number | null;
  status: string | null;
}

export interface GateTwapPlanSlice {
  index: number;
  scheduled_at: string;
  raw_size: number;
  order_size: number;
  signed_order_size: number;
  remaining_after: number;
  skipped_reason: string | null;
}

export interface GateTwapPlan {
  request: GateTwapRequest;
  contract: string;
  settle: string;
  side: GateTwapSide;
  order_count: number;
  initial_size: number | null;
  signed_position_size: number | null;
  has_credentials: boolean;
  rules: GateTwapContractRules;
  total_planned_size: number;
  slices: GateTwapPlanSlice[];
  warnings: string[];
}

export interface GateTickerBook {
  bid: number | null;
  ask: number | null;
  bid_size: number | null;
  ask_size: number | null;
  mid: number | null;
  last: number | null;
  volume_24h_usdt: number | null;
}

export interface GateTwapMarketSnapshot {
  contract: string;
  spot_pair: string;
  observed_at: string;
  spot_available: boolean;
  spot: GateTickerBook | null;
  future: GateTickerBook | null;
  mark_price: number | null;
  index_price: number | null;
  mark_index_premium_pct: number | null;
  future_index_premium_pct: number | null;
  future_spot_premium_pct: number | null;
  funding_rate_pct: number | null;
  funding_next_rate_pct: number | null;
  funding_interval_hours: number | null;
  funding_next_time: string | null;
  contract_status: string | null;
  order_size_min: number | null;
  order_size_step: number | null;
  market_order_slip_ratio: number | null;
}

export interface GateTwapJobEvent {
  at: string;
  level: "info" | "warning" | "error";
  message: string;
  order: Record<string, unknown> | null;
  response: Record<string, unknown> | null;
}

export interface GateTwapJobStatus {
  job_id: string;
  state: GateTwapJobState;
  live: boolean;
  request: GateTwapRequest;
  plan: GateTwapPlan | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  completed_orders: number;
  skipped_orders: number;
  total_order_size: number;
  last_error: string | null;
  events: GateTwapJobEvent[];
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
  limit?: number;
}
