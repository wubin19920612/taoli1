import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NegativeBasisMonitorPage } from "../src/pages/NegativeBasisMonitorPage";

const autoScanStrategy = {
  interval_seconds: 60,
  lookback_hours: 4,
  retention_hours: 720,
  watch_threshold_pct: 0.5,
  building_threshold_pct: 1,
  confirmed_threshold_pct: 2,
  strong_threshold_pct: 3,
  extreme_threshold_pct: 10,
  watch_consecutive_hits: 3,
  building_consecutive_hits: 3,
  confirmed_consecutive_hits: 3,
  strong_consecutive_hits: 2,
  extreme_consecutive_hits: 1,
  spot_volume_growth_threshold: 3,
  oi_confirmed_growth_pct: 20,
  oi_strong_growth_pct: 30,
  min_spot_hourly_volume_usdt: 0,
  alert_min_level: "watch",
  cooldown_seconds: 900
};

const autoCandidate = {
  id: "auto-prom-gate-binance",
  symbol: "PROMUSDT",
  spot_exchange: "gate",
  future_exchange: "binance",
  spot_symbol: "PROMUSDT",
  future_symbol: "PROMUSDT",
  future_multiplier: 1,
  signal_level: "strong" as const,
  selection_score: 38,
  selection_reasons: ["现货与合约流动性良好"],
  spot_premium_pct: 13.077,
  spot_price: 3.30235,
  future_price: 2.897,
  spot_volume_24h_usdt: 1_180_000,
  future_volume_24h_usdt: 192_890_000,
  observed_at: "2026-08-12T08:59:00Z"
};

describe("NegativeBasisMonitorPage", () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    scrollIntoView.mockReset();
    window.history.pushState({}, "", "/?page=negative-basis");
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView
    });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/negative-basis-monitor/exchanges")) {
          return Response.json({
            spot: ["binance"],
            future: ["gate"]
          });
        }
        if (url.includes("/negative-basis-monitor/status")) {
          return Response.json({
            running: true,
            auto_scan_enabled: true,
            auto_scan_settings: {
              enabled: true,
              strategy: autoScanStrategy,
              blocked_exchanges: [],
              blocked_symbols: [],
              blocked_exchange_symbols: [],
              updated_at: "2026-08-12T00:00:00Z"
            },
            auto_scan_last_at: "2026-08-12T00:00:00Z",
            auto_scan_error: null,
            auto_candidate_count: 1,
            auto_candidates: [autoCandidate],
            watch_count: 0,
            enabled_watch_count: 0,
            sample_count: 0,
            event_count: 0,
            latest_error: null,
            watchlist: [],
            latest_samples: [],
            latest_events: []
          });
        }
        return Response.json({});
      })
    );
  });

  it("shows the global auto-monitoring strategy before any manual route", async () => {
    render(<NegativeBasisMonitorPage />);

    expect(await screen.findByText("全市场自动监控策略")).toBeTruthy();
    expect(document.querySelector(".negative-basis-manual-config")).toBeNull();
    expect(screen.queryByLabelText("标的")).toBeNull();
  });

  it("opens and scrolls to the manual route form from the toolbar", async () => {
    render(<NegativeBasisMonitorPage />);

    await screen.findByText("自动发现候选");
    await userEvent.click(screen.getByRole("button", { name: /^plus 手动指定路线$/ }));

    expect(document.querySelector(".negative-basis-manual-config")).not.toBeNull();
    expect(screen.getByDisplayValue("PROMUSDT")).toBeTruthy();
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "smooth",
        block: "start"
      });
    });
  });

  it("saves the global strategy without requiring a symbol or exchanges", async () => {
    const fetchMock = vi.mocked(fetch);
    render(<NegativeBasisMonitorPage />);

    await screen.findByText("全市场自动监控策略");
    await userEvent.click(screen.getByRole("button", { name: /保存策略并重扫/ }));

    await waitFor(() => {
      const strategyRequest = fetchMock.mock.calls.find(([input, init]) => {
        return (
          String(input).includes("/negative-basis-monitor/auto-scan/settings") &&
          init?.method === "PUT"
        );
      });

      expect(strategyRequest).toBeTruthy();
      expect(JSON.parse(String(strategyRequest?.[1]?.body))).toMatchObject({
        enabled: true,
        strategy: {
          watch_threshold_pct: 0.5,
          interval_seconds: 60
        }
      });
    });
  });

  it("opens a candidate's exact spot and futures route in pair spread query", async () => {
    render(<NegativeBasisMonitorPage />);

    await userEvent.click(await screen.findByRole("link", { name: "在价差查询查看 PROM" }));

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("pair-monitor");
    expect(params.get("leg1_exchange")).toBe("gate");
    expect(params.get("leg1_market_type")).toBe("spot");
    expect(params.get("leg1_symbol")).toBe("PROMUSDT");
    expect(params.get("leg2_exchange")).toBe("binance");
    expect(params.get("leg2_market_type")).toBe("future");
    expect(params.get("leg2_symbol")).toBe("PROMUSDT");
    expect(params.get("leg2_multiplier")).toBe("1");
    expect(params.get("hours")).toBe("4");
    expect(params.get("interval_seconds")).toBe("60");
  });
});
