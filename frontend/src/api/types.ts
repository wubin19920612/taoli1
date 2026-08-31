export type MarketType = "spot" | "future";
export type OpportunityType = "SF" | "FF" | "SS";
export type AlertSeverity = "info" | "warning" | "critical";
export type PhonePriceAlertCondition = "above" | "below";
export type PhonePriceAlertPriceField = "mark_price" | "index_price" | "mid_price" | "bid" | "ask";
export type PairSpreadPriceField = "mark_price" | "mid_price" | "index_price" | "last_price";
export type AnnouncementKind = "listing" | "delisting" | "other";
export type SecondLevelSampleStatus = "ok" | "partial" | "error";
export type NewListingAlertLevel = "none" | "normal" | "strong" | "extreme";
export type NegativeBasisSignalLevel = "none" | "watch" | "building" | "confirmed" | "strong" | "extreme";
export type FatFingerMarketMode = "SF" | "FF";
export type FatFingerMakerSide = "buy" | "sell";
export type FatFingerExitReason = "target" | "timeout";

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
  price_multiplier: number;
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
  symbol_alias_original_symbol?: string | null;
  symbol_alias_price_multiplier?: number | null;
}

export interface SecondLevelSamplingConfig {
  enabled: boolean;
  interval_seconds: number;
  retention_hours: number;
  exchanges: string[];
  symbols: string[];
  max_concurrent_requests: number;
  capture_index_components: boolean;
  component_signal_window_seconds: number;
}

export interface SecondLevelMarketSample {
  id?: number | null;
  observed_at: string;
  exchange: string;
  symbol: string;
  status: SecondLevelSampleStatus;
  spot_bid?: number | null;
  spot_ask?: number | null;
  spot_bid_size?: number | null;
  spot_ask_size?: number | null;
  spot_mid?: number | null;
  spot_last?: number | null;
  future_bid?: number | null;
  future_ask?: number | null;
  future_bid_size?: number | null;
  future_ask_size?: number | null;
  future_mid?: number | null;
  future_last?: number | null;
  mark_price?: number | null;
  index_price?: number | null;
  mark_premium_pct?: number | null;
  mid_premium_pct?: number | null;
  funding_rate_pct?: number | null;
  raw_spot_symbol?: string | null;
  raw_future_symbol?: string | null;
  latency_ms?: number | null;
  error?: string | null;
}

export interface SecondLevelPairSpreadSnapshot {
  symbol: string;
  left_exchange: string;
  right_exchange: string;
  observed_at: string;
  left_spot_mid?: number | null;
  right_spot_mid?: number | null;
  left_future_mid?: number | null;
  right_future_mid?: number | null;
  spot_spread_pct?: number | null;
  future_spread_pct?: number | null;
  future_spot_spread_gap_pct?: number | null;
  left_future_spot_basis_pct?: number | null;
  right_future_spot_basis_pct?: number | null;
  future_spot_basis_gap_pct?: number | null;
  left_mark_premium_pct?: number | null;
  right_mark_premium_pct?: number | null;
  premium_gap_pct?: number | null;
}

export interface SecondLevelIndexComponentSample {
  id?: number | null;
  observed_at: string;
  target_exchange: string;
  symbol: string;
  component_source: string;
  component_symbol: string;
  weight_pct?: number | null;
  component_price?: number | null;
  contribution_price?: number | null;
  official_index_price?: number | null;
  reconstructed_index_price?: number | null;
  mark_price?: number | null;
  future_mid?: number | null;
  mark_premium_pct?: number | null;
  funding_rate_pct?: number | null;
  latency_ms?: number | null;
  error?: string | null;
}

export interface SecondLevelIndexComponentSignal {
  observed_at: string;
  target_exchange: string;
  symbol: string;
  component_source: string;
  component_symbol: string;
  window_seconds: number;
  weight_pct?: number | null;
  component_price?: number | null;
  component_price_change_pct?: number | null;
  estimated_index_impact_pct?: number | null;
  official_index_change_pct?: number | null;
  mark_premium_change_pct?: number | null;
  lag_vs_official_index_pct?: number | null;
  signal_level: "high" | "medium" | "watch";
  reason: string;
}

export interface SecondLevelSamplingStatus {
  running: boolean;
  config: SecondLevelSamplingConfig;
  sample_count: number;
  component_sample_count: number;
  latest_observed_at?: string | null;
  latest_error?: string | null;
  latest_samples: SecondLevelMarketSample[];
  latest_spreads: SecondLevelPairSpreadSnapshot[];
  latest_component_samples: SecondLevelIndexComponentSample[];
  latest_component_signals: SecondLevelIndexComponentSignal[];
}

