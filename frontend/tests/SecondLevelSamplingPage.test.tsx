import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SecondLevelSamplingPage } from "../src/pages/SecondLevelSamplingPage";

const config = {
  enabled: false,
  interval_seconds: 1,
  retention_hours: 48,
  exchanges: ["bybit", "bitget"],
  symbols: ["DEXEUSDT"],
  max_concurrent_requests: 8,
  capture_index_components: true,
  component_signal_window_seconds: 10
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
  component_sample_count: 1,
  latest_observed_at: "2026-07-23T08:00:00Z",
  latest_error: null,
  latest_samples: [sample],
  latest_spreads: [
    {
      symbol: "DEXEUSDT",
      left_exchange: "bitget",
      right_exchange: "bybit",
      observed_at: "2026-07-23T08:00:00Z",
      left_spot_mid: 9.95,
      right_spot_mid: 10.05,
      left_future_mid: 10,
      right_future_mid: 10.1,
      spot_spread_pct: -0.995,
      future_spread_pct: -0.9901,
      future_spot_spread_gap_pct: 0.0049,
      left_future_spot_basis_pct: 0.5025,
      right_future_spot_basis_pct: 0.4975,
      future_spot_basis_gap_pct: 0.005,
      left_mark_premium_pct: 0.8,
      right_mark_premium_pct: 1.1,
      premium_gap_pct: -0.3
    }
  ],
  latest_component_samples: [
    {
      id: 10,
      observed_at: "2026-07-23T08:00:00Z",
      target_exchange: "bybit",
      symbol: "DEXEUSDT",
      component_source: "binance",
      component_symbol: "DEXEUSDT",
      weight_pct: 81.75,
      component_price: 3.47,
      contribution_price: 2.83725,
      official_index_price: 3.55,
      reconstructed_index_price: 3.54,
      mark_price: 3.33,
      future_mid: 3.25,
      mark_premium_pct: -6.08,
      funding_rate_pct: 0.005,
      latency_ms: 50,
      error: null
    }
  ],
  latest_component_signals: [
    {
      observed_at: "2026-07-23T08:00:00Z",
      target_exchange: "bybit",
      symbol: "DEXEUSDT",
      component_source: "binance",
      component_symbol: "DEXEUSDT",
      window_seconds: 10,
      weight_pct: 81.75,
      component_price: 3.47,
      component_price_change_pct: 1.2,
      estimated_index_impact_pct: 0.98,
      official_index_change_pct: 0.5,
      mark_premium_change_pct: -0.4,
      lag_vs_official_index_pct: 0.48,
      signal_level: "high",
      reason: "binance 成分源价格变化 +1.2000%；权重 81.75%；预计推动指数 +0.9800%"
    }
  ]
};

describe("SecondLevelSamplingPage", () => {
  const requests: string[] = [];
  let currentStatus = status;

  beforeEach(() => {
    requests.length = 0;
    currentStatus = status;
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
          return Response.json(currentStatus);
        }
        if (url.pathname.includes("/second-level-sampling/component-samples")) {
          return Response.json(currentStatus.latest_component_samples);
        }
        if (url.pathname.includes("/second-level-sampling/samples")) {
          return Response.json([sample]);
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads status, samples, and saves config", async () => {
    render(<SecondLevelSamplingPage />);

    expect(await screen.findByText("1s 采样")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getAllByText("DEXEUSDT").length).toBeGreaterThan(0);
    });
    expect(await screen.findByText("bitget / bybit")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getAllByText("现货价差").length).toBeGreaterThan(0);
      expect(screen.getAllByText("合约-现货").length).toBeGreaterThan(0);
      expect(screen.getAllByText("左基差").length).toBeGreaterThan(0);
    });
    expect(await screen.findByText("指数组成痕迹")).toBeTruthy();
    expect(await screen.findByText("强痕迹")).toBeTruthy();
    expect(await screen.findByText("Bybit DEXE 指数组成")).toBeTruthy();
    expect(await screen.findByText("加权价格")).toBeTruthy();
    expect(await screen.findByText("接口正常")).toBeTruthy();
    expect(screen.getAllByText("binance").length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.startsWith("PUT"))).toBe(true);
    });
  });

  it("does not keep polling while the sampler is paused", async () => {
    render(<SecondLevelSamplingPage />);

    expect(await screen.findByText("1s 采样")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getAllByText("DEXEUSDT").length).toBeGreaterThan(0);
    });

    await new Promise((resolve) => window.setTimeout(resolve, 100));
    requests.length = 0;
    await new Promise((resolve) => window.setTimeout(resolve, 3500));

    expect(requests).toHaveLength(0);
  });

  it("does not reload component details on every automatic poll when composition is unchanged", async () => {
    currentStatus = { ...status, running: true };
    render(<SecondLevelSamplingPage />);

    expect(await screen.findByText("1s 采样")).toBeTruthy();
    await waitFor(() => {
      expect(requests.filter((request) => request.includes("/second-level-sampling/component-samples")).length)
        .toBeGreaterThan(0);
    });

    requests.length = 0;
    currentStatus = {
      ...currentStatus,
      latest_component_signals: [
        {
          ...status.latest_component_signals[0],
          observed_at: "2026-07-23T08:00:05Z",
          component_price_change_pct: 2
        }
      ]
    };
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    });

    expect(requests.some((request) => request.includes("/second-level-sampling/status"))).toBe(true);
    expect(requests.some((request) => request.includes("/second-level-sampling/samples"))).toBe(true);
    expect(requests.some((request) => request.includes("/second-level-sampling/component-samples"))).toBe(false);
    expect(screen.queryByText("07-23 16:00:05")).toBeNull();

    currentStatus = {
      ...currentStatus,
      latest_component_samples: [
        {
          ...status.latest_component_samples[0],
          id: 11,
          observed_at: "2026-07-23T08:00:02Z",
          component_source: "gateio",
          component_symbol: "DEXE_USDT",
          weight_pct: 16.1
        }
      ],
      latest_component_signals: [
        {
          ...status.latest_component_signals[0],
          observed_at: "2026-07-23T08:00:10Z",
          component_source: "gateio",
          component_symbol: "DEXE_USDT",
          weight_pct: 16.1,
          signal_level: "watch"
        }
      ]
    };
    requests.length = 0;
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    });

    expect(requests.some((request) => request.includes("/second-level-sampling/component-samples"))).toBe(true);
    await waitFor(() => {
      expect(screen.getAllByText("07-23 16:00:10").length).toBeGreaterThan(0);
    });
  });
});
