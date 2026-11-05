import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthGate } from "../src/components/AuthGate";
import { LoginDevices } from "../src/components/LoginDevices";
import { AUTH_REQUIRED_EVENT, clearLegacyPasswords, loginReturnPath, redirectToLogin, sessionFetch } from "../src/api/auth";
import { listMarkets, listOpportunities, logout } from "../src/api/client";

describe("session login", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/?page=pair-monitor&leg1_dex=io#chart");
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
  afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

  it("clears old credentials from both stores and preserves unrelated preferences", () => {
    for (const storage of [localStorage, sessionStorage]) {
      storage.setItem("dashboard_password", "synthetic-obsolete-value");
      storage.setItem("preference", "keep");
    }
    render(<AuthGate><div>业务页面</div></AuthGate>);
    for (const storage of [localStorage, sessionStorage]) {
      expect(storage.getItem("dashboard_password")).toBeNull();
      expect(storage.getItem("preference")).toBe("keep");
    }
  });

  it("survives unavailable storage", () => {
    const mock = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(() => clearLegacyPasswords()).not.toThrow();
    mock.mockRestore();
  });

  it("redirects a 401 and returns to the exact page after password plus code login", async () => {
    const initial = location.pathname + location.search + location.hash;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("/auth/login")
      ? Response.json({ ok: true }) : Response.json({ detail: "Authentication required" }, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>业务页面</div></AuthGate>);
    await act(async () => { await sessionFetch("/api/private"); });
    expect(screen.queryByText("业务页面")).toBeNull();
    expect(location.pathname).toBe("/login");
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "synthetic-login-value" } });
    fireEvent.change(screen.getByLabelText("6 位验证码"), { target: { value: "123456" } });
    fireEvent.submit(screen.getByLabelText("密码").closest("form")!);
    await screen.findByText("业务页面");
    expect(location.pathname + location.search + location.hash).toBe(initial);
    const options = fetchMock.mock.calls.slice(-1)[0]?.[1] as RequestInit | undefined;
    // No secrets in snapshots; only test structural properties.
    expect(options?.credentials).toBe("same-origin");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("keeps login failures on the login page and clears the inputs", async () => {
    redirectToLogin();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "密码或验证码错误" }, { status: 401 })));
    render(<AuthGate><div>private</div></AuthGate>);
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "invalid" } });
    fireEvent.change(screen.getByLabelText("6 位验证码"), { target: { value: "123456" } });
    fireEvent.submit(screen.getByLabelText("密码").closest("form")!);
    await screen.findByRole("alert");
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("6 位验证码") as HTMLInputElement).value).toBe("");
    expect(location.pathname).toBe("/login");
  });

  it.each(["https://evil.test/", "//evil.test/", "/login"])("rejects unsafe return targets", (next) => {
    history.replaceState(null, "", `/login?next=${encodeURIComponent(next)}`);
    expect(loginReturnPath()).toBe("/");
  });

  it("keeps a rate-limited login on the form without storing or echoing credentials", async () => {
    redirectToLogin();
    const fetchMock = vi.fn(async () => Response.json({ detail: "登录尝试过于频繁，请稍后重试" },
      { status: 429, headers: { "Retry-After": "60" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<AuthGate><div>private</div></AuthGate>);
    const values = ["synthetic-private-input", "654321"];
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: values[0] } });
    fireEvent.change(screen.getByLabelText("6 位验证码"), { target: { value: values[1] } });
    fireEvent.submit(screen.getByLabelText("密码").closest("form")!);
    const alert = await screen.findByRole("alert");
    expect(values.every(value => !alert.textContent?.includes(value))).toBe(true);
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("6 位验证码") as HTMLInputElement).value).toBe("");
    expect(location.pathname).toBe("/login");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.length + sessionStorage.length).toBe(0);
  });

  it.each([() => listMarkets(), () => listOpportunities({}), () => logout()])("redirects all API call styles on 401", async (call) => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({}, { status: 401 })));
    await expect(call()).rejects.toThrow();
    expect(location.pathname).toBe("/login");
  });

  it("does not send a request to another origin", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    await expect(sessionFetch("https://evil.test/api")).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([false, true])("lists devices and revokes the selected scope", async (all) => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "POST"
      ? Response.json({ ok: true }) : Response.json([
        { user_agent: "Chrome / Windows", ip: "192.0.2.1", current: true, created_at: 1, last_seen_at: 2, expires_at: 3 },
        { user_agent: "Safari / iOS", ip: "192.0.2.2", current: false, created_at: 1, last_seen_at: 2, expires_at: 3 }
      ]));
    vi.stubGlobal("fetch", fetchMock);
    render(<LoginDevices />);
    await screen.findByText("Chrome / Windows（当前设备）");
    expect(screen.getByText("Safari / iOS")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: all ? "退出所有设备" : "退出登录" }));
    await waitFor(() => expect(location.pathname).toBe("/login"));
    const endpoint = all ? "/auth/sessions/revoke-all" : "/auth/logout";
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith(endpoint) && init?.method === "POST")).toBe(true);
  });
});
