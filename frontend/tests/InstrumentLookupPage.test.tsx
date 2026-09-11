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
        mark_price: 100100,
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
  ]
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
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
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
