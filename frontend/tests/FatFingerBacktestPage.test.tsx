import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FatFingerBacktestPage } from "../src/pages/FatFingerBacktestPage";

const result = {
  request: {
    symbol: "DEXEUSDT",
    market_mode: "SF",
    hours: 6,
    sample_limit: 150000,
    entry_spread_pct: 1,
    ladder_levels: 3,
    ladder_step_pct: 0.5,
    order_notional_usdt: 100,
    maker_fill_assumption_pct: 25,
    maker_fee_pct: 0.02,
    taker_fee_pct: 0.06,
    taker_slippage_pct: 0.05,
    hedge_delay_seconds: 1,
    order_expiry_seconds: 30,
    take_profit_pct: 0.15,
    max_hold_seconds: 120,
    min_hedge_depth_usdt: 100,
    max_quote_age_seconds: 2,
    require_known_hedge_depth: true,
    cooldown_seconds: 10
  },
  start_at: "2026-08-13T00:00:00Z",
  end_at: "2026-08-13T01:00:00Z",
  raw_sample_count: 1000,
  samples_truncated: false,
  frame_count: 1000,
  exchange_count: 2,
  order_placed_count: 12,
  order_expired_count: 4,
  order_skipped_depth_count: 2,
  exit_skipped_depth_count: 1,
  quote_touch_count: 3,
  hedge_completed_count: 2,
  unhedged_touch_count: 1,
  open_position_count: 0,
  closed_trade_count: 1,
  target_exit_count: 1,
  timeout_exit_count: 0,
  win_count: 1,
  loss_count: 0,
  win_rate_pct: 100,
  hedge_success_rate_pct: 66.67,
  total_notional_usdt: 25,
  total_net_pnl_usdt: 12.34,
  average_net_pnl_pct: 0.5,
  median_net_pnl_pct: 0.5,
  worst_net_pnl_pct: 0.5,
  average_hold_seconds: 4,
  average_hedge_delay_seconds: 1,
  route_summaries: [
    {
      maker_exchange: "gate",
      maker_market_type: "spot",
      hedge_exchange: "binance",
      hedge_market_type: "future",
      maker_side: "buy",
      touch_count: 2,
      hedge_count: 1,
      unhedged_count: 1,
      closed_trade_count: 1,
      win_count: 1,
      total_notional_usdt: 25,
      total_net_pnl_usdt: 12.34,
      average_net_pnl_pct: 0.5,
      median_net_pnl_pct: 0.5,
      worst_net_pnl_pct: 0.5,
      average_hold_seconds: 4
    }
  ],
  trades: [
    {
      id: "trade-1",
      symbol: "DEXEUSDT",
      market_mode: "SF",
      maker_exchange: "gate",
      maker_market_type: "spot",
      hedge_exchange: "binance",
      hedge_market_type: "future",
      maker_side: "buy",
      tier: 1,
      entry_target_spread_pct: 1,
      order_placed_at: "2026-08-13T00:00:00Z",
      maker_filled_at: "2026-08-13T00:00:01Z",
      hedge_filled_at: "2026-08-13T00:00:02Z",
      closed_at: "2026-08-13T00:00:06Z",
      exit_reason: "target",
      maker_entry_price: 1,
      hedge_entry_price: 1.01,
      maker_exit_price: 1.02,
      hedge_exit_price: 1,
      notional_usdt: 25,
      hedge_depth_usdt: 1000,
      entry_hedge_edge_pct: 1,
      gross_pnl_usdt: 12.5,
      net_pnl_usdt: 12.34,
      net_pnl_pct: 0.5,
      max_favorable_pnl_pct: 0.6,
      max_adverse_pnl_pct: -0.1,
      hedge_delay_seconds: 1,
      hold_seconds: 4
    }
  ],
  warnings: []
};

describe("FatFingerBacktestPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(result))
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("submits the default symbol and renders route and paper-trade results", async () => {
    render(<FatFingerBacktestPage />);

    expect(screen.getByDisplayValue("DEXEUSDT")).toBeTruthy();
    await userEvent.click(screen.getAllByRole("button", { name: /开始回测/ })[0]);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/second-level-sampling/fat-finger-backtest"),
        expect.objectContaining({ method: "POST" })
      );
    });
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({
      symbol: "DEXEUSDT",
      market_mode: "SF"
    });

    expect((await screen.findAllByText(/Gate/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText("+12.34 USDT").length).toBeGreaterThan(0);
    expect(screen.getByText("25.00 USDT")).toBeTruthy();
  });
});
