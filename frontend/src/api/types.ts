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
  buy_volume_24h_usdt: number | null;
  sell_volume_24h_usdt: number | null;
  funding_rate_buy_pct: number | null;
  funding_rate_sell_pct: number | null;
  net_funding_pct: number | null;
  mark_index_diff_buy_pct: number | null;
  mark_index_diff_sell_pct: number | null;
  risk_labels: string[];
  last_seen_at: string;
}

export interface HealthStatus {
  status: string;
  markets: number;
  opportunities: number;
  exchange_errors: Record<string, string>;
}

export interface RiskSettings {
  min_volume_24h_usdt: number;
  stale_after_seconds: number;
  huge_spread_pct: number;
  wide_spread_pct: number;
  mark_index_deviation_pct: number;
  funding_against_pct: number;
  ticker_collision_symbols: string[];
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

export interface OpportunityFilters {
  type?: OpportunityType;
  symbol?: string;
  exchange?: string;
  min_open_spread_pct?: number;
  include_risky?: boolean;
  hidden_risk_labels?: string[];
  min_volume_24h_k?: number;
}
