import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createChart, HistogramSeries } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { chartMocks } from "../src/components/pairChartV2/chartTestUtils";
import { liveFixtures, trade } from "./liveExecFixtures";

import { fixtures as adminFixtures } from "./liveExecAdminFixtures";

let requests: URL[];
let fetchMock: ReturnType<typeof vi.fn>;
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); }); };
const count = (path: string) => requests.filter(url => url.pathname.endsWith(`/${path}`)).length;
const adminCounts = () => ["config", "universe", "mappings", "config/audit", "external-positions"].map(count);
function visibility(value: "hidden" | "visible") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
  fireEvent(document, new Event("visibilitychange"));
}

const metricTitles = ["开仓基准（bp）", "开仓超额价差（bp）", "预估两小时资金费成本（bp）",
  "开仓盘口时间差（毫秒）", "平仓盘口时间差（毫秒）"];
const snapshotFields = ["baseline_bps", "excess_bps", "funding_cost_bps", "open_book_skew_ms", "close_book_skew_ms"];
const pairCountLabels = ["基准预热完成数", "结构性拉黑数", "冷却数"];
const skipLabels = ["盘口不同步", "盘口陈旧", "资金费率检查未通过", "基准不平坦", "非单一交易所异常", "交易所数量不足"];

function descriptionValue(label: string): string | null | undefined {
  return screen.getByText(label).closest(".ant-descriptions-item")?.querySelector(".ant-descriptions-item-content")?.firstChild?.textContent;
}

function metricValues(section: string, rowKey: string) {
  const card = screen.getByText(section).closest<HTMLElement>(".ant-card")!;
  const row = card.querySelector<HTMLTableRowElement>(`tr[data-row-key="${rowKey}"]`)!;
  return metricTitles.map(title => {
    const header = within(card).getByRole("columnheader", { name: title }) as HTMLTableCellElement;
    return row.cells[header.cellIndex].textContent;
  });
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
    const path = url.pathname.replace("/api/live-exec/", "");
    const data = adminFixtures[path as keyof typeof adminFixtures] ?? liveFixtures[path as keyof typeof liveFixtures];
    return { ok: true, status: 200, json: async () => ({ available: true, data }) };
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); visibility("visible"); });

