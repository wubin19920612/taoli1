import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FloatingWatchPanel } from "../src/components/FloatingWatchPanel";

const preset = {
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
  savedAt: "2026-09-13T01:00:00Z"
};

const instrument = {
  query: "BTCUSDT",
  symbol: "BTCUSDT",
  base: "BTC",
  quote: "USDT",
  observed_at: "2026-09-13T01:00:00Z",
  exchange_count: 2,
  market_count: 2,
  exchanges: [
    {
      exchange: "binance",
      spot: null,
      future: {
        symbol: "BTCUSDT",
        base: "BTC",
        quote: "USDT",
        exchange: "binance",
        market_type: "future",
        bid: 99,
        ask: 101,
        timestamp: "2026-09-13T01:00:00Z",
        raw_symbol: "BTCUSDT"
      },
      error: null
    },
    {
      exchange: "okx",
      spot: null,
      future: {
        symbol: "BTCUSDT",
        base: "BTC",
        quote: "USDT",
        exchange: "okx",
        market_type: "future",
        bid: 101,
        ask: 103,
        timestamp: "2026-09-13T01:00:00Z",
        raw_symbol: "BTC-USDT-SWAP"
      },
      error: null
    }
  ],
  spreads: [
    {
      id: "first",
      buy_exchange: "binance",
      buy_market_type: "future",
      buy_ask: 101,
      sell_exchange: "okx",
      sell_market_type: "future",
      sell_bid: 101,
      price_difference: 0,
      executable_spread_pct: 0.1,
      mid_spread_pct: 1,
      opportunity_type: "FF",
      astro_supported: true,
      astro_blocker: null
    },
    {
      id: "best",
      buy_exchange: "binance",
      buy_market_type: "future",
      buy_ask: 101,
      sell_exchange: "okx",
      sell_market_type: "future",
      sell_bid: 102,
      price_difference: 1,
      executable_spread_pct: 0.99,
      mid_spread_pct: 1,
      opportunity_type: "FF",
      astro_supported: true,
      astro_blocker: null
    }
  ]
};

const pairResult = {
  leg1: { exchange: "binance", symbol: "BTCUSDT", market_type: "future" },
  leg2: { exchange: "okx", symbol: "BTCUSDT", market_type: "future" },
  hours: 1,
  interval_minutes: 1,
  interval_seconds: 5,
  leg2_multiplier: 1,
  observed_at: "2026-09-13T01:00:00Z",
  point_count: 0,
  first_seen_at: null,
  last_seen_at: null,
  spread_abs: { min: null, max: null, mean: null, current: 2 },
  spread_pct: { min: null, max: null, mean: null, current: 2 },
  current: {
    observed_at: "2026-09-13T01:00:00Z",
    leg1: { exchange: "binance", symbol: "BTCUSDT", market_type: "future", raw_symbol: "BTCUSDT", price: 100, price_field: "mid_price" },
    leg2: { exchange: "okx", symbol: "BTCUSDT", market_type: "future", raw_symbol: "BTC-USDT-SWAP", price: 102, price_field: "mid_price" },
    spread_abs: 2,
    spread_pct: 2,
    open_spread_abs: 1.8,
    open_spread_pct: 1.8,
    close_spread_abs: 2.2,
    close_spread_pct: 2.2,
    mark_spread_abs: null,
    mark_spread_pct: null
  },
  points: [],
  funding_history: [],
  warnings: []
};

