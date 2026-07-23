import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SecondLevelSamplingPage } from "../src/pages/SecondLevelSamplingPage";

const config = {
  enabled: false,
  interval_seconds: 1,
  retention_hours: 48,
  exchanges: ["bybit", "bitget"],
  symbols: ["DEXEUSDT"],
  max_concurrent_requests: 8
};

const sample = {
  id: 1,
  observed_at: "2026-07-23T08:00:00Z",
  exchange: "bybit",
  symbol: "DEXEUSDT",
  status: "ok",
  spot_mid: 10,
  future_mid: 10.1,
  mark_price: 10.11,
  index_price: 10,
  mark_premium_pct: 1.1,
  mid_premium_pct: 1,
  funding_rate_pct: 0.005,
  latency_ms: 120,
  error: null
};

const status = {
  running: false,
  config,
  sample_count: 2,
  latest_observed_at: "2026-07-23T08:00:00Z",
  latest_error: null,
  latest_samples: [sample],
  latest_spreads: [
    {
      symbol: "DEXEUSDT",
      left_exchange: "bitget",
      right_exchange: "bybit",
      observed_at: "2026-07-23T08:00:00Z",
      left_future_mid: 10,
      right_future_mid: 10.1,
      future_spread_pct: -0.9901,
      left_mark_premium_pct: 0.8,
      right_mark_premium_pct: 1.1,
      premium_gap_pct: -0.3
    }
  ]
};

describe("SecondLevelSamplingPage", () => {
  const requests: string[] = [];

  beforeEach(() => {
    requests.length = 0;
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlText = String(input);
        requests.push(`${init?.method ?? "GET"} ${urlText}`);
        const url = new URL(urlText, "http://localhost");
        if (url.pathname.includes("/second-level-sampling/exchanges")) {
          return Response.json(["bybit", "bitget", "binance"]);
        }
        if (url.pathname.includes("/second-level-sampling/config")) {
          if (init?.method === "PUT") {
            return Response.json(JSON.parse(String(init.body)));
          }
          return Response.json(config);
        }
        if (url.pathname.includes("/second-level-sampling/status")) {
          return Response.json(status);
        }
        if (url.pathname.includes("/second-level-sampling/samples")) {
          return Response.json([sample]);
        }
        return Response.json({});
      })
    );
  });

  it("loads status, samples, and saves config", async () => {
    render(<SecondLevelSamplingPage />);

    expect(await screen.findByText("1s 采样")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getAllByText("DEXEUSDT").length).toBeGreaterThan(0);
    });
    expect(await screen.findByText("bitget / bybit")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.startsWith("PUT"))).toBe(true);
    });
  });
});
