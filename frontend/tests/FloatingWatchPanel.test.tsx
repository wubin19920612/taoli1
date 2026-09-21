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
        funding_rate_pct: 0.01234,
        funding_interval_hours: 8,
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
        funding_rate_pct: -0.00456,
        funding_interval_hours: 4,
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

function astroSpotVenue(exchange: string, symbol: string, bid: number, ask: number) {
  return {
    exchange,
    spot: {
      symbol,
      base: symbol.replace(/USDT$/, ""),
      quote: "USDT",
      exchange,
      market_type: "spot",
      bid,
      ask,
      timestamp: "2026-09-13T01:00:00Z",
      raw_symbol: symbol
    },
    future: null,
    error: null
  };
}

function astroInstrument(
  symbol: string,
  exchanges: Array<ReturnType<typeof astroFutureVenue> | ReturnType<typeof astroSpotVenue>>
) {
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

function accountPosition(overrides: Record<string, unknown> = {}) {
  return {
    id: "position_gate_btc_long",
    account_id: "gate:primary",
    account_label: "主账户",
    exchange: "gate",
    market_type: "future",
    raw_symbol: "BTC_USDT",
    symbol: "BTCUSDT",
    side: "long",
    dex: null,
    quantity: 0.02,
    quantity_unit: "BTC",
    contract_quantity: 20,
    contract_multiplier: 0.001,
    entry_price: 60000,
    mark_price: 61000,
    notional_usdt: 1220,
    unrealized_pnl_usdt: 20,
    roi_pct: 8.197,
    leverage: 5,
    price_basis: "Gate 标记价（mark_price）",
    estimated_fields: ["roi_pct"],
    updated_at: "2026-09-21T08:00:00Z",
    freshness: "fresh",
    age_seconds: 2,
    ...overrides
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

  it("shows real account fields, partial failures, and distinct same-ticker Hyperliquid DEX positions", async () => {
    const positions = [
      accountPosition(),
      accountPosition({
        id: "position_hl_btc_main_short",
        account_id: "hl:primary",
        account_label: "策略账户",
        exchange: "hyperliquid",
        raw_symbol: "BTC",
        side: "short",
        dex: "main",
        quantity: 0.3,
        contract_quantity: null,
        contract_multiplier: null,
        entry_price: null,
        mark_price: 61050,
        notional_usdt: 18315,
        unrealized_pnl_usdt: -15,
        roi_pct: null,
        leverage: null,
        price_basis: "Hyperliquid markPx",
        estimated_fields: [],
        freshness: "stale",
        age_seconds: 75
      }),
      accountPosition({
        id: "position_hl_btc_xyz_long",
        account_id: "hl:primary",
        account_label: "策略账户",
        exchange: "hyperliquid",
        raw_symbol: "xyz:BTC",
        side: "long",
        dex: "xyz",
        quantity: 0.1,
        contract_quantity: null,
        contract_multiplier: 1,
        entry_price: 60900,
        mark_price: 61040,
        notional_usdt: 6104,
        unrealized_pnl_usdt: 14,
        roi_pct: 1.15,
        leverage: 5,
        price_basis: "Hyperliquid markPx"
      })
    ];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) {
        return Response.json({ symbols: [], pair_ids: [], hidden_positions: [] });
      }
      if (url.includes("/account-positions")) {
        return Response.json({
          positions,
          accounts: [
            {
              account_id: "gate:primary",
              account_label: "主账户",
              exchange: "gate",
              configured: true,
              state: "ok",
              message: "持仓读取成功",
              position_count: 1,
              queried_at: "2026-09-21T08:00:02Z",
              data_updated_at: "2026-09-21T08:00:00Z",
              age_seconds: 2
            },
            {
              account_id: "hl:primary",
              account_label: "策略账户",
              exchange: "hyperliquid",
              configured: true,
              state: "stale",
              message: "账户持仓接口查询失败；当前显示最近一次成功快照",
              position_count: 2,
              queried_at: "2026-09-21T08:01:15Z",
              data_updated_at: "2026-09-21T08:00:00Z",
              age_seconds: 75
            }
          ],
          queried_at: "2026-09-21T08:01:15Z"
        });
      }
      if (url.includes("/astro/pairs")) return Response.json([]);
      return Response.json({});
    });

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("持仓 0"));

    expect(await within(panel).findByText("BTC_USDT")).not.toBeNull();
    expect(within(panel).getByText("xyz:BTC")).not.toBeNull();
    expect(within(panel).getByText("永续 · main")).not.toBeNull();
    expect(within(panel).getByText("永续 · xyz")).not.toBeNull();
    expect(within(panel).getAllByText("收益率（估）")).toHaveLength(2);
    expect(within(panel).getAllByText("--").length).toBeGreaterThan(1);
    expect(within(panel).getByText("过期 · 75s")).not.toBeNull();
    expect(within(panel).getByText(/账户持仓接口查询失败；当前显示最近一次成功快照/)).not.toBeNull();
    expect(within(panel).getByText("持仓 3")).not.toBeNull();
  });

  it("persists one exact hidden position across remount and restores it", async () => {
    const gatePosition = accountPosition();
    const otherMarket = accountPosition({
      id: "position_hl_btc_main_long",
      account_id: "hl:primary",
      account_label: "策略账户",
      exchange: "hyperliquid",
      raw_symbol: "BTC",
      dex: "main"
    });
    let hiddenPositions: Array<Record<string, unknown>> = [];
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch/positions") && init?.method === "POST") {
        const mutation = JSON.parse(String(init.body)) as {
          action: "add" | "remove";
          position: Record<string, unknown>;
        };
        hiddenPositions = mutation.action === "add"
          ? [mutation.position]
          : hiddenPositions.filter((item) => item.id !== mutation.position.id);
        return Response.json({ symbols: [], pair_ids: [], hidden_positions: hiddenPositions });
      }
      if (url.includes("/settings/floating-watch")) {
        return Response.json({ symbols: [], pair_ids: [], hidden_positions: hiddenPositions });
      }
      if (url.includes("/account-positions")) {
        return Response.json({
          positions: [gatePosition, otherMarket],
          accounts: [{
            account_id: "gate:primary",
            account_label: "主账户",
            exchange: "gate",
            configured: true,
            state: "ok",
            message: "持仓读取成功",
            position_count: 2,
            queried_at: "2026-09-21T08:00:02Z",
            data_updated_at: "2026-09-21T08:00:00Z",
            age_seconds: 2
          }],
          queried_at: "2026-09-21T08:00:02Z"
        });
      }
      if (url.includes("/astro/pairs")) return Response.json([]);
      return Response.json({});
    });

    const first = render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    let panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("持仓 0"));
    const hideGate = await within(panel).findByRole("button", {
      name: /在浮窗中屏蔽持仓 Gate 主账户 · BTC_USDT/
    });
    await userEvent.click(hideGate);
    await waitFor(() => expect(within(panel).queryByText("BTC_USDT")).toBeNull());
    expect(within(panel).getByText("BTC")).not.toBeNull();
    first.unmount();

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("持仓 0"));
    expect(await within(panel).findByText("持仓 1")).not.toBeNull();
    expect(within(panel).queryByText("BTC_USDT")).toBeNull();
    await userEvent.click(within(panel).getByRole("button", { name: "管理已屏蔽持仓" }));
    expect(within(panel).getByText("BTC_USDT")).not.toBeNull();
    await userEvent.click(within(panel).getByRole("button", { name: /恢复持仓 Gate 主账户/ }));
    await waitFor(() => expect(within(panel).getByText("持仓 2")).not.toBeNull());
    expect(hiddenPositions).toEqual([]);
  });

  it("distinguishes an unconfigured account from a verified empty account", async () => {
    let accountState = "not_configured";
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) {
        return Response.json({ symbols: [], pair_ids: [], hidden_positions: [] });
      }
      if (url.includes("/account-positions")) {
        return Response.json({
          positions: [],
          accounts: [{
            account_id: "gate:default",
            account_label: "默认账户",
            exchange: "gate",
            configured: accountState !== "not_configured",
            state: accountState,
            message: accountState === "empty" ? "账户已核验，当前无未平仓持仓" : "尚未配置账户凭据",
            position_count: 0,
            queried_at: "2026-09-21T08:00:02Z",
            data_updated_at: null,
            age_seconds: null
          }],
          queried_at: "2026-09-21T08:00:02Z"
        });
      }
      if (url.includes("/astro/pairs")) return Response.json([]);
      return Response.json({});
    });

    const view = render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    let panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("持仓 0"));
    expect(await within(panel).findByText("尚未配置支持持仓读取的账户")).not.toBeNull();

    accountState = "empty";
    view.unmount();
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("持仓 0"));
    expect(await within(panel).findByText("当前无持仓")).not.toBeNull();
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
    expect(within(panel).getByText("买 +0.0123%/8h · 卖 -0.0046%/4h")).not.toBeNull();
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

  it("opens an Astro ratio card with its actual legs, original symbols, and market multiplier", async () => {
    window.history.replaceState({}, "", "/?page=dashboard&leg1_dex=stale&leg2_dex=stale");
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("Astro 3 · 1 持仓"));
    const link = await within(panel).findByRole("button", {
      name: "打开 Astro 交易对 ANTHROPIC-ANTHROPIC 的价差查询"
    });
    await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false));

    await userEvent.click(link);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("pair-monitor");
    expect(params.get("leg1_exchange")).toBe("bitget");
    expect(params.get("leg1_market_type")).toBe("future");
    expect(params.get("leg1_symbol")).toBe("ANTHROPICUSDT");
    expect(params.get("leg1_dex")).toBeNull();
    expect(params.get("leg2_exchange")).toBe("okx");
    expect(params.get("leg2_market_type")).toBe("future");
    expect(params.get("leg2_symbol")).toBe("ANTHROPIC-USDT-SWAP");
    expect(params.get("leg2_dex")).toBeNull();
    expect(params.get("leg2_multiplier")).toBe("0.1");
    expect(params.get("hours")).toBe("4");
    expect(params.get("interval_seconds")).toBe("60");
  });

  it("keeps the buy and sell legs for forward and reverse Astro cards", async () => {
    const zetaPairs = [
      {
        id: "zeta-forward",
        name: "ZETA",
        type: "FF",
        status: true,
        buyEx: "gc-okx",
        sellEx: "gc-hl",
        aExPosition: 0,
        bExPosition: 0
      },
      {
        id: "zeta-reverse",
        name: "ZETA",
        type: "FF",
        status: true,
        buyEx: "hl",
        sellEx: "okx",
        aExPosition: 0,
        bExPosition: 0
      }
    ];
    const zetaInstrument = astroInstrument("ZETAUSDT", [
      {
        ...astroFutureVenue("okx", "ZETAUSDT", 0.061, 0.062),
        future: {
          ...astroFutureVenue("okx", "ZETAUSDT", 0.061, 0.062).future,
          raw_symbol: "ZETA-USDT-SWAP"
        }
      },
      {
        ...astroFutureVenue("hyperliquid", "ZETAUSDT", 0.063, 0.064),
        future: {
          ...astroFutureVenue("hyperliquid", "ZETAUSDT", 0.063, 0.064).future,
          raw_symbol: "ZETA"
        }
      }
    ]);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) return Response.json({ symbols: [], pair_ids: [] });
      if (url.includes("/astro/pairs")) return Response.json(zetaPairs);
      if (url.includes("/instruments/ZETAUSDT")) return Response.json(zetaInstrument);
      return Response.json({});
    });
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("Astro 2"));
    const links = await within(panel).findAllByRole("button", {
      name: "打开 Astro 交易对 ZETA 的价差查询"
    });
    await waitFor(() => expect(links.every((link) => !(link as HTMLButtonElement).disabled)).toBe(true));

    await userEvent.click(links[0]);
    let params = new URLSearchParams(window.location.search);
    expect(params.get("leg1_exchange")).toBe("okx");
    expect(params.get("leg1_symbol")).toBe("ZETA-USDT-SWAP");
    expect(params.get("leg1_dex")).toBeNull();
    expect(params.get("leg2_exchange")).toBe("hyperliquid");
    expect(params.get("leg2_symbol")).toBe("ZETA");
    expect(params.get("leg2_dex")).toBe("main");

    await userEvent.click(links[1]);
    params = new URLSearchParams(window.location.search);
    expect(params.get("leg1_exchange")).toBe("hyperliquid");
    expect(params.get("leg1_symbol")).toBe("ZETA");
    expect(params.get("leg1_dex")).toBe("main");
    expect(params.get("leg2_exchange")).toBe("okx");
    expect(params.get("leg2_symbol")).toBe("ZETA-USDT-SWAP");
    expect(params.get("leg2_dex")).toBeNull();
  });

  it("preserves spot and perpetual types for an SR Astro card", async () => {
    const lskPair = [{
      id: "lsk-sr",
      name: "LSK-LSK",
      type: "SR",
      status: true,
      buyEx: "gc-okx",
      sellEx: "gc-binance",
      regressionValue: 1,
      aExPosition: 0,
      bExPosition: 0
    }];
    const okxSpot = astroSpotVenue("okx", "LSKUSDT", 0.4, 0.41);
    const binanceFuture = astroFutureVenue("binance", "LSKUSDT", 0.42, 0.43);
    const lskInstrument = astroInstrument("LSKUSDT", [
      { ...okxSpot, spot: { ...okxSpot.spot, raw_symbol: "LSK-USDT" } },
      binanceFuture
    ]);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) return Response.json({ symbols: [], pair_ids: [] });
      if (url.includes("/astro/pairs")) return Response.json(lskPair);
      if (url.includes("/instruments/LSKUSDT")) return Response.json(lskInstrument);
      return Response.json({});
    });
    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("Astro 1"));
    const link = await within(panel).findByRole("button", {
      name: "打开 Astro 交易对 LSK-LSK 的价差查询"
    });
    await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false));

    await userEvent.click(link);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("leg1_exchange")).toBe("okx");
    expect(params.get("leg1_market_type")).toBe("spot");
    expect(params.get("leg1_symbol")).toBe("LSK-USDT");
    expect(params.get("leg2_exchange")).toBe("binance");
    expect(params.get("leg2_market_type")).toBe("future");
    expect(params.get("leg2_symbol")).toBe("LSKUSDT");
    expect(params.get("leg2_multiplier")).toBe("1");
  });

  it("shows spot legs and unavailable perpetual funding without implying a zero rate", async () => {
    const mixedPair = [{
      id: "spot-future",
      name: "MIXED",
      type: "SF",
      status: true,
      buyEx: "bitget",
      sellEx: "okx",
      aExPosition: 0,
      bExPosition: 0
    }];
    const mixedInstrument = astroInstrument("MIXEDUSDT", [
      astroSpotVenue("bitget", "MIXEDUSDT", 99, 100),
      astroFutureVenue("okx", "MIXEDUSDT", 101, 102)
    ]);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) return Response.json({ symbols: [], pair_ids: [] });
      if (url.includes("/astro/pairs")) return Response.json(mixedPair);
      if (url.includes("/instruments/MIXEDUSDT")) return Response.json(mixedInstrument);
      return Response.json({});
    });

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("Astro 1"));

    expect(await within(panel).findByText("买 现货 · 卖 --/?h")).not.toBeNull();
  });

  it("uses the Astro Hyperliquid DEX when loading an aliased HIP-3 leg", async () => {
    const hip3Pair = [{
      id: "anthropic-io",
      name: "ANTHROPIC-ANTH",
      type: "FR",
      status: true,
      buyEx: "bitget",
      sellEx: "hl",
      bEffectiveHlDex: "io",
      regressionValue: 10,
      aExPosition: 0,
      bExPosition: 0
    }];
    const hyperliquidVenue = astroFutureVenue("hyperliquid", "ANTHROPICUSDT", 215, 216);
    const hyperliquidAnth = astroInstrument("ANTHROPICUSDT", [{
      ...hyperliquidVenue,
      future: {
        ...hyperliquidVenue.future,
        raw_symbol: "io:ANTH"
      }
    }]);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) return Response.json({ symbols: [], pair_ids: [] });
      if (url.includes("/astro/pairs")) return Response.json(hip3Pair);
      if (url.includes("/instruments/ANTHROPICUSDT")) return Response.json(astroAnthropicInstrument);
      if (url.includes("/instruments/ANTHUSDT") && url.includes("dex=io")) {
        return Response.json(hyperliquidAnth);
      }
      return Response.json({});
    });

    render(<FloatingWatchPanel visible onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "关注浮窗" });
    await userEvent.click(await within(panel).findByText("Astro 1"));

    const card = within(panel).getByText("ANTHROPIC-ANTH").closest(".floating-watch-astro-row") as HTMLElement;
    await waitFor(() => {
      expect(card.querySelector(".floating-watch-astro-route")?.textContent)
        .toBe("Bitget 永续 200 → Hyperliquid io 永续 215.5");
    });
    expect(within(card).queryByText(/未找到.*实时行情/)).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([input]) => {
      const url = new URL(String(input));
      return url.pathname === "/api/instruments/ANTHUSDT" && url.searchParams.get("dex") === "io";
    })).toBe(true);
  });

  it("opens an Astro spread in a new tab without replacing an orphaned standalone window", async () => {
    window.history.replaceState({}, "", "/?floating_watch=standalone");
    const hip3Pair = [{
      id: "anthropic-io",
      name: "ANTHROPIC-ANTH",
      type: "FR",
      status: true,
      buyEx: "bitget",
      sellEx: "hl",
      bEffectiveHlDex: "io",
      regressionValue: 1,
      aExPosition: 0,
      bExPosition: 0
    }];
    const hyperliquidVenue = astroFutureVenue("hyperliquid", "ANTHROPICUSDT", 215, 216);
    const hyperliquidAnth = astroInstrument("ANTHROPICUSDT", [{
      ...hyperliquidVenue,
      future: { ...hyperliquidVenue.future, raw_symbol: "io:ANTH" }
    }]);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/settings/floating-watch")) return Response.json({ symbols: [], pair_ids: [] });
      if (url.includes("/astro/pairs")) return Response.json(hip3Pair);
      if (url.includes("/instruments/ANTHROPICUSDT")) return Response.json(astroAnthropicInstrument);
      if (url.includes("/instruments/ANTHUSDT") && url.includes("dex=io")) return Response.json(hyperliquidAnth);
      return Response.json({});
    });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<FloatingWatchPanel visible standalone onClose={vi.fn()} />);
    const panel = await screen.findByRole("complementary", { name: "独立关注窗口" });
    await userEvent.click(await within(panel).findByText("Astro 1"));
    const link = await within(panel).findByRole("button", {
      name: "打开 Astro 交易对 ANTHROPIC-ANTH 的价差查询"
    });
    await waitFor(() => expect((link as HTMLButtonElement).disabled).toBe(false));

    await userEvent.click(link);

    expect(window.location.search).toBe("?floating_watch=standalone");
    expect(open).toHaveBeenCalledOnce();
    const [destination, target, features] = open.mock.calls[0];
    const url = new URL(String(destination), window.location.origin);
    expect(target).toBe("_blank");
    expect(features).toBe("noopener,noreferrer");
    expect(url.searchParams.get("floating_watch")).toBeNull();
    expect(url.searchParams.get("page")).toBe("pair-monitor");
    expect(url.searchParams.get("leg1_exchange")).toBe("bitget");
    expect(url.searchParams.get("leg1_symbol")).toBe("ANTHROPICUSDT");
    expect(url.searchParams.get("leg2_exchange")).toBe("hyperliquid");
    expect(url.searchParams.get("leg2_symbol")).toBe("ANTH");
    expect(url.searchParams.get("leg2_dex")).toBe("io");
    expect(url.searchParams.get("leg2_multiplier")).toBe("1");
    open.mockRestore();
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
