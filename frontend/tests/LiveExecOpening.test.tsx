import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_REQUIRED_EVENT } from "../src/api/auth";
import type { LiveSummary } from "../src/api/liveExec";
import { AuthGate } from "../src/components/AuthGate";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { LiveOpeningControl } from "../src/pages/liveExec/LiveOpeningControl";
import { useLiveExec } from "../src/pages/liveExec/useLiveExec";
import { useLiveExecAdmin } from "../src/pages/liveExec/useLiveExecAdmin";
import { fixtures, freshAdminSnapshot } from "./liveExecAdminFixtures";
import { liveFixtures } from "./liveExecFixtures";

let summary: Record<string, unknown>;
let sessionStatus: number;
let sessions: unknown;
let writeResult: { status: number; body: unknown } | null;
let writeGate: Promise<void> | null;
let summaryGate: Promise<void> | null;
let writes: { path: string; options: RequestInit }[];
let reads: { path: string; options: RequestInit }[];
const response = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => { await act(async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); }); };
const dialog = () => within(screen.getByRole("dialog"));
const click = async (name: string) => { fireEvent.click(screen.getByRole("button", { name })); await flush(); };
const confirm = async (name: string) => { fireEvent.click(dialog().getByRole("button", { name })); await flush(); };
const summaryReads = () => reads.filter(read => read.path === "summary");

function Control() {
  const { summary: data, refreshSummary } = useLiveExec();
  const { hasAdminSession } = useLiveExecAdmin();
  return <LiveOpeningControl data={data} hasAdminSession={hasAdminSession} refreshSummary={refreshSummary} />;
}

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-16T00:00:00Z"));
  window.history.replaceState(null, "", "/");
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  summary = { ...structuredClone(liveFixtures.summary), opening_paused: false, stop_file: false,
    opening_paused_reason: null };
  sessionStatus = 200; sessions = [{ current: true }]; writeResult = null;
  writeGate = null; summaryGate = null; writes = []; reads = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, options: RequestInit = {}) => {
    const url = new URL(input);
    if (url.pathname === "/api/auth/sessions") return response(sessionStatus, sessions);
    const path = url.pathname.replace("/api/live-exec/", "");
    if (options.method && options.method !== "GET") {
      writes.push({ path, options });
      if (writeGate) await writeGate;
      if (writeResult) return response(writeResult.status, writeResult.body);
      const expected = JSON.parse(String(options.body)).expected_paused;
      if (expected !== (summary.opening_paused ?? summary.stop_file))
        return response(409, { error: "status_conflict", paused: summary.opening_paused });
      summary.opening_paused = path === "opening/pause";
      return response(200, { available: true, data: { paused: summary.opening_paused } });
    }
    reads.push({ path, options });
    const data = structuredClone(path === "remote-positions" ? freshAdminSnapshot() : path === "summary" ? summary
      : fixtures[path as keyof typeof fixtures] ?? liveFixtures[path as keyof typeof liveFixtures]);
    if (path === "summary" && summaryGate) await summaryGate;
    return response(200, { available: true, data });
  }));
});

afterEach(() => {
  cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/");
});

