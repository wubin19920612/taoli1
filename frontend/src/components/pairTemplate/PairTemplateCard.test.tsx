import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createInstrumentAstroCard, lookupInstrumentAstroCard, previewInstrumentAstroPair, updateInstrumentAstroCard } from "../../api/client";
import type { AstroCardLookup, AstroPairPlan, PairSpreadCurrentSnapshot } from "../../api/types";
import { chartFixture } from "../pairChartV2/chartTestUtils";
import { PairTemplateCard, templateRoute } from "./PairTemplateCard";

vi.mock("../../api/client", () => ({
  createInstrumentAstroCard: vi.fn(), lookupInstrumentAstroCard: vi.fn(),
  previewInstrumentAstroPair: vi.fn(), updateInstrumentAstroCard: vi.fn()
}));
const existing: AstroCardLookup = { found: true, count: 1, pair_name: "BTC", status: true, disable_open: false,
  open_position: "0.020000", close_position: "0.001000", open_spread_pct: 2, close_spread_pct: 0.1 };
function fixture() {
  const result = chartFixture();
  result.points = Array.from({ length: 721 }, (_, i) => ({ ...result.points[0],
    bucket_at: new Date(Date.UTC(2026, 10, 12) + i * 60000).toISOString(), spread_pct: 0.7 + 0.6 * Math.sin(i / 10) }));
  result.current = { open_spread_pct: 0.8, close_spread_pct: 1,
    leg1: { ...result.leg1, raw_symbol: "BTCUSDT", ask_price: 100, bid_price: 99, funding_rate_pct: 0.01, funding_interval_hours: 8 },
    leg2: { ...result.leg2, raw_symbol: "BTCUSDT", ask_price: 102, bid_price: 101, funding_rate_pct: 0.02, funding_interval_hours: 8 }
  } as PairSpreadCurrentSnapshot;
  return result;
}
const button = (name: string) => screen.getByRole("button", { name });
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear();
  vi.mocked(lookupInstrumentAstroCard).mockResolvedValue(existing);
  vi.mocked(previewInstrumentAstroPair).mockResolvedValue({ can_submit: true, source_open_spread_pct: 0.8,
    pair: { name: "BTC", type: "FF", openPosition: "0.012000", closePosition: "0.002000" }, warnings: [], blockers: [],
    card_variant: "non_gc", route_variants: [{ card_variant: "non_gc", buy_exchange: "binance", sell_exchange: "bitget" }]
  } as unknown as AstroPairPlan);
  vi.mocked(createInstrumentAstroCard).mockResolvedValue({ enabled: true, status: "created", action: "add", message: "created", warnings: [], pair_name: "BTC", pair_type: "FF" });
  vi.mocked(updateInstrumentAstroCard).mockResolvedValue({ enabled: true, status: "updated", action: "update", message: "updated", warnings: [], pair_name: "BTC", pair_type: "FF" });
});

