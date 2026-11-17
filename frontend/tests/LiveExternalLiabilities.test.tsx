import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AUTH_REQUIRED_EVENT } from "../src/api/auth";
import type { LiveExternalLiabilities as Registry, LiveSummary } from "../src/api/liveExec";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { LiveExternalLiabilities } from "../src/pages/liveExec/LiveExternalLiabilities";
import { useLiveExecAdmin } from "../src/pages/liveExec/useLiveExecAdmin";
import { fixtures, freshAdminSnapshot } from "./liveExecAdminFixtures";
import { liveFixtures } from "./liveExecFixtures";

let registry: Registry;
let summary: LiveSummary;
let sessionStatus: number;
let sessions: unknown;
let writeResult: { status: number; body: unknown } | null;
let readResult: { status: number; body: unknown } | null;
let writeGate: Promise<void> | null;
let readGate: Promise<void> | null;
let writes: { path: string; options: RequestInit }[];
let reads: string[];
const response = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });
const flush = async () => { await act(async () => { for (let i = 0; i < 50; i++) await Promise.resolve(); }); };
const dialog = () => within(screen.getByRole("dialog"));
const click = async (name: string) => { fireEvent.click(screen.getByRole("button", { name })); await flush(); };
const confirm = async () => { fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush(); };
const fill = (cap = "28.500000000000000001", reason = "人工确认负债") => {
  fireEvent.change(screen.getByLabelText("负债额度"), { target: { value: cap } });
  fireEvent.change(screen.getByLabelText("负债额度备注"), { target: { value: reason } });
};
const liabilityReads = () => reads.filter(path => path === "external-liabilities").length;
function Panel() {
  const management = useLiveExecAdmin();
  return <LiveExternalLiabilities registry={management.data["external-liabilities"]} summary={summary}
    error={management.errors["external-liabilities"]} hasAdminSession={management.hasAdminSession}
    refresh={() => management.refresh(true)} />;
}

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-17T00:00:00Z"));
  window.history.replaceState(null, "", "/");
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  registry = { revision: 0, items: [
    { exchange_type: "HYPERLIQUID", coin: "USDC", cap: "50", reason: "确认第一项",
      registered_at: 1794873600, liability: "28.500000000000000001", within_cap: true },
    { exchange_type: "BINANCE", coin: "USDT", cap: "10", reason: "确认第二项",
      registered_at: "2026-11-17T00:00:00Z", liability: "11", within_cap: false },
    { exchange_type: "BITGET", coin: "USDT", cap: "5", reason: "确认第三项",
      registered_at: 1794873600, liability: null, within_cap: null },
  ] };
  summary = { ...structuredClone(liveFixtures.summary), liabilities: [
    { exchange_type: "BINANCE", coin: "USDT", liability: 0 },
    { exchange_type: "HYPERLIQUID", coin: "USDC", liability: "28.500000000000000001" },
  ] } as LiveSummary;
  sessionStatus = 200; sessions = [{ current: true }]; writeResult = null; readResult = null;
  writeGate = null; readGate = null; writes = []; reads = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, options: RequestInit = {}) => {
    const url = new URL(input);
    if (url.pathname === "/api/auth/sessions") return response(sessionStatus, sessions);
    const path = url.pathname.replace("/api/live-exec/", "");
    if (options.method && options.method !== "GET") {
      writes.push({ path, options });
      if (writeGate) await writeGate;
      if (writeResult) return response(writeResult.status, writeResult.body);
      const body = JSON.parse(String(options.body));
      if (body.expected_revision !== registry.revision) return response(409, { error: "revision_conflict", revision: registry.revision });
      registry.revision++;
      registry.items = registry.items.filter(row => row.exchange_type !== body.exchange_type || row.coin !== body.coin);
      if (path.endsWith("/register")) registry.items.push({ exchange_type: body.exchange_type, coin: body.coin,
        cap: body.cap, reason: body.reason, registered_at: 1794873600, liability: null, within_cap: null });
      return response(200, { available: true, data: {} });
    }
    reads.push(path);
    if (path === "external-liabilities") {
      if (readGate) await readGate;
      if (readResult) return response(readResult.status, readResult.body);
      return response(200, { available: true, data: structuredClone(registry) });
    }
    const data = path === "remote-positions" ? freshAdminSnapshot() : path === "summary" ? summary
      : fixtures[path as keyof typeof fixtures] ?? liveFixtures[path as keyof typeof liveFixtures];
    return response(200, { available: true, data: structuredClone(data) });
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("外部负债额度", () => {
  it("mounts next to external positions and defaults to the exchange with positive snapshot liability", async () => {
    render(<LiveExecPage />); await flush();
    const external = screen.getByText("外部持仓").closest(".ant-card")!;
    const allowance = screen.getByText("外部负债额度").closest(".ant-card")!;
    expect(external.compareDocumentPosition(allowance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "负债交易所" }).closest(".ant-select")?.textContent).toContain("HYPERLIQUID");
    expect((screen.getByLabelText("负债币种") as HTMLInputElement).value).toBe("USDC");
    expect(writes).toHaveLength(0);
  });

  it("renders all statuses, native precision, notes, timestamps and unknown liability without zero substitution", async () => {
    render(<Panel />); await flush();
    for (const title of ["交易所", "币种", "当前负债", "登记额度", "状态", "备注", "登记时间", "操作"])
      expect(screen.getByRole("columnheader", { name: title })).toBeTruthy();
    expect(screen.getByText("额度内")).toBeTruthy(); expect(screen.getByText("超额度")).toBeTruthy();
    const unknown = within(screen.getByText("确认第三项").closest("tr")!);
    expect(unknown.getByText("未知")).toBeTruthy(); expect(unknown.getByText("—")).toBeTruthy();
    expect(screen.getByText("28.500000000000000001")).toBeTruthy();
    expect(screen.getAllByText(/2026\/11\/17/)).toHaveLength(3);
  });

  it.each([
    { liability: null, within_cap: true },
    { liability: null, within_cap: false },
    { cap: null, within_cap: true },
    { liability: "50.000000000000000001", within_cap: true },
    { liability: "49.999999999999999999", within_cap: false },
    { liability: "28.5", within_cap: null },
  ])("renders incomplete or contradictory observations as unknown without green: %j", async observation => {
    readResult = { status: 200, body: { available: true, data: { ...registry,
      items: [{ ...registry.items[0], ...observation }] } } };
    render(<Panel />); await flush();
    const row = screen.getByText("确认第一项").closest("tr")!;
    expect(within(row).getByText("未知")).toBeTruthy();
    expect(within(row).queryByText("额度内")).toBeNull();
    expect(within(row).queryByText("超额度")).toBeNull();
    expect(row.querySelector(".ant-tag-green")).toBeNull();
  });

  it.each([
    ["负债币种", "USDT"],
    ["负债额度", "50"],
    ["负债额度备注", "保留草稿"],
  ])("keeps the exchange and draft fixed after editing %s across polling and submission", async (label, value) => {
    render(<Panel />); await flush();
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
    summary.liabilities = [{ exchange_type: "BINANCE", coin: "USDT", liability: 30 }];
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(liabilityReads()).toBe(2);
    expect(screen.getByRole("combobox", { name: "负债交易所" }).closest(".ant-select")?.textContent).toContain("HYPERLIQUID");
    expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe(value);
    fill(label === "负债额度" ? value : "50", label === "负债额度备注" ? value : "人工确认负债");
    await click("登记额度");
    expect(dialog().getByText("HYPERLIQUID")).toBeTruthy();
    await confirm();
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ exchange_type: "HYPERLIQUID",
      coin: label === "负债币种" ? value : "USDC", cap: "50",
      reason: label === "负债额度备注" ? value : "人工确认负债", expected_revision: 0 });
  });

  it("requires an explicit exchange change if polling removes the draft exchange", async () => {
    render(<Panel />); await flush(); fill();
    registry.items = registry.items.filter(row => row.exchange_type !== "HYPERLIQUID");
    summary.liabilities = [{ exchange_type: "BINANCE", coin: "USDT", liability: 30 }];
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByRole("combobox", { name: "负债交易所" }).closest(".ant-select")?.textContent).toContain("HYPERLIQUID");
    await click("登记额度");
    expect(screen.getByText("请选择快照或登记记录中的交易所")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull(); expect(writes).toHaveLength(0);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "负债交易所" })); await flush();
    fireEvent.click(document.querySelector('.ant-select-item-option[title="BINANCE"]')!); await flush();
    await click("登记额度"); await confirm();
    expect(JSON.parse(String(writes[0].options.body)).exchange_type).toBe("BINANCE");
  });

  it.each(["", "0", "-1", "1001", "1000.000000000000000001", "NaN", "Infinity", "1_0", "0x10", "1e99999"])
    ("rejects invalid cap %s before confirmation or network write", async value => {
      render(<Panel />); await flush(); fill(value); await click("登记额度");
      expect(screen.getByText("额度须大于 0 且不超过 1000，按原币数量填写")).toBeTruthy();
      expect(screen.queryByRole("dialog")).toBeNull(); expect(writes).toHaveLength(0);
    });

  it.each(["", "   ", "a".repeat(201), "note\u0001"])("rejects invalid mandatory note %s", async value => {
    render(<Panel />); await flush(); fill("50", value); await click("登记额度");
    expect(screen.getByText("备注须为 1 至 200 字符且不含控制字符")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull(); expect(writes).toHaveLength(0);
  });

  it("does not assign an exchange to an existing draft when a later snapshot adds one", async () => {
    registry.items = []; summary.liabilities = [];
    render(<Panel />); await flush(); fill(); await click("登记额度");
    expect(screen.getByText("请选择快照或登记记录中的交易所")).toBeTruthy();
    summary.liabilities = [{ exchange_type: "HYPERLIQUID", coin: "USDC", liability: 20 }];
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await click("登记额度");
    expect(screen.getByText("请选择快照或登记记录中的交易所")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull(); expect(writes).toHaveLength(0);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "负债交易所" })); await flush();
    fireEvent.click(document.querySelector('.ant-select-item-option[title="HYPERLIQUID"]')!); await flush();
    fireEvent.change(screen.getByLabelText("负债币种"), { target: { value: "USDC/../../config" } });
    await click("登记额度");
    expect(screen.getByText(/请输入有效币种标识/)).toBeTruthy(); expect(writes).toHaveLength(0);
  });

  it.each(["28.500000000000000001", "1000", "0.000000000000000001", "1e3"])
    ("requires explicit consent and sends native cap %s plus the displayed revision", async value => {
      render(<Panel />); await flush(); fill(value); await click("登记额度");
      expect(dialog().getByText("登记后，该负债在额度内不会再阻止程序开仓；负债由你自行负责，超出额度程序仍会停开。")).toBeTruthy();
      expect(dialog().getByText(`${value} USDC`)).toBeTruthy(); expect(writes).toHaveLength(0);
      fireEvent.click(dialog().getByRole("button", { name: /取\s*消/ })); await flush();
      expect(writes).toHaveLength(0);
      await click("登记额度"); await confirm();
      expect(writes).toHaveLength(1);
      expect(writes[0].path).toBe("external-liabilities/register");
      expect(JSON.parse(String(writes[0].options.body))).toEqual({ exchange_type: "HYPERLIQUID", coin: "USDC",
        cap: value, reason: "人工确认负债", expected_revision: 0 });
      expect(liabilityReads()).toBe(2); expect(screen.getByText("额度已登记，已刷新")).toBeTruthy();
    });

  it("requires separate unregister confirmation and omits registration-only fields", async () => {
    render(<Panel />); await flush(); await click("注销额度 HYPERLIQUID USDC");
    expect(dialog().getByText(/注销后，该交易所和币种的负债不再享有此额度/)).toBeTruthy();
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: /取\s*消/ })); await flush();
    expect(writes).toHaveLength(0);
    await click("注销额度 HYPERLIQUID USDC"); await confirm();
    expect(writes).toHaveLength(1); expect(writes[0].path).toBe("external-liabilities/unregister");
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ exchange_type: "HYPERLIQUID", coin: "USDC", expected_revision: 0 });
    expect(screen.queryByRole("button", { name: "注销额度 HYPERLIQUID USDC" })).toBeNull();
    expect(screen.getByText("额度已注销，已刷新")).toBeTruthy();
  });

  it.each(["register", "unregister"])("refreshes a %s conflict, preserves original consent and never retries", async action => {
    render(<Panel />); await flush(); fill();
    await click(action === "register" ? "登记额度" : "注销额度 HYPERLIQUID USDC");
    registry.revision = 9; registry.items[0].cap = "60";
    await confirm();
    expect(writes).toHaveLength(1); expect(JSON.parse(String(writes[0].options.body)).expected_revision).toBe(0);
    expect(liabilityReads()).toBe(2); expect(screen.getByText("已被修改，已刷新")).toBeTruthy();
    expect(screen.getByText("60")).toBeTruthy();
    await click("登记额度"); await confirm();
    expect(JSON.parse(String(writes[1].options.body)).expected_revision).toBe(9);
  });

  it("does not report a completed refresh after a conflicting write when the read fails", async () => {
    render(<Panel />); await flush(); fill(); await click("登记额度"); registry.revision++;
    readResult = { status: 200, body: { available: false, error: "timeout" } };
    await confirm();
    expect(screen.getByText("已被修改，刷新失败，请稍后重试")).toBeTruthy();
    expect(screen.queryByText("已被修改，已刷新")).toBeNull();
    expect((screen.getByRole("button", { name: "登记额度" }) as HTMLButtonElement).disabled).toBe(true);
    expect(writes).toHaveLength(1);
  });

  it("disables controls throughout submission and refresh and rejects duplicate confirmations", async () => {
    let releaseWrite!: () => void; let releaseRead!: () => void;
    writeGate = new Promise<void>(resolve => { releaseWrite = resolve; });
    render(<Panel />); await flush(); fill(); await click("登记额度");
    const button = dialog().getByRole("button", { name: "确认提交" });
    fireEvent.click(button); fireEvent.click(button); await flush();
    expect(writes).toHaveLength(1);
    for (const control of screen.getAllByRole("button").filter(element => !element.classList.contains("ant-select-clear")))
      expect((control as HTMLButtonElement).disabled).toBe(true);
    readGate = new Promise<void>(resolve => { releaseRead = resolve; });
    releaseWrite(); await flush();
    expect((screen.getByRole("button", { name: "登记额度" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "刷新额度" }) as HTMLButtonElement).disabled).toBe(true);
    releaseRead(); await flush();
    expect((screen.getByRole("button", { name: "登记额度" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each([[401, []], [403, []], [200, []], [200, [{ current: false }]]])
    ("disables writes with unverified admin session %s without login navigation", async (status, body) => {
      sessionStatus = status as number; sessions = body;
      window.history.replaceState(null, "", "/?page=live-exec#liabilities");
      const expired = vi.fn(); window.addEventListener(AUTH_REQUIRED_EVENT, expired);
      try {
        render(<Panel />); await flush();
        for (const button of screen.getAllByRole("button", { name: /登记额度|注销额度/ })) {
          expect((button as HTMLButtonElement).disabled).toBe(true); fireEvent.click(button);
        }
        expect(screen.getByText("额度内")).toBeTruthy(); expect(writes).toHaveLength(0);
        expect(screen.queryByRole("dialog")).toBeNull(); expect(expired).not.toHaveBeenCalled();
        expect(window.location.pathname + window.location.search + window.location.hash).toBe("/?page=live-exec#liabilities");
      } finally { window.removeEventListener(AUTH_REQUIRED_EVENT, expired); }
    });

  it("revokes an open confirmation when the existing auth handler reports session expiry", async () => {
    render(<Panel />); await flush(); fill(); await click("登记额度");
    act(() => window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT)));
    expect((dialog().getByRole("button", { name: "确认提交" }) as HTMLButtonElement).disabled).toBe(true);
    await confirm(); expect(writes).toHaveLength(0);
  });

  it("handles session expiry during a write locally and disables subsequent writes", async () => {
    render(<Panel />); await flush(); fill(); await click("登记额度");
    writeResult = { status: 401, body: {} }; await confirm();
    expect(screen.getByText("管理员会话已失效，请重新登录后操作")).toBeTruthy();
    expect(window.location.pathname).toBe("/");
    expect((screen.getByRole("button", { name: "登记额度" }) as HTMLButtonElement).disabled).toBe(true);
    expect(writes).toHaveLength(1);
  });

  it("shows upstream errors in Chinese and does not automatically retry writes", async () => {
    render(<Panel />); await flush(); fill(); await click("登记额度");
    writeResult = { status: 503, body: { detail: "实盘管理接口未配置，无法写入" } }; await confirm();
    expect(dialog().getByText("实盘管理接口未配置，无法写入")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(writes).toHaveLength(1);
  });

  it("polls current liability status and distinguishes unreadable registries from an empty list", async () => {
    render(<Panel />); await flush();
    registry.items[0].within_cap = false; registry.items[0].liability = "55";
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getAllByText("超额度")).toHaveLength(2);
    readResult = { status: 200, body: { available: true, data: null } };
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText(/负债额度读取失败/)).toBeTruthy();
    expect(screen.getAllByText("超额度")).toHaveLength(2);
    expect(screen.queryByText("暂无负债额度登记")).toBeNull();
    expect((screen.getByRole("button", { name: "登记额度" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
