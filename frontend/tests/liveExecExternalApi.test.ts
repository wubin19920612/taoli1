import { afterEach, describe, expect, it, vi } from "vitest";
import { getLiveExecAdmin, LiveAdminConflictError, writeLiveExecAdmin } from "../src/api/liveExec";

const identity = { route: "crossex", exchange_type: "HYPERLIQUID", business: "FUTURE",
  symbol: "HYPERLIQUID_FUTURE_io:OAI_USDC", quote: "USDC", dex: "io" };
const status = "x1:" + "a".repeat(64);

afterEach(() => vi.unstubAllGlobals());

describe("外仓 API", () => {
  it("accepts the round eleven object and preserves later optional fields", async () => {
    const data = { status, contracts: [], income: null, flatten_status: null, projection: null,
      future_optional_field: { amount: 0 } };
    const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ available: true, data }) }));
    vi.stubGlobal("fetch", fetch);
    expect(await getLiveExecAdmin("external-positions", new AbortController().signal)).toEqual({ available: true, data });
    expect((fetch.mock.calls[0] as unknown as [string])[0]).toContain("/api/live-exec/external-positions");
  });

  it.each([null, [], {}, { contracts: null }])("rejects invalid external resource shape %s", async data => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ available: true, data }) })));
    expect(await getLiveExecAdmin("external-positions", new AbortController().signal)).toEqual({ available: false, error: "invalid_json" });
  });

  it.each(["register", "unregister"] as const)("sends %s with exact identity, original state and CSRF", async action => {
    const fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ available: true, data: {} }) }));
    vi.stubGlobal("fetch", fetch);
    await writeLiveExecAdmin({ resource: "external-positions", action, identity, expected_status: status, reason: "人工核对" });
    expect(fetch).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain(`/api/live-exec/external-positions/${action}`);
    expect(options).toMatchObject({ method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Live-Exec-CSRF": "1" } });
    expect(JSON.parse(String(options.body))).toEqual({ identity, expected_status: status, reason: "人工核对" });
    expect(Object.keys(options.headers!)).not.toContain("X-Api-Token");
  });

  it.each(["external_conflict", "external_remote_position"])("does not retry a 409 %s", async error => {
    const fetch = vi.fn(async () => ({ ok: false, status: 409, json: async () => ({ error }) }));
    vi.stubGlobal("fetch", fetch);
    await expect(writeLiveExecAdmin({ resource: "external-positions", action: "unregister", identity,
      expected_status: status, reason: "人工核对" })).rejects.toBeInstanceOf(LiveAdminConflictError);
    expect(fetch).toHaveBeenCalledOnce();
  });
});
