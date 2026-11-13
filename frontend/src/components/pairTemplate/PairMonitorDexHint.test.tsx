import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AstroInstrumentRouteRequest, PairSpreadCurrentSnapshot } from "../../api/types";
import { PairMonitorPage } from "../../pages/PairMonitorPage";
import { chartFixture } from "../pairChartV2/chartTestUtils";

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
afterEach(() => {
  vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("PairMonitor other DEX query", () => {
  it.each([
    { hlLeg: "leg1", target: "xyz" }, { hlLeg: "leg2", target: "xyz" },
    { hlLeg: "leg1", target: "main" }, { hlLeg: "leg2", target: "main" }
  ] as const)("switches $hlLeg to $target and queries again through the page form", async ({ hlLeg, target }) => {
    const queries: URL[] = [];
    const lookups: AstroInstrumentRouteRequest[] = [];
    const params = new URLSearchParams({ page: "pair-monitor", leg1_exchange: "binance", leg2_exchange: "bitget",
      leg1_market_type: "future", leg2_market_type: "future", leg1_symbol: "BTCUSDT", leg2_symbol: "BTCUSDT",
      hours: "12", interval_seconds: "60", [`${hlLeg}_exchange`]: "hyperliquid", [`${hlLeg}_dex`]: "para" });
    window.history.replaceState({}, "", `/?${params}`);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname.endsWith("/pair-spread/presets")) return Response.json([]);
      if (url.pathname.endsWith("/pair-spread/funding-records/status")) return Response.json({ watched: false, item: null, samples: [], warnings: [] });
      if (url.pathname.endsWith("/pair-spread/hyperliquid-markets")) return Response.json(["para", "xyz", "main"].map((dex) => ({
        dex, full_name: dex, assets: [{ base: "BTC", symbol: "BTCUSDT", delisted: false }]
      })));
      if (url.pathname.endsWith("/pair-spread/query")) {
        queries.push(url);
        const result = chartFixture();
        const dex = url.searchParams.get(`${hlLeg}_dex`) || "main";
        result.hours = 12;
        result[hlLeg] = { ...result[hlLeg], exchange: "hyperliquid", dex,
          symbol: dex === "main" ? "BTCUSDT" : `${dex}:BTCUSDT` };
        result.points = Array.from({ length: 721 }, (_, i) => ({ ...result.points[0],
          bucket_at: new Date(Date.UTC(2026, 10, 12) + i * 60000).toISOString(), spread_pct: 0.7 + 0.6 * Math.sin(i / 10) }));
        result.current = { open_spread_pct: 0.8, close_spread_pct: 1,
          leg1: { ...result.leg1, symbol: "BTCUSDT", raw_symbol: "BTCUSDT", ask_price: 100, bid_price: 99 },
          leg2: { ...result.leg2, symbol: "BTCUSDT", raw_symbol: "BTCUSDT", ask_price: 102, bid_price: 101 }
        } as PairSpreadCurrentSnapshot;
        result.current[hlLeg].raw_symbol = dex === "main" ? "BTC" : `${dex}:BTC`;
        return Response.json(result);
      }
      if (url.pathname.endsWith("/astro/instrument/card/lookup")) {
        const route = JSON.parse(String(init?.body)) as AstroInstrumentRouteRequest;
        lookups.push(route);
        const buyHl = route.buy_exchange === "hyperliquid";
        const matched = (buyHl ? route.buy_dex : route.sell_dex) === target;
        return Response.json({ found: matched, count: matched ? 1 : 0, pair_name: matched ? "BTC" : null,
          status: true, disable_open: false, open_position: "0.012000", close_position: "0.003000",
          open_spread_pct: 1.2, close_spread_pct: 0.3,
          other_dex_cards: matched ? [] : [{ pair_name: "BTC", buy_ex: buyHl ? "hl" : route.buy_exchange,
            sell_ex: buyHl ? route.sell_exchange : "hl", buy_dex: buyHl ? target : null,
            sell_dex: buyHl ? null : target, open_spread_pct: 1.2, close_spread_pct: 0.3, status: true }] });
      }
      return Response.json({});
    }));
    render(<PairMonitorPage />);
    const switchButton = await screen.findByRole("button", { name: `切换到 ${target} 查询` });
    expect(screen.getByRole("button", { name: "修改卡片" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(switchButton);
    await waitFor(() => expect(queries[queries.length - 1]?.searchParams.get(`${hlLeg}_dex`)).toBe(target));
    const original = queries[0].searchParams;
    for (const key of ["leg1_exchange", "leg2_exchange", "leg1_symbol", "leg2_symbol", "hours", "interval_seconds"]) {
      expect(queries[queries.length - 1].searchParams.get(key)).toBe(original.get(key));
    }
    await waitFor(() => expect(lookups[lookups.length - 1]).toEqual(expect.objectContaining({
      [hlLeg === "leg1" ? "buy_dex" : "sell_dex"]: target,
      [hlLeg === "leg1" ? "buy_raw_symbol" : "sell_raw_symbol"]: target === "main" ? "BTC" : `${target}:BTC`
    })));
    expect(new URL(window.location.href).searchParams.get(`${hlLeg}_dex`)).toBe(target);
    await waitFor(() => expect(screen.getByRole("button", { name: "修改卡片" }).hasAttribute("disabled")).toBe(false));
    expect(screen.queryByRole("button", { name: `切换到 ${target} 查询` })).toBeNull();
  });
});
