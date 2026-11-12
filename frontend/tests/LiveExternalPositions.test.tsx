import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExternalPositions, type ExternalPositionRow } from "../src/pages/liveExec/LiveExternalPositions";
import { remoteSnapshot, remoteAt } from "./liveExecRemoteFixtures";

const identity = { route: "crossex", exchange_type: "HYPERLIQUID", business: "FUTURE",
  symbol: "HYPERLIQUID_FUTURE_io:OAI_USDC", quote: "USDC", dex: "io" };
const status = "x1:" + "a".repeat(64);
const registry = { status, contracts: [], income: null };
const baseRow: ExternalPositionRow = { key: "remote-io-long", identity, route: "crossex", exchange: "HYPERLIQUID",
  symbol: identity.symbol, dex: "io", side: "long", quantity: "2.000", ownership: "unregistered",
  nativeUpnl: "-2.25", pnlCurrency: "USDC", usdUpnl: null, conversion: null, action: "register" };
const flush = async () => { await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); }); };
const open = async () => { fireEvent.click(screen.getByRole("button", { name: /登记为外部持仓/ })); await flush(); };
const confirm = async () => { fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认提交" })); await flush(); };
let fetch: ReturnType<typeof vi.fn>;
let refresh: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(remoteAt));
  fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ available: true, data: {} }) }));
  vi.stubGlobal("fetch", fetch);
  refresh = vi.fn(async () => undefined);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("外部持仓确认与显示", () => {
  it("requires snapshot evidence even if an outdated caller supplies actionable rows", () => {
    // @ts-expect-error Simulate an outdated caller that omits the required snapshot proof.
    render(<LiveExternalPositions registry={registry} rows={[baseRow]} refresh={refresh} />);
    expect(screen.getByRole("button", { name: /登记为外部持仓/ }).hasAttribute("disabled")).toBe(true);
  });
  it("shows all ownership categories and preserves same-ticker DEX identities", () => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[
      baseRow,
      { ...baseRow, key: "xyz", identity: { ...identity, symbol: "HYPERLIQUID_FUTURE_xyz:OAI_USDC", dex: "xyz" },
        symbol: "HYPERLIQUID_FUTURE_xyz:OAI_USDC", dex: "xyz", ownership: "external", action: undefined },
      { ...baseRow, key: "program", symbol: "BINANCE_FUTURE_BTC_USDT", ownership: "program", action: undefined },
    ]} refresh={refresh} />);
    expect(screen.getByText("未登记——导致停开").className).toContain("red");
    expect(screen.getByText("已登记外仓")).toBeTruthy();
    expect(screen.getByText("程序")).toBeTruthy();
    expect(screen.getByText(identity.symbol)).toBeTruthy();
    expect(screen.getByText("HYPERLIQUID_FUTURE_xyz:OAI_USDC")).toBeTruthy();
    expect(screen.getAllByText("-2.25 USDC")).toHaveLength(3);
    const row = screen.getByText(identity.symbol).closest("tr")!;
    const heads = screen.getAllByRole("columnheader").map(item => item.textContent);
    expect(row.cells[heads.indexOf("浮盈亏（USD）")].textContent).toBe("—");
    expect(row.cells[heads.indexOf("折算状态")].textContent).toBe("—");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows verified zero USD without substituting missing values", () => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[{ ...baseRow, usdUpnl: 0, conversion: "已核实" }]} refresh={refresh} />);
    expect(screen.getByText("0.00 USD")).toBeTruthy();
    expect(screen.getByText("已核实")).toBeTruthy();
  });

  it("requires reason and second confirmation with all original identity fields and direction", async () => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[baseRow]} refresh={refresh} />);
    await open();
    const dialog = within(screen.getByRole("dialog"));
    for (const value of ["crossex", "HYPERLIQUID", "FUTURE", identity.symbol, "USDC", "io", "多"])
      expect(dialog.getAllByText(value).length).toBeGreaterThan(0);
    expect(dialog.getByText(/两个方向永久禁止/)).toBeTruthy();
    expect(dialog.getByText(/取消登记仍保留/)).toBeTruthy();
    expect(dialog.getByText(/重新建立收益基线/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
    await confirm(); expect(fetch).not.toHaveBeenCalled();
    expect(dialog.getByText(/请输入/)).toBeTruthy();
    fireEvent.change(dialog.getByLabelText("操作说明"), { target: { value: "已核对外部工具持仓" } });
    await confirm();
    expect(fetch).toHaveBeenCalledOnce();
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ identity, expected_status: status, reason: "已核对外部工具持仓" });
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("cancel confirmation sends nothing", async () => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[baseRow]} refresh={refresh} />);
    await open();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /取\s*消/ })); await flush();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["", "   ", "a".repeat(201), "why\nnext"])("rejects invalid reason %s", async reason => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[baseRow]} refresh={refresh} />);
    await open();
    fireEvent.change(screen.getByLabelText("操作说明"), { target: { value: reason } });
    await confirm();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("freezes identity and status on open, closes on 409, refreshes and never retries", async () => {
    fetch.mockImplementation(async () => ({ ok: false, status: 409, json: async () => ({ error: "external_conflict" }) }));
    const changingRow = { ...baseRow, identity: { ...identity } };
    const view = render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[changingRow]} refresh={refresh} />);
    await open();
    fireEvent.change(screen.getByLabelText("操作说明"), { target: { value: "核实登记" } });
    Object.assign(changingRow.identity, { dex: "xyz", symbol: "HYPERLIQUID_FUTURE_xyz:OAI_USDC" });
    Object.assign(changingRow, { key: "xyz", dex: "xyz", symbol: changingRow.identity.symbol });
    // The original contract remains actionable; mutation of another observation must
    // not mutate the identity or expected_status frozen in the open confirmation.
    view.rerender(<LiveExternalPositions snapshot={remoteSnapshot} registry={{ ...registry, status: "x1:" + "b".repeat(64) }}
      rows={[baseRow, changingRow]} refresh={refresh} />);
    await confirm();
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ identity, expected_status: status, reason: "核实登记" });
    expect(screen.getByText("数据已被其他人修改，请刷新后重新确认")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(screen.getByRole("dialog").style.display).toBe("none");
    expect(fetch).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("only offers unregister for the certified absent registered contract", async () => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[
      { ...baseRow, ownership: "external", action: undefined },
      { ...baseRow, key: "absent", side: null, quantity: null, ownership: "external", action: "unregister" },
    ]} refresh={refresh} />);
    expect(screen.queryByRole("button", { name: /登记为外部持仓/ })).toBeNull();
    expect(screen.getAllByRole("button", { name: /取消登记/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /取消登记/ })); await flush();
    expect(within(screen.getByRole("dialog")).getByText("多、空均无仓位")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("操作说明"), { target: { value: "已核实远端无仓位" } });
    await confirm();
    expect(fetch.mock.calls[0][0]).toContain("external-positions/unregister");
  });

  it.each([undefined, "invalid", "x1:" + "a".repeat(64) + "\n"])("disables writes without exact concurrency state %s", status => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={{ ...registry, status }} rows={[baseRow]} refresh={refresh} />);
    expect(screen.getByRole("button", { name: /登记为外部持仓/ }).hasAttribute("disabled")).toBe(true);
  });

  it("keeps failed reads distinct from empty accounts", () => {
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={null} error="upstream_http_503" rows={null} refresh={refresh} />);
    expect(screen.getByText(/外部持仓读取失败/)).toBeTruthy();
    expect(screen.getByText(/远端持仓数据不可用/)).toBeTruthy();
    expect(screen.queryByText("账户无远端持仓")).toBeNull();
  });

  it("holds one in-flight request and retains a failed-write message without retry", async () => {
    let resolve: (value: unknown) => void = () => undefined;
    fetch.mockImplementation(() => new Promise(done => { resolve = done; }));
    render(<LiveExternalPositions snapshot={remoteSnapshot} registry={registry} rows={[baseRow]} refresh={refresh} />);
    await open();
    fireEvent.change(screen.getByLabelText("操作说明"), { target: { value: "核实登记" } });
    const button = within(screen.getByRole("dialog")).getByRole("button", { name: "确认提交" });
    fireEvent.click(button); fireEvent.click(button); await flush();
    expect(fetch).toHaveBeenCalledOnce();
    await act(async () => { resolve({ ok: false, status: 504, json: async () => ({ detail: "请求超时，请刷新核实" }) }); });
    expect(within(screen.getByRole("dialog")).getByText("请求超时，请刷新核实")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(fetch).toHaveBeenCalledOnce();
  });
});