describe("FloatingWatchPanel", () => {
  beforeEach(() => {
    vi.useRealTimers();
    window.localStorage.clear();
    window.history.replaceState({}, "", "/?page=dashboard");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024, writable: true });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 768, writable: true });
    let settings = { symbols: ["BTCUSDT"], pair_ids: [preset.id] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch/items") && init?.method === "POST") {
        const mutation = JSON.parse(String(init.body)) as {
          action: "add" | "remove";
          item_type: "symbol" | "pair";
          value: string;
        };
        const key = mutation.item_type === "symbol" ? "symbols" : "pair_ids";
        settings = {
          ...settings,
          [key]: mutation.action === "add"
            ? Array.from(new Set([...settings[key], mutation.value]))
            : settings[key].filter((item) => item !== mutation.value)
        };
        return Response.json(settings);
      }
      if (url.includes("/settings/floating-watch")) return Response.json(settings);
      if (url.includes("/pair-spread/presets")) return Response.json([preset]);
      if (url.includes("/pair-spread/query")) return Response.json(pairResult);
      if (url.includes("/instruments/")) return Response.json(instrument);
      return Response.json({});
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows live symbols and pairs and opens their detail pages", async () => {
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });

    expect(await within(panel).findByText("+0.990%")).not.toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/pair-spread/"))).toBe(false);
    await userEvent.click(within(panel).getByText("BTC"));
    expect(new URLSearchParams(window.location.search).get("page")).toBe("instrument");
    expect(new URLSearchParams(window.location.search).get("symbol")).toBe("BTCUSDT");

    await userEvent.click(within(panel).getByText("交易对 1"));
    expect(await within(panel).findByText("+1.800%")).not.toBeNull();
    await userEvent.click(within(panel).getByText("BTC / BTC"));
    expect(new URLSearchParams(window.location.search).get("page")).toBe("pair-monitor");
    expect(new URLSearchParams(window.location.search).get("leg1_exchange")).toBe("binance");
    expect(new URLSearchParams(window.location.search).get("leg2_exchange")).toBe("okx");
  });

  it("removes a watched symbol from the server-synced list", async () => {
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const remove = await screen.findByRole("button", { name: "取消关注标的 BTCUSDT" });

    await userEvent.click(remove);

    await waitFor(() => expect(screen.queryByRole("button", { name: "取消关注标的 BTCUSDT" })).toBeNull());
    const calls = vi.mocked(fetch).mock.calls;
    const update = calls.find(([input, init]) => String(input).includes("/settings/floating-watch/items") && init?.method === "POST");
    expect(update).toBeTruthy();
    expect(JSON.parse(String(update?.[1]?.body))).toEqual({
      action: "remove",
      item_type: "symbol",
      value: "BTCUSDT"
    });
  });

  it("waits for a slow refresh before scheduling the next poll", async () => {
    vi.useFakeTimers();
    let lookupCalls = 0;
    let resolveLookup: ((response: Response) => void) | undefined;
    const pendingLookup = new Promise<Response>((resolve) => {
      resolveLookup = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) {
        return Response.json({ symbols: ["BTCUSDT"], pair_ids: [] });
      }
      if (url.includes("/instruments/")) {
        lookupCalls += 1;
        return pendingLookup;
      }
      return Response.json({});
    });

    const view = render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    for (let index = 0; index < 5; index += 1) {
      await act(async () => Promise.resolve());
    }
    expect(lookupCalls).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(lookupCalls).toBe(1);

    resolveLookup?.(Response.json(instrument));
    await act(async () => Promise.resolve());
    view.unmount();
  });

  it("clamps a saved desktop position after the viewport shrinks", async () => {
    window.localStorage.setItem(
      "taoli1:floating-watch-position.v1",
      JSON.stringify({ left: 900, top: 700 })
    );
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      bottom: 700,
      height: 300,
      left: 900,
      right: 1290,
      top: 700,
      width: 390,
      x: 900,
      y: 700,
      toJSON: () => ({})
    });

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await waitFor(() => {
      expect(panel.style.left).toBe("626px");
      expect(panel.style.top).toBe("460px");
    });

    window.innerWidth = 800;
    window.innerHeight = 600;
    await act(async () => {
      window.dispatchEvent(new Event("resize"));
    });
    await waitFor(() => {
      expect(panel.style.left).toBe("402px");
      expect(panel.style.top).toBe("292px");
    });
    rectSpy.mockRestore();
  });
});
