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
      buy_bid: 99990,
      buy_ask: 100010,
      sell_exchange: "binance",
      sell_market_type: "future",
      sell_bid: 100090,
      sell_ask: 100110,
      price_difference: 80,
      executable_spread_pct: 0.08,
      close_spread_pct: 0.12,
      mid_spread_pct: 0.1,
      opportunity_type: "SF",
      astro_supported: true,
      astro_blocker: null
    },
    {
      id: "okx:future->binance:future",
      buy_exchange: "okx",
      buy_market_type: "future",
      buy_bid: 100040,
      buy_ask: 100060,
      sell_exchange: "binance",
      sell_market_type: "future",
      sell_bid: 100090,
      sell_ask: 100110,
      price_difference: 30,
      executable_spread_pct: 0.03,
      close_spread_pct: 0.07,
      mid_spread_pct: 0.05,
      opportunity_type: "FF",
      astro_supported: true,
      astro_blocker: null
    },
    {
      id: "binance:spot->okx:future",
      buy_exchange: "binance",
      buy_market_type: "spot",
      buy_bid: 99990,
      buy_ask: 100010,
      sell_exchange: "okx",
      sell_market_type: "future",
      sell_bid: 100040,
      sell_ask: 100060,
      price_difference: 30,
      executable_spread_pct: 0.03,
      close_spread_pct: 0.07,
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
  card_variant: "both",
  route_variants: [
    { card_variant: "non_gc", buy_exchange: "binance", sell_exchange: "binance" },
    { card_variant: "gc", buy_exchange: "gc-binance", sell_exchange: "gc-binance" }
  ],
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
  index_compositions: [
    {
      exchange: "binance",
      market_type: "future",
      symbol: "ZETAUSDT",
      raw_symbol: "ZETAUSDT",
      dex: null,
      status: "available",
      source: "Binance fapi constituents",
      index_price: 0.068025,
      observed_at: "2026-09-21T04:54:00Z",
      weight_total: 1,
      components: [
        {
          source_exchange: "Coinbase",
          market_type: "spot",
          raw_symbol: "ZETA-USD",
          weight: 0.45,
          price: 0.06801
        }
      ],
      note: "仅展示官方指数成分接口原始返回",
      error: null
    },
    {
      exchange: "hyperliquid",
      market_type: "future",
      symbol: "ZETAUSDT",
      raw_symbol: "ZETA",
      dex: "main",
      status: "not_returned",
      source: "Hyperliquid metaAndAssetCtxs",
      index_price: null,
      observed_at: "2026-09-21T04:54:00Z",
      weight_total: null,
      components: [],
      note: "官方未返回可核验的加权指数成分与权重",
      error: null
    }
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
      bid_depth_01pct_usdt: 9000,
      ask_depth_01pct_usdt: 11000,
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
        state: "available",
        reason_code: "REDUCE_ONLY_AVAILABLE",
        reason: "公开规则允许已有对应空仓使用 Reduce Only 减仓",
        scope: "public_market",
        executable_price: 0.06803,
        depth_1pct_usdt: 22000
      },
      sell_reduce_only: {
        state: "available",
        reason_code: "REDUCE_ONLY_AVAILABLE",
        reason: "公开规则允许已有对应多仓使用 Reduce Only 减仓",
        scope: "public_market",
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
          index_compositions: [],
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
    expect(screen.getByText("100,000 - 100,100")).not.toBeNull();
    expect(screen.getByText("+0.100% · Binance")).not.toBeNull();
    expect(screen.getAllByText("Binance").length).toBeGreaterThan(0);
    expect(screen.queryByText("行情实时")).toBeNull();
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
      last_buy_reduce_only_state: "available",
      last_sell_reduce_only_state: "available",
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

    const marketTable = await screen.findByRole("table", { name: "精确行情市场" });
    const zetaRow = within(marketTable).getByText(/^永续 · DEX main · ZETA$/).closest("article")!;
    expect(zetaRow.children[4].textContent).toContain("诊断已过期");
    expect(zetaRow.children[5].textContent).toContain("诊断已过期");
    expect(zetaRow.children[4].textContent).toContain("9000 USDT");
    expect(zetaRow.children[5].textContent).toContain("上次公开可用");
    await userEvent.click(within(zetaRow).getByText(/市场诊断/));
    const details = zetaRow.querySelector("details") as HTMLDetailsElement;
    expect(within(zetaRow).getAllByText("诊断已过期").length).toBeGreaterThan(0);
    expect(within(zetaRow).getByText(/与上方行情时间分别记录，不视为同步报价/)).not.toBeNull();
    expect(within(zetaRow).getByText("Maker / Taker")).not.toBeNull();
    expect(within(zetaRow).getByText("手续费未计入；实际费率取决于账户等级和订单类型")).not.toBeNull();
    expect(screen.getByText("账户数据未接入 · 未发送探测订单 · 当前仅依据公开市场数据判断")).not.toBeNull();
    expect(within(details).getByText("官方 OI 已达上限，普通增仓受限")).not.toBeNull();
    expect(within(details).getByText(/市场诊断 · Hyperliquid · 永续 · DEX main · ZETA/)).not.toBeNull();
    expect(screen.queryByText("账户未接入")).toBeNull();
    expect(screen.queryByText("真实订单 未提供")).toBeNull();
    expect(within(details).getByText("开多")).not.toBeNull();
    expect(within(details).getByText("开空")).not.toBeNull();
    expect(within(details).getByText("平空")).not.toBeNull();
    expect(within(details).getByText("平多")).not.toBeNull();
    expect(screen.getByText("Reduce Only Buy")).not.toBeNull();
    expect(screen.getByText("Reduce Only Sell")).not.toBeNull();
    expect(screen.getAllByText("受限")).toHaveLength(2);
    expect(screen.getAllByText("可用")).toHaveLength(2);
    expect(screen.getByText("合约指数成分")).not.toBeNull();
    expect(screen.getByText("45.00%")).not.toBeNull();
    expect(screen.getByText("ZETA-USD")).not.toBeNull();
    expect(screen.getByText("Coinbase · 现货")).not.toBeNull();
    expect(screen.getAllByText("未返回").length).toBeGreaterThan(0);

    expect(screen.queryByRole("table", { name: "交易所市场行情与规格" })).toBeNull();
    expect(within(zetaRow).getByText("盘口实际买一 Bid")).not.toBeNull();
    expect(within(marketTable).getByText("0.1% 买深度")).not.toBeNull();
    expect(within(marketTable).getByText("0.1% 卖深度")).not.toBeNull();
    expect(within(marketTable).getByText("1% 买深度")).not.toBeNull();
    expect(within(marketTable).getByText("1% 卖深度")).not.toBeNull();

    const alertWatchButton = screen.getByRole("button", {
      name: "市场 Hyperliquid future main ZETA 订阅恢复通知"
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
      name: "市场 Hyperliquid future main ZETA 取消恢复通知"
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

    const marketTable = await screen.findByRole("table", { name: "精确行情市场" });
    await waitFor(() => expect(within(marketTable).getByText(/^市场诊断 ·/)).not.toBeNull());
    await userEvent.click(within(marketTable).getByText(/^市场诊断 ·/));
    const details = marketTable.querySelector("details") as HTMLDetailsElement;
    expect(screen.queryByText("不适用")).toBeNull();
    expect(screen.queryByText("平空")).toBeNull();
    expect(screen.queryByText("平多")).toBeNull();
    expect(screen.queryByText("Reduce Only Buy")).toBeNull();
    expect(screen.queryByText("Reduce Only Sell")).toBeNull();
    expect(screen.queryByText("Sell / Short")).toBeNull();
    expect(within(details).getByText("买入")).not.toBeNull();
    expect(within(details).getByText("卖出")).not.toBeNull();
    const transferTable = screen.getByRole("table", { name: "现货逐链充提状态" });
    expect(within(transferTable).getAllByText("BTC").length).toBeGreaterThan(0);
    expect(within(transferTable).getByText("BSC")).not.toBeNull();
    expect(within(transferTable).getAllByText("开启")).toHaveLength(3);
    expect(within(transferTable).getByText("暂停")).not.toBeNull();
    expect(within(transferTable).queryByText("部分开放")).toBeNull();
    expect(within(transferTable).queryByText("2 条链")).toBeNull();
    expect(screen.getByText("现货充提通道")).not.toBeNull();
    expect(screen.getByText("合约指数成分")).not.toBeNull();
    expect(screen.queryByText("交易所市场")).toBeNull();
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
    expect(screen.getByText("按可成交盘口计算，每组市场保留较优方向")).not.toBeNull();
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

    await userEvent.click(screen.getByText("仅非 GC"));
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
    expect(JSON.parse(String(createCall?.[1]?.body)).card.card_variant).toBe("non_gc");
    expect(await screen.findAllByText("已创建暂停卡片 BTC SF binance->binance")).toHaveLength(2);
    expect(screen.getByText(/人工建卡，仅作风险提示，未拦截创建/)).not.toBeNull();
  });

  it("autocompletes exchange names and filters either spread leg alongside type filters", async () => {
    const extendedLookup = {
      ...lookupResult,
      spreads: [
        ...lookupResult.spreads,
        {
          ...lookupResult.spreads[1],
          id: "lighter:future->okx:future",
          buy_exchange: "lighter",
          sell_exchange: "okx",
          executable_spread_pct: 0.12
        },
        {
          ...lookupResult.spreads[1],
          id: "rh-lighter:future->binance:future",
          buy_exchange: "rh-lighter",
          sell_exchange: "binance",
          executable_spread_pct: 0.2
        }
      ]
    };
    const fallbackFetch = vi.mocked(fetch).getMockImplementation();
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/instruments/")) return Response.json(extendedLookup);
      return fallbackFetch!(input, init);
    });

    render(<InstrumentLookupPage />);
    const section = (await screen.findByText("跨市场差价")).closest("section")!;
    const search = within(section).getByRole("combobox", { name: "搜索差价交易所" });
    expect(within(section).getByText("5 / 5 组")).not.toBeNull();

    await userEvent.type(search, "li");
    expect(within(section).getByText("2 / 5 组")).not.toBeNull();
    await waitFor(() => {
      expect([...document.querySelectorAll(".ant-select-item-option")]
        .map((option) => option.textContent?.trim())).toEqual(["Lighter", "RH Lighter"]);
    });

    const rhOption = [...document.querySelectorAll<HTMLElement>(".ant-select-item-option")]
      .find((option) => option.textContent?.trim() === "RH Lighter");
    expect(rhOption).toBeDefined();
    await userEvent.click(rhOption!);
    expect(within(section).getByText("1 / 5 组")).not.toBeNull();
    expect(within(section).getByText("+0.200%")).not.toBeNull();

    await userEvent.clear(search);
    await userEvent.type(search, "lighter");
    expect(within(section).getByText("1 / 5 组")).not.toBeNull();
    await userEvent.clear(search);
    await userEvent.type(search, "OKX");
    expect(within(section).getByText("3 / 5 组")).not.toBeNull();
    await userEvent.click(within(section).getByRole("checkbox", { name: "FF 合约-合约" }));
    expect(within(section).getByText("1 / 5 组")).not.toBeNull();
    await userEvent.clear(search);
    expect(within(section).getByText("2 / 5 组")).not.toBeNull();
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
    const ssFilter = screen.getByRole<HTMLInputElement>("checkbox", { name: "SS 现货-现货" });
    const reverseSfFilter = screen.getByRole<HTMLInputElement>("checkbox", { name: "反向 SF 合约-现货" });
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
      (row) => row.querySelector(".instrument-spread-type-tag strong")?.textContent
    );
    expect(ascendingTypes).toEqual([
      "FF", "FF", "FF",
      "SF", "SF", "SF", "SF",
      "SS", "SS", "SS",
      "反向 SF", "反向 SF", "反向 SF"
    ]);

    await userEvent.click(typeHeader);
    const descendingTypes = Array.from(table?.querySelectorAll("tbody tr.ant-table-row") ?? []).map(
      (row) => row.querySelector(".instrument-spread-type-tag strong")?.textContent
    );
    expect(descendingTypes).toEqual([
      "反向 SF", "反向 SF", "反向 SF",
      "SS", "SS", "SS",
      "SF", "SF", "SF", "SF",
      "FF", "FF", "FF"
    ]);
  });

  it("spells out spread routes and visually distinguishes spot from perpetual markets", async () => {
    render(<InstrumentLookupPage />);

    await screen.findByText("跨市场差价");
    const spreadTags = Array.from(document.querySelectorAll(".instrument-spread-type-tag"));
    expect(spreadTags.map((tag) => tag.querySelector("span")?.textContent)).toEqual([
      "SF · 现货 → 合约",
      "FF · 合约 → 合约",
      "SF · 现货 → 合约"
    ]);
    expect(spreadTags.map((tag) => tag.querySelector("b")?.textContent)).toEqual([
      "bn → bn",
      "okx → bn",
      "bn → okx"
    ]);
    const spreadSection = screen.getByText("跨市场差价").closest("section")!;
    expect(within(spreadSection).getByRole("columnheader", { name: /开仓价差/ })).not.toBeNull();
    expect(within(spreadSection).getByRole("columnheader", { name: /平仓价差/ })).not.toBeNull();
    expect(within(spreadSection).queryByRole("columnheader", { name: /中价差|价差额/ })).toBeNull();
    expect(within(spreadSection).getByText("+0.120%")).not.toBeNull();

    const marketTags = Array.from(document.querySelectorAll(".instrument-market-type-tag"));
    expect(marketTags.filter((tag) => tag.classList.contains("instrument-market-type-tag--spot"))).toHaveLength(2);
    expect(marketTags.filter((tag) => tag.classList.contains("instrument-market-type-tag--future"))).toHaveLength(4);
    expect(marketTags.map((tag) => tag.textContent)).toEqual([
      "现货", "永续合约",
      "永续合约", "永续合约",
      "现货", "永续合约"
    ]);
  });

  it("uses the requested exchange abbreviations in buy-to-sell order", async () => {
    const routes = [
      ["binance", "hyperliquid", "bn → hl"],
      ["bybit", "gate", "by → gate"],
      ["bitget", "lighter", "bg → lit"],
      ["rh-lighter", "aster", "rh-lit → aster"],
      ["okx", "binance", "okx → bn"]
    ];
    const spreads = routes.map(([buy, sell], index) => ({
      ...lookupResult.spreads[1],
      id: `${buy}:${index}->${sell}:${index}`,
      buy_exchange: buy,
      sell_exchange: sell
    }));
    const fallbackFetch = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/instruments/")) return Response.json({ ...lookupResult, spreads });
      return fallbackFetch(input, init);
    });

    render(<InstrumentLookupPage />);
    await screen.findByText("跨市场差价");
    expect(Array.from(document.querySelectorAll(".instrument-spread-type-tag b"), (tag) => tag.textContent))
      .toEqual(routes.map((route) => route[2]));
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
    expect(buyMarkets).toEqual(["Binance现货", "Binance现货", "OKX永续合约"]);

    const sellHeader = screen.getByRole("columnheader", { name: /卖出市场/ });
    await userEvent.click(sellHeader);
    const sellMarkets = Array.from(table?.querySelectorAll("tbody tr.ant-table-row") ?? []).map(
      (row) => row.querySelectorAll("td")[3]?.textContent
    );
    expect(sellMarkets).toEqual(["Binance永续合约", "Binance永续合约", "OKX永续合约"]);
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
    await userEvent.click(screen.getByRole("checkbox", { name: "反向 SF 合约-现货" }));
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

  it("shows exact ANTHROPIC markets without a dedicated Astro route panel", async () => {
    const now = "2026-09-22T09:00:00Z";
    const lighterMarket = {
      symbol: "ANTHROPICUSDT",
      base: "ANTHROPIC",
      quote: "USDT",
      exchange: "lighter",
      market_type: "future" as const,
      bid: 2166,
      ask: 2168,
      bid_size: 2,
      ask_size: 3,
      volume_24h_usdt: 2_500_000,
      funding_rate_pct: 0.003,
      funding_interval_hours: 1,
      funding_next_time: "2026-09-22T10:00:00Z",
      mark_price: 2167,
      index_price: 2166.5,
      timestamp: now,
      raw_symbol: "ANTHROPIC",
      dex: null,
      contract_size_multiplier: 1,
      data_source: "Lighter public API",
      upstream_timestamp: now,
      is_estimated: false,
      estimated_fields: ["funding_next_time"],
      symbol_alias_price_multiplier: 1,
      data_status: "live" as const,
      age_seconds: 1,
      stale_after_seconds: 30,
      error: null
    };
    const hyperMarket = {
      ...lighterMarket,
      exchange: "hyperliquid",
      bid: 2169,
      ask: 2170,
      raw_symbol: "io:ANTH",
      dex: "io",
      data_source: "Hyperliquid io metaAndAssetCtxs + l2Book",
      estimated_fields: []
    };
    const robinhoodMarket = {
      ...lighterMarket,
      exchange: "rh-lighter",
      bid: 2193,
      ask: 2193.1,
      raw_symbol: "ANTHROPIC",
      data_source: "Robinhood Lighter public API (USDG)",
      estimated_fields: []
    };
    const binanceMarket = {
      ...lighterMarket,
      exchange: "binance",
      bid: 2172,
      ask: 2173,
      raw_symbol: "ANTHROPICUSDT",
      data_source: "Binance public futures API",
      estimated_fields: []
    };
    const spread = {
      ...lookupResult.spreads[0],
      id: "lighter:future::ANTHROPIC:1->hyperliquid:future:io:io:ANTH:1",
      buy_exchange: "lighter",
      buy_market_type: "future" as const,
      buy_raw_symbol: "ANTHROPIC",
      buy_dex: null,
      buy_price_multiplier: 1,
      buy_contract_size_multiplier: 1,
      buy_ask: 2168,
      buy_volume_24h_usdt: 2_500_000,
      buy_funding_rate_pct: 0.003,
      buy_funding_interval_hours: 1,
      buy_timestamp: now,
      buy_data_source: "Lighter public API",
      buy_is_estimated: false,
      sell_exchange: "hyperliquid",
      sell_market_type: "future" as const,
      sell_raw_symbol: "io:ANTH",
      sell_dex: "io",
      sell_price_multiplier: 1,
      sell_contract_size_multiplier: 1,
      sell_bid: 2169,
      sell_volume_24h_usdt: 3_000_000,
      sell_funding_rate_pct: -0.002,
      sell_funding_interval_hours: 1,
      sell_timestamp: now,
      sell_data_source: "Hyperliquid public API",
      sell_is_estimated: false
    };
    const anthropicLookup = {
      ...lookupResult,
      query: "ANTH",
      symbol: "ANTHROPICUSDT",
      base: "ANTHROPIC",
      exchange_count: 4,
      market_count: 4,
      markets: [lighterMarket, robinhoodMarket, hyperMarket, binanceMarket],
      astro_routes: [
        {
          card_id: "anth-route",
          card_name: "ANTHROPIC",
          side: "buy",
          route: "lighter",
          exchange: "lighter",
          market_type: "future",
          astro_raw_symbol: "ANTHROPICUSDT",
          canonical_symbol: "ANTHROPICUSDT",
          dex: null,
          counterparty_route: "rh-lighter",
          status: "live_market",
          live_data_supported: true,
          matched_raw_symbol: "ANTHROPIC",
          source: "Astro card response (route discovery only)",
          reason: "已匹配真实公开行情"
        },
        {
          card_id: "anth-route",
          card_name: "ANTHROPIC",
          side: "sell",
          route: "rh-lighter",
          exchange: "rh-lighter",
          market_type: "future",
          astro_raw_symbol: "ANTHROPICUSDT",
          canonical_symbol: "ANTHROPICUSDT",
          dex: null,
          counterparty_route: "lighter",
          status: "live_market",
          live_data_supported: true,
          matched_raw_symbol: "ANTHROPIC",
          source: "Astro card response (route discovery only)",
          reason: "已匹配 Robinhood Lighter 真实公开行情"
        }
      ],
      route_errors: {},
      spreads: [spread]
    };
    const fallbackFetch = vi.mocked(fetch).getMockImplementation();
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/instruments/")) return Response.json(anthropicLookup);
      return fallbackFetch!(input, init);
    });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    window.history.replaceState({}, "", "/?page=instrument&symbol=ANTH");

    render(<InstrumentLookupPage />);

    const exactTable = await screen.findByRole("table", { name: "精确行情市场" });
    expect(within(exactTable).getByText("Lighter")).toBeTruthy();
    expect(within(exactTable).getByText("RH Lighter")).toBeTruthy();
    expect(within(exactTable).getAllByText(/DEX io/).length).toBeGreaterThan(0);
    expect(within(exactTable).getAllByText(/ANTHROPIC/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Astro 路由证据")).toBeNull();
    expect(screen.queryByText("已匹配 Robinhood Lighter 真实公开行情")).toBeNull();

    await userEvent.click(screen.getByRole("button", {
      name: `价差查询 ANTHROPICUSDT ${spread.id}`
    }));
    const params = new URL(String(open.mock.calls[0][0])).searchParams;
    expect(params.get("leg1_raw_symbol")).toBe("ANTHROPIC");
    expect(params.get("leg1_price_multiplier")).toBe("1");
    expect(params.get("leg2_symbol")).toBe("ANTH");
    expect(params.get("leg2_raw_symbol")).toBe("io:ANTH");
    expect(params.get("leg2_dex")).toBe("io");
    expect(params.get("leg2_contract_size_multiplier")).toBe("1");
    open.mockRestore();
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

  it("pairs only the unique raw market and DEX, leaving unmatched diagnostics separate", async () => {
    const quote = lookupResult.exchanges[0].future!;
    const diagnosticTime = new Date().toISOString();
    const freshDiagnostic = {
      ...tradeAvailabilityStatus.markets[0],
      observed_at: diagnosticTime,
      market_data_updated_at: diagnosticTime,
      orderbook_updated_at: diagnosticTime
    };
    const markets = [
      { ...quote, exchange: "hyperliquid", raw_symbol: "ZETA", dex: "main", symbol: "ZETAUSDT", bid: 1, ask: 2, data_status: "live", stale_after_seconds: 30 },
      { ...quote, exchange: "hyperliquid", raw_symbol: "io:ZETA", dex: "io", symbol: "ZETAUSDT", bid: 3, ask: 4, data_status: "live", stale_after_seconds: 30 }
    ];
    const diagnostics = [
      { ...freshDiagnostic, raw_symbol: "ZETA", dex: "main", best_bid: 1.1, bid_depth_01pct_usdt: 9000, public_restrictions: ["main 限制"] },
      { ...freshDiagnostic, raw_symbol: "io:ZETA", dex: "io", best_bid: 3.1, bid_depth_01pct_usdt: 6000, public_restrictions: ["io 限制"] },
      { ...freshDiagnostic, raw_symbol: "xyz:ZETA", dex: "xyz", best_bid: 5.1, bid_depth_01pct_usdt: 3000, public_restrictions: ["xyz 限制"] }
    ];
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/instruments/")) return Response.json({ ...lookupResult, symbol: "ZETAUSDT", base: "ZETA", market_count: 2, markets });
      if (String(input).includes("/trade-status/") && !String(input).includes("/watches")) return Response.json({ ...tradeAvailabilityStatus, markets: diagnostics });
      return fallback(input, init);
    });
    window.history.replaceState({}, "", "/?page=instrument&symbol=ZETAUSDT");
    render(<InstrumentLookupPage />);
    const table = await screen.findByRole("table", { name: "精确行情市场" });
    await waitFor(() => expect(within(table).getAllByText(/市场诊断/)).toHaveLength(3));
    const header = table.querySelector(".instrument-market-data-head") as HTMLElement;
    expect(within(header).getByText("盘口深度")).not.toBeNull();
    expect(within(header).getByText("交易限制")).not.toBeNull();
    expect(within(header).queryByText("倍率")).toBeNull();
    expect(within(header).queryByText("行情数据状态")).toBeNull();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0].children[0] as HTMLElement).getByText(/DEX main · ZETA/)).not.toBeNull();
    expect(within(rows[0]).getByText("2")).not.toBeNull();
    expect(rows[0].children[4].textContent).toContain("9000 USDT");
    expect(rows[0].children[5].textContent).toContain("main 限制");
    expect(rows[0].children[5].textContent).not.toContain("io 限制");
    expect(rows[0].children[5].textContent).toContain("公开可用");
    expect(within(rows[1].children[0] as HTMLElement).getByText(/DEX io · io:ZETA/)).not.toBeNull();
    expect(within(rows[1]).getByText(/市场诊断 · Hyperliquid · 永续 · DEX io · io:ZETA/)).not.toBeNull();
    expect(within(rows[1]).getByText("4")).not.toBeNull();
    expect(rows[1].children[4].textContent).toContain("6000 USDT");
    expect(rows[1].children[5].textContent).toContain("io 限制");
    expect(within(rows[2]).getByText("仅诊断，未可靠关联行情")).not.toBeNull();
    expect(within(rows[2]).getAllByText("-").length).toBeGreaterThan(0);
    expect(rows[2].children[4].textContent).toContain("3000 USDT");
    await userEvent.click(within(rows[0]).getByText(/市场诊断/));
    expect(within(rows[0].lastElementChild as HTMLElement).getByText("main 限制")).not.toBeNull();
    expect(within(rows[0]).queryByText("io 限制")).toBeNull();
    expect(within(rows[0]).getByText("1.1")).not.toBeNull();
  });

  it("shows spot restrictions as public evidence without implying account availability", async () => {
    const diagnosticTime = new Date().toISOString();
    const quote = {
      ...lookupResult.exchanges[0].spot!, symbol: "ZETAUSDT", base: "ZETA", exchange: "binance",
      raw_symbol: "ZETAUSDT", timestamp: diagnosticTime
    };
    const diagnostic = {
      ...tradeAvailabilityStatus.markets[0], exchange: "binance", market_type: "spot", dex: null,
      raw_symbol: "ZETAUSDT", public_restrictions: [], observed_at: diagnosticTime,
      market_data_updated_at: diagnosticTime, orderbook_updated_at: diagnosticTime,
      buy_open: { ...tradeAvailabilityStatus.markets[0].buy_open, state: "conditional", reason: "需账户核验" },
      sell_open: { ...tradeAvailabilityStatus.markets[0].sell_open, state: "available", reason: "公开市场允许卖出" }
    };
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/instruments/")) return Response.json({
        ...lookupResult, query: "ZETAUSDT", symbol: "ZETAUSDT", base: "ZETA", exchange_count: 1,
        market_count: 1, exchanges: [{ exchange: "binance", spot: quote, future: null, error: null }],
        markets: [{ ...quote, data_status: "live", age_seconds: 1, stale_after_seconds: 30, error: null }], spreads: []
      });
      if (String(input).includes("/trade-status/") && !String(input).includes("/watches")) {
        return Response.json({ ...tradeAvailabilityStatus, markets: [diagnostic] });
      }
      return fallback(input, init);
    });
    window.history.replaceState({}, "", "/?page=instrument&symbol=ZETAUSDT");
    render(<InstrumentLookupPage />);
    const table = await screen.findByRole("table", { name: "精确行情市场" });
    await waitFor(() => expect(within(table).getByText(/^市场诊断 ·/)).not.toBeNull());
    const row = within(table).getAllByRole("row")[1];
    expect(row.children[4].textContent).toContain("9000 USDT");
    expect(row.children[5].textContent).toContain("待核实");
    expect(row.children[5].textContent).toContain("需账户核验");
    expect(row.children[5].textContent).toContain("公开可用");
    expect(row.children[5].textContent).not.toContain("平空");
  });

  it("keeps the quote through diagnostic loading and failure", async () => {
    let failDiagnostic!: (error: Error) => void;
    const pending = new Promise<Response>((_, reject) => { failDiagnostic = reject; });
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/trade-status/") && !String(input).includes("/watches")) return pending;
      return fallback(input, init);
    });
    render(<InstrumentLookupPage />);
    const table = await screen.findByRole("table", { name: "精确行情市场" });
    expect(within(table).getByText("100,090")).not.toBeNull();
    expect(within(table).getAllByText("诊断加载中").length).toBeGreaterThan(0);
    expect(within(table).queryByText("公开未发现限制")).toBeNull();
    failDiagnostic(new Error("诊断服务不可用"));
    expect(await screen.findByText("市场诊断失败；行情仍可查看")).not.toBeNull();
    expect(within(table).getByText("100,090")).not.toBeNull();
    expect(within(table).getAllByText("诊断请求失败").length).toBeGreaterThan(0);
    expect(within(table).queryByText(/市场诊断 ·/)).toBeNull();
  });

  it("keeps expanded market diagnostics in place while background refreshes finish or fail", async () => {
    const quote = {
      ...lookupResult.exchanges[0].future!,
      symbol: "ZETAUSDT", base: "ZETA", exchange: "hyperliquid", raw_symbol: "ZETA", dex: "main"
    };
    const zetaLookup = {
      ...lookupResult,
      query: "ZETAUSDT", symbol: "ZETAUSDT", base: "ZETA", exchange_count: 1, market_count: 1,
      markets: [{ ...quote, data_status: "live", age_seconds: 1, stale_after_seconds: 30, error: null }],
      exchanges: [{ exchange: "hyperliquid", spot: null, future: quote, error: null }],
      spreads: []
    };
    let resolveSecond!: (response: Response) => void;
    let rejectThird!: (error: Error) => void;
    let diagnosticCalls = 0;
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/instruments/")) return Response.json(zetaLookup);
      if (url.includes("/trade-status/") && !url.includes("/watches")) {
        diagnosticCalls += 1;
        if (diagnosticCalls === 2) return new Promise<Response>((resolve) => { resolveSecond = resolve; });
        if (diagnosticCalls === 3) return new Promise<Response>((_, reject) => { rejectThird = reject; });
        return Response.json(tradeAvailabilityStatus);
      }
      return fallback(input, init);
    });
    window.history.replaceState({}, "", "/?page=instrument&symbol=ZETAUSDT");

    render(<InstrumentLookupPage />);
    const table = await screen.findByRole("table", { name: "精确行情市场" });
    const row = within(table).getByText(/^永续 · DEX main · ZETA$/).closest("article")!;
    const summary = await within(row).findByText(/^市场诊断 ·/);
    const details = summary.closest("details") as HTMLDetailsElement;
    await userEvent.click(summary);
    expect(details.open).toBe(true);
    expect(row.children[4].textContent).toContain("9000 USDT");

    await userEvent.click(screen.getByRole("button", { name: "立即刷新" }));
    await waitFor(() => expect(diagnosticCalls).toBe(2));
    expect(within(table).getByText(/^永续 · DEX main · ZETA$/).closest("article")).toBe(row);
    expect(row.contains(details)).toBe(true);
    expect(details.open).toBe(true);
    expect(within(row).queryByText("诊断加载中")).toBeNull();

    resolveSecond(Response.json({ ...tradeAvailabilityStatus, source: "刷新后的诊断来源" }));
    await waitFor(() => expect(within(details).getByText(/刷新后的诊断来源/)).not.toBeNull());
    expect(details.open).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "立即刷新" }));
    await waitFor(() => expect(diagnosticCalls).toBe(3));
    rejectThird(new Error("诊断接口超时"));
    await screen.findByText("市场诊断刷新失败；显示上次诊断");
    expect(within(details).getByText(/刷新后的诊断来源/)).not.toBeNull();
    expect(within(details).getByText(/诊断未更新/)).not.toBeNull();
    expect(row.children[4].textContent).toContain("9000 USDT");
    expect(row.children[5].textContent).toContain("上次公开可用");
    expect(details.open).toBe(true);
  });

  it("does not show the previous symbol's diagnostics while a new symbol loads", async () => {
    const quote = {
      ...lookupResult.exchanges[0].future!,
      symbol: "ZETAUSDT", base: "ZETA", exchange: "hyperliquid", raw_symbol: "ZETA", dex: "main"
    };
    const zetaLookup = {
      ...lookupResult,
      query: "ZETAUSDT", symbol: "ZETAUSDT", base: "ZETA", market_count: 1,
      markets: [{ ...quote, data_status: "live", age_seconds: 1, stale_after_seconds: 30, error: null }],
      spreads: []
    };
    let resolveBtcDiagnostic!: (response: Response) => void;
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/instruments/ZETAUSDT")) return Response.json(zetaLookup);
      if (url.includes("/instruments/BTCUSDT")) return Response.json(lookupResult);
      if (url.includes("/trade-status/ZETAUSDT")) return Response.json(tradeAvailabilityStatus);
      if (url.includes("/trade-status/BTCUSDT")) {
        return new Promise<Response>((resolve) => { resolveBtcDiagnostic = resolve; });
      }
      return fallback(input, init);
    });
    window.history.replaceState({}, "", "/?page=instrument&symbol=ZETAUSDT");

    render(<InstrumentLookupPage />);
    await screen.findByText("合约指数成分");
    fireEvent.change(screen.getByLabelText("查询标的"), { target: { value: "BTCUSDT" } });
    await userEvent.click(screen.getByRole("button", { name: /查询$/ }));
    await screen.findByText("BTC / USDT");
    expect(screen.queryByText("合约指数成分")).toBeNull();
    expect(screen.queryByText(/DEX main · ZETA/)).toBeNull();
    expect(screen.getByRole("table", { name: "精确行情市场" }).querySelectorAll("details")).toHaveLength(0);
    resolveBtcDiagnostic(Response.json({ ...tradeAvailabilityStatus, query: "BTCUSDT", markets: [], index_compositions: [] }));
  });
});