export interface FatFingerBacktestRequest {
  symbol: string;
  market_mode: FatFingerMarketMode;
  hours: number;
  sample_limit: number;
  entry_spread_pct: number;
  ladder_levels: number;
  ladder_step_pct: number;
  order_notional_usdt: number;
  maker_fill_assumption_pct: number;
  maker_fee_pct: number;
  taker_fee_pct: number;
  taker_slippage_pct: number;
  hedge_delay_seconds: number;
  order_expiry_seconds: number;
  take_profit_pct: number;
  max_hold_seconds: number;
  min_hedge_depth_usdt: number;
  max_quote_age_seconds: number;
  require_known_hedge_depth: boolean;
  cooldown_seconds: number;
}

export interface FatFingerBacktestTrade {
  id: string;
  symbol: string;
  market_mode: FatFingerMarketMode;
  maker_exchange: string;
  maker_market_type: MarketType;
  hedge_exchange: string;
  hedge_market_type: MarketType;
  maker_side: FatFingerMakerSide;
  tier: number;
  entry_target_spread_pct: number;
  order_placed_at: string;
  maker_filled_at: string;
  hedge_filled_at: string;
  closed_at: string;
  exit_reason: FatFingerExitReason;
  maker_entry_price: number;
  hedge_entry_price: number;
  maker_exit_price: number;
  hedge_exit_price: number;
  notional_usdt: number;
  hedge_depth_usdt?: number | null;
  entry_hedge_edge_pct: number;
  gross_pnl_usdt: number;
  net_pnl_usdt: number;
  net_pnl_pct: number;
  max_favorable_pnl_pct: number;
  max_adverse_pnl_pct: number;
  hedge_delay_seconds: number;
  hold_seconds: number;
}

export interface FatFingerBacktestRouteSummary {
  maker_exchange: string;
  maker_market_type: MarketType;
  hedge_exchange: string;
  hedge_market_type: MarketType;
  maker_side: FatFingerMakerSide;
  touch_count: number;
  hedge_count: number;
  unhedged_count: number;
  closed_trade_count: number;
  win_count: number;
  total_notional_usdt: number;
  total_net_pnl_usdt: number;
  average_net_pnl_pct?: number | null;
  median_net_pnl_pct?: number | null;
  worst_net_pnl_pct?: number | null;
  average_hold_seconds?: number | null;
}

export interface FatFingerBacktestResult {
  request: FatFingerBacktestRequest;
  start_at: string;
  end_at: string;
  raw_sample_count: number;
  samples_truncated: boolean;
  frame_count: number;
  exchange_count: number;
  order_placed_count: number;
  order_expired_count: number;
  order_skipped_depth_count: number;
  exit_skipped_depth_count: number;
  quote_touch_count: number;
  hedge_completed_count: number;
  unhedged_touch_count: number;
  open_position_count: number;
  closed_trade_count: number;
  target_exit_count: number;
  timeout_exit_count: number;
  win_count: number;
  loss_count: number;
  win_rate_pct?: number | null;
  hedge_success_rate_pct?: number | null;
  total_notional_usdt: number;
  total_net_pnl_usdt: number;
  average_net_pnl_pct?: number | null;
  median_net_pnl_pct?: number | null;
  worst_net_pnl_pct?: number | null;
  average_hold_seconds?: number | null;
  average_hedge_delay_seconds?: number | null;
  route_summaries: FatFingerBacktestRouteSummary[];
  trades: FatFingerBacktestTrade[];
  warnings: string[];
}

export interface NewListingWatchItem {
  id: string;
  enabled: boolean;
  symbol: string;
  market_type: MarketType;
  exchanges: string[];
  interval_seconds: number;
  retention_hours: number;
  normal_threshold_pct: number;
  strong_threshold_pct: number;
  extreme_threshold_pct: number;
  min_executable_notional_usdt: number;
  depth_validation_notional_usdt: number;
  allow_low_liquidity_alert: boolean;
  normal_consecutive_hits: number;
  strong_consecutive_hits: number;
  extreme_consecutive_hits: number;
  cooldown_seconds: number;
  buy_fee_pct: number;
  sell_fee_pct: number;
  slippage_buffer_pct: number;
  start_at: string | null;
  stop_at: string | null;
  note: string;
  created_at: string;
  updated_at: string;
}

