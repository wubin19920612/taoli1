import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mounts = vi.hoisted(() => ({ gate: vi.fn() }));
vi.mock("../src/pages/DashboardPage", () => ({ DashboardPage: () => <div>Dashboard</div> }));
vi.mock("../src/pages/GateTwapPage", () => ({
  GateTwapPage: () => { mounts.gate(); return <div>Gate page mounted</div>; }
}));
vi.mock("../src/components/FloatingWatchPanel", () => ({ FloatingWatchPanel: () => null }));

import { AppShell } from "../src/components/AppShell";

const features = [
  ["gate_twap", "gate-twap", "Gate 定时减仓"]
];

function mockFeatures(disabled: string[], fail = false) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/features")) {
      if (fail) throw new Error("offline");
      return { ok: true, json: async () => ({ disabled }) };
    }
    return { ok: true, json: async () => ({ symbols: [], pair_ids: [] }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  mounts.gate.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

describe("feature flags", () => {
  it.each(features)("hides %s and guards its direct page", async (feature, page, label) => {
    mockFeatures([feature]);
    window.history.replaceState({}, "", `/?page=${page}`);
    render(<AppShell />);
    expect(await screen.findByText("该功能已停用")).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: new RegExp(label) })).toBeNull();
    expect(screen.getByRole("menuitem", { name: /价差查询/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "调整菜单顺序" }));
    expect(within(screen.getByRole("dialog")).queryByText(label)).toBeNull();
  });

  it("hides all disabled menus while preserving saved order for re-enable", async () => {
    const order = JSON.stringify(["gate-twap", "dashboard"]);
    window.localStorage.setItem("taoli1:navigation-order.v1", order);
    mockFeatures(features.map(([key]) => key));
    render(<AppShell />);
    await screen.findByText("Dashboard");
    await waitFor(() => {
      for (const [, , label] of features) expect(screen.queryByRole("menuitem", { name: new RegExp(label) })).toBeNull();
    });
    expect(JSON.parse(window.localStorage.getItem("taoli1:navigation-order.v1")!)[0]).toBe("gate-twap");
  });

  it.each([false, true])("shows all menus with empty flags or failure: %s", async (fail) => {
    const fetchMock = mockFeatures([], fail);
    window.history.replaceState({}, "", "/?page=gate-twap");
    render(<AppShell />);
    await screen.findByText("Gate page mounted");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/features"))).toBe(true);
    for (const [, , label] of features) expect(screen.getByRole("menuitem", { name: new RegExp(label) })).toBeTruthy();
  });

  it("does not mount a direct page while flags are pending or after disabling", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).includes("/features")
      ? pending : Promise.resolve({ ok: true, json: async () => ({ symbols: [], pair_ids: [] }) })));
    window.history.replaceState({}, "", "/?page=gate-twap");
    render(<AppShell />);
    await act(async () => { await Promise.resolve(); });
    expect(mounts.gate).not.toHaveBeenCalled();
    await act(async () => resolve({ ok: true, json: async () => ({ disabled: ["gate_twap"] }) }));
    expect(await screen.findByText("该功能已停用")).toBeTruthy();
    expect(mounts.gate).not.toHaveBeenCalled();
    window.history.pushState({}, "", "/?page=dashboard");
    fireEvent(window, new PopStateEvent("popstate"));
    expect(await screen.findByText("Dashboard")).toBeTruthy();
    window.history.pushState({}, "", "/?page=gate-twap");
    fireEvent(window, new Event("taoli1:navigate"));
    expect(await screen.findByText("该功能已停用")).toBeTruthy();
  });
});
