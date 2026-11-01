import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { getSpreadEvents, spreadEventPairUrl } from "../src/api/spreadEvents";
import type { SpreadEvent, SpreadEventsResponse } from "../src/api/spreadEvents";
import { SpreadEventsPage } from "../src/pages/SpreadEventsPage";

const event: SpreadEvent = {
  series_key: "btc-xyz-okx", symbol: "BTCUSDT",
  buy_leg: { exchange: "hyperliquid", market_type: "future", raw_symbol: "xyz:BTC", dex: "xyz", price_multiplier: null, contract_size_multiplier: null },
  sell_leg: { exchange: "okx", market_type: "spot", raw_symbol: "BTC-USDT", dex: null, price_multiplier: null, contract_size_multiplier: null },
  start_at: "2026-11-01T00:00:00Z", end_at: "2026-11-01T00:04:00Z",
  duration_s: 240, peak: 0.8, peak_at: "2026-11-01T00:02:00Z", mean: 0.5,
  samples: 3, end_reason: "below"
};

const result: SpreadEventsResponse = {
  events: [event, { ...event, series_key: "eth", symbol: "ETHUSDT", end_reason: "gap" },
    { ...event, series_key: "sol", symbol: "SOLUSDT", end_reason: "ongoing" }],
  scanned_rows: 10, series_count: 3, truncated: false,
  params: { start_at: "2026-10-31T00:00:00Z", end_at: "2026-11-01T00:00:00Z",
    threshold: 0.3, metric: "fee_adjusted", min_duration_s: 0, sort: "peak", limit: 100 }
};

