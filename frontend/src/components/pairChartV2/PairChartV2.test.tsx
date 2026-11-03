import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createChart, HistogramSeries, LineSeries } from "lightweight-charts";
import { PairChartV2 } from "./PairChartV2";
import { binanceBybitFixture, chartFixture, chartMocks } from "./chartTestUtils";

beforeEach(() => { localStorage.clear(); chartMocks.length = 0; vi.mocked(createChart).mockClear(); });
const props = () => {
  const result = chartFixture();
  return { result, points: result.points, labels: ["Binance BTC", "Bitget BTC"] as [string, string], loading: false,
    onSwap: vi.fn(), onLoadHours: vi.fn(), onRangeSelect: vi.fn(), onRangeClear: vi.fn() };
};

describe("PairChartV2", () => {
  it("uses the v5 pane API, two axes, realtime rates, net bars and disposes the chart", () => {
    const p = props();
    const { unmount, rerender } = render(<PairChartV2 {...p} />);
    const chart = chartMocks[0];
    expect(chart.addSeries).toHaveBeenNthCalledWith(1, LineSeries, expect.objectContaining({ priceScaleId: "right", color: "#1D9E75" }), 0);
    expect(chart.addSeries).toHaveBeenNthCalledWith(2, LineSeries, expect.objectContaining({ priceScaleId: "left" }), 0);
    expect(chart.addSeries).toHaveBeenNthCalledWith(4, LineSeries, expect.anything(), 1);
    expect(chart.addSeries).toHaveBeenNthCalledWith(6, HistogramSeries, expect.anything(), 1);
    expect(chart.series[3].setData).toHaveBeenLastCalledWith([
      { time: Date.parse("2026-11-03T00:00:00Z") / 1000, value: 0.01 },
      { time: Date.parse("2026-11-03T00:05:00Z") / 1000, value: 0.02 }
    ]);
    expect(chart.series[4].setData).toHaveBeenLastCalledWith([
      { time: Date.parse("2026-11-03T00:00:00Z") / 1000, value: 0.03 },
      { time: Date.parse("2026-11-03T00:05:00Z") / 1000, value: 0.04 }
    ]);
    expect(chart.panes()[0].setStretchFactor).toHaveBeenCalledWith(0.7);
    expect(chart.panes()[1].setStretchFactor).toHaveBeenCalledWith(0.3);
    expect(chart.series[5].setData.mock.calls[0][0][0].value).toBeCloseTo(0.02);
    expect(vi.mocked(createChart).mock.calls[0][1]?.layout?.attributionLogo).not.toBe(false);
    rerender(<PairChartV2 {...p} points={[...p.points]} />);
    expect(createChart).toHaveBeenCalledTimes(1);
    expect(chart.scale.fitContent).toHaveBeenCalledTimes(1);
    unmount();
    expect(chart.remove).toHaveBeenCalledTimes(1);
    expect(chart.unsubscribeCrosshairMove).toHaveBeenCalledTimes(1);
    expect(chart.scale.unsubscribeVisibleLogicalRangeChange).toHaveBeenCalledTimes(1);
  });

  it("updates both price formats and last-value labels when the price magnitude changes", () => {
    const p = props();
    p.points = p.points.map((row) => ({ ...row, leg1_close: 78750, leg2_close: 78751 }));
    const { rerender } = render(<PairChartV2 {...p} />);
    const chart = chartMocks[0];
    for (const index of [1, 2]) {
      expect(chart.series[index].applyOptions).toHaveBeenCalledWith(expect.objectContaining({
        priceFormat: { type: "price", precision: 2, minMove: 0.01 }
      }));
      expect(chart.addSeries.mock.calls[index][1]).toMatchObject({ lastValueVisible: true });
    }
    for (const [value, precision] of [[1.2345, 4], [0.001234, 6]]) {
      rerender(<PairChartV2 {...p} points={p.points.map((row) => ({ ...row, leg1_close: value, leg2_close: value }))} />);
      for (const index of [1, 2]) expect(chart.series[index].applyOptions).toHaveBeenLastCalledWith(expect.objectContaining({
        priceFormat: { type: "price", precision, minMove: 10 ** -precision }
      }));
    }
  });

  it.each([145, 160, 167])("caps one more day at 168 hours from %s and explains disabled loading at the cap", async (hours) => {
    const p = props(); p.result.hours = hours;
    const { rerender } = render(<PairChartV2 {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "再加载 1 天" }));
    expect(p.onLoadHours).toHaveBeenCalledTimes(1);
    expect(p.onLoadHours).toHaveBeenCalledWith(168);
    rerender(<PairChartV2 {...p} result={{ ...p.result, hours: 168 }} />);
    const more = screen.getByRole("button", { name: "再加载 1 天" });
    const sevenDays = screen.getByRole("button", { name: "加载至 7 天" });
    expect(more.hasAttribute("disabled")).toBe(true);
    expect(sevenDays.hasAttribute("disabled")).toBe(true);
    fireEvent.click(more); fireEvent.click(sevenDays);
    expect(p.onLoadHours).toHaveBeenCalledTimes(1);
    fireEvent.mouseEnter(more.parentElement!);
    expect((await screen.findByRole("tooltip")).textContent).toBe("超过 7 天后端会改为小时周期");
  });

  it("loads more, swaps, toggles series and manages empty/visible threshold lines", () => {
    const p = props(); render(<PairChartV2 {...p} />);
    const chart = chartMocks[0];
    fireEvent.click(screen.getByRole("button", { name: "再加载 1 天" }));
    fireEvent.click(screen.getByRole("button", { name: "加载至 7 天" }));
    expect(p.onLoadHours.mock.calls).toEqual([[28], [168]]);
    fireEvent.click(screen.getByRole("button", { name: "⇆ 切换" })); expect(p.onSwap).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "价格 A · Binance BTC" }));
    expect(chart.series[1].applyOptions).toHaveBeenLastCalledWith({ visible: false });
    expect(chart.series[0].createPriceLine).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "开仓阈值 (%)" }), { target: { value: "0.5" } });
    expect(chart.series[0].createPriceLine).toHaveBeenLastCalledWith(expect.objectContaining({ price: 0.5, lineVisible: true, title: "开仓阈值" }));
    expect(JSON.parse(localStorage.getItem("pair-chart-thresholds")!).open).toBe(0.5);
    fireEvent.click(screen.getByRole("button", { name: "阈值" }));
    expect(chart.series[0].createPriceLine).toHaveBeenLastCalledWith(expect.objectContaining({ lineVisible: false, axisLabelVisible: false }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "开仓阈值 (%)" }), { target: { value: "" } });
    expect(JSON.parse(localStorage.getItem("pair-chart-thresholds")!).open).toBeNull();
    expect(chart.series[0].removePriceLine).toHaveBeenCalled();
  });

  it.each(["shift", "switch"])("selects a reverse range only on release (%s), preserves normal pan, and clears with Esc/button", (mode) => {
    const p = props(); const { unmount } = render(<PairChartV2 {...p} />);
    const host = screen.getByTestId("pair-chart-v2-canvas");
    fireEvent.mouseDown(host, { button: 0, clientX: 150, clientY: 50 });
    fireEvent.mouseUp(window, { button: 0, clientX: 450, clientY: 50 });
    expect(p.onRangeSelect).not.toHaveBeenCalled();
    if (mode === "switch") fireEvent.click(screen.getByRole("switch", { name: "区间" }));
    fireEvent.mouseDown(host, { button: 0, shiftKey: mode === "shift", clientX: 480, clientY: 50 });
    fireEvent.mouseMove(window, { clientX: 180, clientY: 50 });
    expect(screen.getByTestId("pair-v2-selection")).toBeTruthy();
    expect(p.onRangeSelect).not.toHaveBeenCalled();
    fireEvent.mouseUp(window, { button: 0, clientX: 180, clientY: 50 });
    const base = Date.parse("2026-11-03T00:00:00Z");
    expect(p.onRangeSelect).toHaveBeenCalledWith(base + 100_000, base + 400_000);
    expect(screen.getByTestId("pair-v2-selection").style.width).toBe("300px");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("pair-v2-selection")).toBeNull(); expect(p.onRangeClear).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "清除" })); expect(p.onRangeClear).toHaveBeenCalledTimes(2);
    unmount(); fireEvent.keyDown(window, { key: "Escape" }); expect(p.onRangeClear).toHaveBeenCalledTimes(2);
  });

  it("shows raw leg prices and the most recent settled net fee at the crosshair", () => {
    render(<PairChartV2 {...props()} />);
    const hover = chartMocks[0].subscribeCrosshairMove.mock.calls[0][0];
    act(() => hover({ time: Date.parse("2026-11-03T00:05:00Z") / 1000, point: { x: 300, y: 30 } }));
    const tooltip = screen.getByRole("status").textContent;
    expect(tooltip).toContain("左价：101 · 右价：102");
    expect(tooltip).toContain("最近净结算：0.0200%");
    expect(tooltip).toContain("价差：0.9900%");
    act(() => hover({})); expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the last aligned settlement at or before the crosshair, formatted once in the browser's local timezone", () => {
    const p = props(); p.result = binanceBybitFixture();
    render(<PairChartV2 {...p} />);
    const hover = chartMocks[0].subscribeCrosshairMove.mock.calls[0][0];
    const local = (iso: string) => new Intl.DateTimeFormat("sv-SE", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
    }).format(new Date(iso));
    const at = (iso: string) => act(() => hover({ time: Date.parse(iso) / 1000, point: { x: 300, y: 30 } }));
    at("2026-11-01T23:59:59Z");
    expect(screen.getByRole("status").textContent).toContain("最近净结算：—");
    at("2026-11-02T00:00:00Z");
    expect(screen.getByRole("status").textContent).toContain(`最近净结算：0.0000% (${local("2026-11-02T00:00:00Z")})`);
    at("2026-11-02T15:59:59Z");
    expect(screen.getByRole("status").textContent).toContain(`最近净结算：-0.0035% (${local("2026-11-02T08:00:00Z")})`);
    at("2026-11-02T16:00:00Z");
    expect(screen.getByRole("status").textContent).toContain(`最近净结算：0.0002% (${local("2026-11-02T16:00:00Z")})`);
    at("2026-11-02T20:00:00Z");
    expect(screen.getByRole("status").textContent).toContain(`最近净结算：0.0002% (${local("2026-11-02T16:00:00Z")})`);
    const chartOptions = vi.mocked(createChart).mock.calls[0][1]!;
    const time = Date.parse("2026-11-02T16:00:00Z") / 1000;
    expect((chartOptions.localization!.timeFormatter as (time: number) => string)(time)).toBe(local("2026-11-02T16:00:00Z"));
  });
});