describe("开仓暂停与恢复", () => {
  it.each(["writesBlocked", "session"])("rechecks %s after the preflight wait before sending resume", async reason => {
    let release!: (value: LiveSummary) => void;
    const data = { ...liveFixtures.summary, opening_paused: true } as LiveSummary;
    const refreshSummary = vi.fn(() => new Promise<LiveSummary>(resolve => { release = resolve; }));
    const { rerender } = render(<LiveOpeningControl data={data} hasAdminSession refreshSummary={refreshSummary} />);
    await click("恢复开仓"); await confirm("确认恢复开仓");
    expect(refreshSummary).toHaveBeenCalledTimes(1);
    rerender(<LiveOpeningControl data={data} hasAdminSession={reason !== "session"}
      writesBlocked={reason === "writesBlocked"} refreshSummary={refreshSummary} />);
    await act(async () => { release(data); });
    expect(writes).toHaveLength(0);
    expect(dialog().getByText("数据或会话状态已变化，写操作已禁用，请刷新后重新确认")).toBeTruthy();
  });

  it("resumes after a delayed successful preflight on the full page", async () => {
    summary.opening_paused = true;
    render(<LiveExecPage />); await flush(); await click("恢复开仓");
    let release!: () => void;
    summaryGate = new Promise<void>(resolve => { release = resolve; });
    await confirm("确认恢复开仓");
    expect(writes).toHaveLength(0);
    summaryGate = null; release(); await flush();
    expect(writes.map(write => write.path)).toEqual(["opening/resume"]);
  });

  it("integrates the control into the live summary", async () => {
    render(<LiveExecPage />); await flush();
    const card = within(screen.getByText("执行汇总").closest<HTMLElement>(".ant-card")!);
    expect(card.getByText("开仓：运行中")).toBeTruthy();
    expect((card.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement).disabled).toBe(false);
    expect(card.getByText("实盘模式")).toBeTruthy();
  });

  it.each([false, true])("uses explicit paused=%s ahead of legacy state and reason", async paused => {
    summary.opening_paused = paused; summary.stop_file = !paused; summary.opening_paused_reason = "旧暂停原因";
    render(<Control />); await flush();
    expect(screen.getByText(paused ? "开仓：已暂停" : "开仓：运行中")).toBeTruthy();
    expect((screen.getByRole("button", { name: paused ? "恢复开仓" : "暂停开仓" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each([false, true])("falls back to stop_file=%s and sends that displayed state", async paused => {
    delete summary.opening_paused; summary.stop_file = paused;
    render(<Control />); await flush();
    const action = paused ? "恢复开仓" : "暂停开仓";
    expect(screen.getByText(paused ? "开仓：已暂停" : "开仓：运行中")).toBeTruthy();
    await click(action);
    expect(writes).toHaveLength(0);
    await confirm(`确认${action}`);
    expect(writes).toHaveLength(1);
    expect(writes[0].path).toBe(paused ? "opening/resume" : "opening/pause");
    expect(writes[0].options).toMatchObject({ method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Live-Exec-CSRF": "1" } });
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ expected_paused: paused });
    expect(Object.keys(writes[0].options.headers!)).not.toContain("X-Api-Token");
    expect(summaryReads()).toHaveLength(3);
    expect(screen.getByText(paused ? "开仓：运行中" : "开仓：已暂停")).toBeTruthy();
  });

  it("warns about real funds before live resume and cancellation never writes", async () => {
    summary.opening_paused = true;
    render(<Control />); await flush(); await click("恢复开仓");
    expect(dialog().getByText("恢复后程序会用真实资金自动开仓")).toBeTruthy();
    expect(dialog().getByText(/已有持仓继续管理和平仓/)).toBeTruthy();
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: /取\s*消/ })); await flush();
    expect(writes).toHaveLength(0);
    await click("恢复开仓"); await confirm("确认恢复开仓");
    expect(writes).toHaveLength(1);
  });

  it.each([false, true])("requires confirmation in shadow mode for paused=%s", async paused => {
    summary.mode = "shadow"; summary.opening_paused = paused;
    render(<Control />); await flush();
    expect(screen.getByText("影子模式")).toBeTruthy();
    await click(paused ? "恢复开仓" : "暂停开仓");
    expect(dialog().queryByText("恢复后程序会用真实资金自动开仓")).toBeNull();
    expect(writes).toHaveLength(0);
    await confirm(paused ? "确认恢复开仓" : "确认暂停开仓");
    expect(writes).toHaveLength(1);
  });

  it("refreshes summary after a post-check 409 without retrying or changing the original precondition", async () => {
    let release!: () => void;
    writeGate = new Promise<void>(resolve => { release = resolve; });
    render(<Control />); await flush(); await click("暂停开仓");
    await confirm("确认暂停开仓");
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ expected_paused: false });
    summary.opening_paused = true;
    release(); await flush();
    expect(summaryReads()).toHaveLength(3);
    expect(screen.getByText("状态已变化，已刷新")).toBeTruthy();
    expect(screen.getByText("开仓：已暂停")).toBeTruthy();
    await click("恢复开仓");
    expect(writes).toHaveLength(1);
    expect(dialog().getByText("恢复后程序会用真实资金自动开仓")).toBeTruthy();
  });

  it("waits for the fresh summary with controls disabled and ignores duplicate confirmations", async () => {
    render(<Control />); await flush(); await click("暂停开仓");
    let release!: () => void;
    summaryGate = new Promise<void>(resolve => { release = resolve; });
    const button = dialog().getByRole("button", { name: "确认暂停开仓" });
    fireEvent.click(button); fireEvent.click(button); await flush();
    expect(summaryReads()).toHaveLength(2);
    expect(writes).toHaveLength(0);
    expect((screen.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement).disabled).toBe(true);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect((dialog().getByRole("button", { name: /取\s*消/ }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(summaryReads()).toHaveLength(2);
    expect(writes).toHaveLength(0);
    summaryGate = null;
    release(); await flush();
    expect(writes).toHaveLength(1);
    expect(summaryReads()).toHaveLength(3);
    expect(screen.getByText("开仓：已暂停")).toBeTruthy();
  });

  it.each([
    { status: 503, body: {} },
    { status: 200, body: { available: false, error: "timeout" } },
    { status: 200, body: { available: true, data: null } },
  ])("does not write when the fresh summary is unavailable: %j", async ({ status, body }) => {
    render(<Control />); await flush(); await click("暂停开仓");
    vi.mocked(fetch).mockResolvedValueOnce(response(status, body) as Response);
    await confirm("确认暂停开仓");
    expect(writes).toHaveLength(0);
    expect(dialog().getByText("无法获取最新开仓状态，请刷新后重新确认")).toBeTruthy();
  });

  it("disables trigger, confirm and cancellation during a write and prevents duplicate clicks", async () => {
    let release!: () => void;
    writeGate = new Promise<void>(resolve => { release = resolve; });
    render(<Control />); await flush(); await click("暂停开仓");
    const button = dialog().getByRole("button", { name: "确认暂停开仓" });
    fireEvent.click(button); fireEvent.click(button); await flush();
    expect((screen.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement).disabled).toBe(true);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect((dialog().getByRole("button", { name: /取\s*消/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(writes).toHaveLength(1);
    release(); await flush();
    expect(screen.getByText("开仓：已暂停")).toBeTruthy();
  });

  it.each([
    [401, []], [403, []], [200, []], [200, [{ current: false }]], [200, { available: true }],
  ])("disables writes without a verified session (%s, %j)", async (status, body) => {
    sessionStatus = status as number; sessions = body;
    render(<Control />); await flush();
    const button = screen.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement;
    expect(screen.getByText("开仓：运行中")).toBeTruthy();
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull(); expect(writes).toHaveLength(0);
  });

  it("revokes confirmation when the shared authentication handler reports an expired session", async () => {
    render(<Control />); await flush(); await click("暂停开仓");
    act(() => window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT)));
    expect((dialog().getByRole("button", { name: "确认暂停开仓" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement).disabled).toBe(true);
    expect(writes).toHaveLength(0);
  });

  it.each([401, 403])("preserves read-only access when the admin session probe returns %s", async status => {
    sessionStatus = status;
    const path = "/?page=live-exec#summary";
    window.history.replaceState(null, "", path);
    const authRequired = vi.fn();
    window.addEventListener(AUTH_REQUIRED_EVENT, authRequired);
    try {
      render(<AuthGate><LiveExecPage /></AuthGate>); await flush();
      expect(window.location.pathname + window.location.search + window.location.hash).toBe(path);
      expect(authRequired).not.toHaveBeenCalled();
      expect(screen.getByText("执行汇总")).toBeTruthy();
      expect(screen.getByText("开仓：运行中")).toBeTruthy();
      expect((screen.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement).disabled).toBe(true);
      expect(reads.some(read => read.path === "config")).toBe(true);
      expect(writes).toHaveLength(0);
    } finally {
      window.removeEventListener(AUTH_REQUIRED_EVENT, authRequired);
    }
  });

  it.each([undefined, null, "false"])("disables unknown or malformed opening status %s", async value => {
    summary.opening_paused = value; summary.stop_file = null;
    render(<Control />); await flush();
    expect(screen.getByText("开仓：状态未知")).toBeTruthy();
    expect((screen.getByRole("button", { name: "暂停开仓" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables resume when the execution mode is unknown", async () => {
    summary.opening_paused = true; summary.mode = null;
    render(<Control />); await flush();
    expect((screen.getByRole("button", { name: "恢复开仓" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(["opening_paused", "mode"])("requires new confirmation after polling changes %s", async field => {
    summary.mode = "shadow"; summary.opening_paused = true;
    render(<Control />); await flush(); await click("恢复开仓");
    summary[field] = field === "mode" ? "live" : false;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await confirm("确认恢复开仓");
    expect(writes).toHaveLength(0);
    expect(screen.getByText("状态已变化，请重新确认")).toBeTruthy();
    if (field === "mode") {
      await click("恢复开仓");
      expect(dialog().getByText("恢复后程序会用真实资金自动开仓")).toBeTruthy();
    }
  });

  it.each(["opening_paused", "mode"])("rechecks upstream %s before writing even when no poll has run", async field => {
    summary.mode = "shadow"; summary.opening_paused = true;
    render(<Control />); await flush(); await click("恢复开仓");
    expect(dialog().queryByText("恢复后程序会用真实资金自动开仓")).toBeNull();
    summary[field] = field === "mode" ? "live" : false;
    expect(summaryReads()).toHaveLength(1);
    expect(screen.getByText("影子模式")).toBeTruthy();
    expect(screen.getByText("开仓：已暂停")).toBeTruthy();
    await confirm("确认恢复开仓");
    expect(writes).toHaveLength(0);
    expect(summaryReads()).toHaveLength(2);
    expect(screen.getByText("状态已变化，请重新确认")).toBeTruthy();
    expect(screen.getByRole("dialog").style.display).toBe("none");
    if (field === "mode") {
      expect(screen.getByText("实盘模式")).toBeTruthy();
      await click("恢复开仓");
      expect(dialog().getByText("恢复后程序会用真实资金自动开仓")).toBeTruthy();
      expect(writes).toHaveLength(0);
      await confirm("确认恢复开仓");
      expect(writes).toHaveLength(1);
      expect(writes[0].path).toBe("opening/resume");
    } else {
      expect(screen.getByText("开仓：运行中")).toBeTruthy();
    }
  });

  it.each([
    [503, "实盘管理接口未配置，无法写入", "实盘管理接口未配置，无法写入"],
    [502, "synthetic upstream error", "操作失败，请刷新核实操作结果"],
  ])("shows Chinese errors for HTTP %s without retrying", async (status, detail, message) => {
    writeResult = { status: status as number, body: { detail } };
    render(<Control />); await flush(); await click("暂停开仓"); await confirm("确认暂停开仓");
    expect(dialog().getByText(message as string)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(writes).toHaveLength(1);
  });

  it("supersedes an older pending summary poll when confirming a write", async () => {
    render(<Control />); await flush(); await click("暂停开仓");
    let release!: () => void;
    summaryGate = new Promise<void>(resolve => { release = resolve; });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    const oldRead = summaryReads()[1];
    summaryGate = null;
    await confirm("确认暂停开仓");
    expect(oldRead.options.signal?.aborted).toBe(true);
    expect(summaryReads()).toHaveLength(4);
    expect(screen.getByText("开仓：已暂停")).toBeTruthy();
    release(); await flush();
    expect(screen.getByText("开仓：已暂停")).toBeTruthy();
    expect(writes).toHaveLength(1);
  });
});
