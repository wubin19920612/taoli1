import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { liveFixtures } from "./liveExecFixtures";

const mappingId = "0123456789abcdef01234567";
const markets = ["binance|future|SKHYNIXUSDT", "bitget|future|SKHYNIXUSDT"];
const fixtures = {
  config: { version: 2, fallback: false, values: { order_usd_max: 50, min_venues: 3 }, schema: {
    order_usd_max: { default: 50, min: 5, max: 150, integer: false },
    min_venues: { default: 3, min: 3, max: 6, integer: true },
  } },
  "config/audit": [{ version: 2, at: "2026-11-12T00:00:00Z", source: "admin_api", old: { order_usd_max: 40 }, new: { order_usd_max: 50 } }],
  universe: [{ base: "SKHYNIX", category: "unconfirmed", excluded: false, underlying_market: null,
    markets: markets.map((market_key, i) => ({ market_key, raw_symbol: "SKHYNIXUSDT", exchange: i ? "bitget" : "binance",
      category: "listed_equity", effective_category: "listed_equity", underlying_market: null,
      volume_usd: i ? null : 3000000, volume_basis: i ? "base_times_low24h_lower_bound" : "quote_volume",
      at: "2026-11-12T00:00:00Z", selected: false, eligible: false, needs_classification: true })) }],
  mappings: [{ id: mappingId, base: "PEPE", status: "pending", at: "2026-11-12T00:00:00Z", ratio: 1000,
    legs: [{ market_key: "binance|future|1000PEPEUSDT", raw_base: "1000PEPE", quantity_multiplier: 1000 },
      { market_key: "bitget|future|PEPEUSDT", raw_base: "PEPE", quantity_multiplier: 1 }] }],
};
let reads: typeof fixtures;
let writes: { path: string; options: RequestInit }[];
let writeError: string | null;
let fetchMock: ReturnType<typeof vi.fn>;
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); }); };
const click = async (name: string) => { fireEvent.click(screen.getByRole("button", { name, exact: true })); await flush(); };
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
      if (path === "config") {
        reads.config.version++;
        Object.assign(reads.config.values, JSON.parse(String(options.body)));
      }
      if (path.endsWith("/exclude")) reads.universe[0].excluded = true;
      if (path.endsWith("/unexclude")) reads.universe[0].excluded = false;
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
    fireEvent.click(dialog().getByRole("button", { name: "取消" })); await flush();
    expect(writes).toHaveLength(0);
    await click("提交参数修改");
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(writes).toHaveLength(1);
    expect(writes[0].path).toBe("config");
    expect(writes[0].options.method).toBe("PUT");
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ order_usd_max: 30 });
    expect(writes[0].options.credentials).toBe("same-origin");
    expect(writes[0].options.headers).toMatchObject({ "Content-Type": "application/json", "X-Live-Exec-CSRF": "1" });
    expect(screen.getByText(/操作成功/)).toBeTruthy();
  });

  it.each(["151", "4", "abc", "Infinity"])("blocks invalid numeric update %s before confirmation", async value => {
    render(<LiveExecPage />); await flush();
    fireEvent.change(screen.getByLabelText("修改 order_usd_max"), { target: { value } });
    await click("提交参数修改");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/范围|有效数字/)).toBeTruthy();
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
    expect(JSON.parse(String(writes[0].options.body))).toEqual({ category: "listed_equity", underlying_market: "KRX", markets: [markets[0]] });
  });

  it("excludes and unexcludes without implicitly confirming category", async () => {
    render(<LiveExecPage />); await flush();
    await click("排除 SKHYNIX");
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    await click("取消排除 SKHYNIX");
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(writes.map(w => w.path)).toEqual(["universe/SKHYNIX/exclude", "universe/SKHYNIX/unexclude"]);
    expect(writes.every(w => w.options.body === "{}")).toBe(true);
  });

  it.each(["确认", "拒绝"])("requires confirmation before %s mapping", async action => {
    render(<LiveExecPage />); await flush();
    await click(`${action}映射 PEPE`);
    expect(dialog().getByText("binance|future|1000PEPEUSDT")).toBeTruthy();
    expect(dialog().getByText(/发现时原生价格比/)).toBeTruthy();
    expect(writes).toHaveLength(0);
    fireEvent.click(dialog().getByRole("button", { name: "确认提交" })); await flush();
    expect(writes[0].path).toBe(`mappings/${mappingId}/${action === "确认" ? "confirm" : "reject"}`);
    expect(writes[0].options.body).toBe("{}");
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

  it("stops admin reads while hidden and cancels on unmount", async () => {
    const { unmount } = render(<LiveExecPage />); await flush();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    fireEvent(document, new Event("visibilitychange"));
    const before = fetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(fetchMock.mock.calls.length).toBe(before);
    unmount();
    expect(fetchMock.mock.calls.every(call => call[1].signal?.aborted)).toBe(true);
  });
});
