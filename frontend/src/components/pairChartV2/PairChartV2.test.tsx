import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createChart, HistogramSeries, LineSeries } from "lightweight-charts";
import { PairChartV2 } from "./PairChartV2";
import { chartFixture, chartMocks } from "./chartTestUtils";

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
    expect(chart.series[3].setData).toHaveBeenLastCalledWith([{ time: Date.parse("2026-11-03T00:05:00Z") / 1000, value: 0.02 }]);
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
});
