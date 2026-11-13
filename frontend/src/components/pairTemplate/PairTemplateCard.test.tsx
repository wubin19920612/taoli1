import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createInstrumentAstroCard, lookupInstrumentAstroCard, previewInstrumentAstroPair, updateInstrumentAstroCard } from "../../api/client";
import type { AstroCardLookup, AstroPairPlan, PairSpreadCurrentSnapshot, PairSpreadQueryResult } from "../../api/types";
import { chartFixture } from "../pairChartV2/chartTestUtils";
import { fundingMetrics } from "../pairSummary/metrics";
import { PairTemplateCard, templateRoute } from "./PairTemplateCard";
import { chartLines, recommend } from "./recommend";

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
  it.each([
    { hlLeg: "leg1", reversed: false }, { hlLeg: "leg2", reversed: false },
    { hlLeg: "leg1", reversed: true }, { hlLeg: "leg2", reversed: true }
  ] as const)("warns about other DEX cards and switches the correct $hlLeg (reversed=$reversed)", async ({ hlLeg, reversed }) => {
    const result = fixture();
    result[hlLeg] = { ...result[hlLeg], exchange: "hyperliquid", dex: "para" };
    result.current![hlLeg] = { ...result.current![hlLeg], exchange: "hyperliquid", dex: "para", raw_symbol: "para:BTC" };
    if (reversed) result.points = result.points.map((point) => ({ ...point, spread_pct: -point.spread_pct }));
    const route = templateRoute(result, reversed ? "long2_short1" : "long1_short2")!;
    const buyHl = route.buy_exchange === "hyperliquid";
    vi.mocked(lookupInstrumentAstroCard).mockResolvedValue({ ...existing, found: false, count: 0, pair_name: null,
      other_dex_cards: ["xyz", "main"].map((dex) => ({ pair_name: "BTC", buy_ex: buyHl ? "gc-hl" : route.buy_exchange,
        sell_ex: buyHl ? route.sell_exchange : "hl", buy_dex: buyHl ? dex : null, sell_dex: buyHl ? null : dex,
        open_spread_pct: 1.2, close_spread_pct: 0.3, status: true })) });
    const onSwitchDex = vi.fn();
    render(<PairTemplateCard result={result} onLinesChange={vi.fn()} onSwitchDex={onSwitchDex} />);
    const warning = await screen.findByText("Astro 已有卡片 BTC 使用 HL xyz（开 1.20% / 平 0.30%），当前查询的是 HL para，价差可能不同");
    expect(warning.closest(".ant-alert-warning")).toBeTruthy();
    expect(button("修改卡片").hasAttribute("disabled")).toBe(true);
    fireEvent.click(button("修改卡片"));
    expect(updateInstrumentAstroCard).not.toHaveBeenCalled();
    fireEvent.click(button("切换到 xyz 查询"));
    expect(onSwitchDex).toHaveBeenLastCalledWith({ [hlLeg]: "xyz" });
    fireEvent.click(button("切换到 main 查询"));
    expect(onSwitchDex).toHaveBeenLastCalledWith({ [hlLeg]: "main" });
  });
  it("shows both HL legs and maps both DEXes back to page order", async () => {
    const result = fixture();
    for (const leg of ["leg1", "leg2"] as const) {
      result[leg] = { ...result[leg], exchange: "hyperliquid", dex: "para" };
      result.current![leg] = { ...result.current![leg], exchange: "hyperliquid", dex: "para", raw_symbol: "para:BTC" };
    }
    result.points = result.points.map((point) => ({ ...point, spread_pct: -point.spread_pct }));
    vi.mocked(lookupInstrumentAstroCard).mockResolvedValue({ ...existing, other_dex_cards: [{
      pair_name: "BTC", buy_ex: "hl", sell_ex: "gc-hl", buy_dex: "xyz", sell_dex: "main",
      open_spread_pct: 1.2, close_spread_pct: null, status: true
    }] });
    const onSwitchDex = vi.fn();
    render(<PairTemplateCard result={result} onLinesChange={vi.fn()} onSwitchDex={onSwitchDex} />);
    await screen.findByText("Astro 已有卡片 BTC 使用 HL 买 xyz / 卖 main（开 1.20% / 平 —），当前查询的是 HL 买 para / 卖 para，价差可能不同");
    fireEvent.click(button("切换到 买 xyz / 卖 main 查询"));
    expect(onSwitchDex).toHaveBeenCalledWith({ leg1: "main", leg2: "xyz" });
    expect(button("修改卡片").hasAttribute("disabled")).toBe(false);
  });
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
  it("rounds recommended inputs to two decimals, steps by 0.01 and restores displayed recommendations", async () => {
    const result = fixture();
    const onLinesChange = vi.fn();
    render(<PairTemplateCard result={result} onLinesChange={onLinesChange} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    const recommendation = recommend(result.points, result.current);
    const checkInputs = () => {
      for (const template of recommendation.templates) {
        for (const [field, label] of [["open", "开仓"], ["close", "平仓"]] as const) {
          const input = screen.getByLabelText(`${template.label}${label}`) as HTMLInputElement;
          expect(input.value).toBe(template[field].toFixed(2));
          expect(input.getAttribute("step")).toBe("0.01");
        }
      }
    };
    checkInputs();
    const open = screen.getByLabelText("均衡（推荐）开仓") as HTMLInputElement;
    const initialOpen = Number(open.value);
    fireEvent.keyDown(open, { key: "ArrowUp" });
    fireEvent.keyUp(open, { key: "ArrowUp" });
    expect(open.value).toBe((initialOpen + 0.01).toFixed(2));
    fireEvent.click(button("恢复推荐值"));
    checkInputs();
    const balanced = recommendation.templates[1];
    expect(onLinesChange).toHaveBeenLastCalledWith(chartLines({
      open: Number(balanced.open.toFixed(2)), close: Number(balanced.close.toFixed(2))
    }, recommendation.h, recommendation.direction));
  });
  it("formats percentages and validation thresholds to two decimals and hourly funding to four", async () => {
    const result = fixture();
    result.current!.leg1.funding_rate_pct = 0.03;
    render(<PairTemplateCard result={result} onLinesChange={vi.fn()} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    const recommendation = recommend(result.points, result.current, 0.16, 0.15,
      fundingMetrics(result.current?.leg1, result.current?.leg2).hourly);
    expect(screen.getByText("半边成本 h=0.10% · 净资金费率 -0.0012%/小时（按当前费率估算）")).toBeTruthy();
    for (const template of recommendation.templates) {
      const card = within(screen.getByText(template.label).closest(".ant-card")! as HTMLElement);
      const open = Number((card.getByLabelText(`${template.label}开仓`) as HTMLInputElement).value);
      const close = Number((card.getByLabelText(`${template.label}平仓`) as HTMLInputElement).value);
      expect(card.getByText(`间距 ${(open - close).toFixed(2)}% · 资金费调整 ${template.fundingAdjustment.toFixed(2)}%`)).toBeTruthy();
      expect(card.getByText(`距开仓线 ${(open - recommendation.openExec!).toFixed(2)}%`)).toBeTruthy();
      const summaries = card.getAllByText(/笔 · 胜率/);
      for (const [index, stats] of [template.inSample, template.outOfSample].entries()) {
        expect(summaries[index].textContent).toBe(`${stats.trades} 笔 · 胜率 ${stats.winRate?.toFixed(0) ?? "—"}${stats.winRate === null ? "" : "%"} · 总净 ${stats.totalNetPct.toFixed(2)}%`);
        expect(card.getAllByText(`平均持仓 ${stats.averageHours.toFixed(2)}h · 最长 ${stats.longestHours.toFixed(2)}h`).length).toBeGreaterThan(0);
        if (stats.holding) expect(card.getAllByText(`末尾仍持仓 · 浮盈亏 ${stats.floatingNetPct!.toFixed(2)}%`).length).toBeGreaterThan(0);
      }
      expect(card.getByText(`验证方向 左多右空 · 开 ${template.validationThresholds.open.toFixed(2)}% / 平 ${template.validationThresholds.close.toFixed(2)}%`)).toBeTruthy();
    }
  });
  it("rounds fractional win rates to an integer and preserves missing values", async () => {
    const result = fixture();
    result.current!.leg1.funding_rate_pct = null;
    result.points = result.points.map((point, i) => ({ ...point, spread_pct: [1.311, 0.8, 1.211, 0.8, 1.111, 0.8][i] ?? 0.8 }));
    render(<PairTemplateCard result={result} onLinesChange={vi.fn()} />);
    await screen.findByText("资金费数据不足，资金费调整按 0 估算");
    expect(screen.getByText("半边成本 h=0.10% · 净资金费率 —/小时（按当前费率估算）")).toBeTruthy();
    expect(screen.getAllByText("0 笔 · 胜率 — · 总净 0.00%").length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("均衡（推荐）开仓"), { target: { value: "0.91" } });
    fireEvent.change(screen.getByLabelText("均衡（推荐）平仓"), { target: { value: "0.90" } });
    expect(screen.getByText("3 笔 · 胜率 67% · 总净 0.15%")).toBeTruthy();
  });
  it("rejects thresholds that become equal at the displayed precision", async () => {
    const onLinesChange = vi.fn();
    render(<PairTemplateCard result={fixture()} onLinesChange={onLinesChange} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    fireEvent.change(screen.getByLabelText("均衡（推荐）开仓"), { target: { value: "1.234" } });
    fireEvent.change(screen.getByLabelText("均衡（推荐）平仓"), { target: { value: "1.231" } });
    expect((screen.getByLabelText("均衡（推荐）开仓") as HTMLInputElement).value).toBe("1.23");
    expect((screen.getByLabelText("均衡（推荐）平仓") as HTMLInputElement).value).toBe("1.23");
    expect(button("快捷建卡").hasAttribute("disabled")).toBe(true);
    expect(button("修改卡片").hasAttribute("disabled")).toBe(true);
    expect(onLinesChange).toHaveBeenLastCalledWith(null);
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
    expect(thresholds).toEqual({
      open_spread_pct: Number((screen.getByLabelText("均衡（推荐）开仓") as HTMLInputElement).value),
      close_spread_pct: Number((screen.getByLabelText("均衡（推荐）平仓") as HTMLInputElement).value)
    });
    fireEvent.click(button("确认创建"));
    await waitFor(() => expect(createInstrumentAstroCard).toHaveBeenCalledWith(route, 0.8, expect.objectContaining(thresholds!)));
  });
  it("confirms updates with expected positions, only after reviewing the change", async () => {
    vi.mocked(lookupInstrumentAstroCard).mockResolvedValue({ ...existing, open_spread_pct: 2.126, close_spread_pct: -0.126 });
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    fireEvent.change(screen.getByLabelText("均衡（推荐）开仓"), { target: { value: "1.236" } });
    fireEvent.change(screen.getByLabelText("均衡（推荐）平仓"), { target: { value: "-0.124" } });
    expect((screen.getByLabelText("均衡（推荐）开仓") as HTMLInputElement).value).toBe("1.24");
    expect((screen.getByLabelText("均衡（推荐）平仓") as HTMLInputElement).value).toBe("-0.12");
    expect(screen.getByText("已有卡片 BTC：开 2.13% → 1.24% / 平 -0.13% → -0.12%")).toBeTruthy();
    fireEvent.click(button("修改卡片"));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("开仓 2.13% → 1.24%")).toBeTruthy();
    expect(dialog.getByText("平仓 -0.13% → -0.12%")).toBeTruthy();
    expect(updateInstrumentAstroCard).not.toHaveBeenCalled();
    fireEvent.click(button("确认修改"));
    await waitFor(() => expect(updateInstrumentAstroCard).toHaveBeenCalledWith(expect.objectContaining({
      expected_open_position: "0.020000", expected_close_position: "0.001000", open_spread_pct: 1.24, close_spread_pct: -0.12
    })));
  });
  it("shows warnings alongside the successful update message", async () => {
    const warning = "当前价差已达到开仓线，修改后可能立即开仓";
    vi.mocked(updateInstrumentAstroCard).mockResolvedValue({ enabled: true, status: "updated", action: "update",
      message: "阈值修改成功", warnings: [warning, "另一条提示"], pair_name: "BTC", pair_type: "FF" });
    render(<PairTemplateCard result={fixture()} onLinesChange={vi.fn()} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    fireEvent.click(button("修改卡片"));
    fireEvent.click(button("确认修改"));
    await screen.findByText(`阈值修改成功；${warning}；另一条提示`);
  });
  it.each<{ name: string; change: (result: PairSpreadQueryResult) => PairSpreadQueryResult }>([
    { name: "direction", change: (result) => ({ ...result, points: result.points.map((point) => ({ ...point, spread_pct: -1.5 * point.spread_pct })) }) },
    { name: "pair", change: (result) => ({ ...result, leg2: { ...result.leg2, symbol: "ETHUSDT" } }) },
    { name: "DEX", change: (result) => ({ ...result, leg1: { ...result.leg1, dex: "xyz" } }) },
    { name: "hours", change: (result) => ({ ...result, hours: 24 }) },
    { name: "sampling interval", change: (result) => ({ ...result, interval_minutes: 5, interval_seconds: 300 }) },
    { name: "window start", change: (result) => ({ ...result, first_seen_at: "2026-11-03T00:01:00Z" }) },
    { name: "window end", change: (result) => ({ ...result, last_seen_at: "2026-11-03T00:11:00Z" }) },
    { name: "multiplier", change: (result) => ({ ...result, leg2_multiplier: 2 }) }
  ])("resets all manual thresholds when the $name changes, including on return", async ({ change }) => {
    const initial = fixture();
    const onLinesChange = vi.fn();
    const { rerender } = render(<PairTemplateCard result={initial} onLinesChange={onLinesChange} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    fireEvent.change(screen.getByLabelText("均衡（推荐）开仓"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("均衡（推荐）平仓"), { target: { value: "-3" } });
    fireEvent.change(screen.getByLabelText("稳健开仓"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("积极平仓"), { target: { value: "-4" } });
    expect(screen.getAllByText("样本内（手调后）")).toHaveLength(3);
    onLinesChange.mockClear();
    for (const result of [change(initial), initial]) {
      rerender(<PairTemplateCard result={result} onLinesChange={onLinesChange} />);
      await waitFor(() => expect(screen.queryByText("正在查找已有卡片…")).toBeNull());
      const recommendation = recommend(result.points, result.current, 0.16, 0.15,
        fundingMetrics(result.current?.leg1, result.current?.leg2).hourly);
      for (const template of recommendation.templates) {
        expect((screen.getByLabelText(`${template.label}开仓`) as HTMLInputElement).value).toBe(template.open.toFixed(2));
        expect((screen.getByLabelText(`${template.label}平仓`) as HTMLInputElement).value).toBe(template.close.toFixed(2));
      }
      expect(screen.queryByText("样本内（手调后）")).toBeNull();
      const balanced = recommendation.templates[1];
      expect(onLinesChange).toHaveBeenLastCalledWith(chartLines({
        open: Number(balanced.open.toFixed(2)), close: Number(balanced.close.toFixed(2))
      }, recommendation.h, recommendation.direction));
    }
  });
  it("preserves manual thresholds during refreshes within the same query and direction", async () => {
    const result = fixture();
    const { rerender } = render(<PairTemplateCard result={result} onLinesChange={vi.fn()} />);
    await waitFor(() => expect(button("修改卡片").hasAttribute("disabled")).toBe(false));
    fireEvent.change(screen.getByLabelText("均衡（推荐）开仓"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("均衡（推荐）平仓"), { target: { value: "-3" } });
    rerender(<PairTemplateCard result={{ ...result, leg1: { ...result.leg1 }, leg2: { ...result.leg2 },
      points: result.points.map((point) => ({ ...point, spread_pct: point.spread_pct + 0.1 })),
      current: { ...result.current!, open_spread_pct: 0.9 }, observed_at: "2026-11-03T00:10:30Z"
    }} onLinesChange={vi.fn()} />);
    expect(Number((screen.getByLabelText("均衡（推荐）开仓") as HTMLInputElement).value)).toBe(7);
    expect(Number((screen.getByLabelText("均衡（推荐）平仓") as HTMLInputElement).value)).toBe(-3);
    expect(screen.getByText("样本内（手调后）")).toBeTruthy();
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
