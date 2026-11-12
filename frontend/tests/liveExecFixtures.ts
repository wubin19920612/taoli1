// Synthetic data matching docs/live-exec-contract-2026-11-11.md, including nulls.
export function trade(id = "t1", symbol = "JCTUSDT", closed_at = "2026-11-11T07:59:00Z") {
  return { id, symbol, pair: "binance|bitget", opened_at: "2026-11-11T07:58:30Z", closed_at, held_seconds: 30,
    legs: [{ exchange: "binance", side: "long", entry_vwap: 0.01, exit_vwap: 0.0101, qty: 1000 },
      { exchange: "bitget", side: "short", entry_vwap: 0.0102, exit_vwap: 0.0101, qty: 1000 }],
    entry_spread_bps: 40.1, fees_usd: 0.22, funding_usd: 0, net_usd: 0.15, net_bps: 15,
    baseline_bps: -7.25, excess_bps: 28.35, funding_cost_bps: 0,
    open_book_skew_ms: 0, close_book_skew_ms: 2300,
    exit_reason: "converged", sim: null, slippage_bps: -3.1, leg_send_gap_ms: 12,
    leg_fill_gap_ms: 230, one_leg_incident: true };
}
const group = { key: "JCTUSDT", n: 5, net_usd: 1, mean_bps: 12, win_rate: 0.8 };
export const liveFixtures = {
  summary: { generated_at: "2026-11-11T08:00:00Z", mode: "live", halted: true, halt_reason: "权益保护",
    stop_file: true, opening_paused_reason: "人工暂停",
    caps: { leg_max_usd: 150, total_max_usd: 600, total_open_usd: 200, equity_drop_max_usd: 150, max_pairs: 3, open_pairs: 1 },
    accounts: {
      crossex: { equity_usd: 612.7, baseline_usd: 752.7, drop_usd: 140, margin_multiple: null, ok: true, error: null },
      bitget: { equity_usd: 30001.2, baseline_usd: 30001.2, drop_usd: 0, margin_multiple: 5, ok: false, error: "账户错误示例" } },
    today: { net_usd: 1.2, fees_usd: 0.5, funding_usd: -0.1, trades: 4, wins: 3, daily_loss_limit_usd: 20 },
    total: { net_usd: 1.2, fees_usd: 0.5, funding_usd: -0.1, trades: 4, wins: 3, total_loss_limit_usd: 100 },
    fees: { crossex_taker: 0.00048, bitget_taker: 0.0006 },
    baseline_ready_pairs: 12, structural_spread_pairs: 2, cooldown_pairs: 1,
    opening_skip_counts: { book_skew: 2, book_stale: 1, funding_diff: 3,
      baseline_not_flat: 4, not_single_venue_outlier: 5, too_few_venues: 6 },
    last_error: "最近错误示例" },
  positions: [{ id: "p1", symbol: "JCTUSDT", opened_at: "2026-11-11T07:59:18Z", held_seconds: 42,
    legs: [{ exchange: "binance", route: "crossex", side: "long", qty: 1000, entry_price: 0.01, notional_usd: 100, mark_price: 0.0101 },
      { exchange: "bitget", route: "bitget", side: "short", qty: 1000, entry_price: 0.0102, notional_usd: 102, mark_price: 0.0101 }],
    entry_spread_bps: 40.1, current_spread_bps: 25, unrealized_usd: 0.12, unrealized_bps: 12,
    baseline_bps: 20, excess_bps: 20.1, funding_cost_bps: 1,
    open_book_skew_ms: 250, close_book_skew_ms: null,
    next_funding_at: null, stop_distance_bps: 35, max_hold_at: "2026-11-11T08:10:00Z", status: "one_leg" }],
  trades: [trade()],
  stats: { days: 7, n: 20, histogram: [{ lo: -50, hi: -25, count: 1 }, { lo: 0, hi: 25, count: 3 }],
    by_symbol: [group], by_pair: [{ ...group, key: "binance|bitget" }],
    by_exit_reason: [{ ...group, key: "pre_funding" }], by_spread_tier: [{ ...group, key: "0.5–1%" }],
    vs_sim: { n: 18, slippage_bps_p50: -2, slippage_bps_p90: -9, leg_fill_gap_ms_p50: 150,
      leg_fill_gap_ms_p90: 600, one_leg_rate: 0.05 } }
};
