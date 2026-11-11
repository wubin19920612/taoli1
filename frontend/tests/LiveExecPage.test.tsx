import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createChart, HistogramSeries } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { chartMocks } from "../src/components/pairChartV2/chartTestUtils";
import { liveFixtures, trade } from "./liveExecFixtures";

let requests: URL[];
let fetchMock: ReturnType<typeof vi.fn>;
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
const count = (path: string) => requests.filter(url => url.pathname.endsWith(`/${path}`)).length;
function visibility(value: "hidden" | "visible") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
  fireEvent(document, new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-11T08:00:00Z"));
  visibility("visible");
  requests = [];
  chartMocks.length = 0;
  vi.mocked(createChart).mockClear();
  fetchMock = vi.fn(async (input: string) => {
    const url = new URL(input);
    requests.push(url);
    const path = url.pathname.split("/").pop() as keyof typeof liveFixtures;
    return { ok: true, status: 200, json: async () => ({ available: true, data: liveFixtures[path] }) };
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); visibility("visible"); });

describe("实盘契约页面", () => {
  it("renders risk state, both legs, all statistics and histogram bins from contract data", async () => {
    const { unmount } = render(<LiveExecPage />);
    await flush();
    expect(screen.getByRole("heading", { name: "实盘" })).toBeTruthy();
    for (const text of ["实盘模式", "熔断：权益保护", "暂停开仓：人工暂停", "最后一个错误：最近错误示例",
      "今日净利润", "累计净利润", "已开名义额", "持仓对数", "保证金倍数", "单腿处理中",
      "收敛止盈", "模拟净收益（bp）", "两腿发送 / 成交时间差（毫秒）",
      "按币种", "按交易对", "按平仓原因", "按开仓价差", "实盘与模拟对比", "结算前平仓"]) {
      expect(screen.getAllByText(text).length).toBeGreaterThan(0);
    }
    const account = screen.getByTestId("live-account-crossex");
    expect(account.textContent).toContain("无保证金占用");
    expect(within(account).getByText("140.00").className).toContain("danger");
    expect(screen.getByText("单腿处理中").className).toContain("red");
    expect(screen.getByText("200.00 / 600.00 USD")).toBeTruthy();
    expect(screen.getByText("1 / 3")).toBeTruthy();
    expect(screen.getAllByText(/crossex/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/多/).length).toBeGreaterThan(0);
    const chart = chartMocks[0];
    expect(chart.addSeries).toHaveBeenCalledWith(HistogramSeries, expect.anything());
    expect(chart.series[0].setData.mock.calls[0][0].map((bar: { value: number }) => bar.value)).toEqual([1, 3]);
    expect(fetchMock.mock.calls.every(call => call[1].credentials === "same-origin")).toBe(true);
    unmount();
    expect(chart.remove).toHaveBeenCalledOnce();
  });

  it("polls at 5/30 seconds, pauses when hidden and cleans up on unmount", async () => {
    const { unmount } = render(<LiveExecPage />); await flush();
    expect(requests).toHaveLength(4);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect([count("summary"), count("positions"), count("trades"), count("stats")]).toEqual([2, 2, 1, 1]);
    await act(async () => { await vi.advanceTimersByTimeAsync(25000); });
    expect([count("summary"), count("positions"), count("trades"), count("stats")]).toEqual([7, 7, 2, 2]);
    visibility("hidden"); const hiddenCount = requests.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(requests).toHaveLength(hiddenCount);
    visibility("visible"); await flush(); expect(requests.length).toBe(hiddenCount + 4);
    unmount(); const endCount = requests.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(requests).toHaveLength(endCount);
  });

  it("does not fetch on a hidden initial mount", async () => {
    visibility("hidden"); render(<LiveExecPage />); await flush();
    expect(requests).toHaveLength(0);
    visibility("visible"); await flush(); expect(requests).toHaveLength(4);
  });

  it("shows a disconnected empty state and clears previously displayed data", async () => {
    render(<LiveExecPage />); await flush();
    fetchMock.mockImplementation(async () => ({ ok: true, status: 200,
      json: async () => ({ available: false, error: "not_configured" }) }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText(/实盘服务未连接（未配置）/)).toBeTruthy();
    expect(screen.queryByText(/最近错误示例/)).toBeNull();
    expect(screen.queryByText("JCTUSDT")).toBeNull();
    expect(screen.getByText("暂无当前持仓")).toBeTruthy();
    expect(screen.getByText("暂无已平仓记录")).toBeTruthy();
  });

  it("handles network errors, nullable fields and missing simulation safely", async () => {
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    render(<LiveExecPage />); await flush();
    expect(screen.getByText(/实盘服务未连接/)).toBeTruthy();
    expect(screen.queryByText("offline")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText("实盘模式")).toBeTruthy();
  });

  it("appends using before, deduplicates and keeps history during a refresh", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      const url = new URL(input); requests.push(url);
      const path = url.pathname.split("/").pop() as keyof typeof liveFixtures;
      const data = path === "trades"
        ? (url.searchParams.has("before") ? [trade("older", "OLDERUSDT", "2026-11-11T07:00:00Z")] : [trade()])
        : liveFixtures[path];
      return { ok: true, status: 200, json: async () => ({ available: true, data }) };
    });
    render(<LiveExecPage />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "加载更多" })); await flush();
    expect(requests[requests.length - 1]?.searchParams.get("before")).toBe("2026-11-11T07:59:00Z");
    expect(screen.getByText("OLDERUSDT")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByText("OLDERUSDT")).toBeTruthy();
    expect(screen.getAllByText("JCTUSDT").length).toBe(3); // position, closed trade, symbol group
  });

  it("aborts hidden requests and ignores their late responses", async () => {
    let resolveOld: (value: unknown) => void = () => undefined;
    let oldSignal: AbortSignal | undefined;
    fetchMock.mockImplementationOnce((_input: string, options: RequestInit) => {
      oldSignal = options.signal as AbortSignal;
      return new Promise(resolve => { resolveOld = resolve; });
    });
    render(<LiveExecPage />); await flush();
    visibility("hidden"); expect(oldSignal?.aborted).toBe(true);
    visibility("visible"); await flush();
    await act(async () => {
      resolveOld({ ok: true, status: 200, json: async () => ({ available: false, error: "timeout" }) });
    });
    expect(screen.getByText("实盘模式")).toBeTruthy();
    expect(screen.queryByText(/实盘服务未连接/)).toBeNull();
  });

  it("serializes pagination with refreshes, then stops on an empty page", async () => {
    render(<LiveExecPage />); await flush();
    let resolvePage: (value: unknown) => void = () => undefined;
    fetchMock.mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "加载更多" })); await flush();
    const before = count("trades");
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(count("trades")).toBe(before);
    await act(async () => {
      resolvePage({ ok: true, status: 200, json: async () => ({ available: true, data: [] }) });
    });
    expect(screen.getByRole("button", { name: "加载更多" }).hasAttribute("disabled")).toBe(true);
  });

  it("supports new trades after an empty start and nullable contract objects", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      const path = new URL(input).pathname.split("/").pop();
      const data = path === "summary" ? { ...liveFixtures.summary, mode: "shadow", caps: null, accounts: null, today: null, total: null, fees: null }
        : path === "stats" ? { ...liveFixtures.stats, histogram: null, by_symbol: null, by_pair: null, by_exit_reason: null, by_spread_tier: null, vs_sim: null } : [];
      return { ok: true, status: 200, json: async () => ({ available: true, data }) };
    });
    render(<LiveExecPage />); await flush();
    expect(screen.getByText("影子模式")).toBeTruthy();
    expect(screen.getByText("暂无模拟对比数据")).toBeTruthy();
    expect(screen.getByRole("button", { name: "加载更多" }).hasAttribute("disabled")).toBe(true);
    fetchMock.mockImplementation(async (input: string) => {
      const path = new URL(input).pathname.split("/").pop() as keyof typeof liveFixtures;
      return { ok: true, status: 200, json: async () => ({ available: true, data: liveFixtures[path] }) };
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByRole("button", { name: "加载更多" }).hasAttribute("disabled")).toBe(false);
  });
});
