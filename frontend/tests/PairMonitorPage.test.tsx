import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PairMonitorPage } from "../src/pages/PairMonitorPage";

const observedAt = "2026-07-24T02:00:00Z";

function normalizedMockSymbol(exchange: string, symbol: string) {
  const text = symbol.trim().toUpperCase();
  if (exchange === "binance_alpha") {
    return text;
  }
  return text.endsWith("USDT") ? text : `${text}USDT`;
}

function pairSpreadResult(params?: URLSearchParams) {
  const leg1MarketType = params?.get("leg1_market_type") === "spot" ? "spot" : "future";
  const leg2MarketType = params?.get("leg2_market_type") === "spot" ? "spot" : "future";
  const leg1Exchange = params?.get("leg1_exchange") ?? "bitget";
  const leg2Exchange = params?.get("leg2_exchange") ?? "bitget";
  const leg1Symbol = normalizedMockSymbol(leg1Exchange, params?.get("leg1_symbol") ?? "SKHYUSDT");
  const leg2Symbol = normalizedMockSymbol(leg2Exchange, params?.get("leg2_symbol") ?? "SKHYNIXUSDT");
  const leg2Multiplier = Number(params?.get("leg2_multiplier") ?? 10);
  const intervalMinutes = Number(params?.get("interval_minutes") ?? 5);
  const hours = Number(params?.get("hours") ?? 720);
  return {
    leg1: {
      exchange: leg1Exchange,
      symbol: leg1Symbol,
      market_type: leg1MarketType
    },
    leg2: {
      exchange: leg2Exchange,
      symbol: leg2Symbol,
      market_type: leg2MarketType
    },
    hours,
    interval_minutes: intervalMinutes,
    leg2_multiplier: leg2Multiplier,
    observed_at: observedAt,
    point_count: 1,
    first_seen_at: observedAt,
    last_seen_at: observedAt,
    spread_abs: { min: 1, max: 1, mean: 1, current: 1 },
    spread_pct: { min: 0.5, max: 0.5, mean: 0.5, current: 0.5 },
    current: {
      observed_at: observedAt,
      leg1: {
        exchange: leg1Exchange,
        symbol: leg1Symbol,
        market_type: leg1MarketType,
        raw_symbol: leg1Symbol,
        price: 100,
        price_field: "mid_price" as const,
        mark_price: null,
        index_price: null,
        mid_price: 100,
        last_price: 100,
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
        raw_symbol: leg2Symbol,
        price: 101,
        price_field: leg2MarketType === "spot" ? "last_price" as const : "mark_price" as const,
        mark_price: leg2MarketType === "spot" ? null : 101,
        index_price: leg2MarketType === "spot" ? null : 100,
        mid_price: 101,
        last_price: 101,
        funding_rate_pct: leg2MarketType === "spot" ? null : 0.01,
        funding_next_rate_pct: null,
        funding_next_time: null,
        funding_interval_hours: 8,
        funding_rate_upper_pct: null,
        funding_rate_lower_pct: null,
        timestamp: observedAt
      },
      spread_abs: 1,
      spread_pct: 0.5
    },
    points: [
      {
        bucket_at: observedAt,
        leg1_close: 100,
        leg2_close: 101,
        spread_abs: 1,
        spread_pct: 0.5
      }
    ],
    funding_history: [],
    warnings: []
  };
}

describe("PairMonitorPage", () => {
  const requests: string[] = [];

  beforeEach(() => {
    requests.length = 0;
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const urlText = String(input);
        requests.push(urlText);
        const url = new URL(urlText, "http://localhost");
        if (url.pathname.includes("/pair-spread/query")) {
          return Response.json(pairSpreadResult(url.searchParams));
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    vi.unstubAllGlobals();
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

    expect(screen.getByText("bybit 合约 DEXE / bitget 合约 DEXE")).toBeTruthy();
  });

  it("auto-runs a Binance Alpha spread query from URL parameters", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=AKEUSDT" +
        "&leg2_exchange=binance_alpha&leg2_market_type=spot&leg2_symbol=ALPHA_331USDT" +
        "&leg2_multiplier=1&hours=4&interval_minutes=1"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("leg1_exchange=binance") &&
            request.includes("leg2_exchange=binance_alpha") &&
            request.includes("leg2_symbol=ALPHA_331USDT") &&
            request.includes("interval_minutes=1") &&
            request.includes("hours=4")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("Binance Alpha · 现货 · ALPHA_331USDT")).length).toBeGreaterThan(0);
  });

  it("opens the premium index page for a futures leg from the spread result", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    const premiumButtons = await screen.findAllByRole("button", { name: /查看溢价指数/ });
    await user.click(premiumButtons[0]);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("premium-index");
    expect(params.get("exchange")).toBe("bitget");
    expect(params.get("symbol")).toBe("SKHYUSDT");
    expect(params.get("hours")).toBe("720");
    expect(params.get("interval_minutes")).toBe("5");
  });
});
