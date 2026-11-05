import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PairMonitorPage,
  buildFundingRateDiffRows,
  buildMinuteFundingRateDiffRows,
  buildRealtimeFundingRateDiffRows,
  fundingHistoryFailedForLeg
} from "../src/pages/PairMonitorPage";
import type { HyperliquidDexMarket, PairSpreadFundingRecordStatus, PairSpreadQueryResult } from "../src/api/types";
import type { RangeBacktestRequest } from "../src/api/rangeBacktest";
import { rangeBacktestFixture } from "./fixtures/rangeBacktest";

const observedAt = "2026-07-24T02:00:00Z";

function savedPairPreset(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "binance|future||BTC|okx|future||BTC",
    leg1_exchange: "binance",
    leg1_market_type: "future",
    leg1_dex: "",
    leg1_symbol: "BTCUSDT",
    leg2_exchange: "okx",
    leg2_market_type: "future",
    leg2_dex: "",
    leg2_symbol: "BTCUSDT",
    leg2_multiplier: 1,
    hours: 4,
    intervalSeconds: 60,
    showDayCompare: false,
    dayCompareDays: 3,
    dayCompareMode: "query",
    dayCompareStartTime: "",
    dayCompareEndTime: "",
    savedAt: "2026-09-12T02:00:00Z",
    ...overrides
  };
}

function normalizedMockSymbol(exchange: string, symbol: string) {
  const text = symbol.trim().toUpperCase();
  const baseText = exchange === "hyperliquid" && text.includes(":")
    ? text.slice(text.indexOf(":") + 1)
    : text;
  if (exchange === "binance_alpha") {
    return baseText;
  }
  return baseText.endsWith("USDT") ? baseText : `${baseText}USDT`;
}

function pairSpreadResult(params?: URLSearchParams) {
  const leg1MarketType = params?.get("leg1_market_type") === "spot" ? "spot" : "future";
  const leg2MarketType = params?.get("leg2_market_type") === "spot" ? "spot" : "future";
  const leg1Exchange = params?.get("leg1_exchange") ?? "bitget";
  const leg2Exchange = params?.get("leg2_exchange") ?? "bitget";
  const requestedLeg1Symbol = params?.get("leg1_symbol") ?? "SKHYUSDT";
  const requestedLeg2Symbol = params?.get("leg2_symbol") ?? "SKHYNIXUSDT";
  const embeddedLeg1Dex = requestedLeg1Symbol.includes(":") ? requestedLeg1Symbol.split(":", 1)[0] : "";
  const embeddedLeg2Dex = requestedLeg2Symbol.includes(":") ? requestedLeg2Symbol.split(":", 1)[0] : "";
  const leg1Dex =
    leg1Exchange === "hyperliquid"
      ? (params?.get("leg1_dex") ?? embeddedLeg1Dex)
      : "";
  const leg2Dex =
    leg2Exchange === "hyperliquid"
      ? (params?.get("leg2_dex") ?? embeddedLeg2Dex)
      : "";
  const leg1Symbol = normalizedMockSymbol(leg1Exchange, requestedLeg1Symbol);
  const leg2Symbol = normalizedMockSymbol(leg2Exchange, requestedLeg2Symbol);
  const leg2Multiplier = Number(params?.get("leg2_multiplier") ?? 1);
  const intervalSeconds = Number(params?.get("interval_seconds") ?? 5);
  const intervalMinutes = Number(params?.get("interval_minutes") ?? Math.max(1, Math.round(intervalSeconds / 60)));
  const hours = Number(params?.get("hours") ?? 4);
  const includeCurrent = params?.get("include_current") !== "false";
  const requestedEndAt = params?.get("end_at");
  const baseObservedAt = requestedEndAt ?? observedAt;
  const compareDayOffset = requestedEndAt ? Math.max(0, Math.round((Date.parse(observedAt) - Date.parse(requestedEndAt)) / 86_400_000)) : 0;
  const compareSpreadBias = compareDayOffset * 0.18;
  const largePriceGap = leg2Multiplier === 0.1;
  const currentTailMismatch = requestedLeg1Symbol.trim().toUpperCase() === "TAIL";
  const leg1Price = largePriceGap ? 1218.6 : 100;
  const leg2Price = largePriceGap ? 1618.3 : currentTailMismatch ? 99.94 : 101;
  const currentSpreadAbs = largePriceGap ? 399.7 : currentTailMismatch ? -0.06 : 1;
  const currentSpreadPct = largePriceGap ? 28.18 : currentTailMismatch ? -0.06 : 0.5;
  const fundingHistory =
    leg1MarketType === "future" && leg2MarketType === "future"
      ? [
          {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            funding_time: "2026-07-24T00:00:00Z",
            funding_rate_pct: -0.1
          },
          {
            exchange: leg2Exchange,
            symbol: leg2Symbol,
            funding_time: "2026-07-24T00:00:40Z",
            funding_rate_pct: -0.07
          },
          {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            funding_time: "2026-07-23T16:00:00Z",
            funding_rate_pct: 0.01
          },
          {
            exchange: leg2Exchange,
            symbol: leg2Symbol,
            funding_time: "2026-07-23T16:00:00Z",
            funding_rate_pct: 0.01
          },
          {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            funding_time: "2026-07-23T08:00:00Z",
            funding_rate_pct: -0.1
          }
        ]
      : [];
  const realtimeFunding =
    includeCurrent && leg1MarketType === "future" && leg2MarketType === "future"
      ? [
          {
            bucket_at: "2026-07-24T01:59:50Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.015,
            net_rate_pct: 0.005,
            source: "current"
          },
          {
            bucket_at: "2026-07-24T01:59:55Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.02,
            net_rate_pct: 0.01,
            source: "current"
          },
          {
            bucket_at: "2026-07-24T02:00:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.025,
            net_rate_pct: 0.015,
            source: "current"
          }
        ]
      : [];
  const points = currentTailMismatch
    ? [
        {
          bucket_at: "2026-07-24T01:58:00Z",
          leg1_close: 100,
          leg2_close: 98.8,
          spread_abs: -1.2,
          spread_pct: -1.21
        },
        {
          bucket_at: "2026-07-24T01:59:00Z",
          leg1_close: 100,
          leg2_close: 98.5,
          spread_abs: -1.5,
          spread_pct: -1.51
        }
      ]
    : largePriceGap
      ? [
        {
          bucket_at: "2026-07-24T02:00:00Z",
          leg1_close: 1200,
          leg2_close: 1600,
          spread_abs: 400,
          spread_pct: 28.5
        },
        {
          bucket_at: "2026-07-24T02:01:00Z",
          leg1_close: 1210,
          leg2_close: 1590,
          spread_abs: 380,
          spread_pct: 27.2
        },
        {
          bucket_at: "2026-07-24T02:02:00Z",
          leg1_close: leg1Price,
          leg2_close: leg2Price,
          spread_abs: 399.7,
          spread_pct: 28.18
        }
      ]
    : [
        {
          bucket_at: baseObservedAt,
          leg1_close: 100,
          leg2_close: 101,
          spread_abs: 1,
          spread_pct: 0.5
        }
      ];
  const historicalPoints = includeCurrent
    ? points
    : points.map((point, index) => ({
        ...point,
        bucket_at: new Date(Date.parse(baseObservedAt) - (points.length - index - 1) * intervalSeconds * 1000).toISOString(),
        spread_abs: Number((point.spread_abs + compareSpreadBias).toFixed(4)),
        spread_pct: Number((point.spread_pct + compareSpreadBias).toFixed(4))
      }));
  return {
    leg1: {
      exchange: leg1Exchange,
      symbol: leg1Symbol,
      market_type: leg1MarketType,
      ...(leg1Dex ? { dex: leg1Dex } : {})
    },
    leg2: {
      exchange: leg2Exchange,
      symbol: leg2Symbol,
      market_type: leg2MarketType,
      ...(leg2Dex ? { dex: leg2Dex } : {})
    },
    hours,
    interval_minutes: intervalMinutes,
    interval_seconds: intervalSeconds,
    leg2_multiplier: leg2Multiplier,
    observed_at: baseObservedAt,
    point_count: historicalPoints.length,
    first_seen_at: historicalPoints[0]?.bucket_at ?? baseObservedAt,
    last_seen_at: historicalPoints[historicalPoints.length - 1]?.bucket_at ?? baseObservedAt,
    spread_abs: { min: 1, max: 1, mean: 1, current: 1 },
    spread_pct: { min: 0.5, max: 0.5, mean: 0.5, current: 0.5 },
    current: includeCurrent
      ? {
          observed_at: observedAt,
          leg1: {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            market_type: leg1MarketType,
            ...(leg1Dex ? { dex: leg1Dex } : {}),
            raw_symbol: leg1Symbol,
            price_multiplier: 1,
            contract_size_multiplier: 1,
            data_source: "Left public order book",
            upstream_timestamp: "2026-07-24T01:59:59Z",
            is_estimated: false,
            estimated_fields: [],
            estimated_taker_fee_pct: leg1MarketType === "spot" ? 0.1 : 0.05,
            fee_is_estimated: true,
            price: leg1Price,
            price_field: "mid_price" as const,
            bid_price: leg1Price - 0.1,
            ask_price: leg1Price + 0.1,
            mark_price: leg1Price - 0.2,
            index_price: null,
            mid_price: leg1Price,
            last_price: leg1Price,
            volume_24h_usdt: 15_330_064.8066,
            funding_rate_pct: leg1MarketType === "spot" ? null : 0.01,
            funding_next_rate_pct: null,
            funding_next_time: null,
            funding_interval_hours: null,
            funding_rate_upper_pct: null,
            funding_rate_lower_pct: null,
            timestamp: observedAt
          },
          leg2: {
            exchange: leg2Exchange,
            symbol: leg2Symbol,
            market_type: leg2MarketType,
            ...(leg2Dex ? { dex: leg2Dex } : {}),
            raw_symbol: leg2Symbol,
            price_multiplier: leg2Exchange === "okx" && leg2Symbol === "ANTHROPICUSDT" ? 10 : 1,
            contract_size_multiplier: 0.01,
            data_source: "Right public order book",
            upstream_timestamp: "2026-07-24T01:59:58Z",
            is_estimated: false,
            estimated_fields: ["funding_next_time"],
            estimated_taker_fee_pct: leg2MarketType === "spot" ? 0.1 : 0.05,
            fee_is_estimated: true,
            price: leg2Price,
            price_field: "mid_price" as const,
            bid_price: leg2Price - 0.1,
            ask_price: leg2Price + 0.1,
            mark_price: leg2MarketType === "spot" ? null : leg2Price + 0.2,
            index_price: leg2MarketType === "spot" ? null : 100,
            mid_price: leg2Price,
            last_price: leg2Price,
            volume_24h_usdt: 2_241_911.7006,
            funding_rate_pct: leg2MarketType === "spot" ? null : 0.01,
            funding_next_rate_pct: null,
            funding_next_time: null,
            funding_interval_hours: 8,
            funding_rate_upper_pct: null,
            funding_rate_lower_pct: null,
            timestamp: observedAt
          },
          spread_abs: currentSpreadAbs,
          spread_pct: currentSpreadPct,
          open_spread_abs: currentSpreadAbs - 0.2,
          open_spread_pct: 0.8,
          close_spread_abs: currentSpreadAbs + 0.2,
          close_spread_pct: 1.2,
          mark_spread_abs: leg2MarketType === "spot" ? null : currentSpreadAbs + 0.4,
          mark_spread_pct: leg2MarketType === "spot" ? null : 1.4
        }
      : null,
    points: historicalPoints,
    open_interest: includeCurrent
      ? [
          {
            bucket_at: "2026-07-24T01:59:55Z",
            leg1_open_interest_usdt: 1000000,
            leg2_open_interest_usdt: 1500000,
            leg1_change_usdt: null,
            leg2_change_usdt: null,
            net_change_usdt: null,
            source: "realtime"
          },
          {
            bucket_at: "2026-07-24T02:00:00Z",
            leg1_open_interest_usdt: 1020000,
            leg2_open_interest_usdt: 1490000,
            leg1_change_usdt: 20000,
            leg2_change_usdt: -10000,
            net_change_usdt: -30000,
            source: "realtime"
          }
        ]
      : [],
    funding_history: includeCurrent ? fundingHistory : [],
    realtime_funding: realtimeFunding,
    warnings: []
  };
}

function pairFundingRecordStatus(watched: boolean) {
  return {
    watched,
    item: watched
      ? {
          pair_key: "bitget|future|SKHYUSDT|bitget|future|SKHYNIXUSDT|1|60",
          leg1: { exchange: "bitget", symbol: "SKHYUSDT", market_type: "future" },
          leg2: { exchange: "bitget", symbol: "SKHYNIXUSDT", market_type: "future" },
          leg2_multiplier: 1,
          interval_seconds: 60,
          created_at: "2026-07-24T01:58:00Z",
          updated_at: "2026-07-24T02:02:00Z",
          sample_count: 3,
          latest_sample_at: "2026-07-24T02:02:00Z"
        }
      : null,
    samples: watched
      ? [
          {
            bucket_at: "2026-07-24T02:00:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.015,
            net_rate_pct: 0.005,
            source: "minute_record"
          },
          {
            bucket_at: "2026-07-24T02:01:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.02,
            net_rate_pct: 0.01,
            source: "minute_record"
          },
          {
            bucket_at: "2026-07-24T02:02:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.025,
            net_rate_pct: 0.015,
            source: "minute_record"
          }
        ]
      : [],
    warnings: []
  };
}

