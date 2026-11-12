import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveExecPage } from "../src/pages/LiveExecPage";
import { fixtures } from "./liveExecAdminFixtures";
import { liveFixtures } from "./liveExecFixtures";

const identity = { route: "crossex", exchange_type: "HYPERLIQUID", business: "FUTURE",
  symbol: "HYPERLIQUID_FUTURE_io:OAI_USDC", quote: "USDC", dex: "io" };
const external = { ...fixtures["external-positions"], contracts: [
  { identity, raw_base: "io:OAI", registered: true, forbidden: true, revision: 1, registered_at: 1794441600, updated_at: 1794441600 },
], flatten_status: { program_flat: true, account_flat: false, captured_at: 1794441600 },
  // Round eleven projection is old, is not a complete current remote snapshot and has no external native PnL.
  projection: { positions: {}, external_positions: { "crossex|HYPERLIQUID_FUTURE_io:OAI_USDC|long": "2" }, captured_at: 1794441500 },
  income: { verified: false, reason: "external_income_unverifiable", at: 1794441600 } };
const flush = async () => { await act(async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); }); };
const value = (label: string) => screen.getByText(label).closest(".ant-descriptions-item")?.querySelector(".ant-descriptions-item-content")?.textContent;
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-12T00:00:00Z"));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("第十一轮外仓兼容接入", () => {
  it.each(["present", "missing", "null"])("keeps flat scopes separate with %s status", async variant => {
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      const path = new URL(input).pathname.replace("/api/live-exec/", "");
      const data = path === "external-positions" ? { ...external,
        flatten_status: variant === "present" ? external.flatten_status : variant === "null" ? null : undefined }
        : fixtures[path as keyof typeof fixtures] ?? liveFixtures[path as keyof typeof liveFixtures];
      return { ok: true, status: 200, json: async () => ({ available: true, data: structuredClone(data) }) };
    }));
    render(<LiveExecPage />); await flush();
    expect(screen.getByText("外部持仓")).toBeTruthy();
    expect(value("程序仓位已平")).toBe(variant === "present" ? "是" : "—");
    expect(value("全账户已平")).toBe(variant === "present" ? "否" : "—");
    expect(screen.getByText(identity.symbol)).toBeTruthy();
    expect(screen.getByText(/远端持仓数据不可用/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /取消登记|登记为外部持仓/ })).toBeNull();
    expect(screen.queryByText("账户无远端持仓")).toBeNull();
  });
});
