import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PremiumIndexPage } from "../src/pages/PremiumIndexPage";

const observedAt = "2026-07-12T02:00:00Z";

function premiumResult(symbol: string, hours: number, intervalMinutes: number) {
  return {
    exchange: "binance",
    symbol,
    hours,
    interval_minutes: intervalMinutes,
    observed_at: observedAt,
    point_count: 1,
    first_seen_at: observedAt,
    last_seen_at: observedAt,
    premium_pct: {
      min: 0.1,
      max: 0.1,
      mean: 0.1,
      current: 0.1
    },
    current: {
      observed_at: observedAt,
      exchange: "binance",
      symbol,
      raw_symbol: `${symbol}USDT`,
      mark_price: 101,
      index_price: 100,
      mid_price: 100.5,
      last_price: 101,
      premium_pct: 1,
      mid_premium_pct: 0.5,
      funding_rate_pct: 0.01,
      funding_next_rate_pct: null,
      funding_next_time: null,
      source: "mark_index"
    },
    points: [
      {
        bucket_at: observedAt,
        premium_pct: 0.1,
        mark_price: 101,
        index_price: 100,
        source: "test"
      }
    ],
    warnings: []
  };
}

describe("PremiumIndexPage", () => {
  const requests: string[] = [];

  beforeEach(() => {
    requests.length = 0;
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const urlText = String(input);
        requests.push(urlText);
        const url = new URL(urlText, "http://localhost");
        if (url.pathname.includes("/premium-index/query")) {
          const symbol = url.searchParams.get("symbol") ?? "BTC";
          const hours = Number(url.searchParams.get("hours") ?? "12");
          const intervalMinutes = Number(url.searchParams.get("interval_minutes") ?? "1");
          return Response.json(premiumResult(symbol, hours, intervalMinutes));
        }
        if (url.pathname.includes("/premium-index/current")) {
          const symbol = url.searchParams.get("symbol") ?? "BTC";
          return Response.json(premiumResult(symbol, 12, 1).current);
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("saves premium index presets and queries a saved preset", async () => {
    const user = userEvent.setup();
    render(<PremiumIndexPage />);

    const symbolInput = screen.getByPlaceholderText("BTC");
    await user.clear(symbolInput);
    await user.type(symbolInput, "eth");
    await user.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByText("Binance ETH")).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem("taoli1.premiumIndex.presets.v1") ?? "[]")[0]).toMatchObject({
      exchange: "binance",
      symbol: "ETH",
      hours: 12,
      intervalMinutes: 1
    });

    await user.clear(symbolInput);
    await user.type(symbolInput, "btc");
    await user.click(screen.getByText("Binance ETH"));

    await waitFor(() => {
      expect(
        requests.some(
          (request) => request.includes("symbol=ETH") && request.includes("hours=12") && request.includes("interval_minutes=1")
        )
      ).toBe(true);
    });
    expect(await screen.findByText("binance · ETH · mark_index")).toBeTruthy();
  });
});
