import { act, cleanup, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_REQUIRED_EVENT } from "../src/api/auth";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { useLiveExec } from "../src/pages/liveExec/useLiveExec";
import { useLiveExecAdmin } from "../src/pages/liveExec/useLiveExecAdmin";
import { programRiskView } from "../src/pages/liveExec/programRiskView";
import { fixtures } from "./liveExecAdminFixtures";
import { liveFixtures } from "./liveExecFixtures";
import { remoteAt, remoteSnapshot, remoteRegistry } from "./liveExecRemoteFixtures";

const response = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => { await act(async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); }); };
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const section = (title: string) => within(screen.getByText(title).closest<HTMLElement>(".ant-card")!);
const disabled = (name: string) => (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;
let failed: Set<string>;
let blocked: Set<string>;
let session: number | "network" | "timeout" | "invalid" | "empty";
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(remoteAt));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  failed = new Set(); blocked = new Set(); session = 200;
  fetchMock = vi.fn(async (input: string) => {
    const path = new URL(input).pathname.replace("/api/live-exec/", "");
    if (path === "/api/auth/sessions") {
      if (session === "network" || session === "timeout") throw new Error(session);
      if (session === "invalid") return { ok: true, status: 200, json: async () => { throw new SyntaxError(); } };
      return response(typeof session === "number" ? session : 200, session === "empty" ? [] : [{ current: true }]);
    }
    if (blocked.has(path)) return new Promise(() => undefined);
    if (failed.has(path)) return response(200, { available: false, error: "timeout" });
    const data = path === "remote-positions" ? { ...remoteSnapshot, snapshot_at: new Date().toISOString(),
      positions: remoteSnapshot.positions.map(row => ({ ...row, snapshot_at: new Date().toISOString() })) }
      : path === "external-positions" ? remoteRegistry
      : fixtures[path as keyof typeof fixtures] ?? liveFixtures[path as keyof typeof liveFixtures];
    return response(200, { available: true, data: structuredClone(data) });
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("实盘轮询稳定性", () => {
  it.each([
    { available: false }, { stale: true },
    { snapshot_at: "2026-11-11T23:59:49Z" }, { snapshot_at: null },
  ])("blocks every write for an HTTP-successful invalid remote snapshot: %j", async invalid => {
    render(<LiveExecPage />); await flush();
    const names = [/恢复开仓/, /登记额度/, /提交参数修改/, /^排除 /, /^判断类别 /, /^确认映射 /, /^拒绝映射 /];
    const buttons = () => [...names.flatMap(name => screen.getAllByRole("button", { name })),
      ...screen.queryAllByRole("button", { name: /登记为外部持仓|^取消登记 / })];
    expect(screen.getAllByRole("button", { name: /登记为外部持仓|^取消登记 / }).length).toBeGreaterThan(0);
    expect(buttons().every(button => !(button as HTMLButtonElement).disabled)).toBe(true);
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: string) => input.endsWith("/remote-positions")
      ? Promise.resolve(response(200, { available: true, data: { ...remoteSnapshot, ...invalid } })) : original(input));
    await advance(5000);
    expect(buttons().every(button => (button as HTMLButtonElement).disabled)).toBe(true);
    fetchMock.mockImplementation(original); await advance(5000);
    expect(buttons().every(button => !(button as HTMLButtonElement).disabled)).toBe(true);
  });

  it("does not send resume when management becomes unhealthy during the summary preflight", async () => {
    render(<LiveExecPage />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "恢复开仓" })); await flush();
    const original = fetchMock.getMockImplementation()!;
    let release!: () => void;
    fetchMock.mockImplementation(async (input: string) => {
      if (input.endsWith("/summary")) await new Promise<void>(resolve => { release = resolve; });
      return original(input);
    });
    fireEvent.click(screen.getByRole("button", { name: "确认恢复开仓" })); await flush();
    failed.add("remote-positions"); await advance(5000);
    release(); await flush();
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/opening/resume"))).toBe(false);
    expect(screen.getByText("数据或会话状态已变化，写操作已禁用，请刷新后重新确认")).toBeTruthy();
  });

  it("expires remote evidence after ten seconds even while the next read is pending", async () => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    blocked.add("remote-positions");
    await advance(10000);
    expect(result.current.writesBlocked).toBe(false);
    await advance(1000);
    expect(result.current.health["remote-positions"].unsafe).toBe(true);
    expect(result.current.writesBlocked).toBe(true);
  });

  it("drains a full refresh queued behind a slow partial poll without downgrading it", async () => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    await advance(25000);
    const original = fetchMock.getMockImplementation()!;
    let release!: (value: unknown) => void;
    let delayed = false;
    fetchMock.mockImplementation((input: string) => {
      if (input.endsWith("/external-positions") && !delayed) {
        delayed = true; return new Promise(resolve => { release = resolve; });
      }
      return original(input);
    });
    act(() => { void result.current.refresh(false, ["external-positions", "remote-positions", "external-liabilities"]); });
    await flush();
    await advance(6000); // the full 30-second poll was requested while busy
    await act(async () => { await result.current.refresh(false, ["remote-positions"]); });
    expect(result.current.health.config.stale).toBe(true);
    await act(async () => { release(response(200, { available: true, data: remoteRegistry })); });
    await flush();
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/config"))).toHaveLength(2);
    expect(result.current.health.config).toMatchObject({ stale: false, unsafe: false, ageSeconds: 0 });
    expect(result.current.writesBlocked).toBe(false);
    await advance(1000);
    expect(result.current.writesBlocked).toBe(false);
  });

  it("publishes all related observations together and still rejects mismatched proof versions", async () => {
    const original = fetchMock.getMockImplementation()!;
    let version = 17;
    let releaseRemote!: () => void;
    let releaseLiabilities!: () => void;
    let delay = false;
    fetchMock.mockImplementation(async (input: string) => {
      if (input.endsWith("/external-positions")) return response(200, { available: true, data: { ...remoteRegistry,
        income: { verified: true, scope: "program", day: remoteAt.slice(0, 10), captured_at: Date.now() / 1000,
          snapshot_version: version, revision: 0, equity_revision: 0, program_journal: "test", program_total: String(version) } } });
      if (input.endsWith("/remote-positions")) {
        if (delay) await new Promise<void>(resolve => { releaseRemote = resolve; });
        return response(200, { available: true, data: { ...remoteSnapshot, snapshot_version: version } });
      }
      if (input.endsWith("/external-liabilities")) {
        if (delay) await new Promise<void>(resolve => { releaseLiabilities = resolve; });
        return response(200, { available: true, data: { revision: version, items: [] } });
      }
      return original(input);
    });
    const observed: unknown[] = [];
    const { result } = renderHook(() => {
      const admin = useLiveExecAdmin();
      const total = programRiskView(admin.data["external-positions"]?.income,
        admin.data["remote-positions"], null, Date.now()).total;
      observed.push(total);
      return { ...admin, total };
    });
    await flush(); expect(result.current.total).toBe("17");
    observed.length = 0;
    const previous = result.current.data;
    version = 18; delay = true;
    act(() => { void result.current.refresh(); }); await flush();
    expect(result.current.data).toBe(previous);
    releaseRemote(); await flush();
    expect(result.current.data).toBe(previous);
    releaseLiabilities(); await flush();
    expect(result.current.total).toBe("18");
    expect(result.current.data["external-liabilities"]?.revision).toBe(18);
    expect(observed.every(total => total === "17" || total === "18")).toBe(true);
    delay = false; version = 19; failed.add("external-positions");
    fetchMock.mockImplementation((input: string) => input.endsWith("/remote-positions")
      ? Promise.resolve(response(200, { available: true, data: { ...remoteSnapshot, snapshot_version: 19 } })) : original(input));
    await act(async () => { await result.current.refresh(); });
    expect(result.current.total).toBeNull();
    expect(result.current.writesBlocked).toBe(true);
  });

  it("discards a staged batch superseded by a forced refresh, including queued reads", async () => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    const original = fetchMock.getMockImplementation()!;
    let release!: (value: unknown) => void;
    let oldSignal: AbortSignal | undefined;
    fetchMock.mockImplementation((input: string, options: RequestInit) => {
      if (input.endsWith("/remote-positions") && !oldSignal) {
        oldSignal = options.signal as AbortSignal;
        return new Promise(resolve => { release = resolve; });
      }
      return original(input);
    });
    act(() => { void result.current.refresh(); }); await flush();
    await act(async () => { await result.current.refresh(); });
    await act(async () => { await result.current.refresh(true); });
    const current = result.current.data;
    expect(oldSignal?.aborted).toBe(true);
    const reads = fetchMock.mock.calls.length;
    release(response(200, { available: true, data: { ...remoteSnapshot, snapshot_version: 99 } }));
    await flush();
    expect(result.current.data).toBe(current);
    expect(fetchMock.mock.calls).toHaveLength(reads);
    expect(result.current.writesBlocked).toBe(false);
  });

  it.each(["summary", "positions", "trades", "stats"] as const)("retains all public data when %s fails, then recovers", async key => {
    const { result } = renderHook(useLiveExec); await flush();
    const previous = { summary: result.current.summary, positions: result.current.positions,
      trades: result.current.trades, stats: result.current.stats };
    failed.add(key);
    const interval = key === "trades" || key === "stats" ? 30000 : 5000;
    await advance(interval);
    expect(result.current[key]).toEqual(previous[key]);
    expect(result.current.health[key]).toMatchObject({ failures: 1, stale: false, unsafe: true });
    expect(result.current.error).toBeNull();
    expect(result.current.writesBlocked).toBe(true);
    for (const other of ["summary", "positions", "trades", "stats"] as const)
      expect(result.current[other]).toEqual(previous[other]);
    await advance(interval);
    expect(result.current.health[key]).toMatchObject({ failures: 2, stale: true });
    expect(result.current.error).toBe("timeout");
    failed.delete(key); await advance(interval);
    expect(result.current.health[key]).toMatchObject({ failures: 0, stale: false, unsafe: false });
    expect(result.current.error).toBeNull();
    expect(result.current.writesBlocked).toBe(false);
  });

  it.each(["config", "universe", "mappings", "config/audit", "external-positions", "remote-positions", "external-liabilities"] as const)(
    "retains %s after ordinary failures and resets its health on recovery", async key => {
      const { result } = renderHook(useLiveExecAdmin); await flush();
      const previous = result.current.data[key];
      failed.add(key);
      await act(async () => { await result.current.refresh(); });
      expect(result.current.data[key]).toEqual(previous);
      expect(result.current.health[key]).toMatchObject({ failures: 1, stale: false, unsafe: true });
      expect(result.current.writesBlocked).toBe(true);
      await act(async () => { await result.current.refresh(); });
      expect(result.current.health[key]).toMatchObject({ failures: 2, stale: true });
      failed.delete(key);
      await act(async () => { await result.current.refresh(); });
      expect(result.current.health[key]).toMatchObject({ failures: 0, stale: false, unsafe: false });
      expect(result.current.writesBlocked).toBe(false);
    });

  it.each(["network", "timeout", "invalid", "empty", 500] as const)("keeps the last verified session on %s but blocks writes", async failure => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    expect(result.current.hasAdminSession).toBe(true);
    session = failure; await advance(5000);
    expect(result.current.hasAdminSession).toBe(true);
    expect(result.current.writesBlocked).toBe(true);
    session = 200; await advance(5000);
    expect(result.current.hasAdminSession).toBe(true);
    expect(result.current.writesBlocked).toBe(false);
  });

  it.each([401, 403, "event"] as const)("revokes the verified session on %s", async failure => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    if (failure === "event") act(() => { window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT)); });
    else { session = failure; await advance(5000); }
    expect(result.current.hasAdminSession).toBe(false);
    expect(result.current.writesBlocked).toBe(true);
  });

  it("preserves the verified session while hidden and rejects aborted management responses", async () => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    const previous = result.current.data;
    const original = fetchMock.getMockImplementation()!;
    let resolveOld: (value: unknown) => void = () => undefined;
    let signal: AbortSignal | undefined;
    fetchMock.mockImplementation((input: string, options: RequestInit) => {
      if (input.endsWith("/config") && !signal) {
        signal = options.signal as AbortSignal;
        return new Promise(resolve => { resolveOld = resolve; });
      }
      return original(input);
    });
    act(() => { void result.current.refresh(); }); await flush();
    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      fireEvent(document, new Event("visibilitychange"));
    });
    expect(signal?.aborted).toBe(true);
    expect(result.current.hasAdminSession).toBe(true);
    expect(result.current.writesBlocked).toBe(true);
    act(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      fireEvent(document, new Event("visibilitychange"));
    });
    await flush();
    await act(async () => { resolveOld(response(200, { available: false, error: "timeout" })); });
    expect(result.current.data).toEqual(previous);
    expect(result.current.health.config.failures).toBe(0);
    expect(result.current.writesBlocked).toBe(false);
  });

  it("ages management data while its serial read is stalled without clearing its last result", async () => {
    const { result } = renderHook(useLiveExecAdmin); await flush();
    const previous = result.current.data.config;
    blocked.add("config");
    act(() => { void result.current.refresh(); }); await flush();
    await advance(31000);
    expect(result.current.data.config).toEqual(previous);
    expect(result.current.health.config).toMatchObject({ stale: true, unsafe: true, ageSeconds: 31, failures: 0 });
    expect(result.current.writesBlocked).toBe(true);
  });

  it("clears forced summary and admin refreshes immediately, and does not restore old data after failure", async () => {
    const { result } = renderHook(() => ({ live: useLiveExec(), admin: useLiveExecAdmin() })); await flush();
    blocked.add("summary"); blocked.add("config");
    act(() => { void result.current.live.refreshSummary(); void result.current.admin.refresh(true); });
    await flush();
    expect(result.current.live.summary).toBeNull();
    for (const value of Object.values(result.current.admin.data)) expect(value).toBeNull();
    expect(result.current.live.writesBlocked).toBe(true);
    expect(result.current.admin.writesBlocked).toBe(true);
    blocked.clear(); failed.add("summary"); failed.add("external-liabilities");
    await act(async () => { await result.current.live.refreshSummary(); await result.current.admin.refresh(true); });
    expect(result.current.live.summary).toBeNull();
    expect(result.current.admin.data["external-liabilities"]).toBeNull();
    expect(result.current.admin.data.config).not.toBeNull();
  });

  it("shows per-section stale badges after two failures, blocks every write from the first and removes badges on recovery", async () => {
    render(<LiveExecPage />); await flush();
    const writeNames = [/恢复开仓/, /登记为外部持仓/, /^取消登记 /, /登记额度/, /提交参数修改/, /^排除 /, /^判断类别 /, /^确认映射 /, /^拒绝映射 /];
    const buttons = () => writeNames.flatMap(name => screen.getAllByRole("button", { name }));
    expect(buttons().every(button => !(button as HTMLButtonElement).disabled)).toBe(true);
    failed.add("summary"); failed.add("external-liabilities");
    await advance(5000);
    expect(screen.queryByText(/实盘服务未连接/)).toBeNull();
    expect(screen.queryByText(/数据已过期/)).toBeNull();
    expect(buttons().every(button => (button as HTMLButtonElement).disabled)).toBe(true);
    expect(section("执行汇总").getByText(/最近错误示例/)).toBeTruthy();
    await advance(5000);
    expect(section("执行汇总").getByText("数据已过期 10 秒")).toBeTruthy();
    expect(section("外部负债额度").getByText("数据已过期 10 秒")).toBeTruthy();
    expect(screen.getByText(/实盘服务未连接/).closest(".live-notice-slot")).toBeTruthy();
    failed.clear(); await advance(5000);
    expect(screen.queryByText(/数据已过期/)).toBeNull();
    expect(screen.queryByText(/实盘服务未连接/)).toBeNull();
    expect(buttons().every(button => !(button as HTMLButtonElement).disabled)).toBe(true);
  });

  it("ages stalled reads past 30 seconds, disables an already-open confirmation, and preserves table layout", async () => {
    const { container } = render(<LiveExecPage />); await flush();
    fireEvent.click(screen.getByRole("button", { name: "恢复开仓" })); await flush();
    expect(disabled("确认恢复开仓")).toBe(false);
    const tables = [...container.querySelectorAll<HTMLTableElement>(".ant-table table")];
    expect(tables.length).toBeGreaterThan(0);
    expect(tables.every(table => table.style.tableLayout === "fixed")).toBe(true);
    const held = section("当前持仓").getByRole("columnheader", { name: "已持有时长" });
    const cols = [...held.closest("table")!.querySelectorAll("col")].map(col => col.style.width);
    expect(cols).toContain("180px");
    blocked.add("summary");
    await advance(30000);
    expect(section("执行汇总").queryByText(/数据已过期/)).toBeNull();
    await advance(1000);
    expect(section("执行汇总").getByText("数据已过期 31 秒")).toBeTruthy();
    expect(disabled("确认恢复开仓")).toBe(true);
    expect(screen.queryByText(/实盘服务未连接/)).toBeNull();
    expect([...held.closest("table")!.querySelectorAll("col")].map(col => col.style.width)).toEqual(cols);
    expect(section("执行汇总").getByText(/最近错误示例/)).toBeTruthy();
  });

  it("blocks an open management confirmation immediately on a polling error", async () => {
    render(<LiveExecPage />); await flush();
    fireEvent.click(screen.getByRole("button", { name: /^排除 / })); await flush();
    expect(disabled("确认提交")).toBe(false);
    failed.add("positions"); await advance(5000);
    expect(disabled("确认提交")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "确认提交" })); await flush();
    expect(fetchMock.mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });
});
