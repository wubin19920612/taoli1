import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InstrumentLookupPage } from "../src/pages/InstrumentLookupPage";

const lookupResult = {
  query: "BTCUSDT",
  symbol: "BTCUSDT",
  base: "BTC",
  quote: "USDT",
  observed_at: "2026-09-11T04:00:00Z",
  exchange_count: 2,
  market_count: 3,
  exchanges: [
    {
      exchange: "binance",
      spot: {
        symbol: "BTCUSDT",
        base: "BTC",
        quote: "USDT",
        exchange: "binance",
        market_type: "spot",
        bid: 99990,
        ask: 100010,
        volume_24h_usdt: 1000000000,
        timestamp: "2026-09-11T04:00:00Z",
        raw_symbol: "BTCUSDT"
      },
      future: {
        symbol: "BTCUSDT",
        base: "BTC",
        quote: "USDT",
        exchange: "binance",
        market_type: "future",
        bid: 100090,
        ask: 100110,
        volume_24h_usdt: 2000000000,
        funding_rate_pct: 0.01,
        funding_next_rate_pct: 0.012,
        funding_interval_hours: 8,
        mark_price: 100180,
        index_price: 100000,
        timestamp: "2026-09-11T04:00:00Z",
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
        bid: 100040,
        ask: 100060,
        volume_24h_usdt: 1500000000,
        funding_rate_pct: 0.008,
        funding_interval_hours: 8,
        mark_price: 100050,
        index_price: 100000,
        timestamp: "2026-09-11T04:00:00Z",
        raw_symbol: "BTC-USDT-SWAP"
      },
      error: null
    },
    ...["bybit", "gate", "bitget", "aster", "hyperliquid"].map((exchange) => ({
      exchange,
      spot: null,
      future: null,
      error: exchange === "bitget" ? "temporary timeout" : null
    }))
  ],
  spreads: [
    {
      id: "binance:spot->binance:future",
      buy_exchange: "binance",
      buy_market_type: "spot",
      buy_ask: 100010,
      sell_exchange: "binance",
      sell_market_type: "future",
      sell_bid: 100090,
      price_difference: 80,
      executable_spread_pct: 0.08,
      mid_spread_pct: 0.1,
      opportunity_type: "SF",
      astro_supported: true,
      astro_blocker: null
    },
    {
      id: "okx:future->binance:future",
      buy_exchange: "okx",
      buy_market_type: "future",
      buy_ask: 100060,
      sell_exchange: "binance",
      sell_market_type: "future",
      sell_bid: 100090,
      price_difference: 30,
      executable_spread_pct: 0.03,
      mid_spread_pct: 0.05,
      opportunity_type: "FF",
      astro_supported: true,
      astro_blocker: null
    },
    {
      id: "binance:spot->okx:future",
      buy_exchange: "binance",
      buy_market_type: "spot",
      buy_ask: 100010,
      sell_exchange: "okx",
      sell_market_type: "future",
      sell_bid: 100040,
      price_difference: 30,
      executable_spread_pct: 0.03,
      mid_spread_pct: 0.05,
      opportunity_type: "SF",
      astro_supported: true,
      astro_blocker: null
    }
  ]
};

const astroPlan = {
  opportunity_id: "instrument-pair",
  symbol: "BTCUSDT",
  source_open_spread_pct: 0.08,
  quoted_at: "2026-09-11T04:00:00Z",
  mode: "dry_run",
  can_submit: true,
  pair: {
    name: "BTC",
    type: "SF",
    buyEx: "binance",
    sellEx: "binance",
    openPosition: "0.000800",
    closePosition: "0.000000",
    maxTradeUSDT: "10",
    leverage: "1",
    minNotional: "10",
    maxNotional: "10",
    status: false,
    disableOpen: true
  },
  sdk_payload: {},
  blockers: [],
  warnings: [],
  assumptions: []
};

const trendResult = {
  symbol: "BTCUSDT",
  market_type: "future",
  base_exchange: "binance",
  exchanges: ["binance", "okx"],
  hours: 24,
  interval_minutes: 1,
  interval_seconds: 60,
  observed_at: "2026-09-11T04:00:00Z",
  point_count: 2,
  first_seen_at: "2026-09-11T03:58:00Z",
  last_seen_at: "2026-09-11T03:59:00Z",
  current_prices: [],
  warnings: [],
  series: [
    {
      exchange: "okx",
      symbol: "BTCUSDT",
      market_type: "future",
      point_count: 2,
      first_seen_at: "2026-09-11T03:58:00Z",
      last_seen_at: "2026-09-11T03:59:00Z",
      spread_abs: { min: 40, max: 50, mean: 45, current: 50 },
      spread_pct: { min: 0.04, max: 0.05, mean: 0.045, current: 0.05 },
      current: null,
      points: [
        { bucket_at: "2026-09-11T03:58:00Z", base_close: 100000, exchange_close: 100040, spread_abs: 40, spread_pct: 0.04 },
        { bucket_at: "2026-09-11T03:59:00Z", base_close: 100010, exchange_close: 100060, spread_abs: 50, spread_pct: 0.05 }
      ]
    }
  ]
};