export interface NewListingSpreadSample {
  id?: number | null;
  watch_id: string;
  observed_at: string;
  symbol: string;
  market_type: MarketType;
  buy_exchange: string;
  sell_exchange: string;
  buy_bid?: number | null;
  buy_ask?: number | null;
  buy_bid_size?: number | null;
  buy_ask_size?: number | null;
  sell_bid?: number | null;
  sell_ask?: number | null;
  sell_bid_size?: number | null;
  sell_ask_size?: number | null;
  buy_price: number;
  sell_price: number;
  raw_spread_pct: number;
  net_spread_pct: number;
  executable_notional_usdt?: number | null;
  buy_latency_ms?: number | null;
  sell_latency_ms?: number | null;
  alert_level: NewListingAlertLevel;
  alert_triggered: boolean;
  no_alert_reason?: string | null;
  risk_labels: string[];
}

export interface NewListingAlertEvent {
  id: string;
  watch_id: string;
  symbol: string;
  market_type: MarketType;
  level: NewListingAlertLevel;
  buy_exchange: string;
  sell_exchange: string;
  net_spread_pct: number;
  raw_spread_pct: number;
  executable_notional_usdt?: number | null;
  message: string;
  created_at: string;
}

export interface NewListingMonitorStatus {
  running: boolean;
  watch_count: number;
  enabled_watch_count: number;
  active_watch_count: number;
  sample_count: number;
  event_count: number;
  latest_error?: string | null;
  watchlist: NewListingWatchItem[];
  latest_samples: NewListingSpreadSample[];
  latest_events: NewListingAlertEvent[];
}

export interface NewListingHistoryResult {
  symbol?: string | null;
  watch_id?: string | null;
  start_at: string;
  end_at: string;
  sample_count: number;
  event_count: number;
  max_raw_spread_pct?: number | null;
  max_net_spread_pct?: number | null;
  max_sample?: NewListingSpreadSample | null;
  samples: NewListingSpreadSample[];
  events: NewListingAlertEvent[];
  warnings: string[];
}

export interface NegativeBasisWatchItem {
  id: string;
  auto_managed: boolean;
  enabled: boolean;
  symbol: string;
  spot_exchange: string;
  future_exchange: string;
  spot_symbol: string | null;
  future_symbol: string | null;
  future_multiplier: number;
  interval_seconds: number;
  lookback_hours: number;
  retention_hours: number;
  watch_threshold_pct: number;
  building_threshold_pct: number;
  confirmed_threshold_pct: number;
  strong_threshold_pct: number;
  extreme_threshold_pct: number;
  watch_consecutive_hits: number;
  building_consecutive_hits: number;
  confirmed_consecutive_hits: number;
  strong_consecutive_hits: number;
  extreme_consecutive_hits: number;
  spot_volume_growth_threshold: number;
  oi_confirmed_growth_pct: number;
  oi_strong_growth_pct: number;
  min_spot_hourly_volume_usdt: number;
  alert_min_level: NegativeBasisSignalLevel;
  cooldown_seconds: number;
  note: string;
  created_at: string;
  updated_at: string;
}

export interface NegativeBasisAutoScanStrategy {
  interval_seconds: number;
  lookback_hours: number;
  retention_hours: number;
  watch_threshold_pct: number;
  building_threshold_pct: number;
  confirmed_threshold_pct: number;
  strong_threshold_pct: number;
  extreme_threshold_pct: number;
  watch_consecutive_hits: number;
  building_consecutive_hits: number;
  confirmed_consecutive_hits: number;
  strong_consecutive_hits: number;
  extreme_consecutive_hits: number;
  spot_volume_growth_threshold: number;
  oi_confirmed_growth_pct: number;
  oi_strong_growth_pct: number;
  min_spot_hourly_volume_usdt: number;
  alert_min_level: NegativeBasisSignalLevel;
  cooldown_seconds: number;
}

export interface NegativeBasisAutoScanSettings {
  enabled: boolean;
  feishu_notifications_enabled: boolean;
  strategy: NegativeBasisAutoScanStrategy;
  blocked_exchanges: string[];
  blocked_symbols: string[];
  blocked_exchange_symbols: string[];
  updated_at: string;
}

export interface NegativeBasisPoint {
  bucket_at: string;
  spot_close: number;
  future_close: number;
  spot_premium_abs: number;
  spot_premium_pct: number;
}

