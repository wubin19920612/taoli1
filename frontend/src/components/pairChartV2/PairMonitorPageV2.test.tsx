import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PairMonitorPage } from "../../pages/PairMonitorPage";
import { chartFixture } from "./chartTestUtils";
import type { RangeBacktestRequest } from "../../api/rangeBacktest";
import { rangeBacktestFixture } from "../../../tests/fixtures/rangeBacktest";

const requests: URL[] = [];
const backtests: RangeBacktestRequest[] = [];
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); requests.length = 0; backtests.length = 0;
  window.history.replaceState({}, "", "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=BTCUSDT&leg2_exchange=bitget&leg2_market_type=future&leg2_symbol=BTCUSDT&hours=4&interval_seconds=60");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost"); requests.push(url);
    if (url.pathname.endsWith("/pair-spread/presets")) return Response.json([]);
    if (url.pathname.endsWith("/pair-spread/funding-records/status")) return Response.json({ watched: false, item: null, samples: [], warnings: [] });
    if (url.pathname.endsWith("/pair-spread/query")) {
      const result = chartFixture();
      result.hours = Number(url.searchParams.get("hours"));
      const requestedInterval = Number(url.searchParams.get("interval_seconds"));
      result.interval_seconds = result.hours > 168 ? Math.max(3600, requestedInterval) : requestedInterval;
      result.interval_minutes = result.interval_seconds / 60;
      result.leg1.exchange = url.searchParams.get("leg1_exchange")!;
      result.leg2.exchange = url.searchParams.get("leg2_exchange")!;
      return Response.json(result);
    }
    if (url.pathname.endsWith("/pair-spread/range-backtest")) {
      const req = JSON.parse(String(init?.body)) as RangeBacktestRequest;
      backtests.push(req); return Response.json(rangeBacktestFixture(req.leg1, req.leg2, req));
    }
    return Response.json({});
  }));
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); window.history.replaceState({}, "", "/"); });
const latestQuery = () => requests.filter((r) => r.pathname.endsWith("/pair-spread/query")).slice(-1)[0];

describe("PairMonitorPage v2 integration", () => {
  it("defaults to v2, switches to the original SVG charts and persists across a remount", async () => {
    const first = render(<PairMonitorPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "再加载 1 天" }).hasAttribute("disabled")).toBe(false));
    expect(screen.getByTestId("pair-chart-v2")).toBeTruthy();
    expect(document.querySelector(".pair-spread-chart")).toBeNull();
    expect(document.querySelector(".pair-price-card")).toBeNull();
    fireEvent.click(screen.getByText("原版", { exact: true }));
    await waitFor(() => expect(document.querySelector(".pair-spread-chart")).toBeTruthy());
    expect(document.querySelector(".pair-price-card")).toBeTruthy();
    expect(screen.queryByTestId("pair-chart-v2")).toBeNull();
    expect(localStorage.getItem("pair-chart-version")).toBe("v1");
    first.unmount();
    const second = render(<PairMonitorPage />);
    await waitFor(() => expect(document.querySelector(".pair-spread-chart")).toBeTruthy());
    expect(screen.queryByTestId("pair-chart-v2")).toBeNull();
    fireEvent.click(screen.getByText("新版图表", { exact: true }));
    expect(screen.getByTestId("pair-chart-v2")).toBeTruthy();
    expect(localStorage.getItem("pair-chart-version")).toBe("v2");
    second.unmount();
  });

  it("reuses query and swap callbacks, preserving identity and interval while limiting history requests to seven days", async () => {
    render(<PairMonitorPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "再加载 1 天" }).hasAttribute("disabled")).toBe(false));
    const original = latestQuery().searchParams;
    fireEvent.click(screen.getByRole("button", { name: "再加载 1 天" }));
    await waitFor(() => expect(latestQuery().searchParams.get("hours")).toBe("28"));
    for (const key of ["leg1_exchange", "leg2_exchange", "leg1_symbol", "leg2_symbol", "interval_seconds"]) {
      expect(latestQuery().searchParams.get(key)).toBe(original.get(key));
    }
    await waitFor(() => expect(screen.getByRole("button", { name: "加载至 7 天" }).className).not.toContain("loading"));
    fireEvent.click(screen.getByRole("button", { name: "加载至 7 天" }));
    await waitFor(() => expect(latestQuery().searchParams.get("hours")).toBe("168"));
    await waitFor(() => expect(screen.getByRole("button", { name: "再加载 1 天" }).className).not.toContain("loading"));
    const queries = requests.filter((r) => r.pathname.endsWith("/pair-spread/query")).length;
    expect(screen.getByRole("button", { name: "再加载 1 天" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "加载至 7 天" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "再加载 1 天" }));
    fireEvent.click(screen.getByRole("button", { name: "加载至 7 天" }));
    expect(requests.filter((r) => r.pathname.endsWith("/pair-spread/query"))).toHaveLength(queries);
    expect(latestQuery().searchParams.get("interval_seconds")).toBe("60");
    await waitFor(() => expect(screen.getByRole("button", { name: "⇆ 切换" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "⇆ 切换" }));
    await waitFor(() => expect(latestQuery().searchParams.get("leg1_exchange")).toBe("bitget"));
    expect(latestQuery().searchParams.get("leg2_exchange")).toBe("binance");
    for (const request of requests.filter((r) => r.pathname.endsWith("/pair-spread/query"))) {
      expect(Number(request.searchParams.get("hours"))).toBeLessThanOrEqual(168);
    }
  });

  it("requests only the remaining hours when one more day would exceed seven days", async () => {
    window.history.replaceState({}, "", window.location.href.replace("hours=4", "hours=160"));
    render(<PairMonitorPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "再加载 1 天" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "再加载 1 天" }));
    await waitFor(() => expect(latestQuery().searchParams.get("hours")).toBe("168"));
    expect(latestQuery().searchParams.get("interval_seconds")).toBe("60");
    await waitFor(() => expect(screen.getByRole("button", { name: "再加载 1 天" }).hasAttribute("disabled")).toBe(true));
    for (const request of requests.filter((r) => r.pathname.endsWith("/pair-spread/query"))) {
      expect(Number(request.searchParams.get("hours"))).toBeLessThanOrEqual(168);
    }
  });

  it("keeps the original RangeBacktestPanel connected to v2 range selection and clearing", async () => {
    render(<PairMonitorPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "再加载 1 天" }).hasAttribute("disabled")).toBe(false));
    fireEvent.mouseDown(screen.getByTestId("pair-chart-v2-canvas"), { button: 0, shiftKey: true, clientX: 180, clientY: 50 });
    fireEvent.mouseMove(window, { clientX: 480, clientY: 50 });
    expect(backtests).toHaveLength(0);
    fireEvent.mouseUp(window, { button: 0, clientX: 480, clientY: 50 });
    await screen.findByRole("region", { name: "区间回测结果" });
    await waitFor(() => expect(backtests).toHaveLength(1));
    expect(backtests[0]).toMatchObject({ direction: "long1_short2", start_at: "2026-11-03T00:01:40.000Z", end_at: "2026-11-03T00:06:40.000Z" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "区间回测结果" })).toBeNull();
  });
});