function pairSpreadDiagnosticResult() {
  return {
    leg1: { exchange: "bitget", symbol: "SKHYUSDT", market_type: "future" },
    leg2: { exchange: "hyperliquid", symbol: "SKHYNIXUSDT", market_type: "future" },
    hours: 4,
    requested_interval_seconds: 5,
    interval_seconds: 60,
    observed_at: observedAt,
    point_count: 7,
    threshold_pct: 1,
    peak_at: "2026-07-24T01:59:00Z",
    peak_spread_pct: 11.23,
    peak_spread_abs: 30,
    peak_leg1_close: 100,
    peak_leg2_close: 112,
    points_over_threshold: 6,
    first_over_threshold_at: "2026-07-24T01:54:00Z",
    last_over_threshold_at: "2026-07-24T01:59:00Z",
    longest_run: {
      start_at: "2026-07-24T01:54:00Z",
      end_at: "2026-07-24T01:59:00Z",
      point_count: 6,
      peak_spread_pct: 11.23,
      peak_at: "2026-07-24T01:59:00Z"
    },
    current_spread_pct: 0.5,
    inferred_type: "FF",
    alert_rules: [
      {
        id: "rule-1",
        name: "规则1",
        enabled: true,
        matches_pair_scope: true,
        min_open_spread_pct: 1.5,
        min_fee_adjusted_open_pct: 0.05,
        consecutive_hits: 3,
        cooldown_seconds: 300,
        reasons: ["历史峰值达到规则的开仓价差阈值"]
      }
    ],
    alert_events: {
      total: 1,
      sent: 0,
      muted: 1,
      failed: 0,
      latest_status: "muted",
      latest_at: "2026-07-24T01:58:00Z",
      latest_message: "订单簿深度不足",
      events: [
        {
          rule_id: "rule-1",
          status: "muted",
          created_at: "2026-07-24T01:58:00Z",
          message: "订单簿深度不足"
        }
      ]
    },
    suppress_when_card_conditions_fail: true,
    notes: ["历史 K 线和实时告警使用不同数据链路。"],
    warnings: []
  };
}

