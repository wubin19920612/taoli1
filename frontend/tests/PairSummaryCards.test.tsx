import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PairSpreadCurrentLeg, PairSpreadQueryResult } from "../src/api/types";
import { PairSummaryCards } from "../src/components/pairSummary/PairSummaryCards";

const observedAt = "2026-11-03T02:00:00Z";
const signedPct = (value: number | null | undefined, digits = 2) =>
  typeof value === "number" && Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%` : "-";
const price = (value: number | null | undefined) => value == null ? "-" : String(value);
const props = { hours: 4, ratioMode: false, spreadTone: "positive" as const, closeSpreadTone: "negative" as const,
  price, signedPct, exchangeLabels: { hyperliquid: "Hyperliquid", binance: "Binance" } };

function result(): PairSpreadQueryResult {
  const leg = {
    exchange: "binance", market_type: "future", raw_symbol: "BTCUSDT", price: 1,
    mid_price: 1, bid_price: 1, ask_price: 1, funding_rate_pct: 0.01,
    funding_interval_hours: 8, funding_next_time: null, funding_next_rate_pct: null
  } as PairSpreadCurrentLeg;
  return {
    hours: 48, observed_at: observedAt, leg2_multiplier: 9,
    current: {
      observed_at: observedAt, spread_pct: 0.5,
      open_spread_pct: 0.8, close_spread_pct: 1.2,
      leg1: { ...leg, price: 40, mid_price: 40, ask_price: 40.2, bid_price: 40 }, leg2: { ...leg }
    },
    points: [38, 42].map((value, index) => ({
      bucket_at: `2026-11-03T0${index}:00:00Z`, leg1_close: value, leg2_close: 1, spread_pct: index, spread_abs: index
    }))
  } as PairSpreadQueryResult;
}

describe("PairSummaryCards", () => {
  it("keeps ratios and their signed differences in ratio units using already scaled prices", () => {
    render(<PairSummaryCards {...props} result={result()} ratioMode />);
    const executable = within(screen.getByRole("region", { name: "可成交比值" }));
    expect(executable.getByText("40.2")).toBeTruthy();
    expect(executable.getByText("40")).toBeTruthy();
    expect(executable.getByText("+0.2（+0.50%）")).toBeTruthy();
    const position = within(screen.getByRole("region", { name: "价差位置（查询区间 2 天）" }));
    expect(position.getByText("P50")).toBeTruthy();
    expect(position.getByText("38")).toBeTruthy();
    expect(position.getByText("42")).toBeTruthy();
    expect(position.getByText("偏离均值 +0（+0.00%）")).toBeTruthy();
    expect(position.getByText("近 1h 变化 -2")).toBeTruthy();
    expect(position.getByText("波动 σ 2")).toBeTruthy();
    expect(document.querySelector<HTMLElement>(".pair-summary-current")?.style.left).toBe("50%");
    expect(document.querySelector<HTMLElement>(".pair-summary-mean")?.style.left).toBe("50%");
  });

  it("shows placeholders for missing quotes/data and never infers missing next rates", () => {
    const view = render(<PairSummaryCards {...props} result={null} />);
    expect(screen.getByText("下次结算 -")).toBeTruthy();
    expect(screen.queryByText(/下期净/)).toBeNull();
    expect(screen.getByText("近 1h 变化 -")).toBeTruthy();
    expect(screen.getByText("波动 σ -")).toBeTruthy();
    expect(document.querySelector(".pair-summary-current")).toBeNull();
    const data = result();
    data.current!.leg1.ask_price = null;
    view.rerender(<PairSummaryCards {...props} result={data} ratioMode />);
    const cost = screen.getByText("开平差（来回成本）").parentElement!;
    expect(within(cost).getByText("-")).toBeTruthy();
  });

  it("labels spot funding, preserves the DEX, and colors a net payment red", () => {
    const data = result();
    data.current!.leg1.exchange = "hyperliquid";
    data.current!.leg1.dex = "xyz";
    data.current!.leg1.raw_symbol = "xyz:BTC";
    data.current!.leg2.market_type = "spot";
    render(<PairSummaryCards {...props} result={data} />);
    const funding = within(screen.getByRole("region", { name: "资金费（左多 / 右空）" }));
    expect(funding.getByText(/左 Hyperliquid \(xyz\)/).title).toBe("xyz:BTC");
    expect(funding.getByText("右 Binance 现货 · 无资金费")).toBeTruthy();
    expect(funding.getByText("-0.0100%").classList.contains("pair-summary-funding-negative")).toBe(true);
    expect(funding.queryByText(/下期净/)).toBeNull();
  });

  it("refreshes the earlier settlement countdown each second and clears the timer on unmount", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-03T00:00:00Z"));
    const data = result();
    Object.assign(data.current!.leg1, { funding_next_time: "2026-11-03T08:00:00Z", funding_next_rate_pct: 0.01 });
    Object.assign(data.current!.leg2, { funding_next_time: "2026-11-03T02:41:00Z", funding_next_rate_pct: 0.02, funding_interval_hours: 4 });
    const baselineTimers = vi.getTimerCount();
    const view = render(<PairSummaryCards {...props} result={data} />);
    try {
      expect(screen.getByText("下次结算 2h 41m")).toBeTruthy();
      expect(screen.getByText("下期净 +0.0300% / 8h")).toBeTruthy();
      act(() => { vi.advanceTimersByTime(1000); });
      expect(screen.getByText("下次结算 2h 40m")).toBeTruthy();
      view.unmount();
      expect(vi.getTimerCount()).toBe(baselineTimers);
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
});
