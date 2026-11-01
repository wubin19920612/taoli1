import type { PairSpreadLegQuery } from "./types";

export type RangeBacktestDirection = "long1_short2" | "short1_long2";

export interface RangeBacktestRange {
  start_at: string;
  end_at: string;
}

export interface RangeBacktestRequest extends RangeBacktestRange {
  leg1: PairSpreadLegQuery;
  leg2: PairSpreadLegQuery;
  direction: RangeBacktestDirection;
  notional_usd: number;
}

export interface RangeEndpoint {
  market: PairSpreadLegQuery;
  price: number;
  timestamp: string;
  target_at: string;
  deviation_seconds: number;
}

export interface RangeSettlement {
  leg: "leg1" | "leg2";
  exchange: string;
  symbol: string;
  dex: string | null;
  funding_time: string;
  rate_pct: number;
  pnl: number;
}

export interface RangeDataGap {
  leg: "leg1" | "leg2";
  kind: "entry" | "exit" | "funding";
  deviation_seconds?: number | null;
  expected?: number | null;
  actual?: number | null;
}

export interface RangeBacktestResult {
  price_pnl: { leg1: number; leg2: number; total: number };
  entry: { leg1: RangeEndpoint; leg2: RangeEndpoint };
  exit: { leg1: RangeEndpoint; leg2: RangeEndpoint };
  funding_pnl: {
    settlements: RangeSettlement[];
    total: number;
    coverage: { leg1: number | null; leg2: number | null };
  };
  fees: number;
  spread_cost_est: number | null;
  total: number;
  total_pct_of_notional: number;
  data_quality: {
    source: Record<string, string>;
    granularity_seconds: Record<string, number>;
    gaps: RangeDataGap[];
    is_estimate: true;
  };
  warnings: string[];
}
