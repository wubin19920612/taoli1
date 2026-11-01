import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RangeBacktestPanel } from "../src/components/RangeBacktestPanel";
import { backtestLeg1, backtestLeg2, backtestRange, rangeBacktestFixture } from "./fixtures/rangeBacktest";

const props = { leg1: backtestLeg1, leg2: backtestLeg2, range: backtestRange };
const requestBody = (index = 0) => JSON.parse(String(vi.mocked(fetch).mock.calls[index][1]?.body));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(rangeBacktestFixture())));
});
afterEach(() => vi.unstubAllGlobals());

describe("区间回测结果面板", () => {
  it("提交对齐后端的请求并显示分项、估算、起止时间与可折叠资金费明细", async () => {
    render(<RangeBacktestPanel {...props} />);
    expect(await screen.findByText("+56.9000 美元")).toBeTruthy();
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/api/pair-spread/range-backtest");
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe("POST");
    expect(requestBody()).toEqual({ leg1: backtestLeg1, leg2: backtestLeg2, ...backtestRange, direction: "long1_short2", notional_usd: 1000 });
    expect(screen.getByText("估算")).toBeTruthy();
    expect(screen.getByText(/起始时间：2026-11-01/).textContent).toContain("结束时间：");
    for (const text of ["+100.0000 美元", "-40.0000 美元", "+60.0000 美元", "-0.1000 美元", "2.0000 美元", "1.0000 美元", "5.6900%"]) {
      expect(screen.getByText(text)).toBeTruthy();
    }
    expect(screen.getByText("数据质量（估算）")).toBeTruthy();
    expect(screen.getAllByText(/历史K线收盘价；粒度：60 秒；资金费覆盖率：100.00%/)).toHaveLength(2);
    const summary = screen.getByText("资金费逐笔明细（2 笔）");
    expect(summary.closest("details")?.open).toBe(false);
    await userEvent.click(summary);
    expect(summary.closest("details")?.open).toBe(true);
    expect(screen.getByText("-0.2000 美元")).toBeTruthy();
    expect(screen.getByText("+0.1000 美元")).toBeTruthy();
    expect(screen.getAllByText(/市场分区 xyz/).length).toBeGreaterThan(0);
  });

  it("有警告和资金费缺失时展示黄色提示与覆盖率", async () => {
    const response = rangeBacktestFixture();
    response.funding_pnl.coverage.leg1 = 0.5;
    response.data_quality.gaps = [{ leg: "leg1", kind: "funding", expected: 2, actual: 1 }];
    response.warnings = ["leg1 资金费缺失：实际 1 / 预计 2 次结算，未补零。"];
    vi.mocked(fetch).mockResolvedValue(Response.json(response));
    render(<RangeBacktestPanel {...props} />);
    const warning = await screen.findByText("资金费覆盖率不足，缺失记录未补零，估算结果可能不完整。");
    expect(warning.closest(".ant-alert")?.classList.contains("ant-alert-warning")).toBe(true);
    expect(screen.getByText(/资金费覆盖率：50.00%/)).toBeTruthy();
    expect(screen.getByText("左腿 资金费缺失：实际 1 / 预计 2 次结算，未补零。")).toBeTruthy();
    expect(screen.getByText("左腿资金费结算：实际 1 / 预计 2 次结算")).toBeTruthy();
  });

  it("未知覆盖率和点差成本保留未知状态", async () => {
    const response = rangeBacktestFixture();
    response.funding_pnl.coverage.leg1 = null;
    response.spread_cost_est = null;
    vi.mocked(fetch).mockResolvedValue(Response.json(response));
    render(<RangeBacktestPanel {...props} />);
    expect(await screen.findByText("未知（总收益未扣除点差成本）")).toBeTruthy();
    expect(screen.getByText(/资金费覆盖率：未知（未补零）/)).toBeTruthy();
    expect(screen.queryByText(/覆盖率不足/)).toBeNull();
  });

  it("请求进行中显示加载状态，请求失败后显示中文错误", async () => {
    let rejectRequest!: (error: Error) => void;
    vi.mocked(fetch).mockReturnValue(new Promise((_, reject) => { rejectRequest = reject; }));
    render(<RangeBacktestPanel {...props} />);
    expect(screen.getByRole("status").textContent).toContain("正在计算");
    await act(async () => rejectRequest(new TypeError("Failed to fetch")));
    const error = await screen.findByText("区间回测失败，请检查网络或稍后重试。");
    expect(error.closest(".ant-alert")?.classList.contains("ant-alert-error")).toBe(true);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("422显示区间、金额和缺少端点价格的提示", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json({ detail: [{ msg: "Value error", loc: ["body"] }] }, { status: 422 }));
    render(<RangeBacktestPanel {...props} />);
    expect(await screen.findByText(/区间或参数无法回测（422）/)).toBeTruthy();
    expect(screen.getByText(/跨度不超过30天/)).toBeTruthy();
    expect(screen.queryByText(/Value error/)).toBeNull();
  });

  it("名义金额修改后重算，图表刷新相同字段不重复请求", async () => {
    const view = render(<RangeBacktestPanel {...props} />);
    await screen.findByText("+56.9000 美元");
    fireEvent.change(screen.getByRole("spinbutton", { name: "每腿名义金额（美元）" }), { target: { value: "2000" } });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(requestBody(1).notional_usd).toBe(2000);
    await screen.findByText("+56.9000 美元");
    view.rerender(<RangeBacktestPanel {...props} leg1={{ ...backtestLeg1 }} range={{ ...backtestRange }} />);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("默认使用页面方向，切换方向后重新计算", async () => {
    render(<RangeBacktestPanel {...props} defaultDirection="short1_long2" />);
    await screen.findByText("+56.9000 美元");
    expect(requestBody().direction).toBe("short1_long2");
    await userEvent.click(screen.getByRole("combobox", { name: "方向" }));
    await userEvent.click(screen.getByText("做多左腿、做空右腿"));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(requestBody(1).direction).toBe("long1_short2");
  });

  it("无效金额不请求，较早的响应不能覆盖新结果", async () => {
    let resolveOld!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
    render(<RangeBacktestPanel {...props} />);
    const input = screen.getByRole("spinbutton", { name: "每腿名义金额（美元）" });
    fireEvent.change(input, { target: { value: "0" } });
    expect(screen.getByText(/请输入大于0/)).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect((vi.mocked(fetch).mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true);
    fireEvent.change(input, { target: { value: "2000" } });
    await screen.findByText("+56.9000 美元");
    const stale = rangeBacktestFixture();
    stale.total = -999;
    await act(async () => resolveOld(Response.json(stale)));
    expect(screen.queryByText("-999.0000 美元")).toBeNull();
    expect(screen.getByText("+56.9000 美元")).toBeTruthy();
  });
});
