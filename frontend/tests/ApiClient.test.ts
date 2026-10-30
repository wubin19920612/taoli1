import { afterEach, describe, expect, it, vi } from "vitest";

import { deletePairSpreadPreset, listOpportunities, lookupInstrument } from "../src/api/client";

function mockResponse(body: string, status: number, contentType: string): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(body, {
        status,
        headers: { "Content-Type": contentType }
      })
    )
  );
}

describe("API error messages", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it.each([
    [502, "后端服务暂时不可用，可能正在重启，请稍后重试。"],
    [503, "服务正在启动或暂时不可用，请稍后重试。"],
    [504, "后端服务响应超时，请稍后重试。"]
  ])("replaces an HTML %i proxy response with a readable message", async (status, message) => {
    mockResponse(
      `<html><head><title>${status} Bad Gateway</title></head><body>nginx</body></html>`,
      status,
      "text/html"
    );

    await expect(lookupInstrument("ZETAUSDT")).rejects.toThrow(message);
  });

  it("preserves a structured backend detail even when the status is 502", async () => {
    mockResponse(
      JSON.stringify({ detail: "Hyperliquid 公开状态接口暂时不可用" }),
      502,
      "application/json"
    );

    await expect(lookupInstrument("ZETAUSDT")).rejects.toThrow(
      "Hyperliquid 公开状态接口暂时不可用"
    );
  });

  it("preserves a plain-text business error", async () => {
    mockResponse("invalid symbol", 422, "text/plain");

    await expect(lookupInstrument("UNKNOWN")).rejects.toThrow("invalid symbol");
  });

  it("uses the same proxy error handling for direct API calls", async () => {
    mockResponse(
      "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>",
      502,
      "text/html"
    );

    await expect(listOpportunities({})).rejects.toThrow(
      "后端服务暂时不可用，可能正在重启，请稍后重试。"
    );
  });

  it("exposes the HTTP status and translates a dashboard password rejection", async () => {
    mockResponse(JSON.stringify({ detail: "Invalid dashboard password" }), 401, "application/json");

    await expect(deletePairSpreadPreset("BTC")).rejects.toMatchObject({
      status: 401,
      message: "本设备未保存有效的面板密码，请验证密码后重试。"
    });
  });

  it("tries a supplied password without overwriting the saved credential", async () => {
    window.localStorage.setItem("dashboard_password", "old-password");
    mockResponse(JSON.stringify({ ok: true }), 200, "application/json");

    await deletePairSpreadPreset("io:OAI/OPENAI", "candidate-password");

    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("io%3AOAI%2FOPENAI");
    expect(new Headers(options?.headers).get("X-Dashboard-Password")).toBe("candidate-password");
    expect(window.localStorage.getItem("dashboard_password")).toBe("old-password");
  });
});
