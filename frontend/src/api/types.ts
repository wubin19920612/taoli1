export type MarketType = "spot" | "future";
export type OpportunityType = "SF" | "FF" | "SS";
export type AlertSeverity = "info" | "warning" | "critical";
export type PhonePriceAlertCondition = "above" | "below";
export type PhonePriceAlertPriceField = "mark_price" | "index_price" | "mid_price" | "bid" | "ask";
export type PairSpreadPriceField = "mark_price" | "mid_price" | "index_price" | "last_price";
export type AnnouncementKind = "listing" | "delisting" | "launchpool" | "other";

export interface Opportunity {
  identity_enforced?: boolean;
  validation?: { capabilities: { manual_card: boolean }; blockers: Array<{ code: string; message: string }> } | null;
  id: string;
  type: OpportunityType;
  symbol: string;
  buy_exchange: string;
  buy_market_type: MarketType;
  buy_raw_symbol?: string | null;
  buy_dex?: string | null;
  buy_price_multiplier?: number;
  buy_contract_size_multiplier?: number | null;
  buy_timestamp?: string | null;
  buy_age_ms?: number | null;
  buy_source_age_ms?: number | null;
  buy_time_source?: string | null;
  buy_data_source?: string | null;
  buy_is_estimated?: boolean;
  buy_estimated_fields?: string[];
  sell_exchange: string;
  sell_market_type: MarketType;
  sell_raw_symbol?: string | null;
  sell_dex?: string | null;
  sell_price_multiplier?: number;
  sell_contract_size_multiplier?: number | null;
  sell_timestamp?: string | null;
  sell_age_ms?: number | null;
  sell_source_age_ms?: number | null;
  sell_time_source?: string | null;
  sell_data_source?: string | null;
  sell_is_estimated?: boolean;
  sell_estimated_fields?: string[];
  buy_fee_pct?: number;
  sell_fee_pct?: number;
  safety_slippage_pct?: number;
  fees_are_estimated?: boolean;
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


export interface OpportunityList {
  opportunities: Opportunity[];
  excluded_stale?: number | null;
}

export interface SymbolAlias {
  exchange: string;
  symbol: string;
  canonical_symbol: string;
  market_type?: MarketType | null;
  dex?: string | null;
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
  dex?: string | null;
  contract_size_multiplier?: number | null;
  data_source?: string | null;
  upstream_timestamp?: string | null;
  is_estimated?: boolean;
  estimated_fields?: string[];
  symbol_alias_original_symbol?: string | null;
  symbol_alias_price_multiplier?: number;
  asset_id?: string | null;
  asset_name?: string | null;
  identity_status?: string;
  unit_status?: string;
  quote_kind?: string;
  original_quote_currency?: string | null;
  quote_conversion_source?: string | null;
  quote_conversion_valid_until?: string | null;
}

export interface InstrumentMarketCandidate extends MarketSnapshot {
  market_id?: string | null;
  data_status: "live" | "stale";
  age_seconds: number;
  stale_after_seconds: number;
  error: string | null;
}

export interface InstrumentRouteStatus {
  card_id: string | null;
  card_name: string;
  side: "buy" | "sell";
  route: string;
  exchange: string | null;
  market_type: MarketType;
  astro_raw_symbol: string;
  canonical_symbol: string;
  dex: string | null;
  counterparty_route: string;
  status: "live_market" | "market_missing" | "route_only";
  live_data_supported: boolean;
  matched_raw_symbol: string | null;
  source: string;
  reason: string;
}

export interface InstrumentExchangeSnapshot {
  exchange: string;
  spot: MarketSnapshot | null;
  future: MarketSnapshot | null;
  error: string | null;
}

export interface InstrumentSpreadComparison {
  reference_only?: boolean;
  validation_reasons?: string[];
  id: string;
  buy_exchange: string;
  buy_market_type: MarketType;
  buy_raw_symbol: string;
  buy_dex: string | null;
  buy_price_multiplier: number;
  buy_contract_size_multiplier: number | null;
  buy_bid: number;
  buy_ask: number;
  buy_volume_24h_usdt: number | null;
  buy_funding_rate_pct: number | null;
  buy_funding_interval_hours: number | null;
  buy_timestamp: string;
  buy_data_source: string | null;
  buy_is_estimated: boolean;
  sell_exchange: string;
  sell_market_type: MarketType;
  sell_raw_symbol: string;
  sell_dex: string | null;
  sell_price_multiplier: number;
  sell_contract_size_multiplier: number | null;
  sell_bid: number;
  sell_ask: number;
  sell_volume_24h_usdt: number | null;
  sell_funding_rate_pct: number | null;
  sell_funding_interval_hours: number | null;
  sell_timestamp: string;
  sell_data_source: string | null;
  sell_is_estimated: boolean;
  price_difference: number;
  executable_spread_pct: number;
  close_spread_pct: number;
  mid_spread_pct: number;
  opportunity_type: OpportunityType | null;
  astro_supported: boolean;
  astro_blocker: string | null;
}

export interface InstrumentLookupResult {
  asset_groups?: Array<{ asset_id: string | null; display_name: string; market_ids: string[] }>;
  identity_ambiguous?: boolean;
  query: string;
  symbol: string;
  base: string;
  quote: string;
  observed_at: string | null;
  exchange_count: number;
  market_count: number;
  markets: InstrumentMarketCandidate[];
  astro_routes: InstrumentRouteStatus[];
  route_errors: Record<string, string>;
  exchanges: InstrumentExchangeSnapshot[];
  spreads: InstrumentSpreadComparison[];
}

export interface InstrumentPriceChangeStats {
  change_1h_pct: number | null;
  change_24h_pct: number | null;
  observed_at: string | null;
  source: string;
  error: string | null;
}

export interface InstrumentSpread24hStats {
  max_spread_pct: number | null;
  max_at: string | null;
  min_spread_pct: number | null;
  min_at: string | null;
  point_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  complete: boolean;
  is_estimated: boolean;
  source: string;
  error: string | null;
}

export interface InstrumentStatisticsResult {
  symbol: string;
  observed_at: string;
  markets: Record<string, InstrumentPriceChangeStats>;
  spreads: Record<string, InstrumentSpread24hStats>;
}

export interface InstrumentMarketCapResult {
  base: string;
  status: "available" | "ambiguous" | "not_found" | "unavailable" | "source_error";
  candidates: Array<{
    id: string;
    name: string;
    symbol: string;
    market_cap_rank: number | null;
  }>;
  selected_id: string | null;
  market_cap_usd: number | null;
  updated_at: string | null;
  source: string;
}

export type HyperliquidActionState = "available" | "blocked" | "conditional" | "unknown";

export interface HyperliquidTradeActionStatus {
  state: HyperliquidActionState;
  reason_code: string;
  reason: string;
  executable_price: number | null;
  depth_1pct_usdt: number | null;
}

export interface HyperliquidMarketTradeStatus {
  symbol: string;
  dex: string;
  raw_symbol: string;
  observed_at: string;
  at_open_interest_cap: boolean | null;
  is_delisted: boolean;
  only_isolated: boolean;
  margin_mode: string | null;
  max_leverage: number | null;
  size_decimals: number | null;
  best_bid: number | null;
  best_ask: number | null;
  bid_depth_1pct_usdt: number | null;
  ask_depth_1pct_usdt: number | null;
  mark_price: number | null;
  oracle_price: number | null;
  open_interest: number | null;
  open_interest_usdt: number | null;
  volume_24h_usdt: number | null;
  funding_rate_pct: number | null;
  funding_interval_hours: number;
  market_multiplier: number;
  fees_included: boolean;
  fee_note: string;
  buy_open: HyperliquidTradeActionStatus;
  sell_open: HyperliquidTradeActionStatus;
  buy_reduce_only: HyperliquidTradeActionStatus;
  sell_reduce_only: HyperliquidTradeActionStatus;
}

export interface HyperliquidTradeStatusResult {
  query: string;
  observed_at: string;
  source: string;
  markets: HyperliquidMarketTradeStatus[];
  limitations: string[];
}

export interface HyperliquidTradeStatusWatch {
  id: string;
  symbol: string;
  dex: string;
  raw_symbol: string;
  monitor_buy: boolean;
  monitor_sell: boolean;
  enabled: boolean;
  last_buy_state: HyperliquidActionState | null;
  last_sell_state: HyperliquidActionState | null;
  last_checked_at: string | null;
  last_notified_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export type TradeAvailabilityState = "available" | "blocked" | "conditional" | "unknown" | "not_applicable";
export type TradeEvidenceScope = "public_market" | "account" | "order_error" | "platform_capability" | "none";
export type TradeEvidenceState = "confirmed" | "not_checked" | "not_provided" | "error";

export interface TradeActionStatus {
  state: TradeAvailabilityState;
  reason_code: string;
  reason: string;
  scope: TradeEvidenceScope;
  executable_price: number | null;
  depth_1pct_usdt: number | null;
}

export interface TradeDiagnosticEvidence {
  scope: TradeEvidenceScope;
  state: TradeEvidenceState;
  reason_code: string;
  message: string;
  source: string | null;
  raw_error: string | null;
}

export type TransferAvailabilityState = "enabled" | "partial" | "disabled" | "unknown";

export interface SpotTransferNetworkStatus {
  network: string;
  deposit_enabled: boolean | null;
  withdraw_enabled: boolean | null;
}

export interface SpotTransferAvailability {
  asset: string;
  deposit_state: TransferAvailabilityState;
  withdraw_state: TransferAvailabilityState;
  all_enabled: boolean | null;
  publicly_queryable: boolean;
  source: string;
  observed_at: string | null;
  networks: SpotTransferNetworkStatus[];
  note: string;
  error: string | null;
}

export interface MarketTradeAvailability {
  exchange: string;
  market_type: MarketType;
  symbol: string;
  raw_symbol: string;
  dex: string | null;
  coverage_tier: "core" | "existing" | "evaluated";
  observed_at: string;
  market_data_updated_at: string;
  orderbook_updated_at: string | null;
  orderbook_source: string;
  public_status_code: string;
  public_status_source: string;
  public_restrictions: string[];
  diagnostics: TradeDiagnosticEvidence[];
  best_bid: number | null;
  best_ask: number | null;
  bid_depth_01pct_usdt: number | null;
  ask_depth_01pct_usdt: number | null;
  bid_depth_1pct_usdt: number | null;
  ask_depth_1pct_usdt: number | null;
  volume_24h_usdt: number | null;
  funding_rate_pct: number | null;
  funding_next_rate_pct: number | null;
  funding_interval_hours: number | null;
  funding_next_time: string | null;
  mark_price: number | null;
  index_price: number | null;
  maker_fee_pct: number | null;
  taker_fee_pct: number | null;
  fees_included: boolean;
  fee_note: string;
  market_multiplier: number;
  contract_size_multiplier: number;
  spot_transfer: SpotTransferAvailability | null;
  buy_open: TradeActionStatus;
  sell_open: TradeActionStatus;
  buy_reduce_only: TradeActionStatus;
  sell_reduce_only: TradeActionStatus;
}

export interface ContractIndexComponent {
  source_exchange: string;
  market_type: MarketType | null;
  raw_symbol: string;
  weight: number | null;
  price: number | null;
}

export interface ContractIndexComposition {
  exchange: string;
  market_type: MarketType;
  symbol: string;
  raw_symbol: string;
  dex: string | null;
  status: "available" | "not_returned" | "error";
  source: string;
  index_price: number | null;
  observed_at: string | null;
  weight_total: number | null;
  components: ContractIndexComponent[];
  note: string;
  error: string | null;
}

export interface TradeAvailabilityCoverage {
  exchange: string;
  tier: "core" | "existing" | "evaluated";
  public_status_supported: boolean;
  orderbook_supported: boolean;
  note: string;
}

export interface TradeAvailabilityResult {
  query: string;
  observed_at: string;
  source: string;
  markets: MarketTradeAvailability[];
  index_compositions: ContractIndexComposition[];
  coverage: TradeAvailabilityCoverage[];
  errors: Record<string, string>;
  limitations: string[];
}

export interface TradeAvailabilityWatch {
  id: string;
  symbol: string;
  exchange: string;
  market_type: MarketType;
  raw_symbol: string;
  dex: string | null;
  monitor_buy: boolean;
  monitor_sell: boolean;
  enabled: boolean;
  last_buy_state: TradeAvailabilityState | null;
  last_sell_state: TradeAvailabilityState | null;
  last_buy_reduce_only_state: TradeAvailabilityState | null;
  last_sell_reduce_only_state: TradeAvailabilityState | null;
  last_checked_at: string | null;
  last_notified_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface HealthStatus {
  status: string;
  markets: number;
  opportunities: number;
  exchange_errors: Record<string, string>;
  exchange_states: Record<string, ExchangePollState>;
}

export interface RiskSettings {
  big_spread_alert_enabled?: boolean;
  big_spread_alert_pct?: number;
  big_spread_alert_hits?: number;
  big_spread_alert_cooldown_seconds?: number;
  big_spread_alert_auto_create?: boolean;
  min_volume_24h_usdt: number;
  min_volume_24h_k?: number;
  stale_after_seconds: number;
  huge_spread_pct: number;
  wide_spread_pct: number;
  mark_index_deviation_pct: number;
  multi_exchange_premium_alert_enabled?: boolean;
  multi_exchange_premium_threshold_pct?: number;
  multi_exchange_premium_cooldown_seconds?: number;
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
  format: "compact" | "detailed";
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
  allow_same_name_variants: boolean;
}

export interface AlertRule {
  id?: string;
  name: string;
  enabled: boolean;
  types: OpportunityType[];
  suppress_sf_negative_funding: boolean;
  include_exchanges: string[];
  exclude_exchanges: string[];
  include_symbols: string[];
  exclude_symbols: string[];
  min_open_spread_pct: number;
  favorable_funding_open_spread_pct: number | null;
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

export interface IndexComponentAutoWatchStatus {
  enabled: boolean;
  items: { source: string; symbol: string }[];
  error: string | null;
}

export interface IndexComponentNotificationSettings {
  enabled: boolean;
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

export interface AnnouncementResearchSource {
  title: string;
  url: string;
}

export interface AnnouncementAssetResearch {
  symbol: string;
  canonical_symbol?: string | null;
  asset_type: string;
  name?: string | null;
  summary?: string | null;
  business?: string | null;
  sources: AnnouncementResearchSource[];
  status: string;
  searched_at: string;
}

export interface ExchangeAnnouncement {
  id: string;
  exchange: string;
  announcement_id: string;
  kind: AnnouncementKind;
  title: string;
  url: string;
  source: string;
  title_zh?: string | null;
  url_zh?: string | null;
  category?: string | null;
  symbols: string[];
  market_type?: string | null;
  event_time?: string | null;
  event_schedule?: AnnouncementEventScheduleItem[];
  summary?: string | null;
  asset_research?: AnnouncementAssetResearch[];
  published_at: string;
  fetched_at: string;
  alert_status: string;
  event_reminder_status: string;
  event_reminder_sent_at?: string | null;
}

export interface AnnouncementSettings {
  enabled: boolean;
  poll_interval_seconds: number;
  alert_max_age_minutes: number;
  record_exchanges: string[];
  alert_exchanges: string[];
  listing_delisting_alerts_enabled: boolean;
  launchpool_alerts_enabled: boolean;
  other_alerts_enabled: boolean;
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
  dex?: string | null;
}

export interface PairSpreadPreset {
  id: string;
  leg1_exchange: string;
  leg1_market_type: MarketType;
  leg1_dex: string;
  leg1_symbol: string;
  leg1_raw_symbol?: string | null;
  leg1_price_multiplier?: number;
  leg1_contract_size_multiplier?: number | null;
  leg2_exchange: string;
  leg2_market_type: MarketType;
  leg2_dex: string;
  leg2_symbol: string;
  leg2_raw_symbol?: string | null;
  leg2_price_multiplier?: number;
  leg2_contract_size_multiplier?: number | null;
  leg2_multiplier: number;
  hours: number;
  intervalSeconds: number;
  showDayCompare: boolean;
  dayCompareDays: number;
  dayCompareMode: "query" | "custom";
  dayCompareStartTime: string;
  dayCompareEndTime: string;
  savedAt: string;
}

export interface FloatingWatchSettings {
  symbols: string[];
  pair_ids: string[];
  hidden_positions: AccountPositionIdentity[];
}

export type AccountPositionSide = "long" | "short";
export type AccountPositionFreshness = "fresh" | "stale";
export type AccountPositionAccountState =
  | "not_configured"
  | "ok"
  | "empty"
  | "permission_denied"
  | "error"
  | "stale";

export interface AccountPositionIdentity {
  id: string;
  account_id: string;
  account_label: string;
  exchange: string;
  market_type: MarketType;
  raw_symbol: string;
  symbol: string;
  side: AccountPositionSide;
  dex: string | null;
}

export interface AccountPosition extends AccountPositionIdentity {
  quantity: number;
  quantity_unit: string;
  contract_quantity: number | null;
  contract_multiplier: number | null;
  entry_price: number | null;
  mark_price: number | null;
  notional_usdt: number | null;
  unrealized_pnl_usdt: number | null;
  roi_pct: number | null;
  leverage: number | null;
  price_basis: string;
  estimated_fields: string[];
  updated_at: string;
  freshness: AccountPositionFreshness;
  age_seconds: number;
}

export interface AccountPositionAccountStatus {
  account_id: string;
  account_label: string;
  exchange: string;
  market_type: MarketType;
  dex: string | null;
  configured: boolean;
  state: AccountPositionAccountState;
  message: string;
  position_count: number;
  queried_at: string;
  data_updated_at: string | null;
  age_seconds: number | null;
}

export interface AccountPositionSnapshot {
  positions: AccountPosition[];
  accounts: AccountPositionAccountStatus[];
  queried_at: string;
}

export type AccountConnectionExchange =
  | "binance"
  | "okx"
  | "bybit"
  | "gate"
  | "bitget"
  | "hyperliquid";

export interface SupportedAccountExchange {
  exchange: AccountConnectionExchange;
  label: string;
  supports_spot: boolean;
  supports_futures: boolean;
  requires_passphrase: boolean;
  uses_public_address: boolean;
  note: string;
}

export interface AccountConnection {
  id: string;
  exchange: AccountConnectionExchange;
  account_label: string;
  enabled: boolean;
  include_spot: boolean;
  include_futures: boolean;
  dex: string | null;
  credential_hint: string;
  last_test_state: AccountPositionAccountState | null;
  last_test_message: string | null;
  last_tested_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AccountConnectionOverview {
  storage_ready: boolean;
  storage_message: string;
  supported_exchanges: SupportedAccountExchange[];
  connections: AccountConnection[];
}

export interface AccountConnectionWrite {
  exchange: AccountConnectionExchange;
  account_label: string;
  enabled: boolean;
  include_spot: boolean;
  include_futures: boolean;
  dex?: string | null;
  api_key?: string;
  api_secret?: string;
  passphrase?: string;
  public_address?: string;
}

export type AccountConnectionUpdate = Partial<Omit<AccountConnectionWrite, "exchange">>;

export interface AccountConnectionTestScope {
  market_type: MarketType;
  dex: string | null;
  state: AccountPositionAccountState;
  message: string;
  position_count: number;
}

export interface AccountConnectionTestResult {
  connection_id: string | null;
  exchange: AccountConnectionExchange;
  account_label: string;
  success: boolean;
  scopes: AccountConnectionTestScope[];
  tested_at: string;
}

export interface HyperliquidMarketAsset {
  raw_symbol: string;
  symbol: string;
  base: string;
  delisted: boolean;
}

export interface HyperliquidDexMarket {
  dex: string;
  full_name: string;
  assets: HyperliquidMarketAsset[];
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
  dex?: string | null;
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
  dex?: string | null;
  raw_symbol: string;
  price_multiplier: number;
  contract_size_multiplier: number | null;
  data_source: string | null;
  upstream_timestamp: string | null;
  age_ms?: number | null;
  source_age_ms?: number | null;
  time_source?: string | null;
  is_estimated: boolean;
  estimated_fields: string[];
  estimated_taker_fee_pct: number | null;
  fee_is_estimated: boolean;
  price: number;
  price_field: PairSpreadPriceField;
  bid_price: number | null;
  ask_price: number | null;
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
  open_spread_abs: number | null;
  open_spread_pct: number | null;
  close_spread_abs: number | null;
  close_spread_pct: number | null;
  mark_spread_abs: number | null;
  mark_spread_pct: number | null;
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
  expected_samples?: number | null;
  actual_samples?: number | null;
  max_gap_ms?: number | null;
  complete?: boolean | null;
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
  dex?: string | null;
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
  dex?: string | null;
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
  card_variant: AstroCardVariant;
  close_position_buffer_pct: number;
  unfavorable_funding_weight: number;
  close_position_floor_pct: number;
}

export interface AstroCardCreateRequest {
  open_spread_pct?: number;
  close_spread_pct?: number;
  target?: CardTarget;
  max_trade_usdt?: number;
  leverage?: number;
  min_notional?: number;
  max_notional?: number;
  open_enabled?: boolean;
  card_variant?: AstroCardVariant;
  save_as_default?: boolean;
}

export type AstroCardVariant = "both" | "non_gc" | "gc";
export type CardTarget = "astro" | "arb_paper" | "arb_live";
export interface ArbMvpStatus {
  paper: { configured: boolean };
  live: { configured: boolean };
  default_max_position_usd: number;
}

export interface AstroCardRouteVariant {
  card_variant: "non_gc" | "gc";
  buy_exchange: string;
  sell_exchange: string;
}

export interface AstroInstrumentRouteRequest {
  buy_raw_symbol?: string;
  sell_raw_symbol?: string;
  buy_dex?: string | null;
  sell_dex?: string | null;
  comparison_kind?: "same_asset" | "custom_cross_asset";
  symbol: string;
  buy_exchange: string;
  buy_market_type: MarketType;
  sell_exchange: string;
  sell_market_type: MarketType;
}

export interface AstroOtherDexCard {
  pair_name: string;
  buy_ex: string;
  sell_ex: string;
  buy_dex: string | null;
  sell_dex: string | null;
  open_spread_pct: number | null;
  close_spread_pct: number | null;
  status: boolean | null;
}

export interface AstroCardLookup {
  found: boolean;
  count: number;
  pair_name: string | null;
  status: boolean | null;
  disable_open: boolean | null;
  open_position: string | null;
  close_position: string | null;
  open_spread_pct: number | null;
  close_spread_pct: number | null;
  other_dex_cards?: AstroOtherDexCard[];
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
  source_open_spread_pct: number | null;
  quoted_at: string | null;
  mode: "dry_run";
  can_submit: boolean;
  pair: Record<string, unknown> | null;
  sdk_payload: Record<string, unknown> | null;
  card_variant?: AstroCardVariant;
  route_variants?: AstroCardRouteVariant[];
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
  warnings?: string[];
}

export interface AstroPairStatus {
  id?: string;
  name?: string;
  type?: string;
  status?: boolean;
  disableOpen?: boolean;
  disableClose?: boolean;
  buyEx?: string;
  sellEx?: string;
  openPosition?: string | number | null;
  closePosition?: string | number | null;
  aExPosition?: string | number | null;
  bExPosition?: string | number | null;
  avgOpenAExPrice?: string | number | null;
  avgOpenBExPrice?: string | number | null;
  avgCloseAExPrice?: string | number | null;
  avgCloseBExPrice?: string | number | null;
  positionRate?: string | number | null;
  regressionValue?: string | number | null;
  profit?: string | number | null;
  realizedProfit?: string | number | null;
  aHlDex?: string | null;
  bHlDex?: string | null;
  aEffectiveHlDex?: string | null;
  bEffectiveHlDex?: string | null;
  maxTradeUSDT?: string | number | null;
  leverage?: string | number | null;
  minNotional?: string | number | null;
  maxNotional?: string | number | null;
  [key: string]: unknown;
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

export interface AstroPreaddSettings {
  enabled: boolean;
  exchanges: string[];
  funding_threshold_pct: number;
  premium_threshold_pct: number;
  open_spread_threshold_pct: number;
  min_volume_24h_usdt: number;
  scan_interval_seconds: number;
  max_routes_per_run: number;
  stale_after_seconds: number;
}

export interface AstroPreaddLegSnapshot {
  exchange: string;
  premium_index_pct: number | null;
  funding_rate_pct: number | null;
  funding_interval_hours: number | null;
  volume_24h_usdt: number | null;
}

export interface AstroPreaddCandidate {
  id: string;
  symbol: string;
  buy_exchange: string;
  sell_exchange: string;
  signal_exchange: string;
  signal_type: "funding" | "premium_proxy";
  signal_value_pct: number;
  funding_source: "predicted" | "current" | "missing";
  funding_interval_hours: number | null;
  entry_mode: "convergence" | "bybit_funding_reverse";
  card_open_spread_pct: number;
  buy_leg: AstroPreaddLegSnapshot;
  sell_leg: AstroPreaddLegSnapshot;
  live_spread_pct: number;
  observed_at: string;
}

export interface AstroPreaddPreview {
  items: AstroPreaddCandidate[];
  warnings: string[];
  total_matches: number;
}

export interface AstroPreaddRunResult {
  attempted: number;
  created: number;
  skipped: number;
  failed: number;
  results: string[];
  warnings: string[];
}

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
  long_raw_symbol?: string | null;
  short_exchange: string;
  short_market_type: MarketType;
  short_raw_symbol?: string | null;
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
export type FundingOpportunityType =
  | "BASIS_AND_FUNDING_ALIGNED"
  | "STRONG_FUNDING_NEAR_SETTLEMENT"
  | "INTERVAL_MISMATCH"
  | "FORMULA_DIVERGENCE"
  | "BASIS_CARRY_CONFLICTED"
  | "BASIS_MEAN_REVERSION"
  | "PURE_FUNDING_SPREAD";

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

export type OpportunitySort = "symbol" | "buy_exchange" | "sell_exchange"
  | "open_spread_pct" | "close_spread_pct" | "fee_adjusted_open_pct"
  | "buy_volume_24h_usdt" | "sell_volume_24h_usdt"
  | "net_funding_hourly_pct" | "net_funding_next_hourly_pct";

export interface OpportunityFilters {
  sort_by?: OpportunitySort;
  sort_order?: "asc" | "desc";
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

export interface ExactMarketKey { exchange: string; market_type: MarketType; dex: string | null; raw_symbol: string; }
export interface MarketIdentityEvidence { source_type: "official_announcement" | "official_metadata" | "official_specification"; url: string; summary: string; fetched_at: string; }
export interface MarketBinding {
  tracking_definition?: string;
  market: ExactMarketKey; asset_id: string | null; display_name: string; asset_kind: string;
  identity_status: "pending" | "verified" | "conflict" | "expired"; unit_status: "pending" | "verified";
  quote_currency: string; settlement_currency: string | null; price_multiplier: number; contract_size: number | null;
  contract_kind: string; revision: number; verified_at: string | null; valid_until: string | null; evidence: MarketIdentityEvidence[];
}
export interface MarketValidationSettings {
  mode: "shadow" | "enforce"; max_leg_time_skew_seconds: number; review_ttl_seconds: number; review_concurrency: number;
  review_timeout_seconds: number; review_failure_cooldown_seconds: number;
}
export interface MarketIdentityOverview {
  items: MarketBinding[]; total: number; settings: MarketValidationSettings;
  coverage: { mode: string; markets: number; verified: number; pending: number; conflict: number; expired: number; reference_quotes: number; };
}
export interface MarketIdentityMutation { binding: MarketBinding; expected_revision: number; reason: string; }

export type OptionalFeature = "gate_twap";

export interface FeaturesResponse {
  disabled: OptionalFeature[];
}
