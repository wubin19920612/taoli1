import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type {
  SqueezePaperReport, SqueezePaperStatus, SqueezePaperTrade
} from "../src/api/types";
import { SqueezePaperView } from "../src/pages/SqueezePaperView";

afterEach(cleanup);

const status: SqueezePaperStatus = {
  enabled: true,
  source_capability: "research_only_public_rest_paper",
  rule_version: "squeeze-paper-s3-v1",
  started_at: "2026-09-26T00:00:00Z",
  continuous_started_at: "2026-09-26T00:00:00Z",
  coverage_gap_count: 0,
  coverage_gap_open: false,
  last_processed_at: "2026-09-26T00:00:10Z",
  last_success_at: "2026-09-26T00:00:10Z",
  last_error: null,
  trade_counts: { unresolved: 1 },
  unresolved_exposure_count: 1,
  accounts: []
};

const trade: SqueezePaperTrade = {
  id: "event-1:confirmed",
  event_id: "event-1:confirmed",
  route_id: "bybit-future-LSKUSDT__binance-future-LSKUSDT",
  asset_id: "lisk:LSK",
  status: "unresolved",
  signal_at: "2026-09-26T00:00:00Z",
  opened_at: "2026-09-26T00:00:01Z",
  closed_at: null,
  expensive_key: "bybit|future|LSKUSDT|",
  cheap_key: "binance|future|LSKUSDT|",
  target_quantity: "100",
  expensive_open_quantity: "50",
  cheap_open_quantity: "0",
  price_pnl: "0",
  entry_fees: "0.06",
  exit_fees: "0",
  funding_total: "0",
  borrow_total: "0",
  max_adverse_net: "-1",
  minimum_expensive_free_balance: "9900",
  minimum_cheap_free_balance: "10000",
  funding_gap_at: null,
  exit_reason: "entry_partial_or_leg_failure",
  last_error: "paper_naked_leg_recovery_timeout",
  risk_labels: ["collateral_model_incomplete"],
  fills: [],
  cashflows: []
};

const report: SqueezePaperReport = {
  rule_version: "squeeze-paper-s3-v1",
  frozen_parameters: { entry_notional: "100", delay_ms: 500 },
  started_at: "2026-09-26T00:00:00Z",
  continuous_started_at: "2026-09-26T00:00:00Z",
  coverage_gap_count: 0,
  coverage_gap_open: false,
  as_of: "2026-09-26T01:00:00Z",
  elapsed_days: 0.042,
  independent_events: 1,
  total_independent_events: 1,
  minimum_days: 14,
  minimum_independent_events: 30,
  sample_status: "sample_insufficient",
  closed_trades: 0,
  closed_with_funding_gap: 0,
  unresolved_events: 1,
  unfinished_events: 1,
  funding_gap_trades: 0,
  single_leg_failure_rate: 1,
  closed_net_pnl: "0",
  net_per_closed_trade: null,
  closed_trade_capital_return: "0",
  max_drawdown_closed_trade_only: "0",
  average_holding_seconds: null,
  maximum_adverse_spread_expansion: "0.03",
  minimum_free_balance_by_leg: { "bybit|future|LSKUSDT|": "9900" },
  funding_cashflow_actual_mark: "0",
  funding_cashflow_proxy_mark: "0",
  borrow_cost: "0",
  capacity_by_target_notional: {},
  by_route: {},
  collateral_model_incomplete: true,
  drawdown_excludes_open_positions: true
};

describe("SqueezePaperView", () => {
  it("keeps insufficient samples and unresolved exposure visible", () => {
    render(<SqueezePaperView status={status} positions={[trade]} trades={[]}
      report={report} error={null} loading={false} />);
    expect(screen.getByText("样本不足")).toBeTruthy();
    expect(screen.getByText(/1 笔模拟敞口未决/)).toBeTruthy();
    expect(screen.getByText("未决敞口", { selector: ".ant-tag" })).toBeTruthy();
    expect(screen.getAllByText("bybit / future / LSKUSDT /", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText(/维持保证金模型不完整/)).toBeTruthy();
  });
});
