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
  const leg2Multiplier = Number(params?.get("leg2_multiplier") ?? 1);
  const intervalSeconds = Number(params?.get("interval_seconds") ?? 5);
  const intervalMinutes = Number(params?.get("interval_minutes") ?? Math.max(1, Math.round(intervalSeconds / 60)));
  const hours = Number(params?.get("hours") ?? 4);
  const largePriceGap = leg2Multiplier === 0.1;
  const leg1Price = largePriceGap ? 1218.6 : 100;
  const leg2Price = largePriceGap ? 1618.3 : 101;
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
          }
        ]
      : [];
  const points = largePriceGap
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
          bucket_at: observedAt,
          leg1_close: 100,
          leg2_close: 101,
          spread_abs: 1,
          spread_pct: 0.5
        }
      ];
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
    interval_seconds: intervalSeconds,
    leg2_multiplier: leg2Multiplier,
    observed_at: observedAt,
    point_count: points.length,
    first_seen_at: points[0]?.bucket_at ?? observedAt,
    last_seen_at: points[points.length - 1]?.bucket_at ?? observedAt,
    spread_abs: { min: 1, max: 1, mean: 1, current: 1 },
    spread_pct: { min: 0.5, max: 0.5, mean: 0.5, current: 0.5 },
    current: {
      observed_at: observedAt,
      leg1: {
        exchange: leg1Exchange,
        symbol: leg1Symbol,
        market_type: leg1MarketType,
        raw_symbol: leg1Symbol,
        price: leg1Price,
        price_field: "mid_price" as const,
        mark_price: null,
        index_price: null,
        mid_price: leg1Price,
        last_price: leg1Price,
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
        price: leg2Price,
        price_field: leg2MarketType === "spot" ? "last_price" as const : "mark_price" as const,
        mark_price: leg2MarketType === "spot" ? null : leg2Price,
        index_price: leg2MarketType === "spot" ? null : 100,
        mid_price: leg2Price,
        last_price: leg2Price,
        funding_rate_pct: leg2MarketType === "spot" ? null : 0.01,
        funding_next_rate_pct: null,
        funding_next_time: null,
        funding_interval_hours: 8,
        funding_rate_upper_pct: null,
        funding_rate_lower_pct: null,
        timestamp: observedAt
      },
      spread_abs: largePriceGap ? 399.7 : 1,
      spread_pct: largePriceGap ? 28.18 : 0.5
    },
    points,
    funding_history: fundingHistory,
    warnings: []
  };
}

describe("PairMonitorPage", () => {
  const requests: string[] = [];

  beforeEach(() => {
    requests.length = 0;
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    window.sessionStorage.clear();
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
    window.sessionStorage.clear();
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

  it("sends a custom second interval when selected", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[comboboxes.length - 1]);
    await user.click(await screen.findByText("自定义"));
    const customInput = screen.getByRole("spinbutton", { name: /自定义秒/ });
    await user.clear(customInput);
    await user.type(customInput, "7");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("interval_seconds=7"))).toBe(true);
    });
    expect(new URLSearchParams(window.location.search).get("interval_seconds")).toBe("7");
  });

  it("shows the funding rate difference table", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("资金费率差")).toBeTruthy();
    expect(screen.getAllByText("净费率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.0300%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bitget 费率").length).toBeGreaterThan(0);
    const pageText = document.body.textContent ?? "";
    expect(pageText.indexOf("资金费率差")).toBeLessThan(pageText.indexOf("标的价格"));
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
    expect(params.get("leg2_symbol")).toBe("SKHYNIXUSDT");
    expect(params.get("leg2_multiplier")).toBe("1");
    expect(params.get("hours")).toBe("4");
    expect(params.get("interval_minutes")).toBe("1");
    expect(params.get("interval_seconds")).toBe("5");
  });

  it("restores the last spread result from session storage after returning from another page", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    expect((await screen.findAllByText("Bitget · 合约 · SKHYUSDT")).length).toBeGreaterThan(0);

    window.history.pushState(
      {},
      "",
      "/?page=premium-index&exchange=bitget&symbol=SKHYUSDT&hours=4&interval_minutes=5"
    );
    unmount();
    requests.length = 0;

    render(<PairMonitorPage />);

    expect(screen.getAllByText("+0.50%").length).toBeGreaterThan(0);
    expect((screen.getByPlaceholderText("SKHY") as HTMLInputElement).value).toBe("SKHYUSDT");
    expect((await screen.findAllByText("Bitget · 合约 · SKHYUSDT")).length).toBeGreaterThan(0);
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });
});