describe("PairTemplateCard", () => {
  it("renders three templates, looks up the route and sends chart lines", async () => {
    const onLinesChange = vi.fn();
    render(<PairTemplateCard result={fixture()} onLinesChange={onLinesChange} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    expect(screen.getByText("均衡（推荐）")).toBeTruthy();
    expect(screen.getAllByText("样本内")).toHaveLength(3);
    expect(screen.getAllByText("样本外（训练模板，未使用手调值）")).toHaveLength(3);
    expect(lookupInstrumentAstroCard).toHaveBeenCalledWith(expect.objectContaining({ buy_raw_symbol: "BTCUSDT", sell_raw_symbol: "BTCUSDT" }));
    expect(onLinesChange).toHaveBeenCalledWith({ open: expect.any(Number), close: expect.any(Number) });
  });
  it.each([0, 2])("disables modification for %s matching cards", async (count) => {
    vi.mocked(lookupInstrumentAstroCard).mockResolvedValue({ ...existing, found: count > 0, count });
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    await waitFor(() => expect(screen.queryByText("正在查找已有卡片…")).toBeNull());
    expect(button("修改卡片").hasAttribute("disabled")).toBe(true);
    expect(button("快捷建卡").hasAttribute("disabled")).toBe(false);
  });
  it("disables both writes for multipliers and does not query Astro", () => {
    render(<PairTemplateCard result={{ ...fixture(), leg2_multiplier: 10 }} onLinesChange={vi.fn()} />);
    expect(button("修改卡片").hasAttribute("disabled")).toBe(true);
    expect(button("快捷建卡").hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("带倍数的价差暂不支持建卡")).toBeTruthy();
    expect(lookupInstrumentAstroCard).not.toHaveBeenCalled();
  });
  it("does not recommend or write with insufficient history", () => {
    const result = fixture(); result.points = result.points.slice(0, 100);
    render(<PairTemplateCard result={result} onLinesChange={vi.fn()} />);
    expect(button("快捷建卡").hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/数据不足：至少需要/)).toBeTruthy();
  });
  it("requires preview then explicit confirmation and submits the previewed thresholds", async () => {
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    fireEvent.click(button("快捷建卡"));
    await waitFor(() => expect(button("确认创建").hasAttribute("disabled")).toBe(false));
    expect(createInstrumentAstroCard).not.toHaveBeenCalled();
    expect(screen.getByText("Astro 平仓阈值")).toBeTruthy();
    expect(screen.getByText("模板开仓价差")).toBeTruthy();
    expect(screen.getByText("模板平仓价差")).toBeTruthy();
    expect(screen.queryByText("arb-mvp live")).toBeNull();
    const [route, , thresholds] = vi.mocked(previewInstrumentAstroPair).mock.calls[0];
    fireEvent.click(button("确认创建"));
    await waitFor(() => expect(createInstrumentAstroCard).toHaveBeenCalledWith(route, 0.8, expect.objectContaining(thresholds!)));
  });
  it("confirms updates with expected positions, only after reviewing the change", async () => {
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    fireEvent.click(button("修改卡片"));
    expect(updateInstrumentAstroCard).not.toHaveBeenCalled();
    fireEvent.click(button("确认修改"));
    await waitFor(() => expect(updateInstrumentAstroCard).toHaveBeenCalledWith(expect.objectContaining({
      expected_open_position: "0.020000", expected_close_position: "0.001000", open_spread_pct: expect.any(Number), close_spread_pct: expect.any(Number)
    })));
  });
  it("persists fees and disables invalid manual thresholds", async () => {
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("模板往返手续费"), { target: { value: "0.2" } });
    expect(localStorage.getItem("taoli1.pairTemplate.roundTripFee.v1")).toBe("0.2");
    fireEvent.change(screen.getByLabelText("均衡（推荐）开仓"), { target: { value: "-1" } });
    expect(button("快捷建卡").hasAttribute("disabled")).toBe(true);
    expect(button("修改卡片").hasAttribute("disabled")).toBe(true);
  });
  it("preserves raw markets and DEX when reversing a cross-asset route", () => {
    const result = fixture();
    result.current!.leg2 = { ...result.current!.leg2, exchange: "hyperliquid", symbol: "ETHUSDT", raw_symbol: "xyz:ETH", dex: "xyz" };
    expect(templateRoute(result, "long2_short1")).toEqual(expect.objectContaining({ comparison_kind: "custom_cross_asset",
      buy_raw_symbol: "xyz:ETH", buy_dex: "xyz", sell_raw_symbol: "BTCUSDT", symbol: "ETHUSDT" }));
  });
  it("marks different query symbols as cross-asset even when normalized snapshots share a symbol", () => {
    const result = fixture();
    result.leg2.symbol = "BTC2USDT";
    result.current!.leg2.raw_symbol = "BTC2USDT";
    expect(templateRoute(result, "long1_short2")?.comparison_kind).toBe("custom_cross_asset");
  });
  it("keeps modification disabled after a lookup error", async () => {
    vi.mocked(lookupInstrumentAstroCard).mockRejectedValue(new Error("unavailable"));
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    await screen.findByText(/查卡失败：Error: unavailable/);
    expect(button("修改卡片").hasAttribute("disabled")).toBe(true);
  });
});
