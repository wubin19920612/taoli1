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

const astroPairs = [
  {
    id: "active-position",
    name: "ANTHROPIC-ANTHROPIC",
    type: "FR",
    status: true,
    disableOpen: false,
    disableClose: false,
    buyEx: "bitget",
    sellEx: "gc-okx",
    openPosition: 9.706375012316485,
    closePosition: 9.71900419028839,
    regressionValue: 10,
    aExPosition: 40,
    bExPosition: 400,
    avgOpenAExPrice: 190,
    avgOpenBExPrice: 21,
    profit: -1.3,
    realizedProfit: 10
  },
  {
    id: "active-close-only",
    name: "OPENAI",
    type: "FF",
    status: true,
    disableOpen: true,
    disableClose: false,
    buyEx: "gc-binance",
    sellEx: "gc-gate",
    openPosition: "0.0030",
    closePosition: "-0.0015",
    aExPosition: 0,
    bExPosition: 0,
    realizedProfit: 0
  },
  {
    id: "active-invalid-ratio-threshold",
    name: "OPENAI-OAI",
    type: "FR",
    status: true,
    disableOpen: false,
    disableClose: false,
    buyEx: "gc-gate",
    sellEx: "hl",
    openPosition: "0",
    closePosition: "Infinity",
    regressionValue: 1,
    aExPosition: 0,
    bExPosition: 0,
    realizedProfit: 0
  },
  {
    id: "paused",
    name: "STEEM",
    type: "FF",
    status: false,
    disableOpen: true,
    disableClose: false,
    buyEx: "gate",
    sellEx: "bybit"
  }
];

const astroAnthropicInstrument = {
  ...instrument,
  query: "ANTHROPICUSDT",
  symbol: "ANTHROPICUSDT",
  base: "ANTHROPIC",
  exchanges: [
    {
      exchange: "bitget",
      spot: null,
      future: {
        symbol: "ANTHROPICUSDT",
        base: "ANTHROPIC",
        quote: "USDT",
        exchange: "bitget",
        market_type: "future",
        bid: 199,
        ask: 201,
        timestamp: "2026-09-13T01:00:00Z",
        raw_symbol: "ANTHROPICUSDT"
      },
      error: null
    },
    {
      exchange: "okx",
      spot: null,
      future: {
        symbol: "ANTHROPICUSDT",
        base: "ANTHROPIC",
        quote: "USDT",
        exchange: "okx",
        market_type: "future",
        bid: 19.9,
        ask: 20.1,
        timestamp: "2026-09-13T01:00:00Z",
        raw_symbol: "ANTHROPIC-USDT-SWAP"
      },
      error: null
    }
  ],
  spreads: []
};

const astroOpenAiInstrument = {
  ...instrument,
  query: "OPENAIUSDT",
  symbol: "OPENAIUSDT",
  base: "OPENAI",
  exchanges: [
    {
      exchange: "binance",
      spot: null,
      future: {
        symbol: "OPENAIUSDT",
        base: "OPENAI",
        quote: "USDT",
        exchange: "binance",
        market_type: "future",
        bid: 99,
        ask: 100,
        timestamp: "2026-09-13T01:00:00Z",
        raw_symbol: "OPENAIUSDT"
      },
      error: null
    },
    {
      exchange: "gate",
      spot: null,
      future: {
        symbol: "OPENAIUSDT",
        base: "OPENAI",
        quote: "USDT",
        exchange: "gate",
        market_type: "future",
        bid: 101,
        ask: 102,
        timestamp: "2026-09-13T01:00:00Z",
        raw_symbol: "OPENAI_USDT"
      },
      error: null
    }
  ],
  spreads: []
};

function astroFutureVenue(exchange: string, symbol: string, bid: number, ask: number) {
  return {
    exchange,
    spot: null,
    future: {
      symbol,
      base: symbol.replace(/USDT$/, ""),
      quote: "USDT",
      exchange,
      market_type: "future",
      bid,
      ask,
      timestamp: "2026-09-13T01:00:00Z",
      raw_symbol: symbol
    },
    error: null
  };
}