describe("InstrumentLookupPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/?page=instrument");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/astro/instrument/preview")) return Response.json(astroPlan);
        if (url.includes("/astro/instrument/card") && init?.method === "POST") {
          return Response.json({
            enabled: true,
            status: "created",
            action: "add",
            message: "已创建暂停卡片 BTC SF binance->binance",
            pair_name: "BTC",
            pair_type: "SF",
            warnings: ["订单簿深度不足；本次为人工建卡，仅作风险提示，未拦截创建"]
          });
        }
        if (url.includes("/pair-spread/symbol-query")) return Response.json(trendResult);
        if (url.includes("/instruments/")) return Response.json(lookupResult);
        return Response.json({});
      })
    );
  });

  it("shows exact cross-exchange spot and perpetual basics", async () => {
    render(<InstrumentLookupPage />);

    expect(await screen.findByText("BTC / USDT")).not.toBeNull();
    expect(screen.getByText("2 / 7")).not.toBeNull();
    expect(screen.queryByText("HTX")).toBeNull();
    expect(screen.getByText("1 现货 · 2 永续")).not.toBeNull();
    expect(screen.getAllByText("盘口中价")).toHaveLength(3);
    expect(screen.getByText("标记价 100,180")).not.toBeNull();
    expect(screen.getByText("100,000 - 100,100")).not.toBeNull();
    expect(screen.getByText("+0.100% · Binance")).not.toBeNull();
    expect(screen.getByText(/BTC-USDT-SWAP/)).not.toBeNull();
    expect(screen.getAllByText("Binance").length).toBeGreaterThan(0);
    expect(screen.getAllByText("暂无数据").length).toBeGreaterThan(0);
    expect(String((fetch as ReturnType<typeof vi.fn>).mock.calls[0][0])).toContain("/instruments/BTCUSDT");
  });

  it("loads trend history only after the section is expanded", async () => {
    render(<InstrumentLookupPage />);
    await screen.findByText("BTC / USDT");

    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([input]) => String(input).includes("symbol-query"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: /展开/ }));

    await waitFor(() => {
      expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([input]) => String(input).includes("symbol-query"))).toBe(true);
    });
    expect(await screen.findByRole("img", { name: "多交易所价格走势" })).not.toBeNull();
    expect(screen.getAllByText("最新价差").length).toBeGreaterThan(0);
  });

  it("lists executable spreads and creates a card from the selected live route", async () => {
    render(<InstrumentLookupPage />);

    expect(await screen.findByText("跨市场差价")).not.toBeNull();
    expect(screen.getByText("按买入 Ask、卖出 Bid 计算，每组市场保留较优方向")).not.toBeNull();
    expect(screen.getByText("+0.080%")).not.toBeNull();

    await userEvent.click(screen.getAllByRole("button", { name: /建卡/ })[0]);
    expect(await screen.findByText("创建 Astro 卡片")).not.toBeNull();

    const previewCall = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(([input]) =>
      String(input).includes("/astro/instrument/preview")
    );
    expect(JSON.parse(String(previewCall?.[1]?.body))).toEqual({
      symbol: "BTCUSDT",
      buy_exchange: "binance",
      buy_market_type: "spot",
      sell_exchange: "binance",
      sell_market_type: "future"
    });

    await userEvent.click(screen.getByRole("button", { name: "确认创建" }));
    await waitFor(() => {
      expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([input]) =>
        String(input).includes("/astro/instrument/card")
      )).toBe(true);
    });
    const createCall = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(([input]) =>
      String(input).includes("/astro/instrument/card")
    );
    expect(JSON.parse(String(createCall?.[1]?.body)).expected_open_spread_pct).toBe(0.08);
    expect(await screen.findAllByText("已创建暂停卡片 BTC SF binance->binance")).toHaveLength(2);
    expect(screen.getByText(/人工建卡，仅作风险提示，未拦截创建/)).not.toBeNull();
  });

  it("shows every spread without pagination and sorts by spread type", async () => {
    const spreadTypes = ["SF", "FF", "SS", null] as const;
    const manySpreads = Array.from({ length: 13 }, (_, index) => ({
      ...lookupResult.spreads[index % lookupResult.spreads.length],
      id: `spread-${index}`,
      opportunity_type: spreadTypes[index % spreadTypes.length]
    }));
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/instruments/")) {
        return Response.json({ ...lookupResult, spreads: manySpreads });
      }
      return Response.json({});
    });

    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    expect(screen.getAllByRole("button", { name: /建卡/ })).toHaveLength(13);
    expect(document.querySelector(".ant-pagination")).toBeNull();

    const typeHeader = screen.getByRole("columnheader", { name: /差价类型/ });
    await userEvent.click(typeHeader);
    const table = typeHeader.closest("table");
    const ascendingTypes = Array.from(table?.querySelectorAll("tbody tr.ant-table-row") ?? []).map(
      (row) => row.querySelector("td")?.textContent
    );
    expect(ascendingTypes).toEqual([
      "FF", "FF", "FF",
      "SF", "SF", "SF", "SF",
      "SS", "SS", "SS",
      "反向 SF", "反向 SF", "反向 SF"
    ]);

    await userEvent.click(typeHeader);
    const descendingTypes = Array.from(table?.querySelectorAll("tbody tr.ant-table-row") ?? []).map(
      (row) => row.querySelector("td")?.textContent
    );
    expect(descendingTypes).toEqual([
      "反向 SF", "反向 SF", "反向 SF",
      "SS", "SS", "SS",
      "SF", "SF", "SF", "SF",
      "FF", "FF", "FF"
    ]);
  });

  it("opens the selected market pair in the spread query", async () => {
    window.history.replaceState({}, "", "/?page=instrument&symbol=BTCUSDT&leg1_dex=stale&leg2_dex=stale");
    const navigate = vi.fn();
    window.addEventListener("taoli1:navigate", navigate);
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getByRole("button", {
      name: "价差查询 BTCUSDT okx:future->binance:future"
    }));

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("pair-monitor");
    expect(params.get("symbol")).toBeNull();
    expect(params.get("leg1_exchange")).toBe("okx");
    expect(params.get("leg1_market_type")).toBe("future");
    expect(params.get("leg1_symbol")).toBe("BTC-USDT-SWAP");
    expect(params.get("leg1_dex")).toBeNull();
    expect(params.get("leg2_exchange")).toBe("binance");
    expect(params.get("leg2_market_type")).toBe("future");
    expect(params.get("leg2_symbol")).toBe("BTCUSDT");
    expect(params.get("leg2_dex")).toBeNull();
    expect(params.get("leg2_multiplier")).toBe("1");
    expect(params.get("hours")).toBe("4");
    expect(params.get("interval_seconds")).toBe("60");
    expect(navigate).toHaveBeenCalledOnce();
    window.removeEventListener("taoli1:navigate", navigate);
  });

  it("preserves a Hyperliquid sub-DEX when opening its spread", async () => {
    const hyperliquidResult = {
      ...lookupResult,
      query: "ANTHROPICUSDT",
      symbol: "ANTHROPICUSDT",
      base: "ANTHROPIC",
      exchanges: [
        {
          exchange: "hyperliquid",
          spot: null,
          future: {
            ...lookupResult.exchanges[0].future,
            symbol: "ANTHROPICUSDT",
            base: "ANTHROPIC",
            exchange: "hyperliquid",
            raw_symbol: "io:ANTH",
            symbol_alias_original_symbol: "ANTHUSDT"
          },
          error: null
        },
        {
          exchange: "bitget",
          spot: null,
          future: {
            ...lookupResult.exchanges[0].future,
            symbol: "ANTHROPICUSDT",
            base: "ANTHROPIC",
            exchange: "bitget",
            raw_symbol: "ANTHROPICUSDT"
          },
          error: null
        }
      ],
      spreads: [{
        ...lookupResult.spreads[0],
        id: "hyperliquid:future->bitget:future",
        buy_exchange: "hyperliquid",
        buy_market_type: "future" as const,
        sell_exchange: "bitget",
        sell_market_type: "future" as const
      }]
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/instruments/")) return Response.json(hyperliquidResult);
      return Response.json({});
    });
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getByRole("button", {
      name: "价差查询 ANTHROPICUSDT hyperliquid:future->bitget:future"
    }));

    const params = new URLSearchParams(window.location.search);
    expect(params.get("leg1_symbol")).toBe("ANTH");
    expect(params.get("leg1_dex")).toBe("io");
    expect(params.get("leg2_symbol")).toBe("ANTHROPICUSDT");
    expect(params.get("leg2_dex")).toBeNull();
  });

  it("selects the Hyperliquid main DEX instead of keeping a stale DEX", async () => {
    window.history.replaceState({}, "", "/?page=instrument&symbol=BTCUSDT&leg1_dex=io");
    const hyperliquidMainResult = {
      ...lookupResult,
      exchanges: [
        {
          exchange: "hyperliquid",
          spot: null,
          future: {
            ...lookupResult.exchanges[0].future,
            exchange: "hyperliquid",
            raw_symbol: "BTC"
          },
          error: null
        },
        lookupResult.exchanges[0]
      ],
      spreads: [{
        ...lookupResult.spreads[0],
        id: "hyperliquid:future->binance:future",
        buy_exchange: "hyperliquid",
        buy_market_type: "future" as const
      }]
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/instruments/")) return Response.json(hyperliquidMainResult);
      return Response.json({});
    });
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getByRole("button", {
      name: "价差查询 BTCUSDT hyperliquid:future->binance:future"
    }));

    const params = new URLSearchParams(window.location.search);
    expect(params.get("leg1_symbol")).toBe("BTC");
    expect(params.get("leg1_dex")).toBe("main");
  });

  it("disables spread queries with unsupported spot legs", async () => {
    const unsupportedResult = {
      ...lookupResult,
      exchanges: lookupResult.exchanges.map((item) => item.exchange === "aster" ? {
        ...item,
        spot: {
          ...lookupResult.exchanges[0].spot,
          exchange: "aster",
          raw_symbol: "BTCUSDT"
        }
      } : item),
      spreads: [{
        ...lookupResult.spreads[0],
        id: "aster:spot->binance:future",
        buy_exchange: "aster",
        buy_market_type: "spot" as const
      }]
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/instruments/")) return Response.json(unsupportedResult);
      return Response.json({});
    });
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    expect(screen.getByRole<HTMLButtonElement>("button", {
      name: "价差查询 BTCUSDT aster:spot->binance:future"
    }).disabled).toBe(true);
  });

  it("ignores an older preview response after another route is selected", async () => {
    const pendingPreviews: Array<(response: Response) => void> = [];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/astro/instrument/preview")) {
        return new Promise<Response>((resolve) => pendingPreviews.push(resolve));
      }
      if (url.includes("/instruments/")) return Response.json(lookupResult);
      return Response.json({});
    });
    render(<InstrumentLookupPage />);
    await screen.findByText("跨市场差价");

    const cardButtons = screen.getAllByRole("button", { name: /建卡/ });
    await userEvent.click(cardButtons[0]);
    await waitFor(() => expect(pendingPreviews).toHaveLength(1));
    await userEvent.click(cardButtons[1]);
    await waitFor(() => expect(pendingPreviews).toHaveLength(2));

    pendingPreviews[1](Response.json({
      ...astroPlan,
      source_open_spread_pct: 0.03,
      pair: { ...astroPlan.pair, name: "SECOND", type: "FF" }
    }));
    expect(await screen.findByText("SECOND")).not.toBeNull();

    pendingPreviews[0](Response.json({
      ...astroPlan,
      pair: { ...astroPlan.pair, name: "FIRST" }
    }));
    await waitFor(() => expect(screen.queryByText("FIRST")).toBeNull());
    expect(screen.getByText("SECOND")).not.toBeNull();
  });

  it("locks the preview modal while a card submission is pending", async () => {
    let resolveCreate: ((response: Response) => void) | undefined;
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/astro/instrument/preview")) return Response.json(astroPlan);
      if (url.includes("/astro/instrument/card")) {
        return new Promise<Response>((resolve) => { resolveCreate = resolve; });
      }
      if (url.includes("/instruments/")) return Response.json(lookupResult);
      return Response.json({});
    });
    render(<InstrumentLookupPage />);
    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getAllByRole("button", { name: /建卡/ })[0]);
    await screen.findByText("BTC");

    await userEvent.click(screen.getByRole("button", { name: "确认创建" }));
    await waitFor(() => expect(resolveCreate).toBeTypeOf("function"));
    await waitFor(() => {
      expect(document.querySelectorAll(".ant-modal-footer button:disabled").length).toBeGreaterThanOrEqual(2);
    });

    resolveCreate?.(Response.json({
      enabled: true,
      status: "created",
      action: "add",
      message: "创建完成",
      pair_name: "BTC",
      pair_type: "SF"
    }));
    expect(await screen.findByText("创建完成")).not.toBeNull();
  });

  it("queries a new normalized symbol from the keyboard", async () => {
    render(<InstrumentLookupPage />);
    const input = await screen.findByLabelText("查询标的");
    fireEvent.change(input, { target: { value: "eth-usdt" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      expect((fetch as ReturnType<typeof vi.fn>).mock.calls.some(([value]) => String(value).includes("/instruments/ETHUSDT"))).toBe(true);
    });
  });
});
