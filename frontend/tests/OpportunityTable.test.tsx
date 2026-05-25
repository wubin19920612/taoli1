import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OpportunityTable } from "../src/components/OpportunityTable";
import type { Opportunity } from "../src/api/types";

const row: Opportunity = {
  id: "opp-1",
  type: "FF",
  symbol: "BTCUSDT",
  buy_exchange: "binance",
  buy_market_type: "future",
  sell_exchange: "okx",
  sell_market_type: "future",
  open_spread_pct: 0.78,
  close_spread_pct: 0.42,
  fee_adjusted_open_pct: 0.62,
  spread_width_pct: 0.36,
  buy_bid: 99900,
  buy_ask: 100000,
  sell_bid: 100780,
  sell_ask: 100850,
  buy_volume_24h_usdt: 10000000,
  sell_volume_24h_usdt: 12000000,
  funding_rate_buy_pct: 0.01,
  funding_rate_sell_pct: -0.02,
  funding_next_rate_buy_pct: 0.015,
  funding_next_rate_sell_pct: 0.025,
  funding_next_time_buy: "2026-05-15T08:00:00Z",
  funding_next_time_sell: "2026-05-15T08:00:00Z",
  net_funding_pct: -0.03,
  net_funding_next_pct: 0.01,
  buy_funding_interval_hours: 8,
  sell_funding_interval_hours: 8,
  net_funding_hourly_pct: -0.00375,
  net_funding_daily_pct: -0.09,
  net_funding_next_hourly_pct: 0.00125,
  net_funding_next_daily_pct: 0.03,
  mark_index_diff_buy_pct: 0.01,
  mark_index_diff_sell_pct: 0.02,
  risk_labels: ["FUNDING_AGAINST"],
  last_seen_at: "2026-05-15T02:00:00Z"
};

describe("OpportunityTable", () => {
  it("renders spread legs, funding previews and risk labels", () => {
    render(<OpportunityTable opportunities={[row]} loading={false} />);

    expect(screen.getByText("BTCUSDT")).toBeTruthy();
    expect(screen.getByText("bn")).toBeTruthy();
    expect(screen.getByText("ok")).toBeTruthy();
    expect(screen.getByTitle("binance future")).toBeTruthy();
    expect(screen.getByTitle("okx future")).toBeTruthy();
    expect(screen.queryByText("binance future")).toBeNull();
    expect(screen.queryByText("okx future")).toBeNull();
    expect(screen.getByText("0.620%")).toBeTruthy();
    expect(screen.getByText("当前")).toBeTruthy();
    expect(screen.getByText("预测")).toBeTruthy();
    expect(screen.getByText("周期净")).toBeTruthy();
    expect(screen.getByText("0.015% / 0.025%")).toBeTruthy();
    expect(screen.getByText("0.010%")).toBeTruthy();
  });

  it("does not use mark-index premium as the next-cycle funding edge", () => {
    const missingNextFunding: Opportunity = {
      ...row,
      id: "opp-mark-index",
      funding_next_rate_buy_pct: null,
      funding_next_rate_sell_pct: 0.12,
      net_funding_next_pct: null,
      mark_index_diff_buy_pct: 9.99,
      mark_index_diff_sell_pct: 0.01,
      funding_rate_buy_pct: 0.01,
      funding_rate_sell_pct: 0.02
    };

    render(<OpportunityTable opportunities={[missingNextFunding]} loading={false} />);

    expect(screen.getByText("0.110%")).toBeTruthy();
    expect(screen.queryByText("-9.870%")).toBeNull();
  });
});