describe("SpreadEventsPage", () => {
  let visibility: MockInstance;

  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/?page=spread-events");
    visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(result)));
  });

  afterEach(() => {
    vi.useRealTimers();
    visibility.mockRestore();
    vi.unstubAllGlobals();
  });

  it("renders the table, percentages, duration and all Chinese end reasons", async () => {
    render(<SpreadEventsPage />);
    const row = (await screen.findByRole("link", { name: "BTCUSDT" })).closest("tr")!;
    expect(within(row).getByText("0.800%")).toBeTruthy();
    expect(within(row).getByText("0.500%")).toBeTruthy();
    expect(within(row).getByText("4.0 分钟")).toBeTruthy();
    expect(within(row).getByText("11-01 08:00:00")).toBeTruthy();
    expect(screen.getByText("回落")).toBeTruthy();
    expect(screen.getByText("行情中断")).toBeTruthy();
    expect(screen.getByText("进行中")).toBeTruthy();
    expect(screen.getByText(/扫描 10 条样本，3 个序列/)).toBeTruthy();
    const url = new URL(String(vi.mocked(fetch).mock.calls[0][0]));
    expect(url.pathname).toBe("/api/spread-events");
    expect(url.searchParams.get("threshold")).toBe("0.3");
    expect(url.searchParams.get("metric")).toBe("fee_adjusted");
    expect(Date.parse(url.searchParams.get("end_at")!) - Date.parse(url.searchParams.get("start_at")!)).toBe(86400_000);
  });

  it("clicks a row using the existing exact pair URL and navigation event", async () => {
    window.history.replaceState({}, "", "/?page=spread-events&leg1_price_multiplier=100&leg2_dex=old&leg2_contract_size_multiplier=100&leg2_multiplier=9");
    const listener = vi.fn();
    window.addEventListener("taoli1:navigate", listener);
    try {
      render(<SpreadEventsPage />);
      const link = await screen.findByRole("link", { name: "BTCUSDT" });
      const expected = new URL((link as HTMLAnchorElement).href);
      await userEvent.click(link.closest("tr")!);
      expect(window.location.href).toBe(expected.href);
      expect(expected.searchParams.get("page")).toBe("pair-monitor");
      expect(expected.searchParams.get("leg1_exchange")).toBe("hyperliquid");
      expect(expected.searchParams.get("leg1_market_type")).toBe("future");
      expect(expected.searchParams.get("leg1_symbol")).toBe("BTC");
      expect(expected.searchParams.get("leg1_raw_symbol")).toBe("xyz:BTC");
      expect(expected.searchParams.get("leg1_dex")).toBe("xyz");
      expect(expected.searchParams.get("leg2_exchange")).toBe("okx");
      expect(expected.searchParams.get("leg2_market_type")).toBe("spot");
      expect(expected.searchParams.get("leg2_raw_symbol")).toBe("BTC-USDT");
      expect(expected.searchParams.has("leg2_dex")).toBe(false);
      expect(expected.searchParams.has("leg1_price_multiplier")).toBe(false);
      expect(expected.searchParams.has("leg2_contract_size_multiplier")).toBe(false);
      expect([...expected.searchParams.keys()].some((name) => name.includes("multiplier"))).toBe(false);
      expect(within(link.closest("tr")!).getByText("倍率未知")).toBeTruthy();
      expect(listener).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener("taoli1:navigate", listener);
    }
  });

  it("preserves non-unit price and contract multipliers in the existing protocol", async () => {
    const known: SpreadEvent = {
      ...event,
      buy_leg: { ...event.buy_leg, price_multiplier: 1000, contract_size_multiplier: 0.01 },
      sell_leg: { ...event.sell_leg, price_multiplier: 0.1, contract_size_multiplier: 10 }
    };
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ...result, events: [known] }));
    render(<SpreadEventsPage />);
    const link = await screen.findByRole("link", { name: "BTCUSDT" });
    const url = new URL((link as HTMLAnchorElement).href);
    expect(url.searchParams.get("leg1_price_multiplier")).toBe("1000");
    expect(url.searchParams.get("leg1_contract_size_multiplier")).toBe("0.01");
    expect(url.searchParams.get("leg2_price_multiplier")).toBe("0.1");
    expect(url.searchParams.get("leg2_contract_size_multiplier")).toBe("10");
    expect(url.searchParams.has("leg2_multiplier")).toBe(false);
    expect(within(link.closest("tr")!).queryByText("倍率未知")).toBeNull();
    await userEvent.click(link);
    expect(window.location.href).toBe(url.href);
  });

  it("marks partial multiplier knowledge and only sends known parameters", async () => {
    const partial = { ...event, buy_leg: { ...event.buy_leg, price_multiplier: 1000 } };
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ...result, events: [partial] }));
    render(<SpreadEventsPage />);
    const link = await screen.findByRole("link", { name: "BTCUSDT" });
    expect(within(link.closest("tr")!).getByText("倍率未知")).toBeTruthy();
    const url = new URL((link as HTMLAnchorElement).href);
    expect(url.searchParams.get("leg1_price_multiplier")).toBe("1000");
    expect(url.searchParams.has("leg1_contract_size_multiplier")).toBe(false);
    expect(url.searchParams.has("leg2_price_multiplier")).toBe(false);
    expect(url.searchParams.has("leg2_multiplier")).toBe(false);
  });

  it("navigates an ordinary symbol link without reloading the document", async () => {
    render(<SpreadEventsPage />);
    await userEvent.click(await screen.findByRole("link", { name: "BTCUSDT" }));
    expect(new URLSearchParams(window.location.search).get("page")).toBe("pair-monitor");
  });

  it("does not guess a Hyperliquid market when historical exact identity is missing", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ...result, events: [{
      ...event, buy_leg: { ...event.buy_leg, raw_symbol: null, dex: null }
    }] }));
    render(<SpreadEventsPage />);
    const symbol = await screen.findByText("BTCUSDT");
    expect(symbol.closest("a")).toBeNull();
    expect(screen.getByText(/无法精确跳转/)).toBeTruthy();
    await userEvent.click(symbol.closest("tr")!);
    expect(new URLSearchParams(window.location.search).get("page")).toBe("spread-events");
    expect(spreadEventPairUrl({ ...event, buy_leg: { ...event.buy_leg, raw_symbol: null } })).toBeNull();
  });

  it("shows truncation and API failures", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ...result, truncated: true }));
    render(<SpreadEventsPage />);
    expect(await screen.findByText(/结果已截断/)).toBeTruthy();
    vi.mocked(fetch).mockResolvedValueOnce(Response.json({ detail: "查询时间范围无效" }, { status: 422 }));
    await userEvent.click(screen.getByRole("button", { name: /刷新/ }));
    expect(await screen.findByText("查询时间范围无效")).toBeTruthy();
  });

  it("requests again only on manual refresh and filter changes, without polling", async () => {
    render(<SpreadEventsPage />);
    await screen.findByRole("link", { name: "BTCUSDT" });
    expect(fetch).toHaveBeenCalledOnce();
    vi.useFakeTimers();
    await act(async () => { vi.advanceTimersByTime(10 * 60_000); });
    expect(fetch).toHaveBeenCalledOnce();
    vi.useRealTimers();
    fireEvent.change(screen.getByRole("spinbutton", { name: "阈值 (%)" }), { target: { value: "0.7" } });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(new URL(String(vi.mocked(fetch).mock.calls[1][0])).searchParams.get("threshold")).toBe("0.7");
    await screen.findByRole("link", { name: "BTCUSDT" });
    await userEvent.click(screen.getByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  });

  it("does not request while hidden, including after a filter change", async () => {
    visibility.mockReturnValue("hidden");
    render(<SpreadEventsPage />);
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("spinbutton", { name: "阈值 (%)" }), { target: { value: "0.7" } });
    expect(fetch).not.toHaveBeenCalled();
    visibility.mockReturnValue("visible");
    await userEvent.click(screen.getByRole("button", { name: /刷新/ }));
    await screen.findByRole("link", { name: "BTCUSDT" });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("passes the existing dashboard auth header and cancellation signal", async () => {
    window.localStorage.setItem("dashboard_password", "test-password");
    const controller = new AbortController();
    await getSpreadEvents(result.params, controller.signal);
    expect(fetch).toHaveBeenCalledWith(expect.any(URL), expect.objectContaining({
      headers: { "X-Dashboard-Password": "test-password" }, signal: controller.signal
    }));
  });
});