export interface NegativeBasisHourlyStatPoint {
  bucket_at: string;
  spot_premium_mean_pct: number | null;
  spot_premium_max_pct: number | null;
  spot_premium_last_pct: number | null;
  spot_volume_usdt: number | null;
  future_volume_usdt: number | null;
  spot_volume_growth: number | null;
  future_volume_ratio: number | null;
  open_interest_open_usdt: number | null;
  open_interest_close_usdt: number | null;
  open_interest_change_pct: number | null;
  long_account_pct: number | null;
  short_account_pct: number | null;
  long_account_count: number | null;
  short_account_count: number | null;
  long_short_ratio: number | null;
  funding_rate_pct: number | null;
}

export interface NegativeBasisThresholdState {
  name: NegativeBasisSignalLevel;
  threshold_pct: number;
  required_hits: number;
  first_seen_at: string | null;
  first_consecutive_at: string | null;
  current_consecutive_hits: number;
  max_consecutive_hits: number;
  currently_active: boolean;
}

export interface NegativeBasisCurrentSnapshot {
  observed_at: string;
  spot_leg: PairSpreadCurrentLeg;
  future_leg: PairSpreadCurrentLeg;
  spot_premium_abs: number;
  spot_premium_pct: number;
}

export interface NegativeBasisAnalysisResult {
  item: NegativeBasisWatchItem;
  observed_at: string;
  signal_level: NegativeBasisSignalLevel;
  score: number;
  reasons: string[];
  warnings: string[];
  current: NegativeBasisCurrentSnapshot | null;
  spot_premium: PairSpreadValueStats;
  thresholds: NegativeBasisThresholdState[];
  points: NegativeBasisPoint[];
  hourly_stats: NegativeBasisHourlyStatPoint[];
}

export interface NegativeBasisSignalSample {
  id?: number | null;
  watch_id: string;
  observed_at: string;
  symbol: string;
  spot_exchange: string;
  future_exchange: string;
  signal_level: NegativeBasisSignalLevel;
  score: number;
  spot_premium_pct: number | null;
  spot_price: number | null;
  future_price: number | null;
  spot_volume_24h_usdt: number | null;
  future_volume_24h_usdt: number | null;
  open_interest_usdt: number | null;
  open_interest_change_pct: number | null;
  long_account_pct: number | null;
  short_account_pct: number | null;
  long_short_ratio: number | null;
  funding_rate_pct: number | null;
  reasons: string[];
}

export interface NegativeBasisAlertEvent {
  id: string;
  watch_id: string;
  symbol: string;
  spot_exchange: string;
  future_exchange: string;
  signal_level: NegativeBasisSignalLevel;
  score: number;
  spot_premium_pct: number | null;
  message: string;
  created_at: string;
}

export interface NegativeBasisAutoCandidate {
  id: string;
  symbol: string;
  spot_exchange: string;
  future_exchange: string;
  spot_symbol?: string | null;
  future_symbol?: string | null;
  future_multiplier: number;
  signal_level: NegativeBasisSignalLevel;
  selection_score: number;
  selection_reasons: string[];
  spot_premium_pct: number;
  spot_price: number;
  future_price: number;
  spot_volume_24h_usdt: number | null;
  future_volume_24h_usdt: number | null;
  observed_at: string;
}

export interface NegativeBasisMonitorStatus {
  running: boolean;
  auto_scan_enabled: boolean;
  auto_scan_settings: NegativeBasisAutoScanSettings;
  auto_scan_last_at: string | null;
  auto_scan_error: string | null;
  auto_candidate_count: number;
  auto_candidates: NegativeBasisAutoCandidate[];
  watch_count: number;
  enabled_watch_count: number;
  sample_count: number;
  event_count: number;
  latest_error?: string | null;
  watchlist: NegativeBasisWatchItem[];
  latest_samples: NegativeBasisSignalSample[];
  latest_events: NegativeBasisAlertEvent[];
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
  listing_delisting_alerts_enabled: boolean;
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
  market_type: MarketType;
}

export interface PairSpreadPoint {
  bucket_at: string;
  leg1_close: number;
  leg2_close: number;
  spread_abs: number;
  spread_pct: number;
}

export interface PairSpreadHourlyVolumePoint {
  bucket_at: string;
  leg1_volume_usdt: number | null;
  leg2_volume_usdt: number | null;
  total_volume_usdt: number | null;
  volume_diff_usdt: number | null;
  volume_ratio: number | null;
}

