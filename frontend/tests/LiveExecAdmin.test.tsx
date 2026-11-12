import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { liveFixtures } from "./liveExecFixtures";

import { fixtures, mappingId, markets, universeStatus } from "./liveExecAdminFixtures";

let reads: typeof fixtures;
let writes: { path: string; options: RequestInit }[];
let writeError: string | null;
let fetchMock: ReturnType<typeof vi.fn>;
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); }); };
const click = async (name: string) => { fireEvent.click(screen.getByRole("button", { name })); await flush(); };
const dialog = () => within(screen.getByRole("dialog"));

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-12T00:00:00Z"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  reads = structuredClone(fixtures); writes = []; writeError = null;
  fetchMock = vi.fn(async (input: string, options: RequestInit = {}) => {
    const path = new URL(input).pathname.replace("/api/live-exec/", "");
    if (options.method && options.method !== "GET") {
      writes.push({ path, options });
      if (writeError) return { ok: false, status: 503, json: async () => ({ detail: writeError }) };
      const body = JSON.parse(String(options.body));
      if (path.startsWith("universe/") && body.expected_status !== reads.universe[0].status)
        return { ok: false, status: 409, json: async () => ({ error: "status_conflict", current_status: reads.universe[0].status }) };
      if (path.startsWith("mappings/") && (body.expected_status !== reads.mappings[0].status || body.expected_multiplier !== reads.mappings[0].multiplier))
        return { ok: false, status: 409, json: async () => ({ error: "mapping_conflict", current_status: reads.mappings[0].status, current_multiplier: reads.mappings[0].multiplier }) };
      if (path === "config") {
        if (JSON.parse(String(options.body)).expected_version !== reads.config.version)
          return { ok: false, status: 409, json: async () => ({ error: "version_conflict", current_version: reads.config.version }) };
        reads.config.version++;
        const { expected_version, ...changes } = JSON.parse(String(options.body));
        Object.assign(reads.config.values, changes);
      }
      if (path.endsWith("/exclude")) reads.universe[0].excluded = true;
      if (path.endsWith("/unexclude")) reads.universe[0].excluded = false;
      if (path.startsWith("universe/")) reads.universe[0].status = "u1:" + "b".repeat(64);
      return { ok: true, status: 200, json: async () => ({ available: true, data: {} }) };
    }
    const data = reads[path as keyof typeof reads] ?? liveFixtures[path as keyof typeof liveFixtures];
    return { ok: true, status: 200, json: async () => ({ available: true, data: structuredClone(data) }) };
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("实盘第八轮管理", () => {
  it("shows parameter bounds, exact contracts, turnover basis, mapping ratio timestamp and audit", async () => {
    render(<LiveExecPage />); await flush();
    for (const name of ["参数", "交易范围", "映射确认", "参数审计记录", "当前值", "默认值", "下限", "上限"])
      expect(screen.getAllByText(name).length).toBeGreaterThan(0);
    expect(screen.getAllByText(markets[0]).length).toBeGreaterThan(0);
    expect(screen.getByText("binance|future|1000PEPEUSDT")).toBeTruthy();
    expect(screen.getByText(/发现时原生价格比/)).toBeTruthy();
    expect(screen.getByText(/保守下界/)).toBeTruthy();
    expect(screen.getByText(/40 → 50/)).toBeTruthy();
    expect(writes).toHaveLength(0);
  });

  it("requires a second confirmation showing old/new and only sends edited fields with CSRF header", async () => {
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value: "30" } });
    await click("提交参数修改");
    expect(dialog().getByText(/50 → 30/)).toBeTruthy();
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: /取\s*消/ })); await flush();
    expect(writes).toHaveLength(0);
    await click("提交参数修改");
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(writes).toHaveLength(1);
    expect(writes[0].path).toBe("config");
    expect(writes[0].options.method).toBe("PUT");
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ order_usd_max: 30, expected_version: 2 });
    expect(writes[0].options.credentials).toBe("same-origin");
    expect(writes[0].options.headers).toMatchObject({ "Content-Type": "application/json", "X-Live-Exec-CSRF": "1" });
    expect(screen.getByText(/操作成功/)).toBeTruthy();
  });

  it.each(["151", "4", "abc", "Infinity"])("blocks invalid numeric update %s before confirmation", async value => {
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value } });
    await click("提交参数修改");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/超出允许范围|请输入有效数字/)).toBeTruthy();
    expect(writes).toHaveLength(0);
  });

  it("blocks fractional integer parameters", async () => {
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 min_venues"), { target: { value: "3.5" } });
    await click("提交参数修改");
    expect(screen.getByText(/必须为整数/)).toBeTruthy(); expect(writes).toHaveLength(0);
  });

  it("keeps errors visible in confirmation, does not automatically retry an uncertain write", async () => {
    writeError = "管理令牌未配置，无法写入";
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value: "30" } });
    await click("提交参数修改");
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(dialog().getByText(writeError)).toBeTruthy();
    expect(screen.queryByText(/操作成功/)).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(writes).toHaveLength(1);
  });

  it("rejects a stale config confirmation when polling discovers a newer version", async () => {
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value: "30" } });
    await click("提交参数修改");
    reads.config.version++;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(screen.getByText("数据已被其他人修改，请刷新后重新确认")).toBeTruthy();
    // The shared getComputedStyle stub omits display; inspect the actual hidden modal.
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.getByRole("dialog").style.display).toBe("none");
    expect(writes).toHaveLength(0);
  });

  it("does not overwrite a tightened limit before the next poll; refreshes on 409 and requires a new confirmation", async () => {
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value: "30" } });
    await click("提交参数修改");
    expect(dialog().getByText(/50 → 30/)).toBeTruthy();
    reads.config.version = 3;
    reads.config.values.order_usd_max = 5;
    const readCount = fetchMock.mock.calls.filter(([url, options]) => url.endsWith("/config") && !options.method).length;
    // No timer advance: the browser has not observed the other session's write.
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ order_usd_max: 30, expected_version: 2 });
    expect(reads.config.values.order_usd_max).toBe(5);
    expect(screen.getByText("数据已被其他人修改，请刷新后重新确认")).toBeTruthy();
    // The shared getComputedStyle stub omits display; inspect the actual hidden modal.
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.getByRole("dialog").style.display).toBe("none");
    expect((screen.getByLabelText("修改 order_usd_max") as HTMLInputElement).value).toBe("5");
    expect(fetchMock.mock.calls.filter(([url, options]) => url.endsWith("/config") && !options.method)).toHaveLength(readCount + 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(writes).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value: "30" } });
    await click("提交参数修改");
    expect(dialog().getByText(/5 → 30/)).toBeTruthy();
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(JSON.parse(String(writes[1].options.body))).toEqual({ order_usd_max: 30, expected_version: 3 });
    expect(screen.getByText(/操作成功/)).toBeTruthy();
  });

  it("holds one in-flight write despite repeated confirmation clicks", async () => {
    const original = fetchMock.getMockImplementation()!;
    let resolveWrite: (value: unknown) => void = () => undefined;
    let sent = 0;
    fetchMock.mockImplementation((input: string, options: RequestInit) => {
      if (options.method === "PUT") { sent++; return new Promise(resolve => { resolveWrite = resolve; }); }
      return original(input, options);
    });
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value: "30" } });
    await click("提交参数修改");
    const confirm = dialog().getByRole("button", { name: "确认提交" });
    fireEvent.click(confirm); fireEvent.click(confirm); await flush();
    expect(sent).toBe(1);
    await act(async () => { resolveWrite({ ok: false, status: 504, json: async () => ({ detail: "请求超时，请刷新核实" }) }); });
    expect(dialog().getByText("请求超时，请刷新核实")).toBeTruthy();
  });

  it("requires equity venue selection and US/KRX, then forwards exact selected keys", async () => {
    render(<LiveExecPage />); await flush();
    await click("判断类别 SKHYNIX");
    fireEvent.change(dialog().getByLabelText("类别"), { target: { value: "listed_equity" } });
    fireEvent.click(dialog().getByRole("button", { name: "确认分类" })); await flush();
    expect(dialog().getByText(/请选择标的市场/)).toBeTruthy();
    fireEvent.change(dialog().getByLabelText("标的市场"), { target: { value: "KRX" } });
    fireEvent.click(dialog().getByRole("button", { name: "确认分类" })); await flush();
    expect(dialog().getByText(/至少选择一个/)).toBeTruthy();
    fireEvent.click(dialog().getByLabelText(markets[0]));
    fireEvent.click(dialog().getByRole("button", { name: "确认分类" })); await flush();
    expect(writes[0].path).toBe("universe/SKHYNIX/classify");
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ category: "listed_equity", underlying_market: "KRX", markets: [markets[0]], expected_status: universeStatus });
  });

  it("excludes and unexcludes without implicitly confirming category", async () => {
    render(<LiveExecPage />); await flush();
    await click("排除 SKHYNIX");
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    await click("取消排除 SKHYNIX");
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(writes.map(w => w.path)).toEqual(["universe/SKHYNIX/exclude", "universe/SKHYNIX/unexclude"]);
    expect(writes.map(w => JSON.parse(String(w.options.body)))).toEqual([
      { expected_status: universeStatus }, { expected_status: "u1:" + "b".repeat(64) },
    ]);
  });

  it.each(["确认", "拒绝"])("requires confirmation before %s mapping", async action => {
    render(<LiveExecPage />); await flush();
    await click(`${action}映射 PEPE`);
    expect(dialog().getByText("binance|future|1000PEPEUSDT")).toBeTruthy();
    expect(dialog().getByText(/发现时原生价格比/)).toBeTruthy();
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(writes[0].path).toBe(`mappings/${mappingId}/${action === "确认" ? "confirm" : "reject"}`);
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ expected_status: "pending", expected_multiplier: "1000" });
  });

  it.each([
    ["exclude", false], ["unexclude", false], ["classify", false], ["confirm", false], ["reject", false],
    ["exclude", true], ["unexclude", true], ["classify", true], ["confirm", true], ["reject", true],
  ] as const)("keeps the opening snapshot for %s even if polling=%s, handles 409 and refreshes", async (action, poll) => {
    const mapping = action === "confirm" || action === "reject";
    if (action === "unexclude") reads.universe[0].excluded = true;
    render(<LiveExecPage />); await flush();
    const button = action === "classify" ? "判断类别 SKHYNIX" : action === "exclude" ? "排除 SKHYNIX"
      : action === "unexclude" ? "取消排除 SKHYNIX" : `${action === "confirm" ? "确认" : "拒绝"}映射 PEPE`;
    await click(button);
    if (action === "classify") {
      fireEvent.change(dialog().getByLabelText("类别"), { target: { value: "listed_equity" } });
      fireEvent.change(dialog().getByLabelText("标的市场"), { target: { value: "KRX" } });
      fireEvent.click(dialog().getByLabelText(markets[0]));
    }
    if (mapping) {
      reads.mappings[0].status = "rejected";
      reads.mappings[0].multiplier = "0.001";
    } else reads.universe[0].status = "u1:" + "c".repeat(64);
    if (poll) await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    const adminReads = () => fetchMock.mock.calls.filter(([url, options]) => !options.method && /\/(config|universe|mappings|audit)$/.test(url)).length;
    const before = adminReads();
    fireEvent.click(dialog().getByRole("button", { name: action === "classify" ? "确认分类" : "确认提交" })); await flush();
    expect(writes).toHaveLength(1);
    expect(JSON.parse(String(writes[0].options.body))).toMatchObject(mapping
      ? { expected_status: "pending", expected_multiplier: "1000" } : { expected_status: universeStatus });
    expect(screen.getByText("数据已被其他人修改，请刷新后重新确认")).toBeTruthy();
    expect(adminReads()).toBe(before + 4);
    expect(screen.queryByText(/操作成功/)).toBeNull();
    // The shared getComputedStyle stub omits display; inspect the actual hidden modal.
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.getByRole("dialog").style.display).toBe("none");
    await click(button);
    if (action === "classify") {
      fireEvent.change(dialog().getByLabelText("类别"), { target: { value: "listed_equity" } });
      fireEvent.change(dialog().getByLabelText("标的市场"), { target: { value: "KRX" } });
      fireEvent.click(dialog().getByLabelText(markets[0]));
    }
    fireEvent.click(dialog().getByRole("button", { name: action === "classify" ? "确认分类" : "确认提交" })); await flush();
    expect(writes).toHaveLength(2);
    expect(JSON.parse(String(writes[1].options.body))).toMatchObject(mapping
      ? { expected_status: "rejected", expected_multiplier: "0.001" } : { expected_status: "u1:" + "c".repeat(64) });
    expect(screen.getByText(/操作成功/)).toBeTruthy();
  });

  it.each(["status", "multiplier"])("honors mapping conflicts when only %s changes", async field => {
    render(<LiveExecPage />); await flush();
    await click("确认映射 PEPE");
    if (field === "status") reads.mappings[0].status = "rejected";
    else reads.mappings[0].multiplier = "1000.00";
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(screen.getByText("数据已被其他人修改，请刷新后重新确认")).toBeTruthy();
    expect(writes).toHaveLength(1);
  });

  it("disables unsafe writes for older reads without concurrency fields", async () => {
    Reflect.deleteProperty(reads.universe[0], "status");
    Reflect.deleteProperty(reads.mappings[0], "multiplier");
    render(<LiveExecPage />); await flush();
    for (const name of ["排除 SKHYNIX", "判断类别 SKHYNIX", "确认映射 PEPE", "拒绝映射 PEPE"])
      expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
    expect(writes).toHaveLength(0);
    expect(screen.getByText("0.15 USD / 15.00 bp")).toBeTruthy();
  });

  it("shows read failures without clearing the existing position dashboard", async () => {
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input: string, options: RequestInit) => input.endsWith("/config")
      ? { ok: true, status: 200, json: async () => ({ available: false, error: "not_configured" }) }
      : original(input, options));
    render(<LiveExecPage />); await flush();
    expect(screen.getByText(/参数读取失败：未配置/)).toBeTruthy();
    expect(screen.getByText("0.15 USD / 15.00 bp")).toBeTruthy();
  });

  it("keeps successful and conflicting audits with the same version and timestamp distinct", async () => {
    const conflict = { ...reads["config/audit"][0], old: {}, new: {}, error: "version_conflict",
      expected_version: 1, current_version: 2 };
    reads["config/audit"] = [reads["config/audit"][0], conflict, conflict] as typeof reads["config/audit"];
    render(<LiveExecPage />); await flush();
    const card = screen.getByText("参数审计记录").closest<HTMLElement>(".ant-card")!;
    expect(within(card).getByText(/40 → 50/)).toBeTruthy();
    expect(within(card).getAllByText("版本冲突，未更新（预期 1 / 当前 2）")).toHaveLength(2);
    const keys = [...card.querySelectorAll("tbody tr[data-row-key]")].map(row => row.getAttribute("data-row-key"));
    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(3);
  });

  it("stops admin reads while hidden and cancels on unmount", async () => {
    const { unmount } = render(<LiveExecPage />); await flush();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    fireEvent(document, new Event("visibilitychange"));
    const before = fetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(fetchMock.mock.calls.length).toBe(before);
    let pendingSignal: AbortSignal | undefined;
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input: string, options: RequestInit) => {
      if (input.endsWith("/config")) {
        pendingSignal = options.signal as AbortSignal;
        return new Promise(() => undefined);
      }
      return original(input, options);
    });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    fireEvent(document, new Event("visibilitychange")); await flush();
    expect(pendingSignal?.aborted).toBe(false);
    unmount();
    expect(pendingSignal?.aborted).toBe(true);
  });
});
