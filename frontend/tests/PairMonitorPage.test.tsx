import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PairMonitorPage } from "../src/pages/PairMonitorPage";

const observedAt = "2026-07-24T02:00:00Z";

function pairSpreadResult(leg1MarketType: "spot" | "future" = "future") {
  return {
    leg1: {
      exchange: "bitget",
      symbol: "SKHYUSDT",
      market_type: leg1MarketType
    },
    leg2: {
      exchange: "bitget",
      symbol: "SKHYNIXUSDT",
      market_type: "future" as const
    },
    hours: 720,
    interval_minutes: 5,
    leg2_multiplier: 10,
    observed_at: observedAt,
    point_count: 1,
    first_seen_at: observedAt,
    last_seen_at: observedAt,
    spread_abs: { min: 1, max: 1, mean: 1, current: 1 },
    spread_pct: { min: 0.5, max: 0.5, mean: 0.5, current: 0.5 },
    current: {
      observed_at: observedAt,
      leg1: {
        exchange: "bitget",
        symbol: "SKHYUSDT",
        market_type: leg1MarketType,
        raw_symbol: "SKHYUSDT",
        price: 100,
        price_field: "mid_price" as const,
        mark_price: null,
        index_price: null,
        mid_price: 100,
        last_price: 100,
        funding_rate_pct: leg1MarketType === "spot" ? null : 0.01,
        funding_next_rate_pct: null,
        funding_next_time: null,
        funding_interval_hours: null,
        funding_rate_upper_pct: null,
        funding_rate_lower_pct: null,
        timestamp: observedAt
      },
      leg2: {
        exchange: "bitget",
        symbol: "SKHYNIXUSDT",
        market_type: "future" as const,
        raw_symbol: "SKHYNIXUSDT",
        price: 101,
        price_field: "mark_price" as const,
        mark_price: 101,
        index_price: 100,
        mid_price: 101,
        last_price: 101,
        funding_rate_pct: 0.01,
        funding_next_rate_pct: null,
        funding_next_time: null,
        funding_interval_hours: 8,
        funding_rate_upper_pct: null,
        funding_rate_lower_pct: null,
        timestamp: observedAt
      },
      spread_abs: 1,
      spread_pct: 0.5
    },
    points: [
      {
        bucket_at: observedAt,
        leg1_close: 100,
        leg2_close: 101,
        spread_abs: 1,
        spread_pct: 0.5
      }
    ],
    funding_history: [],
    warnings: []
  };
}

describe("PairMonitorPage", () => {
  const requests: string[] = [];

  beforeEach(() => {
    requests.length = 0;
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const urlText = String(input);
        requests.push(urlText);
        const url = new URL(urlText, "http://localhost");
        if (url.pathname.includes("/pair-spread/query")) {
          return Response.json(pairSpreadResult(url.searchParams.get("leg1_market_type") === "spot" ? "spot" : "future"));
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("sends the selected spot market type with the pair spread query", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[1]);
    const spotOptions = await screen.findAllByText("现货");
    await user.click(spotOptions[spotOptions.length - 1]);
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("leg1_market_type=spot") &&
            request.includes("leg2_market_type=future")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("bitget · 现货 · SKHYUSDT")).length).toBeGreaterThan(0);
  });

  it("loads old saved presets as future contracts", () => {
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([
        {
          id: "legacy",
          leg1_exchange: "bybit",
          leg1_symbol: "DEXE",
          leg2_exchange: "bitget",
          leg2_symbol: "DEXE",
          leg2_multiplier: 1,
          hours: 24,
          intervalMinutes: 5,
          savedAt: observedAt
        }
      ])
    );

    render(<PairMonitorPage />);

    expect(screen.getByText("bybit 合约 DEXE / bitget 合约 DEXE")).toBeTruthy();
  });
});
