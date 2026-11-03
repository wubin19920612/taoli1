export type ShadowDimension = "variant" | "pair" | "spread_tier" | "liquidity" | "routes" | "fake_flag" | "executable" | "strategy_version";

export interface ShadowStatus {
  enabled: boolean;
  ready: boolean;
  open_count: number;
  dropped: number;
  errors: number;
  pending_writes: number;
  catalog_updated_at: string | null;
  catalog_entries: number;
  catalog_errors: number;
}

export interface ShadowBucket {
  dimensions: Partial<Record<ShadowDimension, string | boolean>>;
  status: "INSUFFICIENT" | "POSITIVE" | "NOT_POSITIVE";
  n_evt: number;
  days: number;
  coins: number;
  win_rate: number | null;
  wilson_low: number | null;
  wilson_high: number | null;
  E: number | null;
  lcb: number | null;
  sigma: number | null;
  spread_bps: number | null;
  slippage_bps: number | null;
  fees_bps: number | null;
  funding_bps: number | null;
  n_min: number | null;
  cvar5: number | null;
}

export interface ShadowTrade {
  id: string;
  symbol: string;
  variant: string;
  opened_at: string;
  closed_at: string | null;
  pair: string;
  routes: string;
  executable: boolean;
  fake_flag: boolean;
  fake_score: number | null;
  fake_reasons: string[];
  exit_reason: string | null;
  net_bps: number | null;
  open_spread_pct: number;
  close_spread_pct: number | null;
  holding_seconds: number | null;
  buy_market_key: { exchange: string; raw_symbol: string; dex: string | null };
  sell_market_key: { exchange: string; raw_symbol: string; dex: string | null };
}