describe("PairMonitorPage", () => {
  const requests: string[] = [];
  let fundingRecordWatched = false;
  let serverPresets: Array<Record<string, unknown>> = [];
  let hyperliquidMarketsResponse: HyperliquidDexMarket[] = [];
  let requiredDashboardPassword = "";
  let presetWriteStatus = 200;

  beforeEach(() => {
    requests.length = 0;
    fundingRecordWatched = false;
    serverPresets = [];
    requiredDashboardPassword = "";
    presetWriteStatus = 200;
    hyperliquidMarketsResponse = [
      {
        dex: "main",
        full_name: "Hyperliquid 主站",
        assets: [{ raw_symbol: "BTC", symbol: "BTCUSDT", base: "BTC", delisted: false }]
      },
      {
        dex: "io",
        full_name: "EntropyIO",
        assets: [{ raw_symbol: "io:OAI", symbol: "OAIUSDT", base: "OAI", delisted: false }]
      },
      {
        dex: "xyz",
        full_name: "XYZ",
        assets: [
          { raw_symbol: "xyz:COIN", symbol: "COINUSDT", base: "COIN", delisted: false },
          { raw_symbol: "xyz:CL", symbol: "CLUSDT", base: "CL", delisted: false }
        ]
      }
    ];
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    // Existing SVG/layout regression assertions exercise the retained legacy view.
    window.localStorage.setItem("pair-chart-version", "v1");
    window.sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlText = String(input);
        requests.push(urlText);
        const url = new URL(urlText, "http://localhost");
        if (init?.method && init.method !== "GET" && (
          url.pathname.includes("/pair-spread/presets/") ||
          url.pathname.endsWith("/settings/floating-watch/items")
        )) {
          if (requiredDashboardPassword) {
            return Response.json({ detail: "Invalid dashboard password" }, { status: 401 });
          }
          if (presetWriteStatus !== 200) {
            return Response.json({ detail: "preset service unavailable" }, { status: presetWriteStatus });
          }
        }
        if (url.pathname.endsWith("/pair-spread/presets") && (!init?.method || init.method === "GET")) {
          return Response.json(serverPresets);
        }
        if (url.pathname.endsWith("/pair-spread/presets/merge")) {
          const incoming = JSON.parse(String(init?.body ?? "{}")) as {
            presets?: Array<Record<string, unknown>>;
          };
          const latest = new Map(serverPresets.map((preset) => [String(preset.id), preset]));
          (incoming.presets ?? []).forEach((preset) => {
            const id = String(preset.id);
            const current = latest.get(id);
            if (!current || Date.parse(String(preset.savedAt)) >= Date.parse(String(current.savedAt))) {
              latest.set(id, preset);
            }
          });
          serverPresets = Array.from(latest.values()).sort(
            (left, right) => Date.parse(String(right.savedAt)) - Date.parse(String(left.savedAt))
          );
          return Response.json(serverPresets);
        }
        if (url.pathname.includes("/pair-spread/presets/")) {
          const id = decodeURIComponent(url.pathname.split("/pair-spread/presets/")[1]);
          if (init?.method === "DELETE") {
            serverPresets = serverPresets.filter((preset) => preset.id !== id);
            return Response.json({ ok: true });
          }
          const preset = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
          serverPresets = [preset, ...serverPresets.filter((item) => item.id !== id)];
          return Response.json(preset);
        }
        if (url.pathname.includes("/pair-spread/query")) {
          return Response.json(pairSpreadResult(url.searchParams));
        }
        if (url.pathname.includes("/pair-spread/hyperliquid-markets")) {
          return Response.json(hyperliquidMarketsResponse);
        }
        if (url.pathname.includes("/pair-spread/funding-history")) {
          const result = pairSpreadResult(url.searchParams);
          return Response.json({
            leg1: result.leg1,
            leg2: result.leg2,
            funding_history: [
              {
                exchange: result.leg1.exchange,
                symbol: result.leg1.symbol,
                funding_time: "2026-07-23T16:00:00Z",
                funding_rate_pct: 0.01
              },
              {
                exchange: result.leg1.exchange,
                symbol: result.leg1.symbol,
                funding_time: "2026-07-24T00:00:00Z",
                funding_rate_pct: -0.1
              },
              {
                exchange: result.leg2.exchange,
                symbol: result.leg2.symbol,
                funding_time: "2026-07-24T00:00:00Z",
                funding_rate_pct: -0.07
              },
              {
                exchange: result.leg1.exchange,
                symbol: result.leg1.symbol,
                funding_time: "2026-07-23T08:00:00Z",
                funding_rate_pct: -0.1
              }
            ],
            warnings: []
          });
        }
        if (url.pathname.includes("/pair-spread/funding-records/status")) {
          return Response.json(pairFundingRecordStatus(fundingRecordWatched));
        }
        if (url.pathname.includes("/pair-spread/diagnostics")) {
          return Response.json(pairSpreadDiagnosticResult());
        }
        if (url.pathname.includes("/pair-spread/funding-records/watch")) {
          fundingRecordWatched = init?.method !== "DELETE";
          return Response.json(pairFundingRecordStatus(fundingRecordWatched));
        }
        if (url.pathname.endsWith("/settings/floating-watch/items") && init?.method === "POST") {
          const mutation = JSON.parse(String(init.body)) as { value: string };
          return Response.json({ symbols: [], pair_ids: [mutation.value] });
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it.each([false, true])("pauses polling for 60 seconds while hidden and refreshes once when visible (funding watched: %s)", async watched => {
    vi.useFakeTimers();
    fundingRecordWatched = watched;
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    window.history.pushState({}, "", "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=BTC&leg2_exchange=okx&leg2_market_type=future&leg2_symbol=BTC&hours=4&interval_seconds=5");
    const view = render(<PairMonitorPage />);
    const queryCalls = () => requests.filter(url => url.includes("/pair-spread/query")).length;
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(queryCalls()).toBe(1);
      fireEvent.click(screen.getByRole("switch", { name: "自动刷新" }));
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(queryCalls()).toBe(2);
      const initialRequests = requests.length;
      act(() => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(requests).toHaveLength(initialRequests);
      await act(async () => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      expect(queryCalls()).toBe(3);
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(queryCalls()).toBe(4);
    } finally {
      view.unmount();
      Reflect.deleteProperty(document, "visibilityState");
      vi.useRealTimers();
    }
  });

  it("shows current leg ages and an incomplete realtime window", async () => {
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/query")) return response;
      const data = await response.json();
      Object.assign(data.current.leg1, { age_ms: 1200, source_age_ms: -100, time_source: "upstream" });
      Object.assign(data.current.leg2, { age_ms: 30000, source_age_ms: null, time_source: "received" });
      return Response.json({ ...data, complete: false, actual_samples: 0, expected_samples: 2880, max_gap_ms: 12500 });
    });
    render(<PairMonitorPage />);
    await userEvent.click(screen.getByRole("button", { name: /查.*询/ }));
    const leftAge = await screen.findByLabelText("报价年龄 1.2s");
    const rightAge = screen.getByLabelText("报价年龄 30.0s");
    const legCards = document.querySelectorAll(".pair-focus-metrics > .pair-metric-card:first-child, .pair-focus-metrics > .pair-metric-card:last-child");
    expect(leftAge.closest(".pair-metric-card")).toBe(legCards[0]);
    expect(rightAge.closest(".pair-metric-card")).toBe(legCards[1]);
    expect(leftAge.classList.contains("ant-tag-green")).toBe(true);
    expect(rightAge.classList.contains("ant-tag-yellow")).toBe(true);
    await userEvent.hover(leftAge);
    expect(await screen.findByText("来源年龄：-100ms；时间来源：upstream")).toBeTruthy();
    await userEvent.unhover(leftAge);
    await userEvent.hover(rightAge);
    expect(await screen.findByText("来源年龄：未知；时间来源：received")).toBeTruthy();
    const warning = screen.getByText("样本不足 0/2880，最大缺口 12.5s");
    expect(warning.classList.contains("ant-tag-default")).toBe(true);
  });

  it.each([undefined, null, true])("hides missing leg ages and window warnings when complete=%s", async (complete) => {
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/query")) return response;
      return Response.json({ ...await response.json(), complete });
    });
    render(<PairMonitorPage />);
    await userEvent.click(screen.getByRole("button", { name: /查.*询/ }));
    await screen.findByRole("img", { name: "均值价差率曲线" });
    expect(screen.queryByLabelText(/^报价年龄/)).toBeNull();
    expect(screen.queryByText(/样本不足/)).toBeNull();
  });

  it.each([false, true])("shows XAU/CL ratios consistently, reversed=%s", async (reversed) => {
    const originalFetch = window.fetch;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await originalFetch(input, init);
      if (!String(input).includes("/pair-spread/query")) return response;
      const data = await response.json();
      const left = reversed ? 100 : 4000;
      const right = reversed ? 4000 : 100;
      data.points = data.points.map((point: Record<string, unknown>) => ({ ...point, leg1_close: left, leg2_close: right }));
      if (data.current) {
        Object.assign(data.current.leg1, { price: left, mid_price: left, bid_price: left - 0.1, ask_price: left + 0.1, mark_price: left });
        Object.assign(data.current.leg2, { price: right, mid_price: right, bid_price: right - 0.1, ask_price: right + 0.1, mark_price: null });
      }
      return Response.json(data);
    });
    window.history.pushState({}, "", "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=XAU&leg2_exchange=binance&leg2_market_type=future&leg2_symbol=CL&hours=6&interval_seconds=60");
    render(<PairMonitorPage />);
    await screen.findByRole("img", { name: "价格比值曲线" });
    const focus = document.querySelector(".pair-focus-block")!;
    expect(focus.querySelector(".pair-chart-card")?.textContent).not.toContain("%");
    focus.querySelectorAll(".pair-metric-value").forEach((value) => expect(value.textContent).not.toContain("%"));
    expect(focus.textContent).toContain(reversed ? "0.025" : "40");
    expect(screen.queryByText("标记比值（辅助）")).toBeNull();
    expect(document.querySelector(".pair-chart-head-meta")?.textContent).toContain(reversed ? "最新 0.025" : "最新 40");
    await userEvent.click(screen.getByRole("tab", { name: "最近价差" }));
    expect(screen.getAllByText("价格比值（左 ÷ 右）").length).toBeGreaterThan(0);
    const open = within(screen.getByRole("region", { name: "可成交比值" })).getByText("开仓").parentElement?.textContent;
    expect(open).toContain(reversed ? "0.0250256" : "40.041");
    await userEvent.setup().click(screen.getByRole("button", { name: "交换左右标的" }));
    await waitFor(() => expect(requests.filter(r => r.includes("/pair-spread/query")).length).toBeGreaterThan(1));
  });

  it("queries Arcus independently with its own exchange identity", async () => {
    serverPresets = [savedPairPreset({
      id: "arcus|future||BTC|binance|future||BTC",
      leg1_exchange: "arcus", leg2_exchange: "binance"
    })];
    window.history.pushState({}, "", "/?page=pair-monitor&leg1_exchange=arcus&leg1_market_type=future&leg1_symbol=BTC&leg2_exchange=binance&leg2_market_type=future&leg2_symbol=BTC&hours=4&interval_seconds=60");
    render(<PairMonitorPage />);
    await waitFor(() => {
      const query = requests.map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query"));
      expect(query?.searchParams.get("leg1_exchange")).toBe("arcus");
      expect(query?.searchParams.get("leg1_market_type")).toBe("future");
      expect(query?.searchParams.get("leg1_symbol")).toBe("BTC");
      expect(query?.searchParams.get("leg1_dex")).toBeNull();
      expect(query?.searchParams.get("leg2_exchange")).toBe("binance");
    });
    await waitFor(() => expect(document.querySelector(".pair-exchange-arcus")?.textContent).toContain("arcus"));
  });

  it("disables Arcus spot RFQ rather than querying a perpetual as spot", async () => {
    const user = userEvent.setup();
    window.history.pushState({}, "", "/?page=pair-monitor&leg1_exchange=arcus&leg1_market_type=future&leg1_symbol=ETH&leg2_exchange=binance&leg2_market_type=future&leg2_symbol=BTC&hours=4&interval_seconds=60");
    render(<PairMonitorPage />);
    await user.click(await screen.findByRole("combobox", { name: "左侧市场" }));
    await waitFor(() => {
      const spot = document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) [title="现货"]');
      expect(spot?.classList.contains("ant-select-item-option-disabled")).toBe(true);
    });
  });

  it("groups saved pairs by the left symbol and keeps venue details in compact tags", async () => {
    serverPresets = [
      savedPairPreset(),
      savedPairPreset({
        id: "hyperliquid|future|xyz|BTC|gate|future||BTC",
        leg1_exchange: "hyperliquid", leg1_dex: "xyz", leg2_exchange: "gate",
        savedAt: "2026-09-11T02:00:00Z"
      }),
      savedPairPreset({
        id: "binance|future||BTC|binance|future||ETH",
        leg2_exchange: "binance", leg2_symbol: "ETHUSDT",
        savedAt: "2026-09-14T02:00:00Z"
      }),
      savedPairPreset({
        id: "binance|future||ETH|okx|future||ETH",
        leg1_symbol: "ETHUSDT", leg2_symbol: "ETHUSDT",
        savedAt: "2026-09-13T02:00:00Z"
      })
    ];
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(4));
    const groups = document.querySelectorAll(".pair-saved-group");
    expect(groups).toHaveLength(2);
    expect(groups[0].querySelector(".pair-saved-group-symbol")?.textContent).toBe("BTC");
    expect(groups[0].querySelector(".pair-saved-group-count")?.textContent).toBe("3");
    const tags = groups[0].querySelectorAll(".pair-saved-tag");
    expect(tags).toHaveLength(3);
    expect(tags[0].textContent).not.toContain("BTC");
    expect(tags[0].textContent).toContain("bn");
    expect(tags[0].textContent).toContain("ok");
    expect(tags[1].textContent).not.toContain("BTC");
    expect(tags[1].querySelector(".pair-exchange-dex")?.textContent).toBe("xyz");
    expect(tags[2].querySelectorAll(".pair-exchange-binance")).toHaveLength(2);
    expect(tags[2].textContent).toContain("BTC");
    expect(tags[2].textContent).toContain("ETH");
    expect(tags[2].textContent).toContain("/");
  });

  it("filters individual saved tags by either symbol, exchange id or chip abbreviation and clears", async () => {
    const user = userEvent.setup();
    serverPresets = [
      savedPairPreset(),
      savedPairPreset({
        id: "hyperliquid|future|main|BTC|gate|future||BTC",
        leg1_exchange: "hyperliquid", leg1_dex: "main", leg2_exchange: "gate"
      }),
      savedPairPreset({
        id: "bitget|future||ETH|hyperliquid|future|xyz|CXMT",
        leg1_exchange: "bitget", leg1_symbol: "ETHUSDT",
        leg2_exchange: "hyperliquid", leg2_dex: "xyz", leg2_symbol: "CXMTUSDT"
      })
    ];
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(3));
    const search = screen.getByPlaceholderText("搜标的/交易所");
    for (const term of ["hL", "HYPERLIQUID"]) {
      fireEvent.change(search, { target: { value: term } });
      const tags = document.querySelectorAll(".pair-saved-tag");
      expect(tags).toHaveLength(2);
      tags.forEach((tag) => expect(tag.querySelector(".pair-exchange-hyperliquid")).toBeTruthy());
      expect(document.querySelector(".pair-saved-group-count")?.textContent).toBe("1");
    }
    fireEvent.change(search, { target: { value: "cxmt" } });
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(1);
    expect(document.querySelector(".pair-saved-tag")?.textContent).toContain("CXMT");
    fireEvent.change(search, { target: { value: "bTc" } });
    expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(2);
    fireEvent.change(search, { target: { value: "oKx" } });
    expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1);
    expect(document.querySelector(".pair-saved-tag .pair-exchange-okx")).toBeTruthy();
    fireEvent.change(search, { target: { value: "no-such-saved-pair" } });
    expect(screen.getByText("无匹配")).toBeTruthy();
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(0);
    const savedArea = document.querySelector(".pair-saved-presets") as HTMLElement;
    await user.click(within(savedArea).getByRole("button", { name: "close-circle" }));
    expect((search as HTMLInputElement).value).toBe("");
    expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(3);
    expect(screen.queryByText("无匹配")).toBeNull();
  });

  it("collapses five saved groups, searches all of them and remembers expansion without saving the search", async () => {
    const user = userEvent.setup();
    serverPresets = ["BTC", "BCH", "BNB", "BONK", "BOME"].map((symbol, index) => savedPairPreset({
      id: `binance|future||${symbol}|okx|future||${symbol}`,
      leg1_symbol: `${symbol}USDT`, leg2_symbol: `${symbol}USDT`,
      savedAt: `2026-09-12T0${index}:00:00Z`
    }));
    const mounted = render(<PairMonitorPage />);
    await screen.findByRole("button", { name: "展开全部（共 5 组）" });
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(4);
    expect(Array.from(document.querySelectorAll(".pair-saved-group-symbol"), (node) => node.textContent))
      .toEqual(["BOME", "BONK", "BNB", "BCH"]);
    const search = screen.getByPlaceholderText("搜标的/交易所");
    fireEvent.change(search, { target: { value: "btc" } });
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(1);
    expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("BTC");
    fireEvent.change(search, { target: { value: "b" } });
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: /展开全部|收起/ })).toBeNull();
    fireEvent.change(search, { target: { value: "  " } });
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(4);
    await user.click(screen.getByRole("button", { name: "展开全部（共 5 组）" }));
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(5);
    expect(window.localStorage.getItem("pair-saved-expanded")).toBe("true");
    fireEvent.change(search, { target: { value: "btc" } });
    mounted.unmount();
    render(<PairMonitorPage />);
    await screen.findByRole("button", { name: "收起" });
    expect((screen.getByPlaceholderText("搜标的/交易所") as HTMLInputElement).value).toBe("");
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(5);
    await user.click(screen.getByRole("button", { name: "收起" }));
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(4);
    expect(window.localStorage.getItem("pair-saved-expanded")).toBe("false");
  });

  it("highlights the current form configuration with its exact DEX and follows edits and preset application", async () => {
    const user = userEvent.setup();
    serverPresets = ["io", "xyz"].map((dex) => savedPairPreset({
      id: `hyperliquid|future|${dex}|OAI|rh-lighter|future||OPENAI`,
      leg1_exchange: "hyperliquid", leg1_dex: dex, leg1_symbol: "OAIUSDT",
      leg2_exchange: "rh-lighter", leg2_symbol: "OPENAIUSDT"
    }));
    window.history.pushState({}, "",
      "/?page=pair-monitor&leg1_exchange=hyperliquid&leg1_market_type=future&leg1_dex=io&leg1_symbol=OAI" +
      "&leg2_exchange=rh-lighter&leg2_market_type=future&leg2_symbol=OPENAI&hours=4&interval_seconds=60"
    );
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(2));
    expect(document.querySelectorAll(".pair-saved-tag-active")).toHaveLength(1);
    expect(document.querySelector(".pair-saved-tag-active .pair-exchange-dex")?.textContent).toBe("io");
    await waitFor(() => expect(document.querySelector(".pair-chart-card")).toBeTruthy());
    fireEvent.change(screen.getByRole("textbox", { name: "右侧标的" }), { target: { value: "BTC" } });
    expect(document.querySelectorAll(".pair-saved-tag-active")).toHaveLength(0);
    await user.click(document.querySelector(".pair-saved-tag") as HTMLElement);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag-active")).toHaveLength(1));
    expect(document.querySelector(".pair-saved-tag-active .pair-exchange-dex")?.textContent).toBe("io");
    expect((screen.getByRole("textbox", { name: "右侧标的" }) as HTMLInputElement).value).toBe("OPENAIUSDT");
  });

  it("loads saved pair presets from the server on another device", async () => {
    serverPresets = [savedPairPreset()];
    window.localStorage.setItem("taoli1.pairSpread.presets.serverMigrated.v1", "1");

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("BTC");
      expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1);
      const cached = JSON.parse(
        window.localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]"
      );
      expect(cached).toHaveLength(1);
    });
  });

  it("uploads old local presets exactly once during server migration", async () => {
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([savedPairPreset()])
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(serverPresets).toHaveLength(1);
      expect(window.localStorage.getItem("taoli1.pairSpread.presets.serverMigrated.v1")).toBe("1");
    });
    expect(requests.some((url) => url.endsWith("/pair-spread/presets/merge"))).toBe(true);
  });

  it("does not restore deleted server presets from an already migrated cache", async () => {
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([savedPairPreset()])
    );
    window.localStorage.setItem("taoli1.pairSpread.presets.serverMigrated.v1", "1");

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(0);
    });
    expect(requests.some((url) => url.endsWith("/pair-spread/presets/merge"))).toBe(false);
  });

  it("persists saves and deletes through the preset API", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("taoli1.pairSpread.presets.serverMigrated.v1", "1");
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => expect(serverPresets).toHaveLength(1));
    expect(
      requests.some(
        (url) => url.includes("/pair-spread/presets/") && !url.endsWith("/presets/merge")
      )
    ).toBe(true);

    const closeButton = document.querySelector(".pair-saved-tag .ant-tag-close-icon");
    expect(closeButton).toBeTruthy();
    fireEvent.click(closeButton as Element);
    await waitFor(() => expect(serverPresets).toHaveLength(0));
    expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(0);
  });

  it("redirects an expired session and preserves the exact failed delete and cached presets", async () => {
    requiredDashboardPassword = "session-expired";
    serverPresets = [savedPairPreset(), savedPairPreset({ id: "another-pair", leg1_symbol: "ETHUSDT", leg2_symbol: "ETHUSDT" })];
    const view = render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(2));
    fireEvent.click(document.querySelector(".pair-saved-tag .ant-tag-close-icon") as Element);
    await waitFor(() => expect(location.pathname).toBe("/login"));
    expect(serverPresets).toHaveLength(2);
    expect(JSON.parse(localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]")).toHaveLength(2);
    expect(screen.queryByRole("dialog")).toBeNull();
    // A fresh session returns to the page; the user explicitly repeats the write.
    view.unmount();
    requiredDashboardPassword = "";
    history.replaceState(null, "", "/?page=pair-monitor");
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(2));
    fireEvent.click(document.querySelector(".pair-saved-tag .ant-tag-close-icon") as Element);
    await waitFor(() => expect(serverPresets).toHaveLength(1));
    expect(serverPresets[0].id).toBe("another-pair");
    expect(JSON.parse(localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]")).toHaveLength(1);
    expect(localStorage.getItem("dashboard_password")).toBeNull();
  });

  it("preserves presets on repeated unauthorized deletes without an inline password dialog", async () => {
    requiredDashboardPassword = "session-expired";
    serverPresets = [savedPairPreset()];
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1));
    for (let attempt = 0; attempt < 2; attempt++) {
      fireEvent.click(document.querySelector(".pair-saved-tag .ant-tag-close-icon") as Element);
      await waitFor(() => expect(location.pathname).toBe("/login"));
      expect(serverPresets).toHaveLength(1);
      expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1);
      expect(screen.queryByRole("dialog")).toBeNull();
    }
    expect(localStorage.getItem("dashboard_password")).toBeNull();
  });

  it("preserves Hyperliquid DEX and market identity through login return and a new save", async () => {
    const user = userEvent.setup();
    requiredDashboardPassword = "session-expired";
    const page = "/?page=pair-monitor&leg1_exchange=hyperliquid&leg1_market_type=future&leg1_dex=io&leg1_symbol=OAI&leg1_raw_symbol=io%3AOAI&leg2_exchange=rh-lighter&leg2_market_type=future&leg2_symbol=OPENAI&leg2_multiplier=2&hours=4&interval_seconds=60";
    history.replaceState(null, "", page);
    const view = render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelector(".pair-chart-card")).toBeTruthy());
    const pageBeforeLogin = location.pathname + location.search + location.hash;
    await user.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => expect(location.pathname).toBe("/login"));
    expect(serverPresets).toHaveLength(0);
    const next = new URLSearchParams(location.search).get("next");
    expect(next).toBe(pageBeforeLogin);
    view.unmount(); requiredDashboardPassword = "";
    history.replaceState(null, "", next!);
    render(<PairMonitorPage />);
    await user.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => expect(serverPresets).toHaveLength(1));
    expect(serverPresets[0]).toMatchObject({
      leg1_dex: "io", leg1_raw_symbol: "io:OAI", leg1_symbol: "OAIUSDT",
      leg2_exchange: "rh-lighter", leg2_symbol: "OPENAIUSDT", leg2_multiplier: 2
    });
  });

  it("preserves local migration data until a session is restored", async () => {
    requiredDashboardPassword = "session-expired";
    localStorage.setItem("taoli1.pairSpread.presets.v1", JSON.stringify([savedPairPreset()]));
    const view = render(<PairMonitorPage />);
    await waitFor(() => expect(location.pathname).toBe("/login"));
    expect(localStorage.getItem("taoli1.pairSpread.presets.serverMigrated.v1")).toBeNull();
    expect(JSON.parse(localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]")).toHaveLength(1);
    view.unmount(); requiredDashboardPassword = "";
    history.replaceState(null, "", "/?page=pair-monitor");
    render(<PairMonitorPage />);
    await waitFor(() => expect(localStorage.getItem("taoli1.pairSpread.presets.serverMigrated.v1")).toBe("1"));
    expect(serverPresets).toHaveLength(1);
  });

  it("does not show a password dialog for a non-authentication delete failure", async () => {
    presetWriteStatus = 503;
    serverPresets = [savedPairPreset()];
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1));
    fireEvent.click(document.querySelector(".pair-saved-tag .ant-tag-close-icon") as Element);
    expect(await screen.findByText("删除标的对失败：preset service unavailable")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(serverPresets).toHaveLength(1);
  });

  it("uses session cookies for both writes when adding a pair to the floating watch", async () => {
    const user = userEvent.setup();
    history.replaceState(null, "", "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=BTC&leg2_exchange=okx&leg2_market_type=future&leg2_symbol=BTC&hours=4&interval_seconds=60");
    render(<PairMonitorPage />);
    const addButton = screen.getByRole("button", { name: /加入浮窗/ });
    await waitFor(() => expect(addButton.hasAttribute("disabled")).toBe(false));
    await user.click(addButton);
    await waitFor(() => expect(serverPresets).toHaveLength(1));
    const writes = vi.mocked(fetch).mock.calls.filter(([input, init]) =>
      init?.method === "POST" || init?.method === "PUT");
    expect(writes.some(([url]) => String(url).endsWith("/settings/floating-watch/items"))).toBe(true);
    for (const [, init] of writes) {
      expect(init?.credentials).toBe("same-origin");
      expect(new Headers(init?.headers).has("X-Dashboard-Password")).toBe(false);
    }
    expect(localStorage.getItem("dashboard_password")).toBeNull();
  });

  it.each([1, 2])("keeps same-symbol tags before custom pairs in their shared group and restores multiplier %s", async (multiplier) => {
    const user = userEvent.setup();
    serverPresets = [savedPairPreset({
      id: "hyperliquid|future|io|OAI|rh-lighter|future||OAI",
      leg1_exchange: "hyperliquid",
      leg1_dex: "io",
      leg1_symbol: "OAIUSDT",
      leg2_exchange: "rh-lighter",
      leg2_symbol: "OAIUSDT"
    })];
    window.localStorage.setItem("taoli1.pairSpread.presets.serverMigrated.v1", "1");
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=hyperliquid&leg1_market_type=future&leg1_dex=io&leg1_symbol=OAI" +
        "&leg2_exchange=rh-lighter&leg2_market_type=future&leg2_symbol=OPENAI&hours=4&interval_seconds=60"
    );
    const mounted = render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelector(".pair-chart-card")).toBeTruthy());
    if (multiplier !== 1) {
      const input = screen.getByRole("spinbutton", { name: "右侧倍率" });
      await user.clear(input);
      await user.type(input, String(multiplier));
    }
    await user.click(screen.getByRole("button", { name: /保存/ }));
    await waitFor(() => expect(serverPresets).toHaveLength(2));
    expect(serverPresets[0]).toMatchObject({
      leg1_exchange: "hyperliquid", leg1_dex: "io", leg1_symbol: "OAIUSDT",
      leg2_exchange: "rh-lighter", leg2_symbol: "OPENAIUSDT", leg2_multiplier: multiplier
    });
    const cached = JSON.parse(window.localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]");
    expect(cached).toHaveLength(2);
    expect(cached[0].leg2_symbol).toBe("OPENAIUSDT");
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(1);
    expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("OAI");
    expect(document.querySelector(".pair-saved-group-count")?.textContent).toBe("2");

    mounted.unmount();
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(2));
    expect(document.querySelector(".pair-saved-tag")?.classList.contains("pair-saved-tag-same")).toBe(true);
    const savedTag = document.querySelector(".pair-saved-tag-custom") as HTMLElement;
    expect(savedTag.textContent).toContain("OPENAI");
    requests.length = 0;
    await user.click(savedTag);
    await waitFor(() => {
      const query = requests.map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query") && !url.searchParams.has("include_current"));
      expect(query?.searchParams.get("leg1_symbol")).toBe("OAIUSDT");
      expect(query?.searchParams.get("leg1_dex")).toBe("io");
      expect(query?.searchParams.get("leg2_symbol")).toBe("OPENAIUSDT");
      expect(query?.searchParams.get("leg2_multiplier")).toBe(String(multiplier));
    });
    expect((screen.getByRole("textbox", { name: "右侧标的" }) as HTMLInputElement).value).toBe("OPENAIUSDT");
  });

  it("keeps a newer same-symbol group ahead of an older custom group", async () => {
    serverPresets = [
      savedPairPreset({ savedAt: "2026-09-12T02:00:00Z" }),
      savedPairPreset({
        id: "hyperliquid|future|io|OAI|rh-lighter|future||OPENAI",
        leg1_exchange: "hyperliquid", leg1_dex: "io", leg1_symbol: "OAIUSDT",
        leg2_exchange: "rh-lighter", leg2_symbol: "OPENAIUSDT",
        savedAt: "2026-09-11T02:00:00Z"
      })
    ];
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(2));
    expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("BTC");
    expect(document.querySelector(".pair-saved-group-count")?.textContent).toBe("1");
  });

  it("saves and adds the queried pair to the floating watch", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHY" +
        "&leg1_raw_symbol=SKHYUSDT&leg1_price_multiplier=1&leg1_contract_size_multiplier=1" +
        "&leg2_exchange=bitget&leg2_market_type=future&leg2_symbol=SKHY" +
        "&leg2_raw_symbol=SKHYNIXUSDT&leg2_price_multiplier=1&leg2_contract_size_multiplier=0.01" +
        "&hours=4&interval_seconds=5"
    );
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    const addButton = screen.getByRole("button", { name: /加入浮窗/ });
    await waitFor(() => expect(addButton.hasAttribute("disabled")).toBe(false));
    await user.click(addButton);

    await waitFor(() => {
      const request = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(
        ([input, init]) => String(input).includes("/settings/floating-watch/items") && init?.method === "POST"
      );
      const mutation = JSON.parse(String(request?.[1]?.body)) as Record<string, unknown>;
      expect(mutation).toEqual({
        action: "add",
        item_type: "pair",
        value: serverPresets[0]?.id
      });
    });
    expect(serverPresets[0]).toMatchObject({
      leg1_raw_symbol: "SKHYUSDT",
      leg1_price_multiplier: 1,
      leg1_contract_size_multiplier: 1,
      leg2_raw_symbol: "SKHYNIXUSDT",
      leg2_price_multiplier: 1,
      leg2_contract_size_multiplier: 0.01
    });
  });

  it("keeps local presets visible when the server sync fails", async () => {
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([savedPairPreset()])
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      })
    );

    render(<PairMonitorPage />);

    expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("BTC");
    expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1);
    expect(await screen.findByText(/已保存标的对同步失败.*offline/)).toBeTruthy();
    expect(window.localStorage.getItem("taoli1.pairSpread.presets.serverMigrated.v1")).toBeNull();
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
    expect((await screen.findAllByText("Bitget · 现货 · SKHYUSDT")).length).toBeGreaterThan(0);
  });

  it("queries Lighter when selected as a spread leg", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getAllByRole("combobox")[0]);
    await user.click(await screen.findByText("Lighter"));
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("leg1_exchange=lighter"))).toBe(true);
    });
  });

  it("queries RH Lighter as an independent spread leg", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getAllByRole("combobox")[0]);
    await user.click(await screen.findByText("RH Lighter"));
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(
        requests.some((request) => request.includes("leg1_exchange=rh-lighter"))
      ).toBe(true);
    });
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

    const savedTag = document.querySelector(".pair-saved-tag") as HTMLElement;
    expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("DEXE");
    expect(savedTag.textContent).not.toContain("DEXE");
    expect(document.querySelector(".pair-exchange-bybit")).toBeTruthy();
    expect(document.querySelector(".pair-exchange-bitget")).toBeTruthy();
  });

  it("uses 5-second pair spread queries by default", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("interval_seconds=5") &&
            request.includes("interval_minutes=1")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("5秒")).length).toBeGreaterThan(0);
  });

  it("merges executable open and close spreads and removes the auxiliary mark row", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    const executable = within(await screen.findByRole("region", { name: "可成交价差" }));
    expect(executable.getByText("开仓")).toBeTruthy();
    expect(executable.getByText("平仓")).toBeTruthy();
    expect(screen.queryByText("盘口中间价差")).toBeNull();
    expect(screen.queryByText("标记价差（辅助）")).toBeNull();
    expect(executable.getByText("+0.80%")).toBeTruthy();
    expect(executable.getByText("+1.20%")).toBeTruthy();
    expect(executable.getByText("0.4000%")).toBeTruthy();
    expect(screen.getByText(/左 ask .*右 bid/)).toBeTruthy();
    expect(screen.getByText(/左 bid .*右 ask/)).toBeTruthy();
  });

  it("renders the three summary cards with round trip, normalized funding and current percentile after a query", async () => {
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/query")) return response;
      const data = await response.json();
      data.current.leg1.funding_interval_hours = 8;
      Object.assign(data.current.leg2, { funding_rate_pct: 0.03, funding_interval_hours: 4 });
      data.points = [
        { bucket_at: "2026-07-24T00:00:00Z", leg1_close: 100, leg2_close: 100, spread_abs: 0, spread_pct: 0 },
        { bucket_at: "2026-07-24T01:00:00Z", leg1_close: 100, leg2_close: 101, spread_abs: 1, spread_pct: 1 }
      ];
      return Response.json(data);
    });
    render(<PairMonitorPage />);
    await userEvent.click(screen.getByRole("button", { name: /查询/ }));

    const executable = within(await screen.findByRole("region", { name: "可成交价差" }));
    const funding = within(screen.getByRole("region", { name: "资金费（左多 / 右空）" }));
    const position = within(screen.getByRole("region", { name: "价差位置（查询区间 4 小时）" }));
    expect(document.querySelectorAll(".pair-focus-metrics > .pair-metric-card")).toHaveLength(5);
    expect(executable.getByText("开平差（来回成本）")).toBeTruthy();
    expect(executable.getByText("0.4000%")).toBeTruthy();
    expect(funding.getByText("净费率 / 8h")).toBeTruthy();
    expect(funding.getByText("+0.0500%").classList.contains("pair-summary-funding-positive")).toBe(true);
    expect(funding.getByText("日化 +0.1500% · 年化 +54.75%")).toBeTruthy();
    expect(funding.getByText(/右 Bitget .*4h/)).toBeTruthy();
    expect(position.getByText("当前分位")).toBeTruthy();
    expect(position.getByText("P50")).toBeTruthy();
    expect(position.getByText("偏离均值 +0.00%")).toBeTruthy();
    expect(position.getByText("近 1h 变化 -0.50%")).toBeTruthy();
    expect(position.getByText("波动 σ +0.50%")).toBeTruthy();
  });

  it("shows exact leg identity, source, multipliers, timestamps, and estimated fields", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    await user.click(screen.getByRole("tab", { name: "市场质量" }));
    expect(await screen.findByText("双腿市场身份与行情质量")).toBeTruthy();
    expect(screen.getByText("来源：Left public order book")).toBeTruthy();
    expect(screen.getByText("来源：Right public order book")).toBeTruthy();
    expect(screen.getByText("估算字段：funding_next_time")).toBeTruthy();
    expect(screen.getAllByText("+0.0500%（估算）")).toHaveLength(2);
    expect(screen.getByText("1x / 0.01")).toBeTruthy();
    expect(screen.getByTitle("07-24 09:59:58")).toBeTruthy();
  });

  it("uses hourly history for query windows longer than 7 days", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const hoursInput = document.querySelector(".pair-query-hours input") as HTMLInputElement;
    fireEvent.change(hoursInput, { target: { value: "200" } });
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      const query = requests
        .map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query"));
      expect(query?.searchParams.get("hours")).toBe("200");
      expect(query?.searchParams.get("interval_seconds")).toBe("3600");
    });
    expect((await screen.findAllByText("1小时")).length).toBeGreaterThan(0);
    expect(hoursInput.value).toBe("200");
  });

  it("uses daily history for query windows longer than one year", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const hoursInput = document.querySelector(".pair-query-hours input") as HTMLInputElement;
    fireEvent.change(hoursInput, { target: { value: "8761" } });
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      const query = requests
        .map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query"));
      expect(query?.searchParams.get("hours")).toBe("8761");
      expect(query?.searchParams.get("interval_seconds")).toBe("86400");
    });
    expect((await screen.findAllByText("1天")).length).toBeGreaterThan(0);
  });

  it("loads same-time day comparison with historical minute queries", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    await user.click(screen.getByRole("switch", { name: /同时段/ }));

    await waitFor(() => {
      expect(requests.filter((request) => request.includes("include_current=false"))).toHaveLength(2);
    });
    const compareRequests = requests
      .map((request) => new URL(request, "http://localhost"))
      .filter((url) => url.searchParams.get("include_current") === "false");
    expect(compareRequests.every((url) => url.searchParams.get("interval_seconds") === "60")).toBe(true);
    expect(compareRequests.every((url) => url.searchParams.get("interval_minutes") === "1")).toBe(true);
    expect(compareRequests.map((url) => url.searchParams.get("end_at") ?? "")[0]).toContain("2026-07-23T02:00:00");
    expect(compareRequests.map((url) => url.searchParams.get("end_at") ?? "")[1]).toContain("2026-07-22T02:00:00");

    await waitFor(() => {
      expect(document.querySelector(".pair-day-compare-chart")).toBeTruthy();
      expect(document.querySelectorAll(".pair-day-compare-line").length).toBeGreaterThan(1);
    });
    const axisLabels = Array.from(document.querySelectorAll(".pair-day-compare-axis-label")).map(
      (element) => element.textContent ?? ""
    );
    expect(axisLabels).toEqual(expect.arrayContaining(["06:00", "07:00", "10:00"]));
    expect(axisLabels).not.toContain("+0h");
    expect(axisLabels).not.toContain("+4h");
    const cachedState = JSON.parse(window.sessionStorage.getItem("taoli1.pairSpread.lastState.v1") ?? "{}");
    expect(cachedState.showDayCompare).toBe(true);
    expect(cachedState.dayCompareDays).toBe(3);
  });

  it("loads same-time comparison for a custom Beijing time window", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    await user.click(screen.getByRole("switch", { name: /同时段/ }));
    await waitFor(() => {
      expect(requests.filter((request) => request.includes("include_current=false"))).toHaveLength(2);
    });
    requests.length = 0;

    await user.click(screen.getByText("指定时间"));
    fireEvent.change(screen.getByLabelText("同时段开始时间"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("同时段结束时间"), { target: { value: "15:00" } });

    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);

    await user.click(screen.getByRole("button", { name: /查询同时段/ }));

    await waitFor(() => {
      expect(requests.filter((request) => request.includes("include_current=false"))).toHaveLength(3);
    });
    const compareRequests = requests
      .map((request) => new URL(request, "http://localhost"))
      .filter((url) => url.searchParams.get("include_current") === "false");
    expect(compareRequests.every((url) => url.searchParams.get("interval_seconds") === "60")).toBe(true);
    expect(compareRequests.every((url) => url.searchParams.get("interval_minutes") === "1")).toBe(true);
    expect(compareRequests.every((url) => url.searchParams.get("hours") === "6")).toBe(true);
    expect(compareRequests.map((url) => url.searchParams.get("end_at"))).toEqual([
      "2026-07-23T07:00:00.000Z",
      "2026-07-22T07:00:00.000Z",
      "2026-07-21T07:00:00.000Z"
    ]);

    await waitFor(() => {
      expect(document.querySelector(".pair-day-compare-chart")).toBeTruthy();
      expect(screen.getByText("09:00-15:00")).toBeTruthy();
    });
    const axisLabels = Array.from(document.querySelectorAll(".pair-day-compare-axis-label")).map(
      (element) => element.textContent ?? ""
    );
    expect(axisLabels).toEqual(expect.arrayContaining(["09:00", "10:00", "15:00"]));
    expect(axisLabels).not.toContain("+0h");
    expect(axisLabels).not.toContain("+6h");
    const cachedState = JSON.parse(window.sessionStorage.getItem("taoli1.pairSpread.lastState.v1") ?? "{}");
    expect(cachedState.dayCompareMode).toBe("custom");
    expect(cachedState.dayCompareStartTime).toBe("09:00");
    expect(cachedState.dayCompareEndTime).toBe("15:00");

    await user.click(screen.getByRole("button", { name: /保存/ }));
    const savedPreset = JSON.parse(window.localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]")[0];
    expect(savedPreset.dayCompareMode).toBe("custom");
    expect(savedPreset.dayCompareStartTime).toBe("09:00");
    expect(savedPreset.dayCompareEndTime).toBe("15:00");
  });

  it("sends a custom second interval when selected", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[comboboxes.length - 1]);
    const customOptions = await screen.findAllByText("自定义");
    await user.click(customOptions[customOptions.length - 1]);
    const customInput = screen.getByRole("spinbutton", { name: /自定义秒/ });
    await user.clear(customInput);
    await user.type(customInput, "7");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("interval_seconds=7"))).toBe(true);
    });
    expect(new URLSearchParams(window.location.search).get("interval_seconds")).toBe("7");
  });

  it("does not auto-query or reset form values when editing hours", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHY" +
        "&leg2_exchange=bitget&leg2_market_type=future&leg2_symbol=SKHYNIX&hours=1&interval_seconds=5"
    );
    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    const leftSymbolInput = screen.getByPlaceholderText("SKHY") as HTMLInputElement;
    await user.clear(leftSymbolInput);
    await user.type(leftSymbolInput, "CXMTUSDT");
    const hoursInput = document.querySelector(".pair-query-hours input") as HTMLInputElement;
    fireEvent.change(hoursInput, { target: { value: "12" } });

    await new Promise((resolve) => window.setTimeout(resolve, 20));

    expect(leftSymbolInput.value).toBe("CXMTUSDT");
    expect(hoursInput.value).toBe("12");
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });

  it("swaps same-symbol legs and immediately queries the reversed spread", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHY" +
        "&leg1_raw_symbol=SKHYUSDT&leg1_price_multiplier=1&leg1_contract_size_multiplier=0.01" +
        "&leg2_exchange=bybit&leg2_market_type=future&leg2_symbol=SKHY" +
        "&leg2_raw_symbol=SKHY-PERP&leg2_price_multiplier=10&leg2_contract_size_multiplier=1" +
        "&hours=4&interval_seconds=60"
    );
    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    await user.click(screen.getByRole("button", { name: "交换左右标的" }));

    await waitFor(() => {
      const swappedRequest = requests
        .map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query") && !url.searchParams.has("include_current"));
      expect(swappedRequest?.searchParams.get("leg1_exchange")).toBe("bybit");
      expect(swappedRequest?.searchParams.get("leg2_exchange")).toBe("bitget");
      expect(swappedRequest?.searchParams.get("leg1_symbol")).toBe("SKHYUSDT");
      expect(swappedRequest?.searchParams.get("leg2_symbol")).toBe("SKHYUSDT");
      expect(swappedRequest?.searchParams.get("leg2_multiplier")).toBe("1");
      const locationParams = new URLSearchParams(window.location.search);
      expect(locationParams.get("leg1_raw_symbol")).toBe("SKHY-PERP");
      expect(locationParams.get("leg1_price_multiplier")).toBe("10");
      expect(locationParams.get("leg1_contract_size_multiplier")).toBe("1");
      expect(locationParams.get("leg2_raw_symbol")).toBe("SKHYUSDT");
      expect(locationParams.get("leg2_price_multiplier")).toBe("1");
      expect(locationParams.get("leg2_contract_size_multiplier")).toBe("0.01");
    });
  });

  it("swaps custom symbols and resets the position-specific multiplier", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHY" +
        "&leg2_exchange=bybit&leg2_market_type=future&leg2_symbol=SKHYNIX&leg2_multiplier=2&hours=4&interval_seconds=60"
    );
    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    await user.click(screen.getByRole("button", { name: "交换左右标的" }));

    await waitFor(() => {
      const swappedRequest = requests
        .map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query") && !url.searchParams.has("include_current"));
      expect(swappedRequest?.searchParams.get("leg1_exchange")).toBe("bybit");
      expect(swappedRequest?.searchParams.get("leg1_symbol")).toBe("SKHYNIXUSDT");
      expect(swappedRequest?.searchParams.get("leg2_exchange")).toBe("bitget");
      expect(swappedRequest?.searchParams.get("leg2_symbol")).toBe("SKHYUSDT");
      expect(swappedRequest?.searchParams.get("leg2_multiplier")).toBe("1");
    });
  });

  it("overwrites older saved configs when only the multiplier changes", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([
        {
          id: "old-config",
          leg1_exchange: "bitget",
          leg1_market_type: "future",
          leg1_symbol: "SKHX",
          leg2_exchange: "bybit",
          leg2_market_type: "future",
          leg2_symbol: "SMSN",
          leg2_multiplier: 1,
          hours: 4,
          intervalSeconds: 60,
          savedAt: "2026-07-23T02:00:00Z"
        },
        {
          id: "new-config",
          leg1_exchange: "bitget",
          leg1_market_type: "future",
          leg1_symbol: "SKHX",
          leg2_exchange: "bybit",
          leg2_market_type: "future",
          leg2_symbol: "SMSN",
          leg2_multiplier: 0.1,
          hours: 12,
          intervalSeconds: 300,
          savedAt: observedAt
        }
      ])
    );
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHX" +
        "&leg2_exchange=bybit&leg2_market_type=future&leg2_symbol=SMSN&leg2_multiplier=0.1&hours=12&interval_seconds=300"
    );
    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    expect(document.querySelectorAll(".pair-saved-group")).toHaveLength(1);
    expect(document.querySelectorAll(".pair-saved-tag")).toHaveLength(1);
    expect(document.querySelector(".pair-saved-tag")?.textContent).toContain("右×0.1");
    const migratedPresets = JSON.parse(window.localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]");
    expect(migratedPresets).toHaveLength(1);
    expect(migratedPresets[0].leg2_multiplier).toBe(0.1);

    await user.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => {
      const savedPresets = JSON.parse(window.localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]");
      expect(savedPresets).toHaveLength(1);
      expect(savedPresets[0].leg2_multiplier).toBe(0.1);
      expect(savedPresets[0].hours).toBe(12);
    });
  });

  it("queries immediately when selecting a saved preset", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([
        {
          id: "cxmt",
          leg1_exchange: "hyperliquid",
          leg1_market_type: "future",
          leg1_symbol: "CXMTUSDT",
          leg2_exchange: "gate",
          leg2_market_type: "future",
          leg2_symbol: "CXMTUSDT",
          leg2_multiplier: 1,
          hours: 12,
          intervalSeconds: 60,
          showDayCompare: true,
          dayCompareDays: 4,
          savedAt: observedAt
        }
      ])
    );
    render(<PairMonitorPage />);
    requests.length = 0;

    const savedTag = document.querySelector(".pair-saved-tag") as HTMLElement;
    expect(document.querySelector(".pair-saved-group-symbol")?.textContent).toBe("CXMT");
    expect(savedTag.textContent).not.toContain("CXMT");
    expect(savedTag.textContent).toContain("hl");
    expect(savedTag.textContent).toContain("gt");
    expect(savedTag.textContent).not.toContain("12小时");
    expect(savedTag.textContent).not.toContain("1分钟");
    expect(document.querySelector(".pair-exchange-hyperliquid")).toBeTruthy();
    expect(document.querySelector(".pair-exchange-gate")).toBeTruthy();
    await user.click(savedTag);

    expect((screen.getByPlaceholderText("SKHY") as HTMLInputElement).value).toBe("CXMTUSDT");
    expect((document.querySelector(".pair-query-hours input") as HTMLInputElement).value).toBe("12");
    expect((screen.getByRole("spinbutton", { name: /同时段对比天数/ }) as HTMLInputElement).value).toBe("4");
    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("/pair-spread/query") &&
            request.includes("leg1_exchange=hyperliquid") &&
            request.includes("leg2_exchange=gate") &&
            request.includes("leg1_symbol=CXMTUSDT") &&
            request.includes("leg2_symbol=CXMTUSDT") &&
            request.includes("hours=12") &&
            request.includes("interval_seconds=60") &&
            !request.includes("include_current=false")
        )
      ).toBe(true);
    });
  });

  it("preserves an embedded Hyperliquid perp DEX symbol such as io:OAI", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=hyperliquid&leg1_market_type=future&leg1_symbol=io:OAI" +
        "&leg2_exchange=bitget&leg2_market_type=future&leg2_symbol=OAI&hours=4&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      const query = requests
        .map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query"));
       expect(query?.searchParams.get("leg1_dex")).toBe("io");
       expect(query?.searchParams.get("leg1_symbol")).toBe("OAI");
       expect(query?.searchParams.get("leg2_symbol")).toBe("OAI");
    });
    expect((await screen.findAllByText("Hyperliquid io · 合约 · OAIUSDT")).length).toBeGreaterThan(0);
    expect(new URLSearchParams(window.location.search).get("leg1_symbol")).toBe("OAIUSDT");
    expect(requests.some((request) => request.includes("/pair-spread/hyperliquid-markets"))).toBe(true);
  });

  it("leaves an unselected Hyperliquid DEX unset for backend alias resolution", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=ANTHROPIC" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_symbol=ANTHROPIC&hours=24&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      const query = requests
        .map((request) => new URL(request, "http://localhost"))
        .find((url) => url.pathname.endsWith("/pair-spread/query"));
      expect(query?.searchParams.get("leg2_dex")).toBeNull();
      expect(query?.searchParams.get("leg2_symbol")).toBe("ANTHROPIC");
    });
  });

  it("selects the unique Hyperliquid DEX for CL when the prior selection was main", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=lighter&leg1_market_type=future&leg1_symbol=XAU" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_dex=main&leg2_symbol=CL" +
        "&hours=4&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(requests.some((request) => {
        const url = new URL(request, "http://localhost");
        return url.pathname.endsWith("/pair-spread/query") &&
          url.searchParams.get("leg2_dex") === "xyz" &&
          url.searchParams.get("leg2_symbol") === "CL";
      })).toBe(true);
    });
    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get("leg2_dex")).toBe("xyz");
    });
    expect(screen.getByText("xyz · XYZ")).toBeTruthy();
  });

  it("saves a changed CL leg with its resolved DEX and no identity from the prior result", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=lighter&leg1_market_type=future&leg1_symbol=XAU" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_dex=main&leg2_symbol=BTC" +
        "&leg2_raw_symbol=BTC&hours=4&interval_seconds=60"
    );
    const user = userEvent.setup();
    render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelector(".pair-chart-card")).toBeTruthy());

    const symbolInput = screen.getByPlaceholderText("SKHYNIX");
    await user.clear(symbolInput);
    await user.type(symbolInput, "CL");
    await user.click(screen.getByRole("button", { name: /保存/ }));

    await waitFor(() => expect(serverPresets).toHaveLength(1));
    expect(serverPresets[0]).toMatchObject({ leg2_symbol: "CL", leg2_dex: "xyz" });
    expect(serverPresets[0].leg2_raw_symbol).toBeUndefined();
    expect(requests.some((request) => request.includes("/pair-spread/query") && request.includes("leg2_symbol=CL"))).toBe(false);
  });

  it("replaces a stale non-main DEX when CL has a unique market match", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=lighter&leg1_market_type=future&leg1_symbol=XAU" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_dex=io&leg2_symbol=CL" +
        "&hours=4&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(requests.some((request) => request.includes("/pair-spread/query") &&
        request.includes("leg2_dex=xyz") && request.includes("leg2_symbol=CL"))).toBe(true);
    });
  });

  it("asks for a DEX when an unselected symbol exists on multiple Hyperliquid DEXs", async () => {
    hyperliquidMarketsResponse[1].assets.push({
      raw_symbol: "io:CL", symbol: "CLUSDT", base: "CL", delisted: false
    });
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=lighter&leg1_market_type=future&leg1_symbol=XAU" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_symbol=CL" +
        "&hours=4&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    expect(await screen.findByText(/CL 同时存在于多个 DEX/)).toBeTruthy();
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });

  it("clears a previous DEX when the Hyperliquid symbol changes", async () => {
    hyperliquidMarketsResponse[0].assets.push({
      raw_symbol: "CL", symbol: "CLUSDT", base: "CL", delisted: false
    });
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=lighter&leg1_market_type=future&leg1_symbol=XAU" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_dex=main&leg2_symbol=BTC" +
        "&hours=4&interval_seconds=60"
    );
    const user = userEvent.setup();
    render(<PairMonitorPage />);
    await waitFor(() => {
      expect(requests.some((request) => request.includes("leg2_symbol=BTC"))).toBe(true);
    });

    const symbolInput = screen.getByPlaceholderText("SKHYNIX");
    await user.clear(symbolInput);
    await user.type(symbolInput, "CL");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText(/CL 同时存在于多个 DEX/)).toBeTruthy();
    expect(requests.some((request) => request.includes("leg2_symbol=CL"))).toBe(false);
  });

  function mockFundingQuery(update: (data: ReturnType<typeof pairSpreadResult>) => void) {
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/query")) return response;
      const data = await response.json() as ReturnType<typeof pairSpreadResult>;
      update(data);
      return Response.json(data);
    });
  }

  async function queryFundingCard(leftMarketType = "future") {
    window.history.pushState({}, "", `/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=${leftMarketType}&leg1_symbol=SKHY&leg2_exchange=bitget&leg2_market_type=future&leg2_symbol=SKHYNIX&hours=4&interval_seconds=5`);
    render(<PairMonitorPage />);
    await userEvent.click(screen.getByRole("button", { name: /查询/ }));
    await waitFor(() => {
      expect(document.querySelectorAll(".pair-funding-diff-card tbody tr.ant-table-row").length).toBeGreaterThan(0);
    });
    return document.querySelector(".pair-funding-diff-card") as HTMLElement;
  }

  function fundingTableCells(card: HTMLElement) {
    return Array.from(card.querySelectorAll("tbody tr.ant-table-row"))
      .map((row) => within(row as HTMLElement).getAllByRole("cell"));
  }

  function summaryMetric(card: HTMLElement, label: string) {
    const labelElement = within(card).getByText(label);
    return labelElement.parentElement!.querySelector(".pair-funding-rate-value") as HTMLElement;
  }

  it("shows missing funding as neutral dashes for a spot left leg and keeps right-leg net rates", async () => {
    mockFundingQuery((data) => {
      data.leg1.market_type = "spot";
      data.funding_history = [
        { exchange: data.leg2.exchange, symbol: data.leg2.symbol, funding_time: "2026-07-24T00:00:00Z", funding_rate_pct: -0.07 },
        { exchange: data.leg2.exchange, symbol: data.leg2.symbol, funding_time: "2026-07-23T16:00:00Z", funding_rate_pct: 0.01 }
      ];
      Object.assign(data, { warnings: ["bitget:SKHYUSDT 历史资金费率失败: timeout"] });
    });
    const card = await queryFundingCard("spot");
    const spotHeaders = within(card).getAllByText("Bitget 现货");
    expect(spotHeaders.length).toBeGreaterThan(0);
    expect(spotHeaders[0].parentElement!.querySelector("small")!.textContent).toBe("SKHYUSDT");
    const rows = fundingTableCells(card);
    expect(rows).toHaveLength(2);
    for (const cells of rows) {
      const missing = within(cells[1]).getByText("—");
      expect(missing.title).toBe("现货无资金费率");
      expect(missing.classList.contains("pair-funding-rate-neutral")).toBe(true);
      expect(cells[3].textContent).toBe(cells[2].textContent);
    }
    expect(summaryMetric(card, "左侧合计").textContent).toBe("—");
    expect(summaryMetric(card, "左侧合计").title).toBe("现货无资金费率");
    expect(summaryMetric(card, "右侧合计").textContent).toBe("-0.0700%");
    expect(summaryMetric(card, "总资金费率差（右-左）").textContent).toBe("-0.0700%");
  });

  it("shows missing funding between 4h settlements against a 1h leg and sums only real records", async () => {
    mockFundingQuery((data) => {
      data.funding_history = [
        { ...data.funding_history[0], funding_time: "2026-07-24T00:00:00Z", funding_rate_pct: 0.04 },
        ...[0, 1, 2].map((hour) => ({
          ...data.funding_history[1],
          funding_time: `2026-07-24T0${hour}:00:00Z`,
          funding_rate_pct: 0.01
        }))
      ];
    });
    const card = await queryFundingCard();
    const rows = fundingTableCells(card);
    expect(rows.map((cells) => cells.slice(1).map((cell) => cell.textContent)))
      .toEqual([["—", "+0.0100%", "+0.0100%"], ["—", "+0.0100%", "+0.0100%"], ["+0.0400%", "+0.0100%", "-0.0300%"]]);
    for (const cells of rows.slice(0, 2)) {
      const missing = within(cells[1]).getByText("—");
      expect(missing.title).toBe("该时点无结算");
      expect(missing.classList.contains("pair-funding-rate-neutral")).toBe(true);
    }
    expect(summaryMetric(card, "左侧合计").textContent).toBe("+0.0400%");
    expect(summaryMetric(card, "右侧合计").textContent).toBe("+0.0300%");
    expect(summaryMetric(card, "总资金费率差（右-左）").textContent).toBe("-0.0100%");
    expect(within(card).getByText("计入点数").parentElement!.textContent).toBe("计入点数3 条");
  });

  it("shows a funding warning and failure titles only for the failed leg", async () => {
    const warning = "bitget:SKHYUSDT 历史资金费率失败: timeout";
    mockFundingQuery((data) => {
      data.funding_history = data.funding_history.filter((point) => point.symbol === data.leg2.symbol);
      Object.assign(data, { warnings: [warning, "当前行情失败"] });
    });
    const card = await queryFundingCard();
    const alert = within(card).getByRole("alert");
    expect(alert.classList.contains("ant-alert-warning")).toBe(true);
    expect(alert.textContent).toContain(warning);
    expect(alert.textContent).toContain("合计和净费率只包含已取到的数据");
    expect(alert.textContent).not.toContain("当前行情失败");
    for (const cells of fundingTableCells(card)) {
      expect(within(cells[1]).getByText("—").title).toBe("该腿费率取数失败");
    }
    expect(summaryMetric(card, "左侧合计").textContent).toBe("—");
    expect(document.querySelector(".pair-warning-summary")!.textContent).toContain(warning);
  });

  it("uses custom-range failure titles and the existing alert without duplicate warnings", async () => {
    const card = await queryFundingCard();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    const warning = "bitget:SKHYUSDT 历史资金费率失败: range timeout";
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/funding-history")) return response;
      const data = await response.json();
      data.funding_history = data.funding_history.filter((point: { symbol: string }) => point.symbol === data.leg2.symbol);
      data.warnings = [warning];
      return Response.json(data);
    });
    fireEvent.change(screen.getByLabelText("资金费率累计开始时间"), { target: { value: "2026-07-24T07:30" } });
    fireEvent.change(screen.getByLabelText("资金费率累计结束时间"), { target: { value: "2026-07-24T08:30" } });
    await userEvent.click(screen.getByRole("button", { name: /确定资金费率累计时间/ }));
    await waitFor(() => expect(within(card).getByRole("alert").textContent).toContain(warning));
    expect(within(card).getAllByRole("alert")).toHaveLength(1);
    expect(within(card).getByRole("alert").textContent).toContain("合计和净费率只包含已取到的数据");
    expect(within(fundingTableCells(card)[0][1]).getByText("—").title).toBe("该腿费率取数失败");
    await userEvent.click(screen.getByRole("button", { name: "清空时间" }));
    expect(within(card).queryByRole("alert")).toBeNull();
    expect(fundingTableCells(card)[0][1].textContent).toBe("-0.1000%");
  });

  async function applyFundingTestRange() {
    fireEvent.change(screen.getByLabelText("资金费率累计开始时间"), { target: { value: "2026-07-24T07:30" } });
    fireEvent.change(screen.getByLabelText("资金费率累计结束时间"), { target: { value: "2026-07-24T09:30" } });
    await userEvent.click(screen.getByRole("button", { name: /确定资金费率累计时间/ }));
  }

  it.each(["HTTP 503", "network error"])("marks both legs failed after a funding range %s and clears failure on retry", async (failure) => {
    const card = await queryFundingCard();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    let shouldFail = true;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (!String(input).includes("/pair-spread/funding-history")) return fallback(input, init);
      if (shouldFail) {
        if (failure === "network error") throw new TypeError("Failed to fetch");
        return Response.json({ detail: "Funding service unavailable" }, { status: 503 });
      }
      const response = await fallback(input, init);
      const data = await response.json();
      data.funding_history = [
        { exchange: data.leg1.exchange, symbol: data.leg1.symbol, funding_time: "2026-07-24T00:00:00Z", funding_rate_pct: 0.01 },
        { exchange: data.leg2.exchange, symbol: data.leg2.symbol, funding_time: "2026-07-24T01:00:00Z", funding_rate_pct: 0.02 }
      ];
      return Response.json(data);
    });

    await applyFundingTestRange();
    const errorMessage = failure === "network error" ? "Failed to fetch" : "Funding service unavailable";
    expect(await within(card).findByText(errorMessage)).toBeTruthy();
    for (const label of ["左侧合计", "右侧合计"]) {
      expect(summaryMetric(card, label).textContent).toBe("—");
      expect(summaryMetric(card, label).title).toBe("该腿费率取数失败");
    }

    shouldFail = false;
    await userEvent.click(screen.getByRole("button", { name: /确定资金费率累计时间/ }));
    await waitFor(() => expect(fundingTableCells(card)).toHaveLength(2));
    expect(within(card).queryByRole("alert")).toBeNull();
    const rows = fundingTableCells(card);
    expect(within(rows[0][1]).getByText("—").title).toBe("该时点无结算");
    expect(within(rows[1][2]).getByText("—").title).toBe("该时点无结算");
    expect(summaryMetric(card, "左侧合计").textContent).toBe("+0.0100%");
    expect(summaryMetric(card, "右侧合计").textContent).toBe("+0.0200%");
  });

  it.each(["left", "right"] as const)("keeps the spot %s leg title when the funding range request fails", async (spotSide) => {
    mockFundingQuery((data) => {
      data[spotSide === "left" ? "leg1" : "leg2"].market_type = "spot";
    });
    const card = await queryFundingCard(spotSide === "left" ? "spot" : "future");
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (!String(input).includes("/pair-spread/funding-history")) return fallback(input, init);
      return Response.json({ detail: "Funding service unavailable" }, { status: 503 });
    });

    await applyFundingTestRange();
    expect(await within(card).findByText("Funding service unavailable")).toBeTruthy();
    expect(summaryMetric(card, spotSide === "left" ? "左侧合计" : "右侧合计").title).toBe("现货无资金费率");
    expect(summaryMetric(card, spotSide === "left" ? "右侧合计" : "左侧合计").title).toBe("该腿费率取数失败");
  });

  it("clears both funding range failure titles when the time range is cleared", async () => {
    mockFundingQuery((data) => {
      data.funding_history = [
        { ...data.funding_history[0], funding_time: "2026-07-24T00:00:00Z" },
        { ...data.funding_history[1], funding_time: "2026-07-24T01:00:00Z" }
      ];
    });
    const card = await queryFundingCard();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (!String(input).includes("/pair-spread/funding-history")) return fallback(input, init);
      throw new TypeError("Failed to fetch");
    });

    await applyFundingTestRange();
    expect(await within(card).findByText("Failed to fetch")).toBeTruthy();
    expect(summaryMetric(card, "左侧合计").title).toBe("该腿费率取数失败");
    expect(summaryMetric(card, "右侧合计").title).toBe("该腿费率取数失败");

    await userEvent.click(screen.getByRole("button", { name: "清空时间" }));
    expect(within(card).queryByRole("alert")).toBeNull();
    const rows = fundingTableCells(card);
    expect(rows).toHaveLength(2);
    expect(within(rows[0][1]).getByText("—").title).toBe("该时点无结算");
    expect(within(rows[1][2]).getByText("—").title).toBe("该时点无结算");
  });

  it.each(["left", "right"] as const)("marks only the %s leg failed for a funding range warning", async (failedSide) => {
    const card = await queryFundingCard();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/funding-history")) return response;
      const data = await response.json();
      const leg = data[failedSide === "left" ? "leg1" : "leg2"];
      data.funding_history = [];
      data.warnings = [`${leg.exchange}:${leg.symbol} 历史资金费率失败: range timeout`];
      return Response.json(data);
    });

    await applyFundingTestRange();
    expect(await within(card).findByText(/历史资金费率失败: range timeout/)).toBeTruthy();
    expect(summaryMetric(card, failedSide === "left" ? "左侧合计" : "右侧合计").title).toBe("该腿费率取数失败");
    expect(summaryMetric(card, failedSide === "left" ? "右侧合计" : "左侧合计").title).toBe("该时点无结算");
  });

  it("keeps zero and both populated funding legs as numeric values", async () => {
    mockFundingQuery((data) => {
      data.funding_history = data.funding_history.slice(0, 2).map((point, index) => ({
        ...point, funding_rate_pct: index === 0 ? 0 : 0.02
      }));
    });
    const card = await queryFundingCard();
    const cells = fundingTableCells(card)[0];
    expect(cells.slice(1).map((cell) => cell.textContent)).toEqual(["+0.0000%", "+0.0200%", "+0.0200%"]);
    expect(summaryMetric(card, "左侧合计").textContent).toBe("+0.0000%");
    expect(cells[1].querySelector("span")!.title).toBe("");
  });

  it.each([
    ["bitget:BTCUSDT 历史资金费率失败: timeout", { exchange: "bitget", symbol: "BTCUSDT" }, true],
    ["bitget:BTCUSDT 历史资金费率失败: timeout", { exchange: "bybit", symbol: "BTCUSDT" }, false],
    ["bitget:BTCUSDT2 历史资金费率失败: timeout", { exchange: "bitget", symbol: "BTCUSDT" }, false],
    ["hyperliquid:xyz:BTCUSDT 历史资金费率失败: timeout", { exchange: "hyperliquid", dex: "xyz", symbol: "BTCUSDT" }, true],
    ["hyperliquid:xyz:BTCUSDT 历史资金费率失败: timeout", { exchange: "hyperliquid", dex: "io", symbol: "BTCUSDT" }, false],
    ["hyperliquid:xyz:BTCUSDT 历史资金费率失败: timeout", { exchange: "hyperliquid", symbol: "BTCUSDT" }, false],
    ["hyperliquid:xyz:xyz:BTC 历史资金费率失败: timeout", { exchange: "hyperliquid", dex: "xyz", symbol: "xyz:BTC" }, true],
    ["bitget:BTCUSDT 当前行情失败: timeout", { exchange: "bitget", symbol: "BTCUSDT" }, false],
    ["upstream: bitget:BTCUSDT 历史资金费率失败: timeout", { exchange: "bitget", symbol: "BTCUSDT" }, false]
  ])("matches funding failure warnings by exact leg identity: %s / %j", (warning, leg, expected) => {
    expect(fundingHistoryFailedForLeg([warning], { ...leg, market_type: "future" })).toBe(expected);
  });

  it.each(["left", "right"] as const)("preserves a missing %s rate in current, realtime and minute rows", (missingSide) => {
    const data = pairSpreadResult() as PairSpreadQueryResult;
    data.funding_history = [];
    data.realtime_funding = [];
    data.current![missingSide === "left" ? "leg1" : "leg2"].funding_rate_pct = null;
    const expected = { left_rate_pct: missingSide === "left" ? null : 0.01, right_rate_pct: missingSide === "right" ? null : 0.01, net_rate_pct: missingSide === "left" ? 0.01 : -0.01 };
    expect(buildFundingRateDiffRows(data)[0]).toMatchObject(expected);
    expect(buildRealtimeFundingRateDiffRows(data)[0]).toMatchObject(expected);
    const sample = { bucket_at: observedAt, ...expected, source: "current" };
    data.realtime_funding = [sample];
    expect(buildRealtimeFundingRateDiffRows(data)[0]).toMatchObject(expected);
    const status: PairSpreadFundingRecordStatus = { ...pairFundingRecordStatus(true), item: null, samples: [sample] };
    expect(buildMinuteFundingRateDiffRows(status)[0]).toMatchObject(expected);
  });

  it("leaves the net rate null when both sample legs are missing", () => {
    const sample = { bucket_at: observedAt, left_rate_pct: null, right_rate_pct: null, net_rate_pct: 0, source: "current" };
    const data = { ...(pairSpreadResult() as PairSpreadQueryResult), realtime_funding: [sample] };
    expect(buildRealtimeFundingRateDiffRows(data)[0]).toMatchObject({ left_rate_pct: null, right_rate_pct: null, net_rate_pct: null });
    expect(buildMinuteFundingRateDiffRows({ ...pairFundingRecordStatus(true), item: null, samples: [sample] })[0])
      .toMatchObject({ left_rate_pct: null, right_rate_pct: null, net_rate_pct: null });
  });

  it("shows the funding rate difference table", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHY" +
        "&leg2_exchange=bybit&leg2_market_type=future&leg2_symbol=SKHY&hours=4&interval_seconds=5"
    );
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByRole("heading", { name: "资金费率差" })).toBeTruthy();
    expect(screen.getAllByText("净费率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.0300%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.1000%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.0000%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bitget 费率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bybit 费率").length).toBeGreaterThan(0);
    expect(screen.getByText("总资金费率差（右-左）")).toBeTruthy();
    const summaryPanel = document.querySelector(".pair-funding-summary-panel") as HTMLElement;
    expect(summaryPanel.textContent).toContain("+0.0300%");
    expect(summaryPanel.textContent).toContain("当前周期");
    expect(summaryPanel.textContent).toContain("1 条");
    const fundingLayout = Array.from(document.querySelector(".pair-funding-grid")?.children ?? []).map(
      (element) => (element as HTMLElement).className
    );
    expect(fundingLayout[0]).toContain("pair-funding-diff-card");
    expect(fundingLayout).toHaveLength(1);
    expect(await screen.findByText("未记录分钟费率")).toBeTruthy();
    expect(screen.getByRole("button", { name: /开始记录/ })).toBeTruthy();
    expect(document.querySelector(".pair-funding-diff-chart")).toBeNull();
    expect(document.querySelector(".pair-funding-raw-card")).toBeNull();
    const pageText = document.body.textContent ?? "";
    expect(pageText).not.toContain("分钟资金费率差图");
    const pageLayout = Array.from(document.querySelector(".pair-monitor-page")?.children ?? []).map((element) =>
      (element as HTMLElement).className.toString()
    );
    const focusBlockIndex = pageLayout.findIndex((className) => className.includes("pair-focus-block"));
    const comparisonToolbarIndex = pageLayout.findIndex((className) => className.includes("pair-comparison-panel"));
    const priceChartIndex = pageLayout.findIndex((className) => className.includes("pair-price-card"));
    const fundingGridIndex = pageLayout.findIndex((className) => className.includes("pair-detail-tabs"));
    expect(focusBlockIndex).toBeGreaterThan(-1);
    expect(document.querySelector(".pair-focus-block .pair-chart-card")).toBeTruthy();
    expect(comparisonToolbarIndex).toBeGreaterThan(focusBlockIndex);
    expect(priceChartIndex).toBe(comparisonToolbarIndex + 1);
    expect(fundingGridIndex).toBeGreaterThan(priceChartIndex);
    await user.click(screen.getByRole("tab", { name: "持仓与 OI" }));
    expect(screen.getByText("OI变化量（USDT）")).toBeTruthy();
    expect(document.querySelector(".pair-detail-tabs .ant-tabs-tabpane-active .pair-oi-chart")).toBeTruthy();
    expect(pageText.indexOf("标的价格")).toBeLessThan(pageText.indexOf("资金费率差"));

    await user.click(screen.getByRole("tab", { name: "资金费率差" }));
    const restoredSummaryPanel = document.querySelector(".pair-funding-summary-panel") as HTMLElement;
    requests.length = 0;
    fireEvent.change(screen.getByLabelText("资金费率累计开始时间"), { target: { value: "2026-07-23T15:30" } });
    fireEvent.change(screen.getByLabelText("资金费率累计结束时间"), { target: { value: "2026-07-24T08:30" } });
    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(restoredSummaryPanel.textContent).toContain("+0.0300%");
    expect(restoredSummaryPanel.textContent).toContain("当前周期");
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);

    await user.click(screen.getByRole("button", { name: /确定资金费率累计时间/ }));
    await waitFor(() => {
      expect(restoredSummaryPanel.textContent).toContain("+0.1200%");
      expect(restoredSummaryPanel.textContent).toContain("指定时间");
      expect(restoredSummaryPanel.textContent).toContain("3 条");
    });
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });

  it("runs a saved threshold diagnostic on demand", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await user.click(screen.getByRole("tab", { name: "诊断" }));
    await user.click(await screen.findByRole("button", { name: /诊断监控/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("/pair-spread/diagnostics"))).toBe(true);
    });
    expect(await screen.findByText("历史峰值")).toBeTruthy();
    expect(screen.getByText("+11.23%")).toBeTruthy();
    expect(screen.getByText("已静默")).toBeTruthy();

    const thresholdInput = screen.getByRole("spinbutton", { name: /监控诊断阈值/ });
    await user.clear(thresholdInput);
    await user.type(thresholdInput, "2.5");
    await user.click(screen.getByRole("button", { name: /记住阈值/ }));
    expect(window.localStorage.getItem("taoli1.pairSpread.diagnosticThreshold.v1")).toBe("2.5");
  });

  it("starts minute funding recording before showing the funding rate difference chart", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await user.click(await screen.findByRole("button", { name: /开始记录/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("/pair-spread/funding-records/watch"))).toBe(true);
    });
    expect(await screen.findByText("分钟资金费率差图")).toBeTruthy();
    expect(screen.getByText("分钟记录样本")).toBeTruthy();
    expect(screen.getByText("分钟记录已开启")).toBeTruthy();
    expect(document.querySelector(".pair-funding-diff-chart")).toBeTruthy();
    expect(document.querySelector(".pair-minute-funding-point")).toBeTruthy();
    expect(document.querySelector(".pair-minute-funding-turning-dot")).toBeTruthy();
    expect((document.querySelector(".pair-funding-diff-chart .pair-funding-chart-line") as SVGPathElement).getAttribute("d")).toContain("L ");
  });

  it("uses the realtime current spread as the chart tail point", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=aster&leg1_market_type=future&leg1_symbol=TAIL" +
        "&leg2_exchange=gate&leg2_market_type=future&leg2_symbol=TAIL&hours=6&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-current-point")).toBeTruthy();
    });
    const currentPointTitle = document.querySelector(".pair-chart-current-point title")?.textContent ?? "";
    expect(currentPointTitle).toContain("-0.06%");
    expect((document.querySelector(".pair-spread-chart .pair-chart-line") as SVGPathElement).getAttribute("d")).toContain(
      "L "
    );
  });

  it("shows the nearest point time and spread details when hovering the spread chart", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=aster&leg1_market_type=future&leg1_symbol=TAIL" +
        "&leg2_exchange=gate&leg2_market_type=future&leg2_symbol=TAIL&hours=6&interval_seconds=60"
    );
    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-hover-target")).toBeTruthy();
    });
    const chart = document.querySelector(".pair-spread-chart") as SVGSVGElement;
    const hoverTarget = document.querySelector(".pair-chart-hover-target") as SVGRectElement;
    vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({
      bottom: 330,
      height: 330,
      left: 0,
      right: 1180,
      top: 0,
      width: 1180,
      x: 0,
      y: 0,
      toJSON: () => ({})
    });

    fireEvent.mouseMove(hoverTarget, { clientX: 1148, clientY: 160 });

    expect(document.querySelector(".pair-chart-hover-crosshair")).toBeTruthy();
    expect(document.querySelector(".pair-chart-hover-point")).toBeTruthy();
    const tooltip = document.querySelector(".pair-chart-hover-detail")?.textContent ?? "";
    expect(tooltip).toContain("07-24 10:00");
    expect(tooltip).toContain("-0.06%");
    expect(tooltip).toContain("-0.06");
    expect(tooltip).toContain("100");
    expect(tooltip).toContain("99.94");

    fireEvent.mouseLeave(hoverTarget);
    expect(document.querySelector(".pair-chart-hover-detail")).toBeNull();
  });

  it.each([false, true])("maps spread chart gaps and hover to real time, ratio mode=%s", async (ratioMode) => {
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const response = await fallback(input, init);
      if (!String(input).includes("/pair-spread/query")) return response;
      const data = await response.json();
      const left = ratioMode ? 4000 : 100;
      const right = ratioMode ? 100 : 101;
      Object.assign(data.current.leg1, { price: left, mid_price: left });
      Object.assign(data.current.leg2, { price: right, mid_price: right });
      data.points = [0, 1, 2, 9, 10].map((minutes, index) => {
        const historicalRight = right + index * 0.1;
        return {
          bucket_at: new Date(Date.parse(observedAt) - (12 - minutes) * 60_000).toISOString(),
          leg1_close: left,
          leg2_close: historicalRight,
          spread_abs: historicalRight - left,
          spread_pct: (historicalRight - left) / ((left + historicalRight) / 2) * 100
        };
      });
      return Response.json(data);
    });
    window.history.pushState({}, "", "/?page=pair-monitor&leg1_exchange=aster&leg1_market_type=future&leg1_symbol=TIME&leg2_exchange=gate&leg2_market_type=future&leg2_symbol=TIME&hours=6&interval_seconds=60");
    render(<PairMonitorPage />);

    const chart = await screen.findByRole("img", { name: ratioMode ? "价格比值曲线" : "均值价差率曲线" });
    const left = ratioMode ? 78 : 56;
    const plotWidth = 1180 - left - 28;
    const xAtMinute = (minute: number) => left + plotWidth * minute / 12;
    const line = chart.querySelector(".pair-chart-line")!.getAttribute("d")!;
    const vertices = Array.from(line.matchAll(/([ML]) ([\d.-]+) ([\d.-]+)/g));
    expect(vertices.map((vertex) => vertex[1])).toEqual(["M", "L", "L", "M", "L", "L"]);
    [0, 1, 2, 9, 10, 12].forEach((minute, index) => {
      expect(Number(vertices[index][2])).toBeCloseTo(xAtMinute(minute), 2);
    });
    const area = chart.querySelector(".pair-chart-area")!.getAttribute("d")!;
    expect(area.match(/M /g)).toHaveLength(2);
    expect(area.match(/Z/g)).toHaveLength(2);
    const gap = chart.querySelector(".pair-chart-gap")!;
    expect(Number(gap.getAttribute("x"))).toBeCloseTo(xAtMinute(2));
    expect(Number(gap.getAttribute("width"))).toBeCloseTo(plotWidth * 7 / 12);
    expect(Number(chart.querySelector(".pair-chart-current-point")!.getAttribute("cx"))).toBe(1152);

    // Render at half the SVG viewBox width, offset from the viewport origin.
    vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({
      bottom: 185, height: 165, left: 35, right: 625, top: 20, width: 590,
      x: 35, y: 20, toJSON: () => ({})
    });
    const hoverTarget = chart.querySelector(".pair-chart-hover-target")!;
    for (const [fraction, minute, expectedTime] of [[0.25, 2, "09:50"], [0.7, 9, "09:57"]] as const) {
      fireEvent.mouseMove(hoverTarget, { clientX: 35 + (left + plotWidth * fraction) / 2, clientY: 100 });
      expect(chart.querySelector(".pair-chart-hover-tooltip-time")?.textContent).toContain(expectedTime);
      expect(Number(chart.querySelector(".pair-chart-hover-crosshair")!.getAttribute("x1"))).toBeCloseTo(xAtMinute(minute));
      expect(Number(chart.querySelector(".pair-chart-hover-point")!.getAttribute("cx"))).toBeCloseTo(xAtMinute(minute));
      expect(chart.querySelector(".pair-chart-hover-tooltip-rate")?.textContent?.includes("%")).toBe(!ratioMode);
    }
    fireEvent.mouseLeave(hoverTarget);
    expect(chart.querySelector(".pair-chart-hover-detail")).toBeNull();
  });

  it.each(["Escape", "清除"])("只在松开拖选后请求区间回测，%s取消选区和结果", async (cancel) => {
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    const backtests: RangeBacktestRequest[] = [];
    let backtestSignal: AbortSignal | null | undefined;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input).includes("/pair-spread/range-backtest")) {
        const request = JSON.parse(String(init?.body)) as RangeBacktestRequest;
        backtests.push(request);
        backtestSignal = init?.signal;
        return Response.json(rangeBacktestFixture(request.leg1, request.leg2, request));
      }
      return fallback(input, init);
    });
    window.history.pushState({}, "", "/?page=pair-monitor&leg1_exchange=aster&leg1_market_type=future&leg1_symbol=TAIL&leg2_exchange=gate&leg2_market_type=future&leg2_symbol=TAIL&hours=6&interval_seconds=60");
    render(<PairMonitorPage />);
    const chart = await screen.findByRole("img", { name: "均值价差率曲线" });
    vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({
      bottom: 330, height: 330, left: 0, right: 1180, top: 0, width: 1180,
      x: 0, y: 0, toJSON: () => ({})
    });
    const target = chart.querySelector(".pair-chart-hover-target")!;
    fireEvent.mouseDown(target, { button: 0, clientX: 56 });
    fireEvent.mouseMove(target, { buttons: 1, clientX: 1152 });
    expect(backtests).toHaveLength(0);
    fireEvent.mouseUp(window, { button: 0, clientX: 1152 });
    await screen.findByText("+56.9000 美元");
    expect(backtests).toEqual([{
      leg1: expect.objectContaining({ exchange: "aster", symbol: "TAILUSDT", market_type: "future" }),
      leg2: expect.objectContaining({ exchange: "gate", symbol: "TAILUSDT", market_type: "future" }),
      start_at: "2026-07-24T01:58:00.000Z", end_at: "2026-07-24T02:00:00.000Z",
      direction: "long1_short2", notional_usd: 1000
    }]);
    if (cancel === "Escape") fireEvent.keyDown(window, { key: "Escape" });
    else fireEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(chart.querySelector(".pair-chart-range-selection")).toBeNull();
    expect(screen.queryByRole("region", { name: "区间回测结果" })).toBeNull();
    expect(backtestSignal?.aborted).toBe(true);
    expect(backtests).toHaveLength(1);
  });

  it("auto-runs a Binance Alpha spread query from URL parameters", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=AKEUSDT" +
        "&leg2_exchange=binance_alpha&leg2_market_type=spot&leg2_symbol=ALPHA_331USDT" +
        "&leg2_multiplier=1&hours=4&interval_seconds=5"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("leg1_exchange=binance") &&
            request.includes("leg2_exchange=binance_alpha") &&
            request.includes("leg2_symbol=ALPHA_331USDT") &&
            request.includes("interval_seconds=5") &&
            request.includes("interval_minutes=1") &&
            request.includes("hours=4")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("Binance Alpha · 现货 · ALPHA_331USDT")).length).toBeGreaterThan(0);
  });

  it("switches the price chart to indexed trend view when leg prices are far apart", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=hyperliquid&leg1_market_type=future&leg1_symbol=SKHXUSDT" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_symbol=SKHYUSDT" +
        "&leg2_multiplier=0.1&hours=6&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    expect(await screen.findByText("自动：相对走势 · 首点=100")).toBeTruthy();
    expect(screen.getByText(/原始最新价：左 1218.60 \/ 右 1618.30/)).toBeTruthy();
  });

  it("opens the premium index page for a futures leg from the spread result", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    const premiumButtons = await screen.findAllByRole("button", { name: /查看溢价指数/ });
    await user.click(premiumButtons[0]);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("premium-index");
    expect(params.get("from")).toBe("pair-monitor");
    expect(params.get("exchange")).toBe("bitget");
    expect(params.get("symbol")).toBe("SKHYUSDT");
    expect(params.get("leg1_exchange")).toBe("bitget");
    expect(params.get("leg1_market_type")).toBe("future");
    expect(params.get("leg1_symbol")).toBe("SKHYUSDT");
    expect(params.get("leg2_exchange")).toBe("bitget");
    expect(params.get("leg2_market_type")).toBe("future");
    expect(params.get("leg2_symbol")).toBe("SKHYUSDT");
    expect(params.get("leg2_multiplier")).toBe("1");
    expect(params.get("hours")).toBe("4");
    expect(params.get("interval_minutes")).toBe("1");
    expect(params.get("interval_seconds")).toBe("5");
  });

  it("restores and revalidates the last spread result after returning to a URL without pair parameters", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    expect((await screen.findAllByText("Bitget · 合约 · SKHYUSDT")).length).toBeGreaterThan(0);

    unmount();
    const cachedState = JSON.parse(
      window.sessionStorage.getItem("taoli1.pairSpread.lastState.v1") ?? "null"
    );
    cachedState.result.current.leg2.volume_24h_usdt = 10_640.236;
    window.sessionStorage.setItem("taoli1.pairSpread.lastState.v1", JSON.stringify(cachedState));
    window.history.pushState({}, "", "/?page=pair-monitor");
    requests.length = 0;

    render(<PairMonitorPage />);

    expect(screen.getByText("10.6K")).toBeTruthy();
    expect(screen.getAllByText("+0.50%").length).toBeGreaterThan(0);
    expect((screen.getByPlaceholderText("SKHY") as HTMLInputElement).value).toBe("SKHYUSDT");
    expect(await screen.findByText("2.24M")).toBeTruthy();
    expect(screen.queryByText("10.6K")).toBeNull();
    const spreadRequests = requests.filter((request) => request.includes("/pair-spread/query"));
    expect(spreadRequests).toHaveLength(1);
    const params = new URLSearchParams(window.location.search);
    expect(params.get("leg1_exchange")).toBe("bitget");
    expect(params.get("leg2_exchange")).toBe("bitget");
    expect(params.get("leg1_symbol")).toBe("SKHYUSDT");
    expect(params.get("leg2_symbol")).toBe("SKHYUSDT");
  });

  it("revalidates a cached spread result when reloading the same pair monitor URL", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHYUSDT" +
        "&leg2_exchange=okx&leg2_market_type=future&leg2_symbol=SKHYNIXUSDT" +
        "&leg2_multiplier=1&hours=4&interval_seconds=5&interval_minutes=1"
    );
    const params = new URLSearchParams(window.location.search);
    const cachedResult = pairSpreadResult(params);
    if (!cachedResult.current) {
      throw new Error("Expected the cached result to include current prices");
    }
    cachedResult.current.leg2.volume_24h_usdt = 10_640.236;
    window.sessionStorage.setItem(
      "taoli1.pairSpread.lastState.v1",
      JSON.stringify({
        values: {
          leg1_exchange: "bitget",
          leg1_market_type: "future",
          leg1_dex: "",
          leg1_symbol: "SKHYUSDT",
          leg2_exchange: "okx",
          leg2_market_type: "future",
          leg2_dex: "",
          leg2_symbol: "SKHYNIXUSDT",
          leg2_multiplier: 1
        },
        hours: 4,
        intervalSeconds: 5,
        showDayCompare: false,
        dayCompareDays: 3,
        dayCompareMode: "query",
        dayCompareStartTime: "",
        dayCompareEndTime: "",
        result: cachedResult,
        savedAt: "2026-07-24T01:59:00Z"
      })
    );

    render(<PairMonitorPage />);

    expect(screen.getByText("10.6K")).toBeTruthy();
    expect(await screen.findByText("2.24M")).toBeTruthy();
    expect(screen.queryByText("10.6K")).toBeNull();
    const spreadRequests = requests.filter((request) => request.includes("/pair-spread/query"));
    expect(spreadRequests).toHaveLength(1);
  });
});
