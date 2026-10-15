import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getSqueezeEvents, getSqueezePaperPositions, getSqueezePaperReport,
  getSqueezePaperTrades, getSqueezeRouteEvents, getSqueezeRoutes,
  getSqueezeStatus, getSqueezeWatchlist
} from "../src/api/client";
import type { SqueezeStatus } from "../src/api/types";
import { SqueezeArbitragePage } from "../src/pages/SqueezeArbitragePage";

vi.mock("../src/api/client", () => ({
  getSqueezeEvents: vi.fn(),
  getSqueezePaperPositions: vi.fn(),
  getSqueezePaperReport: vi.fn(),
  getSqueezePaperTrades: vi.fn(),
  getSqueezeRouteEvents: vi.fn(),
  getSqueezeRoutes: vi.fn(),
  getSqueezeStatus: vi.fn(),
  getSqueezeWatchlist: vi.fn()
}));

const status: SqueezeStatus = {
  enabled: true,
  rule_version: "squeeze-watch-s1-v1",
  last_attempt_at: "2026-09-26T10:03:00Z",
  last_success_at: "2026-09-26T10:03:00Z",
  last_bucket_at: "2026-09-26T10:00:00Z",
  last_error: null,
  verified_symbols: ["NEWUSDT"],
  discovery: {
    mode: "auto", selected_at: "2026-09-26T10:03:00Z",
    bucket_at: "2026-09-26T10:00:00Z",
    rule_version: "squeeze-discovery-v1", eligible_count: 19,
    screened_count: 12, stale: false, screened: [],
    selected: [{
      raw_symbol: "NEWUSDT", market_key: "binance|future|NEWUSDT|",
      selection_kind: "early_candidate", return_4h: 0.05,
      return_24h: 0.12, volume_ratio: 2.5,
      oi_current_growth: 0.03, account_ratio: 0.9,
      quote_volume_24h: 5_000_000
    }]
  },
  active_watch_count: 0,
  liquidation_coverage: {
    state: "throttled_public_stream", updated_at: null,
    last_message_at: null, last_error: null, open_gaps: 0,
    public_stream_complete: false
  },
  liquidation_observed_last_hour: {},
  latest_market_scans: [{
    market_key: "binance|future|NEWUSDT|",
    bucket_at: "2026-09-26T10:00:00Z",
    requested_from: "2026-09-19T07:00:00Z",
    requested_to: "2026-09-26T10:00:00Z",
    received_at: "2026-09-26T10:03:00Z",
    candle_count: 172, positioning_count: 30,
    result_status: "ready", error: null
  }]
};

beforeEach(() => {
  vi.mocked(getSqueezeStatus).mockResolvedValue(status);
  vi.mocked(getSqueezeWatchlist).mockResolvedValue([]);
  vi.mocked(getSqueezeEvents).mockResolvedValue([]);
  vi.mocked(getSqueezeRoutes).mockResolvedValue([]);
  vi.mocked(getSqueezeRouteEvents).mockResolvedValue([]);
  vi.mocked(getSqueezePaperPositions).mockResolvedValue([]);
  vi.mocked(getSqueezePaperTrades).mockResolvedValue([]);
  vi.mocked(getSqueezePaperReport).mockRejectedValue(new Error("unavailable"));
});

describe("SqueezeArbitragePage", () => {
  it("shows the current candidate with its source metrics and scan quality", async () => {
    render(<SqueezeArbitragePage />);
    expect(await screen.findAllByText("binance|future|NEWUSDT|")).toHaveLength(2);
    expect(screen.getByText("早期异动")).toBeTruthy();
    expect(screen.getByText("2.50x")).toBeTruthy();
    expect(screen.getByText("完整")).toBeTruthy();
    expect(screen.queryByText(/AKEUSDT/)).toBeNull();
  });

  it("shows current screened markets and rejection reasons when none qualify", async () => {
    vi.mocked(getSqueezeStatus).mockResolvedValue({
      ...status,
      discovery: {
        ...status.discovery,
        selected: [],
        screened: [{
          raw_symbol: "FRESHUSDT", market_key: "binance|future|FRESHUSDT|",
          ticker_change_24h: 0.09, return_4h: 0.02,
          volume_ratio: 1.2, oi_current_growth: 0.03,
          reasons: ["volume_not_accelerating"]
        }]
      },
      latest_market_scans: []
    });
    render(<SqueezeArbitragePage />);
    expect(await screen.findByText("binance|future|FRESHUSDT|")).toBeTruthy();
    expect(screen.getByText("量能未放大")).toBeTruthy();
    expect(screen.getByText("当前无符合早期量价与仓位条件的市场")).toBeTruthy();
  });
});