export interface PairSpreadOpenInterestPoint {
  bucket_at: string;
  leg1_open_interest_usdt: number | null;
  leg2_open_interest_usdt: number | null;
  leg1_change_usdt: number | null;
  leg2_change_usdt: number | null;
  net_change_usdt: number | null;
  source?: string;
  leg1_source?: string;
  leg2_source?: string;
}

export interface PairSpreadFundingPoint {
  exchange: string;
  symbol: string;
  funding_time: string;
  funding_rate_pct: number;
}

export interface PairSpreadRealtimeFundingPoint {
  bucket_at: string;
  left_rate_pct: number | null;
  right_rate_pct: number | null;
  net_rate_pct: number | null;
  source?: string;
}

export interface PairSpreadFundingRecordRequest {
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  leg2_multiplier: number;
}

export interface PairSpreadFundingWatchItem {
  pair_key: string;
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  leg2_multiplier: number;
  interval_seconds: number;
  created_at: string;
  updated_at: string;
  sample_count: number;
  latest_sample_at: string | null;
}

export interface PairSpreadFundingRecordStatus {
  watched: boolean;
  item: PairSpreadFundingWatchItem | null;
  samples: PairSpreadRealtimeFundingPoint[];
  warnings: string[];
}

export interface PairSpreadCurrentLeg {
  exchange: string;
  symbol: string;
  market_type: MarketType;
  raw_symbol: string;
  price: number;
  price_field: PairSpreadPriceField;
  mark_price: number | null;
  index_price: number | null;
  mid_price: number | null;
  last_price: number | null;
  volume_24h_usdt: number | null;
  open_interest_usdt: number | null;
  open_interest_contracts: number | null;
  long_account_pct: number | null;
  short_account_pct: number | null;
  long_account_count: number | null;
  short_account_count: number | null;
  long_short_ratio: number | null;
  funding_rate_pct: number | null;
  funding_next_rate_pct: number | null;
  funding_next_time: string | null;
  funding_interval_hours: number | null;
  funding_rate_upper_pct: number | null;
  funding_rate_lower_pct: number | null;
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
  interval_seconds: number;
  leg2_multiplier: number;
  observed_at: string;
  point_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  spread_abs: PairSpreadValueStats;
  spread_pct: PairSpreadValueStats;
  current: PairSpreadCurrentSnapshot | null;
  points: PairSpreadPoint[];
  hourly_volume?: PairSpreadHourlyVolumePoint[];
  open_interest?: PairSpreadOpenInterestPoint[];
  open_interest_source?: string;
  open_interest_leg1_source?: string;
  open_interest_leg2_source?: string;
  funding_history: PairSpreadFundingPoint[];
  realtime_funding?: PairSpreadRealtimeFundingPoint[];
  warnings: string[];
}

export interface PairSpreadFundingHistoryResult {
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  start_at: string;
  end_at: string;
  funding_history: PairSpreadFundingPoint[];
  warnings: string[];
}

export interface SymbolExchangePriceSnapshot {
  exchange: string;
  symbol: string;
  market_type: MarketType;
  raw_symbol: string;
  price: number;
  price_field: PairSpreadPriceField;
  funding_rate_pct: number | null;
  timestamp: string;
}

export interface SymbolSpreadPoint {
  bucket_at: string;
  base_close: number;
  exchange_close: number;
  spread_abs: number;
  spread_pct: number;
}

export interface SymbolSpreadSeries {
  exchange: string;
  symbol: string;
  market_type: MarketType;
  point_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  spread_abs: PairSpreadValueStats;
  spread_pct: PairSpreadValueStats;
  current: SymbolSpreadPoint | null;
  points: SymbolSpreadPoint[];
}

export interface SymbolSpreadQueryResult {
  symbol: string;
  market_type: MarketType;
  base_exchange: string;
  exchanges: string[];
  hours: number;
  interval_minutes: number;
  interval_seconds: number;
  observed_at: string;
  point_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  current_prices: SymbolExchangePriceSnapshot[];
  series: SymbolSpreadSeries[];
  warnings: string[];
}

export interface PairSpreadDiagnosticThresholdRun {
  start_at: string | null;
  end_at: string | null;
  point_count: number;
  peak_spread_pct: number | null;
  peak_at: string | null;
}

export interface PairSpreadDiagnosticRule {
  id: string;
  name: string;
  enabled: boolean;
  matches_pair_scope: boolean;
  min_open_spread_pct: number;
  min_fee_adjusted_open_pct: number;
  consecutive_hits: number;
  cooldown_seconds: number;
  reasons: string[];
}

