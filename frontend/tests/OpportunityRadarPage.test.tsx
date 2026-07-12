import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OpportunityRadarPage } from "../src/pages/OpportunityRadarPage";

const settings = {
  enabled: true,
  anchor_exchange: "bybit",
  peer_exchanges: ["binance", "okx", "gate", "bitget", "aster", "hyperliquid"],
  premium_direction: "both",
  min_abs_premium_pct: 1.5,
  min_relative_premium_gap_pct: 0.5,
  max_abs_entry_spread_pct: 0.5,
  require_funding_alignment: false,
  min_hourly_funding_edge_pct: 0,
  min_volume_24h_usdt: 1_000_000,
  notional_per_symbol_usdt: 100,
  min_depth_multiple: 5,
  max_data_age_seconds: 120,
  max_candidates: 50
};

const candidate = {
  id: "btc-bybit-binance",
  symbol: "BTCUSDT",
  signal_level: "HIGH",
  score: 86,
  direction: "LONG_ANCHOR_SHORT_PEER",
  long_exchange: "bybit",
  short_exchange: "binance",
  anchor_exchange: "bybit",
  peer_exchange: "binance",
  anchor_premium_pct: -2,
  peer_premium_pct: 0,
  peer_median_premium_pct: 0,
  relative_premium_gap_pct: 2,
  entry_spread_pct: 0.2,
  long_entry_price: 100,
  short_entry_price: 100.2,
  long_funding_pct: -0.1,
  short_funding_pct: 0.04,
  long_funding_interval_hours: 1,
  short_funding_interval_hours: 4,
  hourly_funding_edge_pct: 0.11,
  volume_24h_usdt: 5_000_000,
  depth_usdt: 20_000,
  data_age_seconds: 12,
  reasons: [],
  risk_labels: []
};

describe("OpportunityRadarPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/opportunity-radar/settings")) {
          if (init?.method === "PUT") {
            return Response.json(JSON.parse(String(init.body)));
          }
          return Response.json(settings);
        }
        if (url.includes("/opportunity-radar/preview")) {
          return Response.json({
            observed_at: "2026-07-12T08:00:00Z",
            settings,
            anchor_markets: 1,
            total_pairs_evaluated: 1,
            displayed_candidates: 1,
            high_count: 1,
            medium_count: 0,
            watch_count: 0,
            candidates: [candidate]
          });
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders an extreme-premium candidate and saves adjustable thresholds", async () => {
    const user = userEvent.setup();
    render(<OpportunityRadarPage />);

    expect(await screen.findByText("BTCUSDT")).toBeTruthy();
    expect(await screen.findByText("bybit -2.000%")).toBeTruthy();
    expect(screen.getByText("+0.200%")).toBeTruthy();

    const premiumInput = screen.getByLabelText("最低绝对溢价");
    const spreadInput = screen.getByLabelText("最大试错价差");
    await user.clear(premiumInput);
    await user.type(premiumInput, "2");
    await user.clear(spreadInput);
    await user.type(spreadInput, "0.3");
    await user.click(screen.getByRole("button", { name: /保存策略参数/ }));

    await waitFor(() => {
      const putCall = vi
        .mocked(fetch)
        .mock.calls.find(
          (call) => String(call[0]).includes("/opportunity-radar/settings") && call[1]?.method === "PUT"
        );
      expect(putCall).toBeTruthy();
      expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({
        anchor_exchange: "bybit",
        peer_exchanges: ["binance", "okx", "gate", "bitget", "aster", "hyperliquid"],
        min_abs_premium_pct: 2,
        max_abs_entry_spread_pct: 0.3
      });
    });
  });
});