function astroInstrument(symbol: string, exchanges: ReturnType<typeof astroFutureVenue>[]) {
  return {
    ...instrument,
    query: symbol,
    symbol,
    base: symbol.replace(/USDT$/, ""),
    exchange_count: exchanges.length,
    market_count: exchanges.length,
    exchanges,
    spreads: []
  };
}

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
      if (url.includes("/astro/pairs")) return Response.json(astroPairs);
      if (url.includes("/pair-spread/presets")) return Response.json([preset]);
      if (url.includes("/pair-spread/query")) return Response.json(pairResult);
      if (url.includes("/instruments/ANTHROPICUSDT")) return Response.json(astroAnthropicInstrument);
      if (url.includes("/instruments/OPENAIUSDT")) return Response.json(astroOpenAiInstrument);
      if (url.includes("/instruments/")) return Response.json(instrument);
      return Response.json({});
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
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

  it("shows only running Astro cards with runtime and position status", async () => {
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });

    await userEvent.click(await within(panel).findByText("Astro 3 · 1 持仓"));

    expect(await within(panel).findByText("ANTHROPIC-ANTHROPIC")).not.toBeNull();
    expect(within(panel).getByText("持仓中")).not.toBeNull();
    expect(within(panel).getByText("仅平仓")).not.toBeNull();
    expect(await within(panel).findByText("开 -1.00% / 平 1.00%")).not.toBeNull();
    expect(within(panel).getByText("开 2.98% / 平 2.85%")).not.toBeNull();
    expect(within(panel).getByText("1:10")).not.toBeNull();
    expect(within(panel).getByText("7,600 U")).not.toBeNull();
    expect(within(panel).getByText("8,400 U")).not.toBeNull();
    expect(within(panel).getByText("-1.30 U")).not.toBeNull();
    const positionedCard = within(panel).getByText("ANTHROPIC-ANTHROPIC").closest(".floating-watch-astro-row");
    expect(positionedCard).not.toBeNull();
    expect(within(positionedCard as HTMLElement).getByText("Astro 预估")).not.toBeNull();
    expect(within(positionedCard as HTMLElement).queryByText(/本地估算/)).toBeNull();
    expect(within(positionedCard as HTMLElement).getByText("--")).not.toBeNull();
    expect(within(panel).queryByText("+10.00 U")).toBeNull();
    expect(within(panel).queryByText("+720.00 U")).toBeNull();
    expect(within(panel).queryByText("+730.00 U")).toBeNull();
    expect(within(panel).getByText("开 1.00% / 平 2.99%")).not.toBeNull();
    expect(within(panel).getByText("开 0.30% / 平 -0.15%")).not.toBeNull();
    const invalidThresholdCard = within(panel).getByText("OPENAI-OAI").closest(".floating-watch-astro-row");
    expect(invalidThresholdCard).not.toBeNull();
    expect(within(invalidThresholdCard as HTMLElement).getByText("开 - / 平 -")).not.toBeNull();
    expect(within(panel).getAllByText("当前无持仓 · 已实现 --")).toHaveLength(2);
    expect(within(panel).queryByText("STEEM")).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/astro/pairs"))).toBe(true);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/instruments/ANTHROPICUSDT"))).toBe(true);
  });

  it("marks watched symbols that have an active Astro position as trading", async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) {
        return Response.json({ symbols: ["ANTHROPICUSDT", "OPENAIUSDT", "STEEMUSDT"], pair_ids: [] });
      }
      if (url.includes("/astro/pairs")) return Response.json(astroPairs);
      if (url.includes("/instruments/ANTHROPICUSDT")) return Response.json(astroAnthropicInstrument);
      if (url.includes("/instruments/OPENAIUSDT")) return Response.json(astroOpenAiInstrument);
      if (url.includes("/instruments/")) return Response.json(instrument);
      return Response.json({});
    });

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    const anthropic = await within(panel).findByText("ANTHROPIC");
    const openAi = within(panel).getByText("OPENAI");
    const steem = within(panel).getByText("STEEM");
    const anthropicRow = anthropic.closest(".floating-watch-row") as HTMLElement;
    const openAiRow = openAi.closest(".floating-watch-row") as HTMLElement;
    const steemRow = steem.closest(".floating-watch-row") as HTMLElement;

    expect(within(anthropicRow).getByText("交易中")).not.toBeNull();
    expect(anthropicRow.classList.contains("floating-watch-row-trading")).toBe(true);
    expect(within(openAiRow).queryByText("交易中")).toBeNull();
    expect(within(steemRow).queryByText("交易中")).toBeNull();
    expect(within(panel).getByText("Astro 3 · 1 持仓")).not.toBeNull();
  });

  it("estimates missing Astro profit with exchange-specific fees and labels the fallback", async () => {
    const fallbackPairs = [
      {
        id: "standard-fee",
        name: "STANDARD",
        type: "FF",
        status: true,
        buyEx: "bitget",
        sellEx: "gc-okx",
        aExPosition: 40,
        bExPosition: 400,
        avgOpenAExPrice: 190,
        avgOpenBExPrice: 21
      },
      {
        id: "one-hl-fee",
        name: "ONEHL",
        type: "FF",
        status: true,
        buyEx: "hl",
        sellEx: "gc-okx",
        aExPosition: 40,
        bExPosition: 400,
        avgOpenAExPrice: 190,
        avgOpenBExPrice: 21
      },
      {
        id: "two-hl-fee",
        name: "BOTHHL",
        type: "FF",
        status: true,
        buyEx: "hl",
        sellEx: "gc-hl",
        aExPosition: 40,
        bExPosition: 40,
        avgOpenAExPrice: 190,
        avgOpenBExPrice: 210
      },
      {
        id: "missing-market",
        name: "NOLIVE",
        type: "FF",
        status: true,
        buyEx: "bitget",
        sellEx: "okx",
        aExPosition: 40,
        bExPosition: 400,
        avgOpenAExPrice: 190,
        avgOpenBExPrice: 21
      }
    ];
    const instruments = new Map([
      ["STANDARDUSDT", astroInstrument("STANDARDUSDT", [
        astroFutureVenue("bitget", "STANDARDUSDT", 199, 201),
        astroFutureVenue("okx", "STANDARDUSDT", 19.9, 20.1)
      ])],
      ["ONEHLUSDT", astroInstrument("ONEHLUSDT", [
        astroFutureVenue("hyperliquid", "ONEHLUSDT", 199, 201),
        astroFutureVenue("okx", "ONEHLUSDT", 19.9, 20.1)
      ])],
      ["BOTHHLUSDT", astroInstrument("BOTHHLUSDT", [
        astroFutureVenue("hyperliquid", "BOTHHLUSDT", 199, 201)
      ])],
      ["NOLIVEUSDT", astroInstrument("NOLIVEUSDT", [])]
    ]);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) return Response.json({ symbols: [], pair_ids: [] });
      if (url.includes("/astro/pairs")) return Response.json(fallbackPairs);
      if (url.includes("/pair-spread/presets")) return Response.json([]);
      for (const [symbol, result] of instruments) {
        if (url.includes(`/instruments/${symbol}`)) return Response.json(result);
      }
      return Response.json({});
    });

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("Astro 4 · 4 持仓"));

    const standardCard = within(panel).getByText("STANDARD").closest(".floating-watch-astro-row") as HTMLElement;
    expect(await within(standardCard).findByText("+688.00 U")).not.toBeNull();
    expect(within(standardCard).getByText("本地预估")).not.toBeNull();
    expect(within(standardCard).getByText("本地估算 · 已按双腿仓位扣 0.20% 手续费")).not.toBeNull();

    const oneHlCard = within(panel).getByText("ONEHL").closest(".floating-watch-astro-row") as HTMLElement;
    expect(within(oneHlCard).getByText("+699.20 U")).not.toBeNull();
    expect(within(oneHlCard).getByText("本地估算 · 已按双腿仓位扣 0.13% 手续费")).not.toBeNull();

    const bothHlCard = within(panel).getByText("BOTHHL").closest(".floating-watch-astro-row") as HTMLElement;
    expect(within(bothHlCard).getByText("+712.00 U")).not.toBeNull();
    expect(within(bothHlCard).getByText("本地估算 · 已按双腿仓位扣 0.05% 手续费")).not.toBeNull();

    const noLiveCard = within(panel).getByText("NOLIVE").closest(".floating-watch-astro-row") as HTMLElement;
    expect(within(noLiveCard).getByText("本地预估").parentElement?.textContent).toBe("本地预估--");
    expect(within(noLiveCard).getByText("Astro 未返回，等待实时行情后本地估算")).not.toBeNull();
  });

  it("opens a dedicated watch window and closes the embedded panel", async () => {
    const focus = vi.fn();
    const open = vi.spyOn(window, "open").mockReturnValue({ focus } as unknown as Window);
    const onClose = vi.fn();
    render(<FloatingWatchPanel visible onClose={onClose} />);

    await userEvent.click(await screen.findByRole("button", { name: "打开独立关注窗口" }));

    expect(open).toHaveBeenCalledWith(
      expect.stringContaining("floating_watch=standalone"),
      "taoli1-floating-watch",
      expect.stringContaining("popup=yes")
    );
    expect(focus).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
    open.mockRestore();
  });

  it("keeps the embedded panel open when the browser blocks the dedicated window", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const onClose = vi.fn();
    render(<FloatingWatchPanel visible onClose={onClose} />);

    await userEvent.click(await screen.findByRole("button", { name: "打开独立关注窗口" }));

    expect(await screen.findByText("浏览器拦截了独立窗口，请允许本站打开弹出窗口。")).not.toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    open.mockRestore();
  });

  it("renders a full-window standalone panel without detach or collapse controls", async () => {
    const onClose = vi.fn();
    window.localStorage.setItem("taoli1:floating-watch-collapsed.v1", "1");
    render(<FloatingWatchPanel visible standalone onClose={onClose} />);

    const panel = await screen.findByRole("complementary", { name: "独立关注窗口" });
    expect(panel.classList.contains("floating-watch-panel-standalone")).toBe(true);
    expect(within(panel).queryByRole("button", { name: "打开独立关注窗口" })).toBeNull();
    expect(within(panel).queryByRole("button", { name: "收起关注浮窗" })).toBeNull();
    await act(async () => {
      window.dispatchEvent(new CustomEvent("taoli1:floating-watch-updated", {
        detail: { symbols: ["BTCUSDT"], pair_ids: [preset.id] }
      }));
    });
    expect(window.localStorage.getItem("taoli1:floating-watch-collapsed.v1")).toBe("1");
    await userEvent.click(within(panel).getByRole("button", { name: "关闭独立关注窗口" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("sends detail navigation to a same-origin opener", async () => {
    const postMessage = vi.fn();
    const focus = vi.fn();
    vi.stubGlobal("opener", {
      closed: false,
      focus,
      location: { origin: window.location.origin },
      postMessage
    });
    render(<FloatingWatchPanel visible standalone onClose={vi.fn()} />);

    await userEvent.click(await screen.findByText("BTC"));

    expect(postMessage).toHaveBeenCalledWith({
      type: "taoli1:floating-watch-navigate",
      destination: "/?page=instrument&symbol=BTCUSDT"
    }, window.location.origin);
    expect(focus).toHaveBeenCalledOnce();
  });

  it("opens a new app page when the original opener is now cross-origin", async () => {
    const openerPostMessage = vi.fn();
    vi.stubGlobal("opener", {
      closed: false,
      focus: vi.fn(),
      location: { origin: "https://example.com" },
      postMessage: openerPostMessage
    });
    const focus = vi.fn();
    const open = vi.spyOn(window, "open").mockReturnValue({ focus } as unknown as Window);
    render(<FloatingWatchPanel visible standalone onClose={vi.fn()} />);

    await userEvent.click(await screen.findByText("BTC"));

    expect(openerPostMessage).not.toHaveBeenCalled();
    expect(open).toHaveBeenCalledWith("/?page=instrument&symbol=BTCUSDT", "_blank");
    expect(focus).toHaveBeenCalledOnce();
    open.mockRestore();
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
