import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SqueezeRouteRow } from "../src/api/types";
import { SqueezeRoutesView } from "../src/pages/SqueezeRoutesView";

afterEach(cleanup);

const route: SqueezeRouteRow = {
  route_id: "bybit-future-LSKUSDT__binance-future-LSKUSDT",
  evaluated_at: "2026-09-25T17:00:00Z",
  state: {
    phase: "confirming", baseline: "0.01", peak_difference: "0.2",
    confirmation_count: 2
  },
  evaluation: {
    route_id: "bybit-future-LSKUSDT__binance-future-LSKUSDT",
    rule_version: "squeeze-route-s2-v1",
    calculated_at: "2026-09-25T17:00:00Z",
    expensive_key: "bybit|future|LSKUSDT|",
    cheap_key: "binance|future|LSKUSDT|",
    asset_id: "lisk:LSK",
    quote_asset: "USDT",
    quality: "research_only",
    blockers: [],
    expensive_source_at: "2026-09-25T16:59:59Z",
    cheap_source_at: "2026-09-25T16:59:59Z",
    expensive_received_at: "2026-09-25T17:00:00Z",
    cheap_received_at: "2026-09-25T17:00:00Z",
    expensive_sequence: 12,
    cheap_sequence: 15,
    expensive_last_trade_at: "2026-09-25T16:59:58Z",
    cheap_last_trade_at: "2026-09-25T16:59:58Z",
    expensive_funding_rate: "-0.001",
    cheap_funding_rate: "-0.002",
    expensive_funding_kind: "bybit_current_public_estimate",
    cheap_funding_kind: "binance_last_public_rate_proxy",
    expensive_funding_source_at: "2026-09-25T16:59:59Z",
    cheap_funding_source_at: "2026-09-25T16:59:59Z",
    expensive_funding_interval_hours: 1,
    cheap_funding_interval_hours: 8,
    expensive_next_funding_at: "2026-09-25T18:00:00Z",
    cheap_next_funding_at: "2026-09-26T00:00:00Z",
    fee_assumption: "conservative_public_assumption_not_account_tier",
    expensive_recent_trade_notional: "100",
    cheap_recent_trade_notional: "90",
    expensive_turnover_24h: "1000000",
    cheap_turnover_24h: "900000",
    expensive_contract_base_qty: "1",
    cheap_contract_base_qty: "1",
    expensive_quantity_step: "0.1",
    cheap_quantity_step: "1",
    expensive_taker_fee_rate: "0.0006",
    cheap_taker_fee_rate: "0.0006",
    capacities: [{
      target_notional: "100",
      base_quantity: "100",
      expensive_open_sell: {
        base_quantity: "100", quote_notional: "120", unit_price: "1.2",
        best_unit_price: "1.2", impact_rate: "0"
      },
      expensive_close_buy: {
        base_quantity: "100", quote_notional: "120.1", unit_price: "1.201",
        best_unit_price: "1.201", impact_rate: "0"
      },
      cheap_open_buy: {
        base_quantity: "100", quote_notional: "100", unit_price: "1",
        best_unit_price: "1", impact_rate: "0"
      },
      cheap_close_sell: {
        base_quantity: "100", quote_notional: "99.9", unit_price: "0.999",
        best_unit_price: "0.999", impact_rate: "0"
      },
      open_difference: "0.2",
      close_difference_now: "0.202",
      target_residual: "0.01",
      entry_fee: "0.132",
      estimated_exit_fee: "0.132",
      estimated_funding: "0.08",
      estimated_borrow: "0",
      latency_buffer: "0.1",
      estimated_net: "18.716",
      blockers: ["net_space_below_minimum"]
    }]
  }
};

describe("SqueezeRoutesView", () => {
  it("shows exact legs, fixed quantity, four VWAPs and a research-only verdict", () => {
    render(<SqueezeRoutesView routes={[route]} events={[]} loading={false} />);
    expect(screen.getByText("空 bybit|future|LSKUSDT|")).toBeTruthy();
    expect(screen.getByText("多 binance|future|LSKUSDT|")).toBeTruthy();
    expect(screen.getByText("100.00 USDT / 100")).toBeTruthy();
    expect(screen.getByText("贵卖 1.200000")).toBeTruthy();
    expect(screen.getByText("便宜买 1.000000")).toBeTruthy();
    expect(screen.getByText("仅研究")).toBeTruthy();
    expect(screen.getByText("预计净空间不足")).toBeTruthy();
  });
});
