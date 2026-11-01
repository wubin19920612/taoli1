import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PairSpreadQueryResult } from "../src/api/types";
import { PairSpreadChart } from "../src/pages/PairMonitorPage";
import { backtestLeg1, backtestLeg2 } from "./fixtures/rangeBacktest";

function chartFixture(ratioMode = false): PairSpreadQueryResult {
  const points = [0, 1, 2, 9, 10].map((minute) => ({
    bucket_at: new Date(Date.parse("2026-11-01T00:00:00Z") + minute * 60_000).toISOString(),
    leg1_close: ratioMode ? 4000 : 100,
    leg2_close: 101,
    spread_abs: 1,
    spread_pct: 1
  }));
  const stats = { min: 1, max: 1, mean: 1, current: 1 };
  return {
    leg1: backtestLeg1, leg2: backtestLeg2, hours: 1, interval_minutes: 1, interval_seconds: 60,
    leg2_multiplier: 1, observed_at: points[4].bucket_at, point_count: points.length,
    first_seen_at: points[0].bucket_at, last_seen_at: points[4].bucket_at,
    spread_abs: stats, spread_pct: stats, current: null, points, funding_history: [], warnings: []
  };
}

function setup(ratioMode = false) {
  const onRangeSelect = vi.fn();
  const onRangeClear = vi.fn();
  const result = chartFixture(ratioMode);
  const view = render(<PairSpreadChart result={result} onRangeSelect={onRangeSelect} onRangeClear={onRangeClear} />);
  const chart = screen.getByRole("img", { name: ratioMode ? "价格比值曲线" : "均值价差率曲线" });
  vi.spyOn(chart, "getBoundingClientRect").mockReturnValue({
    bottom: 185, height: 165, left: 35, right: 625, top: 20, width: 590,
    x: 35, y: 20, toJSON: () => ({})
  });
  const target = chart.querySelector(".pair-chart-hover-target")!;
  const left = ratioMode ? 78 : 56;
  const clientX = (fraction: number) => 35 + (left + (1180 - left - 28) * fraction) / 2;
  return { ...view, chart, target, clientX, onRangeSelect, onRangeClear, result };
}

describe("价差图拖选", () => {
  it.each([false, true])("松开后按真实时间回调，允许反向拖选，价格比值模式=%s", (ratioMode) => {
    const { target, chart, clientX, onRangeSelect } = setup(ratioMode);
    fireEvent.mouseDown(target, { button: 0, clientX: clientX(0.8) });
    fireEvent.mouseMove(window, { buttons: 1, clientX: clientX(0.3) });
    expect(chart.querySelector(".pair-chart-range-selection")).toBeTruthy();
    expect(onRangeSelect).not.toHaveBeenCalled();
    // Release outside the target still completes the drag.
    fireEvent.mouseUp(window, { button: 0, clientX: clientX(0.3) });
    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect).toHaveBeenCalledWith(
      Date.parse("2026-11-01T00:03:00Z"), Date.parse("2026-11-01T00:08:00Z")
    );
  });

  it.each([0, 7.9])("单击或不足8屏幕像素的选区不回调，宽度=%s", (width) => {
    const { target, chart, clientX, onRangeSelect } = setup();
    fireEvent.mouseDown(target, { button: 0, clientX: clientX(0.5) });
    fireEvent.mouseMove(target, { buttons: 1, clientX: clientX(0.5) + width });
    fireEvent.mouseUp(target, { button: 0, clientX: clientX(0.5) + width });
    expect(onRangeSelect).not.toHaveBeenCalled();
    expect(chart.querySelector(".pair-chart-range-selection")).toBeNull();
    expect(chart.querySelector(".pair-chart-hover-detail")).toBeTruthy();
    fireEvent.mouseLeave(target);
    expect(chart.querySelector(".pair-chart-hover-detail")).toBeNull();
  });

  it("宽度恰好8屏幕像素也回调", () => {
    const { target, clientX, onRangeSelect } = setup();
    fireEvent.mouseDown(target, { button: 0, clientX: clientX(0) });
    fireEvent.mouseUp(window, { button: 0, clientX: clientX(0) + 8 });
    expect(onRangeSelect).toHaveBeenCalledTimes(1);
  });

  it("拖出绘图区时钳制时间，忽略右键", () => {
    const { target, chart, clientX, onRangeSelect } = setup();
    fireEvent.mouseDown(target, { button: 2, clientX: clientX(0.5) });
    fireEvent.mouseUp(window, { button: 2, clientX: 1000 });
    expect(onRangeSelect).not.toHaveBeenCalled();
    fireEvent.mouseDown(target, { button: 0, clientX: clientX(0.5) });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 1000 });
    fireEvent.mouseUp(window, { button: 0, clientX: 1000 });
    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect).toHaveBeenCalledWith(
      Date.parse("2026-11-01T00:05:00Z"), Date.parse("2026-11-01T00:10:00Z")
    );
    expect(chart.querySelector(".pair-chart-range-selection")).toBeTruthy();
  });

  it("Esc取消进行中的拖选，清除按钮取消已选区间", () => {
    const { target, chart, clientX, onRangeSelect, onRangeClear } = setup();
    fireEvent.mouseDown(target, { button: 0, clientX: clientX(0) });
    fireEvent.mouseMove(window, { buttons: 1, clientX: clientX(1) });
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.mouseUp(window, { button: 0, clientX: clientX(1) });
    expect(onRangeSelect).not.toHaveBeenCalled();
    expect(chart.querySelector(".pair-chart-range-selection")).toBeNull();
    fireEvent.mouseDown(target, { button: 0, clientX: clientX(0) });
    fireEvent.mouseUp(window, { button: 0, clientX: clientX(1) });
    fireEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(chart.querySelector(".pair-chart-range-selection")).toBeNull();
    expect(onRangeClear).toHaveBeenCalledTimes(2);
  });
});
