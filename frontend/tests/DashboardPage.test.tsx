import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listOpportunities } from "../src/api/client";
import { DashboardPage } from "../src/pages/DashboardPage";
import type { Opportunity } from "../src/api/types";

const baseOpportunity: Opportunity = {
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
  risk_labels: [],
  last_seen_at: "2026-05-15T02:00:00Z"
};

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: ["AIUSDT"],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );
  });

  it("hides non-actionable risk rows by default", async () => {
    render(<DashboardPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("include_risky=false"),
        expect.anything()
      );
    });
  });

  it("sends adjustable hidden risk labels and risk-settings min volume in K", async () => {
    render(<DashboardPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("hidden_risk_labels=LOW_VOLUME%2CSTALE_DATA%2CHUGE_SPREAD_VERIFY"),
        expect.anything()
      );
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("min_volume_24h_k=100"),
        expect.anything()
      );
    });
  });

  it("sends excluded opportunity types to the opportunities query", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(String(input));
        return Response.json([]);
      })
    );

    await listOpportunities({ exclude_types: ["SF", "SS"] });

    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain("exclude_types=SF%2CSS");
  });

  it("waits for risk settings before requesting opportunities", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await waitFor(() => {
      expect(calls.some((url) => url.includes("/opportunities"))).toBe(true);
    });
    expect(calls.find((url) => url.includes("/opportunities"))).toContain("min_volume_24h_k=100");
  });

  it("adds a row symbol to the global blacklist from the table action", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/risk") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: ["OLDUSDT"],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 1,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "屏蔽 BTCUSDT" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"excluded_symbols":["OLDUSDT","BTCUSDT"]')
        })
      );
    });
  });

  it("removes a symbol from the global blacklist from the dashboard", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/risk") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: ["BADUSDT"],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "取消屏蔽 BADUSDT" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"excluded_symbols":[]')
        })
      );
    });
  });
});
