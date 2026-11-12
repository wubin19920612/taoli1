import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { fixtures } from "./liveExecAdminFixtures";
import { liveFixtures } from "./liveExecFixtures";
import { remoteAt, remoteIdentity, remotePosition, remoteRegistry, remoteSnapshot } from "./liveExecRemoteFixtures";

let remote: Record<string, unknown>;
let registry: typeof remoteRegistry;
let remoteError: string | null;
let fetchMock: ReturnType<typeof vi.fn>;
let writes: { path: string; body: unknown; options: RequestInit }[];
const flush = async () => { await act(async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); }); };
const card = () => screen.getByText("外部持仓").closest<HTMLElement>(".ant-card")!;
const ui = () => within(card());
const dialog = () => within(screen.getByRole("dialog"));
const readCount = (path: string) => fetchMock.mock.calls.filter(([url, options]) => url.endsWith(`/${path}`) && !options.method).length;
const reads = ["config", "universe", "mappings", "config/audit", "external-positions", "remote-positions"];
const noEnabledActions = () => expect(ui().queryAllByRole("button", { name: /登记为外部持仓|取消登记/ })
  .filter(button => !(button as HTMLButtonElement).disabled)).toHaveLength(0);
const open = async (action = "登记为外部持仓") => {
  fireEvent.click(ui().getByRole("button", { name: new RegExp(action) })); await flush();
};
const confirm = async () => { fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush(); };
const setReason = () => fireEvent.change(dialog().getByLabelText("操作说明"), { target: { value: "核对原始身份后操作" } });
function visibility(value: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
  fireEvent(document, new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(remoteAt)); visibility("visible");
  remote = structuredClone(remoteSnapshot); registry = structuredClone(remoteRegistry); remoteError = null; writes = [];
  fetchMock = vi.fn(async (input: string, options: RequestInit = {}) => {
    const path = new URL(input).pathname.replace("/api/live-exec/", "");
    if (options.method) {
      writes.push({ path, body: JSON.parse(String(options.body)), options });
      return { ok: true, status: 200, json: async () => ({ available: true, data: {} }) };
    }
    const data = path === "remote-positions" ? remote : path === "external-positions" ? registry
      : fixtures[path as keyof typeof fixtures] ?? liveFixtures[path as keyof typeof liveFixtures];
    const result = path === "remote-positions" && remoteError ? { available: false, error: remoteError }
      : { available: true, data: structuredClone(data) };
    return { ok: true, status: 200, json: async () => result };
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("第十三轮完整远端持仓接入", () => {
  it("renders every remote position, exact DEX, ownership and decimal units from the real API shape", async () => {
    const xyz = registry.contracts[0].identity;
    remote.positions = [remotePosition,
      { ...remotePosition, ...xyz, position_key: `crossex|${xyz.symbol}|short`, side: "short", ownership: "external_registered" },
      { ...remotePosition, symbol: "BINANCE_FUTURE_BTC_USDT", dex: "", quote: "USDT", exchange_type: "BINANCE",
        position_key: "crossex|BINANCE_FUTURE_BTC_USDT|long", ownership: "program" },
      { ...remotePosition, route: "bitget", exchange_type: "BITGET", business: "USDT-FUTURES", symbol: "BTCUSDT",
        quote: "USDT", dex: "", position_key: "bitget|BTCUSDT|long", ownership: "unregistered" },
      { ...remotePosition, symbol: "UNPARSEABLE_RAW", quote: null, dex: null, business: null, exchange_type: null,
        position_key: "crossex|UNPARSEABLE_RAW|long", identity_verified: false, unrealized_pnl_usd: null, valuation_verified: false },
    ];
    render(<LiveExecPage />); await flush();
    const tableRows = card().querySelectorAll(".ant-table-wrapper:first-of-type tbody tr[data-row-key]");
    // Scope to the remote table, not the registry table.
    const row = ui().getByText(remoteIdentity.symbol).closest("tr")!;
    expect(row.textContent).toContain("2.000000000000000001");
    expect(row.textContent).toContain("-2.25 USDC");
    expect(row.textContent).toContain("-2.254500000000000001 USD");
    expect(row.textContent).toContain("保守下界");
    expect(ui().getAllByText("未登记——导致停开")).toHaveLength(3);
    expect(ui().getAllByText("未登记——导致停开").every(tag => tag.className.includes("red"))).toBe(true);
    expect(ui().getByText("已登记外仓")).toBeTruthy(); expect(ui().getByText("程序")).toBeTruthy();
    expect(ui().getByText("BTCUSDT")).toBeTruthy(); expect(ui().getByText("UNPARSEABLE_RAW")).toBeTruthy();
    expect(ui().getByText(remoteAt)).toBeTruthy();
    expect(ui().getAllByRole("button", { name: /登记为外部持仓/ })).toHaveLength(1);
    expect(ui().queryByRole("button", { name: /取消登记/ })).toBeNull();
    expect(row.closest("tbody")!.querySelectorAll("tr[data-row-key]")).toHaveLength(5);
    expect(tableRows.length).toBeGreaterThan(0);
  });

  it.each(["missing", "null", "unverified"])("shows dashes for %s optional valuation without inventing USD", async variant => {
    const row: Record<string, unknown> = { ...remotePosition };
    if (variant === "missing") {
      for (const field of ["unrealized_pnl_native", "unrealized_pnl_usd", "valuation", "valuation_verified", "valuation_error"])
        delete row[field];
    } else if (variant === "null") {
      row.unrealized_pnl_native = null; row.unrealized_pnl_usd = null; row.valuation = null; row.valuation_verified = null;
    } else row.valuation_verified = false;
    remote.positions = [row];
    render(<LiveExecPage />); await flush();
    const rendered = ui().getByText(remoteIdentity.symbol).closest("tr")!;
    const heads = ui().getAllByRole("columnheader").map(item => item.textContent);
    expect(rendered.cells[heads.indexOf("浮盈亏（USD）")].textContent).toBe("—");
    if (variant !== "unverified") {
      expect(rendered.cells[heads.indexOf("当前浮盈亏（原币）")].textContent).toBe("—");
      expect(rendered.cells[heads.indexOf("折算状态")].textContent).toBe("—");
    }
    expect(ui().queryByText("0.00 USD")).toBeNull();
  });

  it("preserves zero native and USD values as strings", async () => {
    remote.positions = [{ ...remotePosition, unrealized_pnl_native: "0", unrealized_pnl_usd: "0" }];
    render(<LiveExecPage />); await flush();
    expect(ui().getByText("0 USDC")).toBeTruthy(); expect(ui().getByText("0 USD")).toBeTruthy();
  });

  it.each(["no_snapshot", "stale", "future", "aged", "missing_time", "missing_version", "read_failure"])(
    "never infers empty or enables writes from %s", async state => {
      if (state === "no_snapshot") remote = { available: false, positions: [], snapshot_at: null, snapshot_version: null, stale: true };
      if (state === "stale") remote.stale = true;
      if (state === "future") remote.snapshot_at = "2026-11-12T00:00:01Z";
      if (state === "aged") remote.snapshot_at = "2026-11-11T23:59:49Z";
      if (state === "missing_time") delete remote.snapshot_at;
      if (state === "missing_version") delete remote.snapshot_version;
      if (state === "read_failure") remoteError = "upstream_http_503";
      render(<LiveExecPage />); await flush();
      noEnabledActions();
      expect(ui().queryByText("账户无远端持仓")).toBeNull();
      expect(ui().getAllByText(/快照|远端持仓读取失败/).length).toBeGreaterThan(0);
      expect(ui().getByText(registry.contracts[0].identity.symbol)).toBeTruthy();
    });

  it("only cancels registered absent contracts, without putting registry records into the remote table", async () => {
    remote.positions = [];
    registry.contracts.push({ ...registry.contracts[0], identity: remoteIdentity, registered: false });
    render(<LiveExecPage />); await flush();
    expect(ui().getByText("账户无远端持仓")).toBeTruthy();
    expect(ui().getAllByRole("button", { name: /取消登记/ })).toHaveLength(1);
    await open("取消登记");
    for (const text of [registry.contracts[0].identity.symbol, "xyz", "USDC", "多、空均无仓位"])
      expect(dialog().getByText(text)).toBeTruthy();
    setReason(); await confirm();
    expect(writes[0]).toMatchObject({ path: "external-positions/unregister", body: {
      identity: registry.contracts[0].identity, expected_status: registry.status, reason: "核对原始身份后操作" } });
  });

  it.each(["long", "short", "unknown_identity"])("blocks cancellation if the exact raw contract has %s exposure", async side => {
    const identity = registry.contracts[0].identity;
    remote.positions = [{ ...remotePosition, ...identity, side: side === "short" ? "short" : "long",
      position_key: `crossex|${identity.symbol}|${side === "short" ? "short" : "long"}`, ownership: "external_registered",
      ...(side === "unknown_identity" ? { identity_verified: false, quote: null, dex: null } : {}) }];
    render(<LiveExecPage />); await flush();
    noEnabledActions(); expect(ui().queryByRole("button", { name: /取消登记/ })).toBeNull();
  });

  it.each(["program", "bitget", "identity_unverified", "wrong_dex", "missing_dex", "row_stale", "row_version"])(
    "does not register %s rows", async state => {
      registry.contracts = [];
      const row: Record<string, unknown> = { ...remotePosition };
      if (state === "program") row.ownership = "program";
      if (state === "bitget") Object.assign(row, { route: "bitget", business: "USDT-FUTURES", symbol: "BTCUSDT",
        exchange_type: "BITGET", dex: "", quote: "USDT", position_key: "bitget|BTCUSDT|long" });
      if (state === "identity_unverified") row.identity_verified = false;
      if (state === "wrong_dex") row.dex = "xyz";
      if (state === "missing_dex") delete row.dex;
      if (state === "row_stale") row.stale = true;
      if (state === "row_version") row.snapshot_version = 16;
      remote.positions = [row];
      render(<LiveExecPage />); await flush(); noEnabledActions();
      expect(ui().getByText(String(row.symbol))).toBeTruthy();
    });

  it.each(["register", "unregister"])("freezes %s identity/state, refreshes every management read on 409, then requires manual reconfirmation", async action => {
    if (action === "unregister") remote.positions = [];
    const identity = action === "register" ? remoteIdentity : registry.contracts[0].identity;
    const status = registry.status;
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: string, options: RequestInit = {}) => {
      if (!options.method) return original(input, options);
      writes.push({ path: new URL(input).pathname, body: JSON.parse(String(options.body)), options });
      registry.status = "x1:" + "b".repeat(64);
      return { ok: false, status: 409, json: async () => ({ error: "external_conflict", current_status: registry.status }) };
    });
    render(<LiveExecPage />); await flush();
    const before = reads.map(readCount);
    await open(action === "register" ? "登记为外部持仓" : "取消登记"); setReason();
    for (const field of Object.values(identity)) expect(dialog().getAllByText(field).length).toBeGreaterThan(0);
    expect(writes).toHaveLength(0); await confirm();
    expect(writes).toHaveLength(1);
    expect(writes[0].body).toEqual({ identity, expected_status: status, reason: "核对原始身份后操作" });
    expect(writes[0].options.headers).toMatchObject({ "X-Live-Exec-CSRF": "1" });
    expect(reads.map(readCount)).toEqual(before.map(count => count + 1));
    expect(ui().getByText("数据已被其他人修改，请刷新后重新确认")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(300); });
    expect(screen.getByRole("dialog").style.display).toBe("none"); expect(writes).toHaveLength(1);
    await open(action === "register" ? "登记为外部持仓" : "取消登记");
    expect((dialog().getByLabelText("操作说明") as HTMLTextAreaElement).value).toBe("");
    setReason(); await confirm();
    expect(writes[1].body).toEqual({ identity, expected_status: registry.status, reason: "核对原始身份后操作" });
  });

  it("refreshes all six reads after success and sends only once during a pending write", async () => {
    render(<LiveExecPage />); await flush();
    const before = reads.map(readCount);
    await open(); setReason();
    const original = fetchMock.getMockImplementation()!;
    let resolveWrite: () => void = () => undefined;
    fetchMock.mockImplementation(async (input: string, options: RequestInit = {}) => {
      if (options.method) await new Promise<void>(resolve => { resolveWrite = resolve; });
      return original(input, options);
    });
    const button = dialog().getByRole("button", { name: "确认提交" });
    fireEvent.click(button); fireEvent.click(button); await flush();
    expect(fetchMock.mock.calls.filter(([, options]) => options.method)).toHaveLength(1);
    await act(async () => { resolveWrite(); }); await flush();
    expect(writes).toHaveLength(1); expect(reads.map(readCount)).toEqual(before.map(count => count + 1));
  });

  it("expires displayed evidence locally and rechecks freshness before submitting an open confirmation", async () => {
    render(<LiveExecPage />); await flush(); await open(); setReason();
    await act(async () => { await vi.advanceTimersByTimeAsync(11000); });
    expect(ui().getByText(/快照已过期/)).toBeTruthy(); noEnabledActions();
    await confirm(); expect(writes).toHaveLength(0);
    expect(dialog().getByText(/快照.*重新确认/)).toBeTruthy();
  });

  it("serializes remote reads, aborts when hidden and ignores late responses after resume and unmount", async () => {
    const original = fetchMock.getMockImplementation()!;
    let oldSignal: AbortSignal | null = null;
    let resolveOld: (result: unknown) => void = () => undefined;
    fetchMock.mockImplementation((input: string, options: RequestInit = {}) => {
      if (input.endsWith("/remote-positions") && !oldSignal) {
        oldSignal = options.signal as AbortSignal;
        return new Promise(resolve => { resolveOld = resolve; });
      }
      return original(input, options);
    });
    const { unmount } = render(<LiveExecPage />); await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(readCount("remote-positions")).toBe(1);
    visibility("hidden"); expect(oldSignal!.aborted).toBe(true);
    const hiddenCount = fetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock.mock.calls).toHaveLength(hiddenCount);
    visibility("visible"); await flush();
    await act(async () => { resolveOld({ ok: true, status: 200, json: async () => ({ available: false, error: "timeout" }) }); });
    expect(ui().getByText(remoteIdentity.symbol)).toBeTruthy();
    expect(ui().queryByText(/远端持仓读取失败/)).toBeNull();
    unmount(); const end = fetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); }); expect(fetchMock.mock.calls).toHaveLength(end);
  });
});
