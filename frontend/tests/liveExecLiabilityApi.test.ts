import { afterEach, describe, expect, it, vi } from "vitest";
import { AUTH_REQUIRED_EVENT } from "../src/api/auth";
import { getLiveExecAdmin, LiveAdminConflictError, LiveAdminSessionError, writeLiveExecAdmin } from "../src/api/liveExec";

const liabilityRegistry = { revision: 0, items: [
  { exchange_type: "HYPERLIQUID", coin: "USDC", cap: "50.000000000000000001", reason: "手动确认",
    registered_at: 1794873600, liability: "28.500000000000000001", within_cap: true },
] };

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("外部负债额度 API", () => {
  it("reads exact native quantities, null observations and revision zero", async () => {
    const data = { ...liabilityRegistry, items: [liabilityRegistry.items[0],
      { ...liabilityRegistry.items[0], exchange_type: "BINANCE", liability: null, within_cap: null }] };
    const fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ available: true, data }) }));
    vi.stubGlobal("fetch", fetch);
    expect(await getLiveExecAdmin("external-liabilities", new AbortController().signal)).toEqual({ available: true, data });
    const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/live-exec/external-liabilities");
    expect(options).toMatchObject({ credentials: "same-origin", cache: "no-store" });
  });

  it.each([
    { cap: "50", liability: null, within_cap: true, expected: null },
    { cap: "50", liability: null, within_cap: false, expected: null },
    { cap: null, liability: "28.5", within_cap: true, expected: null },
    { cap: "50", liability: "28.5", within_cap: null, expected: null },
    { cap: "50", liability: "50.000000000000000001", within_cap: true, expected: null },
    { cap: "50", liability: "49.999999999999999999", within_cap: false, expected: null },
    { cap: "50", liability: "50", within_cap: false, expected: null },
    { cap: "50", liability: "50.000000000000000001", within_cap: false, expected: false },
    { cap: "50", liability: "49.999999999999999999", within_cap: true, expected: true },
    { cap: "50.000000000000000001", liability: "50", within_cap: true, expected: true },
    { cap: "+00050.000", liability: "5e1", within_cap: true, expected: true },
    { cap: "50", liability: "5.0000000000000000001e1", within_cap: true, expected: null },
    { cap: "50", liability: "+00050.000000000000000001", within_cap: false, expected: false },
    { cap: "10", liability: "9", within_cap: true, expected: true },
    { cap: "9", liability: "10", within_cap: false, expected: false },
    { cap: ".000000000000000001", liability: "1e-18", within_cap: true, expected: true },
    { cap: "1e-18", liability: "1.000000000000000001e-18", within_cap: true, expected: null },
    { cap: "1e-9007199254740993", liability: "1e-9007199254740992", within_cap: true, expected: null },
    { cap: "1e-9007199254740993", liability: "0", within_cap: true, expected: true },
    { cap: "50", liability: 0, within_cap: true, expected: true },
    { cap: "50", liability: 51, within_cap: false, expected: false },
  ])("cross-checks decimal amounts and upstream status: %j", async ({ expected, ...observation }) => {
    const data = { ...liabilityRegistry, items: [{ ...liabilityRegistry.items[0], ...observation }] };
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ available: true, data }) })));
    expect(await getLiveExecAdmin("external-liabilities", new AbortController().signal)).toEqual({
      available: true, data: { ...data, items: [{ ...data.items[0], within_cap: expected }] },
    });
  });

  it.each([null, [], {}, { ...liabilityRegistry, revision: -1 }, { ...liabilityRegistry, revision: "0" },
    { ...liabilityRegistry, revision: 1.5 }, { ...liabilityRegistry, revision: Number.MAX_SAFE_INTEGER + 1 },
    { ...liabilityRegistry, items: null }, { ...liabilityRegistry, items: [null] },
    { ...liabilityRegistry, items: [liabilityRegistry.items[0], liabilityRegistry.items[0]] },
    ...Object.entries({ exchange_type: {}, coin: "", cap: 50, reason: [], liability: {}, within_cap: "true", registered_at: {} })
      .map(([key, value]) => ({ ...liabilityRegistry, items: [{ ...liabilityRegistry.items[0], [key]: value }] })),
  ])("fails closed on malformed registries: %j", async data => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ available: true, data }) })));
    expect(await getLiveExecAdmin("external-liabilities", new AbortController().signal)).toEqual({ available: false, error: "invalid_json" });
  });

  it.each(["register", "unregister"] as const)("sends %s with only whitelisted fields and the original revision", async action => {
    const fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ available: true, data: {} }) }));
    vi.stubGlobal("fetch", fetch);
    const identity = { exchange_type: "HYPERLIQUID", coin: "USDC", expected_revision: 0 };
    const fields = action === "register" ? { ...identity, cap: "28.500000000000000001", reason: "人工确认" } : identity;
    await writeLiveExecAdmin(action === "register"
      ? { resource: "external-liabilities", action, ...identity, cap: "28.500000000000000001", reason: "人工确认" }
      : { resource: "external-liabilities", action, ...identity });
    const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(fetch).toHaveBeenCalledOnce();
    expect(url).toContain(`/api/live-exec/external-liabilities/${action}`);
    expect(options).toMatchObject({ method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Live-Exec-CSRF": "1" } });
    expect(JSON.parse(String(options.body))).toEqual(fields);
    expect(Object.keys(options.headers!)).not.toContain("X-Api-Token");
  });

  it("surfaces conflicts without retrying", async () => {
    const fetch = vi.fn(async () => ({ ok: false, status: 409 }));
    vi.stubGlobal("fetch", fetch);
    await expect(writeLiveExecAdmin({ resource: "external-liabilities", action: "unregister",
      exchange_type: "HYPERLIQUID", coin: "USDC", expected_revision: 0 })).rejects.toBeInstanceOf(LiveAdminConflictError);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("distinguishes an upstream admin-token error from a missing browser session", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 401,
      json: async () => ({ detail: "实盘服务认证失败" }) })));
    const failure = await writeLiveExecAdmin({ resource: "external-liabilities", action: "unregister",
      exchange_type: "HYPERLIQUID", coin: "USDC", expected_revision: 0 }).catch(error => error);
    expect(failure.message).toBe("实盘服务认证失败");
    expect(failure).not.toBeInstanceOf(LiveAdminSessionError);
    expect(window.location.pathname).toBe("/");
  });

  it.each([401, 403])("keeps read-only navigation intact on HTTP %s", async status => {
    window.history.replaceState(null, "", "/?page=live-exec#liabilities");
    const expired = vi.fn(); window.addEventListener(AUTH_REQUIRED_EVENT, expired);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status, json: async () => ({}) })));
    try {
      expect((await getLiveExecAdmin("external-liabilities", new AbortController().signal)).available).toBe(false);
      await expect(writeLiveExecAdmin({ resource: "external-liabilities", action: "unregister",
        exchange_type: "HYPERLIQUID", coin: "USDC", expected_revision: 0 })).rejects.toThrow();
      expect(window.location.pathname + window.location.search + window.location.hash).toBe("/?page=live-exec#liabilities");
      expect(expired).not.toHaveBeenCalled();
    } finally { window.removeEventListener(AUTH_REQUIRED_EVENT, expired); }
  });
});
