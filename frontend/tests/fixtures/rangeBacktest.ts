import type { RangeBacktestRange, RangeBacktestResult } from "../../src/api/rangeBacktest";
import type { PairSpreadLegQuery } from "../../src/api/types";

export const backtestLeg1: PairSpreadLegQuery = { exchange: "hyperliquid", dex: "xyz", symbol: "OURAUSDT", market_type: "future" };
export const backtestLeg2: PairSpreadLegQuery = { exchange: "binance", dex: null, symbol: "OURAUSDT", market_type: "future" };
export const backtestRange: RangeBacktestRange = { start_at: "2026-11-01T00:00:00.000Z", end_at: "2026-11-01T01:00:00.000Z" };

export function rangeBacktestFixture(
  leg1 = backtestLeg1, leg2 = backtestLeg2, range = backtestRange
): RangeBacktestResult {
  const endpoint = (market: PairSpreadLegQuery, price: number, timestamp: string) => ({
    market, price, timestamp, target_at: timestamp, deviation_seconds: 0
  });
  return {
    price_pnl: { leg1: 100, leg2: -40, total: 60 },
    entry: { leg1: endpoint(leg1, 100, range.start_at), leg2: endpoint(leg2, 100, range.start_at) },
    exit: { leg1: endpoint(leg1, 110, range.end_at), leg2: endpoint(leg2, 104, range.end_at) },
    funding_pnl: {
      settlements: [
        { leg: "leg1", exchange: leg1.exchange, symbol: leg1.symbol, dex: leg1.dex ?? null, funding_time: range.end_at, rate_pct: 0.02, pnl: -0.2 },
        { leg: "leg2", exchange: leg2.exchange, symbol: leg2.symbol, dex: leg2.dex ?? null, funding_time: range.end_at, rate_pct: 0.01, pnl: 0.1 }
      ],
      total: -0.1,
      coverage: { leg1: 1, leg2: 1 }
    },
    fees: 2,
    spread_cost_est: 1,
    total: 56.9,
    total_pct_of_notional: 5.69,
    data_quality: {
      source: { leg1: "kline_close", leg2: "kline_close" },
      granularity_seconds: { leg1: 60, leg2: 60 },
      gaps: [],
      is_estimate: true
    },
    warnings: []
  };
}
