import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const tradeAvailabilityStatus = {
  query: "ZETAUSDT",
  observed_at: "2026-09-21T04:54:00Z",
  source: "各交易所公开市场元数据与实时订单簿；未发送订单",
  coverage: [],
  errors: {},
  limitations: [
    "公开接口只能判断市场级限制；账户级限制需结合真实订单错误确认。",
    "1% 深度来自当前公开盘口快照，不是成交保证；手续费未计入。"
  ],
  markets: [
    {
      exchange: "hyperliquid",
      market_type: "future",
      symbol: "ZETAUSDT",
      dex: "main",
      raw_symbol: "ZETA",
      coverage_tier: "existing",
      observed_at: "2026-09-21T04:54:00Z",
      market_data_updated_at: "2026-09-21T04:54:00Z",
      orderbook_updated_at: "2026-09-21T04:54:00Z",
      orderbook_source: "Hyperliquid l2Book",
      public_status_code: "OPEN_INTEREST_CAP",
      public_status_source: "Hyperliquid public info API",
      public_restrictions: ["官方 OI 已达上限，普通增仓受限"],
      diagnostics: [
        { scope: "public_market", state: "confirmed", reason_code: "PUBLIC_STATUS_OBSERVED", message: "官方 OI 已达上限，普通增仓受限", source: "Hyperliquid public info API", raw_error: null },
        { scope: "account", state: "not_checked", reason_code: "ACCOUNT_NOT_AUTHORIZED", message: "未接入账户私有权限", source: null, raw_error: null },
        { scope: "order_error", state: "not_provided", reason_code: "ORDER_ERROR_NOT_PROVIDED", message: "没有发送探测订单", source: null, raw_error: null }
      ],
      at_open_interest_cap: true,
      is_delisted: false,
      only_isolated: false,
      margin_mode: null,
      max_leverage: 3,
      size_decimals: 1,
      best_bid: 0.06802,
      best_ask: 0.06803,
      bid_depth_1pct_usdt: 18000,
      ask_depth_1pct_usdt: 22000,
      mark_price: 0.06802,
      oracle_price: 0.06802,
      index_price: 0.06802,
      open_interest: 34225314.8,
      open_interest_usdt: 2328000,
      volume_24h_usdt: 1183442,
      funding_rate_pct: 0.00755,
      funding_interval_hours: 1,
      market_multiplier: 1,
      contract_size_multiplier: 1,
      maker_fee_pct: null,
      taker_fee_pct: null,
      fees_included: false,
      fee_note: "手续费未计入；实际费率取决于账户等级和订单类型",
      spot_transfer: null,
      buy_open: {
        state: "blocked",
        reason_code: "OPEN_INTEREST_CAP",
        reason: "官方未平仓量已达上限；普通订单不能新开或增加仓位",
        scope: "public_market",
        executable_price: null,
        depth_1pct_usdt: null
      },
      sell_open: {
        state: "blocked",
        reason_code: "OPEN_INTEREST_CAP",
        reason: "官方未平仓量已达上限；普通订单不能新开或增加仓位",
        scope: "public_market",
        executable_price: null,
        depth_1pct_usdt: null
      },
      buy_reduce_only: {
        state: "conditional",
        reason_code: "REDUCE_ONLY_REQUIRES_POSITION",
        reason: "原则上可用于平空，但必须勾选 Reduce Only，且数量不能超过对应持仓",
        scope: "account",
        executable_price: 0.06803,
        depth_1pct_usdt: 22000
      },
      sell_reduce_only: {
        state: "conditional",
        reason_code: "REDUCE_ONLY_REQUIRES_POSITION",
        reason: "原则上可用于平多，但必须勾选 Reduce Only，且数量不能超过对应持仓",
        scope: "account",
        executable_price: 0.06802,
        depth_1pct_usdt: 18000
      }
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
        if (url.includes("/settings/floating-watch/items") && init?.method === "POST") {
          return Response.json({ symbols: ["BTCUSDT"], pair_ids: [] });
        }
        if (url.includes("/trade-status/watches/list")) return Response.json([]);
        if (url.includes("/trade-status/")) return Response.json({
          query: "BTCUSDT",
          observed_at: "2026-09-21T04:54:00Z",
          source: "各交易所公开市场元数据与实时订单簿；未发送订单",
          markets: [],
          coverage: [],
          errors: {},
          limitations: []
        });
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

  it("shows the unified public restriction and creates an exact-market recovery watch", async () => {
    const zetaLookup = {
      ...lookupResult,
      query: "ZETAUSDT",
      symbol: "ZETAUSDT",
      base: "ZETA",
      exchanges: lookupResult.exchanges.map((exchange) => exchange.exchange === "hyperliquid" ? {
        ...exchange,
        future: {
          symbol: "ZETAUSDT",
          base: "ZETA",
          quote: "USDT",
          exchange: "hyperliquid",
          market_type: "future",
          bid: 0.06802,
          ask: 0.06803,
          volume_24h_usdt: 1183442,
          funding_rate_pct: 0.00755,
          funding_interval_hours: 1,
          mark_price: 0.06802,
          index_price: 0.06802,
          timestamp: "2026-09-21T04:54:00Z",
          raw_symbol: "ZETA"
        }
      } : exchange)
    };
    const savedWatch = {
      id: "watch-zeta",
      symbol: "ZETAUSDT",
      exchange: "hyperliquid",
      market_type: "future",
      dex: "main",
      raw_symbol: "ZETA",
      monitor_buy: true,
      monitor_sell: true,
      enabled: true,
      last_buy_state: "blocked",
      last_sell_state: "blocked",
      last_checked_at: "2026-09-21T04:54:00Z",
      last_notified_at: null,
      last_error: null,
      created_at: "2026-09-21T04:54:00Z",
      updated_at: "2026-09-21T04:54:00Z"
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/trade-status/watches/list")) return Response.json([]);
      if (url.includes("/trade-status/watches") && init?.method === "POST") return Response.json(savedWatch);
      if (url.includes("/trade-status/")) return Response.json(tradeAvailabilityStatus);
      if (url.includes("/instruments/")) return Response.json(zetaLookup);
      return Response.json({});
    });
    window.history.replaceState({}, "", "/?page=instrument&symbol=ZETAUSDT");

    render(<InstrumentLookupPage />);

    expect(await screen.findByText("全交易所交易可用性")).not.toBeNull();
    expect(screen.getByText("官方 OI 已达上限，普通增仓受限")).not.toBeNull();
    expect(screen.getByText(/DEX main/)).not.toBeNull();
    expect(screen.getByText("账户 未核验")).not.toBeNull();
    expect(screen.getByText("真实订单 未提供")).not.toBeNull();
    expect(screen.getByText("普通开仓")).not.toBeNull();
    expect(screen.getByText("Reduce Only 平仓")).not.toBeNull();
    expect(screen.getByText("开多")).not.toBeNull();
    expect(screen.getByText("开空")).not.toBeNull();
    expect(screen.getByText("平空 · Buy")).not.toBeNull();
    expect(screen.getByText("平多 · Sell")).not.toBeNull();
    expect(screen.getAllByText("公开受限")).toHaveLength(2);
    expect(screen.getAllByText("账户有条件")).toHaveLength(2);

    const alertWatchButton = screen.getByRole("button", {
      name: "告警 Hyperliquid / future / main / ZETA 订阅恢复通知"
    });
    expect(alertWatchButton).not.toBeNull();
    await userEvent.click(alertWatchButton);
    await waitFor(() => {
      const request = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(
        ([input, init]) => String(input).includes("/trade-status/watches") && init?.method === "POST"
      );
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        symbol: "ZETAUSDT",
        exchange: "hyperliquid",
        market_type: "future",
        dex: "main",
        raw_symbol: "ZETA",
        monitor_buy: true,
        monitor_sell: true
      });
    });
    expect(screen.getByRole("button", {
      name: "告警 Hyperliquid / future / main / ZETA 取消恢复通知"
    })).not.toBeNull();
  });

  it("hides Reduce Only actions for spot markets", async () => {
    const spotMarket = {
      ...tradeAvailabilityStatus.markets[0],
      exchange: "binance",
      market_type: "spot",
      symbol: "BTCUSDT",
      dex: null,
      raw_symbol: "BTCUSDT",
      coverage_tier: "core",
      public_status_code: "TRADING",
      public_status_source: "Binance spot exchangeInfo",
      public_restrictions: [],
      spot_transfer: {
        asset: "BTC",
        deposit_state: "partial",
        withdraw_state: "enabled",
        all_enabled: false,
        publicly_queryable: true,
        source: "Binance public asset service",
        observed_at: "2026-09-21T04:54:00Z",
        networks: [
          { network: "BTC", deposit_enabled: true, withdraw_enabled: true },
          { network: "BSC", deposit_enabled: false, withdraw_enabled: true }
        ],
        note: "按公开返回的逐链开关汇总",
        error: null
      },
      buy_open: {
        state: "available",
        reason_code: "PUBLIC_MARKET_AVAILABLE",
        reason: "公开市场状态允许且实时盘口有报价",
        scope: "public_market",
        executable_price: 100001,
        depth_1pct_usdt: 22000
      },
      sell_open: {
        state: "available",
        reason_code: "PUBLIC_MARKET_AVAILABLE",
        reason: "公开市场状态允许且实时盘口有报价",
        scope: "public_market",
        executable_price: 100000,
        depth_1pct_usdt: 18000
      },
      buy_reduce_only: {
        state: "not_applicable",
        reason_code: "REDUCE_ONLY_NOT_SUPPORTED_FOR_SPOT",
        reason: "现货市场没有 Reduce Only 持仓语义",
        scope: "platform_capability",
        executable_price: null,
        depth_1pct_usdt: null
      },
      sell_reduce_only: {
        state: "not_applicable",
        reason_code: "REDUCE_ONLY_NOT_SUPPORTED_FOR_SPOT",
        reason: "现货市场没有 Reduce Only 持仓语义",
        scope: "platform_capability",
        executable_price: null,
        depth_1pct_usdt: null
      }
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/trade-status/watches/list")) return Response.json([]);
      if (url.includes("/trade-status/")) return Response.json({
        ...tradeAvailabilityStatus,
        query: "BTCUSDT",
        markets: [spotMarket]
      });
      if (url.includes("/instruments/")) return Response.json(lookupResult);
      return Response.json({});
    });

    render(<InstrumentLookupPage />);

    expect(await screen.findByText("全交易所交易可用性")).not.toBeNull();
    expect(screen.queryByText("不适用")).toBeNull();
    expect(screen.queryByText("平空 · Buy")).toBeNull();
    expect(screen.queryByText("平多 · Sell")).toBeNull();
    expect(screen.queryByText("Reduce Only 平仓")).toBeNull();
    expect(screen.queryByText("Sell / Short")).toBeNull();
    expect(screen.getByText("买入")).not.toBeNull();
    expect(screen.getByText("卖出")).not.toBeNull();
    const transferTable = screen.getByRole("table", { name: "现货逐链充提状态" });
    expect(within(transferTable).getAllByText("BTC").length).toBeGreaterThan(0);
    expect(within(transferTable).getByText("BSC")).not.toBeNull();
    expect(within(transferTable).getAllByText("开启")).toHaveLength(3);
    expect(within(transferTable).getByText("暂停")).not.toBeNull();
    expect(within(transferTable).queryByText("部分开放")).toBeNull();
    expect(within(transferTable).queryByText("2 条链")).toBeNull();
    expect(screen.getByText("1 个现货充提有异常")).not.toBeNull();
  });

  it("adds the current symbol to the floating watch", async () => {
    render(<InstrumentLookupPage />);
    await screen.findByText("BTC / USDT");

    await userEvent.click(screen.getByRole("button", { name: /加入浮窗/ }));

    await waitFor(() => {
      const request = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(
        ([input, init]) => String(input).includes("/settings/floating-watch/items") && init?.method === "POST"
      );
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        action: "add",
        item_type: "symbol",
        value: "BTCUSDT"
      });
    });
  });

  it("saves queried symbols for one-click lookup after reopening the page", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      const symbol = String(input).includes("/instruments/ETHUSDT") ? "ETHUSDT" : "BTCUSDT";
      return Response.json({
        ...lookupResult,
        symbol,
        base: symbol.slice(0, -4),
        query: symbol
      });
    });
    const page = render(<InstrumentLookupPage />);
    await screen.findByText("BTC / USDT");

    await userEvent.click(screen.getByRole("button", { name: "保存当前标的" }));
    expect(JSON.parse(window.localStorage.getItem("taoli1.instrumentLookup.savedSymbols.v1") ?? "null")).toEqual(["BTCUSDT"]);
    expect(screen.getByRole("button", { name: "保存当前标的" }).hasAttribute("disabled")).toBe(true);

    await userEvent.clear(screen.getByRole("textbox", { name: "查询标的" }));
    await userEvent.type(screen.getByRole("textbox", { name: "查询标的" }), "eth{enter}");
    await screen.findByText("ETH / USDT");
    await userEvent.click(screen.getByRole("button", { name: "保存当前标的" }));
    expect(JSON.parse(window.localStorage.getItem("taoli1.instrumentLookup.savedSymbols.v1") ?? "null")).toEqual(["ETHUSDT", "BTCUSDT"]);

    page.unmount();
    window.history.replaceState({}, "", "/?page=instrument&symbol=ETHUSDT");
    render(<InstrumentLookupPage />);
    await screen.findByText("ETH / USDT");
    const btcLookupCount = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([input]) => String(input).includes("/instruments/BTCUSDT")).length;
    await userEvent.click(screen.getByRole("button", { name: "BTCUSDT" }));
    await waitFor(() => {
      expect((fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([input]) => String(input).includes("/instruments/BTCUSDT")).length).toBe(btcLookupCount + 1);
      expect(screen.getByRole<HTMLInputElement>("textbox", { name: "查询标的" }).value).toBe("BTCUSDT");
    });

    await userEvent.click(screen.getByRole("button", { name: "移除已保存标的 BTCUSDT" }));
    expect(screen.queryByRole("button", { name: "BTCUSDT" })).toBeNull();
    expect(JSON.parse(window.localStorage.getItem("taoli1.instrumentLookup.savedSymbols.v1") ?? "null")).toEqual(["ETHUSDT"]);
  });

  it("does not save a typed symbol until it has been looked up and tolerates invalid saved data", async () => {
    window.localStorage.setItem("taoli1.instrumentLookup.savedSymbols.v1", "invalid JSON");
    render(<InstrumentLookupPage />);
    await screen.findByText("BTC / USDT");
    expect(screen.queryByText("已保存")).toBeNull();

    await userEvent.clear(screen.getByRole("textbox", { name: "查询标的" }));
    await userEvent.type(screen.getByRole("textbox", { name: "查询标的" }), "ETH");
    expect(screen.getByRole("button", { name: "保存当前标的" }).hasAttribute("disabled")).toBe(true);
    expect(window.localStorage.getItem("taoli1.instrumentLookup.savedSymbols.v1")).toBe("invalid JSON");
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

  it("reverses both route legs and uses the reverse ask and bid when creating a card", async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/astro/instrument/preview")) {
        return Response.json({
          ...astroPlan,
          source_open_spread_pct: -0.1199,
          pair: {
            ...astroPlan.pair,
            type: "FS",
            buyEx: "binance",
            sellEx: "binance",
            openPosition: "-0.001199",
            closePosition: "-0.002199"
          },
          warnings: ["人工建卡风险提示：反向 SF；本次为人工建卡，仅作风险提示，未拦截创建。"]
        });
      }
      if (url.includes("/astro/instrument/card")) {
        return Response.json({
          enabled: true,
          status: "created",
          action: "add",
          message: "反向卡片创建完成",
          pair_name: "BTC",
          pair_type: "FS",
          warnings: []
        });
      }
      if (url.includes("/instruments/")) return Response.json(lookupResult);
      return Response.json({});
    });
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    expect(screen.getAllByRole("button", { name: /反向/ })).toHaveLength(3);
    await userEvent.click(screen.getAllByRole("button", { name: /反向/ })[0]);

    expect(await screen.findByText("反向创建 Astro 卡片")).not.toBeNull();
    expect(screen.getByText("FS")).not.toBeNull();
    expect(screen.getByText("Binance · 永续 · Ask 100,110")).not.toBeNull();
    expect(screen.getByText("Binance · 现货 · Bid 99,990")).not.toBeNull();
    expect(screen.getByText("当前为反向建卡")).not.toBeNull();
    expect(screen.getByText(/主动承受当前可成交价差/)).not.toBeNull();
    expect(screen.getByText(/现货侧有可卖余额或借币能力/)).not.toBeNull();

    const previewCall = (fetch as ReturnType<typeof vi.fn>).mock.calls.find(([input]) =>
      String(input).includes("/astro/instrument/preview")
    );
    expect(JSON.parse(String(previewCall?.[1]?.body))).toEqual({
      symbol: "BTCUSDT",
      buy_exchange: "binance",
      buy_market_type: "future",
      sell_exchange: "binance",
      sell_market_type: "spot"
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
    const payload = JSON.parse(String(createCall?.[1]?.body));
    expect(payload.route).toEqual({
      symbol: "BTCUSDT",
      buy_exchange: "binance",
      buy_market_type: "future",
      sell_exchange: "binance",
      sell_market_type: "spot"
    });
    expect(payload.expected_open_spread_pct).toBe(-0.1199);
  });

  it("hides uncommon spread types by default and can reveal and sort every spread", async () => {
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
    const ssFilter = screen.getByRole<HTMLInputElement>("checkbox", { name: "SS" });
    const reverseSfFilter = screen.getByRole<HTMLInputElement>("checkbox", { name: "反向 SF" });
    expect(ssFilter.checked).toBe(true);
    expect(reverseSfFilter.checked).toBe(true);
    expect(screen.getAllByRole("button", { name: /建卡/ })).toHaveLength(7);
    expect(screen.getByText("7 / 13 组")).not.toBeNull();

    await userEvent.click(ssFilter);
    await userEvent.click(reverseSfFilter);
    expect(screen.getAllByRole("button", { name: /建卡/ })).toHaveLength(13);
    expect(screen.getByText("13 / 13 组")).not.toBeNull();
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

  it("sorts buy and sell markets by exchange name", async () => {
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    const buyHeader = screen.getByRole("columnheader", { name: /买入市场/ });
    await userEvent.click(buyHeader);
    const table = buyHeader.closest("table");
    const buyMarkets = Array.from(table?.querySelectorAll("tbody tr.ant-table-row") ?? []).map(
      (row) => row.querySelectorAll("td")[1]?.textContent
    );
    expect(buyMarkets).toEqual(["Binance现货", "Binance现货", "OKX永续"]);

    const sellHeader = screen.getByRole("columnheader", { name: /卖出市场/ });
    await userEvent.click(sellHeader);
    const sellMarkets = Array.from(table?.querySelectorAll("tbody tr.ant-table-row") ?? []).map(
      (row) => row.querySelectorAll("td")[3]?.textContent
    );
    expect(sellMarkets).toEqual(["Binance永续", "Binance永续", "OKX永续"]);
  });

  it("opens every selected market pair in a separate isolated tab without changing the lookup page", async () => {
    window.history.replaceState({}, "", "/?page=instrument&symbol=BTCUSDT&leg1_dex=stale&leg2_dex=stale");
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const navigate = vi.fn();
    window.addEventListener("taoli1:navigate", navigate);
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    const chartButton = screen.getByRole("button", {
      name: "价差查询 BTCUSDT okx:future->binance:future"
    });
    const currentUrl = window.location.href;
    await userEvent.click(chartButton);
    await userEvent.click(chartButton);

    expect(open).toHaveBeenCalledTimes(2);
    expect(open.mock.calls[0][1]).toBe("_blank");
    expect(open.mock.calls[0][2]).toBe("noopener,noreferrer");
    expect(open.mock.calls[1][1]).toBe("_blank");
    expect(open.mock.calls[1][2]).toBe("noopener,noreferrer");
    expect(window.location.href).toBe(currentUrl);
    const params = new URL(String(open.mock.calls[0][0])).searchParams;
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
    expect(navigate).not.toHaveBeenCalled();
    window.removeEventListener("taoli1:navigate", navigate);
    open.mockRestore();
  });

  it("keeps the reverse spot-future route direction in the new tab", async () => {
    const reverseResult = {
      ...lookupResult,
      spreads: [{
        ...lookupResult.spreads[0],
        id: "binance:future->binance:spot",
        buy_exchange: "binance",
        buy_market_type: "future" as const,
        sell_exchange: "binance",
        sell_market_type: "spot" as const,
        opportunity_type: null
      }]
    };
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input).includes("/instruments/")) return Response.json(reverseResult);
      return Response.json({});
    });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getByRole("checkbox", { name: "反向 SF" }));
    const currentUrl = window.location.href;
    await userEvent.click(screen.getByRole("button", {
      name: "价差查询 BTCUSDT binance:future->binance:spot"
    }));

    expect(window.location.href).toBe(currentUrl);
    const params = new URL(String(open.mock.calls[0][0])).searchParams;
    expect(params.get("leg1_exchange")).toBe("binance");
    expect(params.get("leg1_market_type")).toBe("future");
    expect(params.get("leg1_symbol")).toBe("BTCUSDT");
    expect(params.get("leg2_exchange")).toBe("binance");
    expect(params.get("leg2_market_type")).toBe("spot");
    expect(params.get("leg2_symbol")).toBe("BTCUSDT");
    expect(params.get("leg2_multiplier")).toBe("1");
    expect(open).toHaveBeenCalledWith(expect.any(String), "_blank", "noopener,noreferrer");
    open.mockRestore();
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
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getByRole("button", {
      name: "价差查询 ANTHROPICUSDT hyperliquid:future->bitget:future"
    }));

    const params = new URL(String(open.mock.calls[0][0])).searchParams;
    expect(params.get("leg1_symbol")).toBe("ANTH");
    expect(params.get("leg1_dex")).toBe("io");
    expect(params.get("leg2_symbol")).toBe("ANTHROPICUSDT");
    expect(params.get("leg2_dex")).toBeNull();
    open.mockRestore();
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
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    await userEvent.click(screen.getByRole("button", {
      name: "价差查询 BTCUSDT hyperliquid:future->binance:future"
    }));

    const params = new URL(String(open.mock.calls[0][0])).searchParams;
    expect(params.get("leg1_symbol")).toBe("BTC");
    expect(params.get("leg1_dex")).toBe("main");
    open.mockRestore();
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