export interface PairSpreadDiagnosticEvent {
  rule_id: string;
  status: string;
  created_at: string;
  message: string;
}

export interface PairSpreadDiagnosticEventSummary {
  total: number;
  sent: number;
  muted: number;
  failed: number;
  latest_status: string | null;
  latest_at: string | null;
  latest_message: string | null;
  events: PairSpreadDiagnosticEvent[];
}

export interface PairSpreadDiagnosticResult {
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  hours: number;
  requested_interval_seconds: number;
  interval_seconds: number;
  observed_at: string;
  point_count: number;
  threshold_pct: number;
  peak_at: string | null;
  peak_spread_pct: number | null;
  peak_spread_abs: number | null;
  peak_leg1_close: number | null;
  peak_leg2_close: number | null;
  points_over_threshold: number;
  first_over_threshold_at: string | null;
  last_over_threshold_at: string | null;
  longest_run: PairSpreadDiagnosticThresholdRun;
  current_spread_pct: number | null;
  inferred_type: OpportunityType;
  alert_rules: PairSpreadDiagnosticRule[];
  alert_events: PairSpreadDiagnosticEventSummary;
  suppress_when_card_conditions_fail: boolean;
  notes: string[];
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
  funding_interval_hours: number | null;
  funding_rate_upper_pct: number | null;
  funding_rate_lower_pct: number | null;
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

export type MinuteSignalEventType =
  | "SHOCK_ALERT"
  | "ENTRY"
  | "TAKE_PROFIT"
  | "STOP_LOSS"
  | "TIME_EXIT";

export interface MinuteSignalEvent {
  event_type: MinuteSignalEventType;
  state_before: string;
  state_after: string;
  signal_time_cst: string;
  planned_execution_time_cst: string;
  reason: string;
  signal_basis_bps: number | null;
  premium_bps: number | null;
  premium_low_5m_bps: number | null;
  premium_low_15m_bps: number | null;
  basis_peak_60m_bps: number | null;
  basis_drawdown_bps: number | null;
  compression_ratio: number | null;
  signal_entry_basis_bps: number | null;
  signal_basis_gain_bps: number | null;
}

export interface MinuteSignalPoint {
  time_cst: string;
  basis_bps: number | null;
  premium_bps: number | null;
  basis_peak_60m_bps: number | null;
  compression_ratio: number | null;
}

export interface MinuteSignalScanResult {
  alpha_symbol: string;
  futures_symbol: string;
  hours: number;
  observed_at: string;
  bar_count: number;
  latest: Record<string, unknown> | null;
  points: MinuteSignalPoint[];
  events: MinuteSignalEvent[];
  warnings: string[];
}

export interface MinuteSignalUniverseCandidate {
  base_asset: string;
  alpha_id: string;
  alpha_symbol: string;
  futures_symbol: string;
  alpha_price: number;
  futures_price: number;
  index_price: number | null;
  volume_24h_usdt: number;
  initial_basis_bps: number;
  initial_premium_bps: number | null;
  score: number;
  event_type: MinuteSignalEventType | null;
  signal_time_cst: string | null;
  planned_execution_time_cst: string | null;
  reason: string;
  basis_bps: number | null;
  premium_bps: number | null;
  basis_peak_60m_bps: number | null;
  compression_ratio: number | null;
  bar_count: number;
  recent_events: MinuteSignalEvent[];
  error: string | null;
}

export interface MinuteSignalUniverseScanResult {
  observed_at: string;
  hours: number;
  max_symbols: number;
  min_volume_24h_usdt: number;
  alert_cooldown_minutes: number;
  max_entry_basis_bps: number;
  require_negative_premium_when_spot_above: boolean;
  max_premium_when_spot_above_bps: number;
  universe_count: number;
  eligible_count: number;
  filtered_by_basis_count: number;
  filtered_by_premium_count: number;
  scanned_count: number;
  signal_count: number;
  error_count: number;
  candidates: MinuteSignalUniverseCandidate[];
  warnings: string[];
}

export interface MinuteSignalSettings {
  hours: number;
  max_symbols: number;
  min_volume_24h_usdt: number;
  alert_cooldown_minutes: number;
  max_entry_basis_bps: number;
  require_negative_premium_when_spot_above: boolean;
  max_premium_when_spot_above_bps: number;
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

export interface AstroNewListingCardSettings extends AstroCardSettings {}

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