describe("实盘契约页面", () => {
  it("renders opening snapshots separately from actual PnL and order timing", async () => {
    render(<LiveExecPage />); await flush();
    expect(metricValues("当前持仓", "p1")).toEqual(["20.00", "20.10", "1.00", "250", "—"]);
    expect(metricValues("已平仓记录", "t1")).toEqual(["-7.25", "28.35", "0.00", "0", "2300"]);
    expect(screen.getByText("0.15 USD / 15.00 bp")).toBeTruthy();
    expect(screen.getByText("12 / 230")).toBeTruthy();
    expect(screen.getByText(/开仓超额价差尚未扣除费用/)).toBeTruthy();
    expect(screen.getByText(/盘口时间差不代表订单发送或成交时间差/)).toBeTruthy();
  });

  it("renders all pair counts and cumulative skip reasons with Chinese explanations", async () => {
    render(<LiveExecPage />); await flush();
    expect(pairCountLabels.map(descriptionValue)).toEqual(["12", "2", "1"]);
    skipLabels.forEach((label, index) => {
      expect(descriptionValue(label)).toBe(String([2, 1, 3, 4, 5, 6][index]));
    });
    for (const explanation of [
      "两腿盘口时间差超过开仓允许范围。",
      "盘口时间信息缺失、无效或已过期。",
      "资金费率数据无效，或归一化后的费率、费率差超限。",
      "历史基准偏移或波动过大，未通过平坦性检查。",
      "价格偏离未能归因于单一交易所，未通过异常模式检查。",
      "有效且及时的参考交易所不足，无法判断单所异常。",
    ]) expect(screen.getByText(explanation)).toBeTruthy();
    expect(screen.getByText(/三项可能重叠，不表示可立即开仓/)).toBeTruthy();
    expect(screen.getByText(/累计放弃机会数（含影子模式），跨日和重启保留/)).toBeTruthy();
  });

  it("uses effective program risk limits after admin changes and shows round eight skip counts", async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: string) => input.endsWith("/summary")
      ? Promise.resolve({ ok: true, status: 200, json: async () => ({ available: true, data: {
        ...liveFixtures.summary, config_version: 8, effective_risk_limits: {
          max_equity_drop_usd: 50, daily_loss_stop_usd: 5, total_loss_stop_usd: 20,
        }, opening_skip_counts: { size_too_small: 1, oi_missing: 2, mapping_ratio_mismatch: 3,
          volume_too_low: 4, volume_unknown: 5, category_unconfirmed: 6 },
      } }) }) : original(input));
    render(<LiveExecPage />); await flush();
    expect(screen.getByTestId("live-program-risk").textContent).toContain("50.00 USD");
    expect(screen.getByTestId("live-account-crossex").textContent).not.toContain("/ 50.00 USD");
    expect(descriptionValue("当前配置版本")).toBe("8");
    ["定量不足", "缺少有效 OI", "映射价格比不符", "成交额不足", "成交额未知", "类别未确认"].forEach((label, i) => {
      expect(descriptionValue(label)).toBe(String(i + 1));
    });
  });

  it.each(["missing", "null"])("shows dashes for %s new fields from older services", async variant => {
    fetchMock.mockImplementation(async (input: string) => {
      const path = new URL(input).pathname.split("/").pop() as keyof typeof liveFixtures;
      let data: unknown = liveFixtures[path];
      if (path === "summary") {
        const row: Record<string, unknown> = { ...liveFixtures.summary };
        for (const key of ["baseline_ready_pairs", "structural_spread_pairs", "cooldown_pairs", "opening_skip_counts"]) {
          if (variant === "missing") delete row[key]; else row[key] = null;
        }
        data = row;
      } else if (path === "positions" || path === "trades") {
        data = liveFixtures[path].map(item => {
          const row: Record<string, unknown> = { ...item };
          for (const key of snapshotFields) {
            if (variant === "missing") delete row[key]; else row[key] = null;
          }
          return row;
        });
      }
      return { ok: true, status: 200, json: async () => ({ available: true, data }) };
    });
    render(<LiveExecPage />); await flush();
    expect(pairCountLabels.map(descriptionValue)).toEqual(["—", "—", "—"]);
    expect(skipLabels.map(descriptionValue)).toEqual(Array(6).fill("—"));
    expect(metricValues("当前持仓", "p1")).toEqual(Array(5).fill("—"));
    expect(metricValues("已平仓记录", "t1")).toEqual(Array(5).fill("—"));
    expect(screen.queryByText(/实盘服务未连接/)).toBeNull();
    expect(screen.getByText("0.15 USD / 15.00 bp")).toBeTruthy();
  });

  it("keeps zero counts and handles partially missing skip reasons", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      const path = new URL(input).pathname.split("/").pop() as keyof typeof liveFixtures;
      const data = path === "summary" ? { ...liveFixtures.summary, baseline_ready_pairs: 0,
        structural_spread_pairs: 0, cooldown_pairs: 0, opening_skip_counts: { book_skew: 0, book_stale: null } }
        : liveFixtures[path];
      return { ok: true, status: 200, json: async () => ({ available: true, data }) };
    });
    render(<LiveExecPage />); await flush();
    expect(pairCountLabels.map(descriptionValue)).toEqual(["0", "0", "0"]);
    expect(skipLabels.map(descriptionValue)).toEqual(["0", "—", "—", "—", "—", "—"]);
  });

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
    expect(within(account).getByText("140.00").className).not.toContain("danger");
    expect(screen.getByText("单腿处理中").className).toContain("red");
    expect(screen.getByText("200.00 / 600.00 USD")).toBeTruthy();
    expect(screen.getByText("1 / 3")).toBeTruthy();
    expect(screen.getByText("0.0480% / 0.0600%（crossex / bitget）")).toBeTruthy();
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
    expect(requests).toHaveLength(10);
    expect(count("remote-positions")).toBe(1);
    expect(adminCounts()).toEqual([1, 1, 1, 1, 1]);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect([count("summary"), count("positions"), count("trades"), count("stats")]).toEqual([2, 2, 1, 1]);
    expect(adminCounts()).toEqual([1, 1, 1, 1, 1]);
    expect(requests).toHaveLength(13);
    expect(count("remote-positions")).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(25000); });
    expect([count("summary"), count("positions"), count("trades"), count("stats")]).toEqual([7, 7, 2, 2]);
    expect(adminCounts()).toEqual([2, 2, 2, 2, 2]);
    expect(requests).toHaveLength(35);
    expect(count("remote-positions")).toBe(7);
    visibility("hidden"); const hiddenCount = requests.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(requests).toHaveLength(hiddenCount);
    visibility("visible"); await flush(); expect(requests.length).toBe(hiddenCount + 10);
    expect(count("remote-positions")).toBe(8);
    expect(adminCounts()).toEqual([3, 3, 3, 3, 3]);
    unmount(); const endCount = requests.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(requests).toHaveLength(endCount);
  });

  it("does not fetch on a hidden initial mount", async () => {
    visibility("hidden"); render(<LiveExecPage />); await flush();
    expect(requests).toHaveLength(0);
    visibility("visible"); await flush(); expect(requests).toHaveLength(10);
    expect(adminCounts()).toEqual([1, 1, 1, 1, 1]);
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
    const original = fetchMock.getMockImplementation()!;
    let failed = false;
    fetchMock.mockImplementation((input: string, options: RequestInit) => {
      if (input.endsWith("/summary") && !failed) { failed = true; return Promise.reject(new Error("offline")); }
      return original(input, options);
    });
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
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: string, options: RequestInit) => {
      if (input.endsWith("/summary") && !oldSignal) {
        oldSignal = options.signal as AbortSignal;
        return new Promise(resolve => { resolveOld = resolve; });
      }
      return original(input, options);
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

  it("retains an upstream halt reason when the halt flag has cleared", async () => {
    fetchMock.mockImplementation(async (input: string) => {
      const path = new URL(input).pathname.split("/").pop() as keyof typeof liveFixtures;
      return { ok: true, status: 200, json: async () => ({ available: true,
        data: path === "summary" ? { ...liveFixtures.summary, halted: false } : liveFixtures[path] }) };
    });
    render(<LiveExecPage />); await flush();
    expect(screen.getByText("未熔断：权益保护")).toBeTruthy();
  });
});
