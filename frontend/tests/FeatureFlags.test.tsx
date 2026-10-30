import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mounts = vi.hoisted(() => ({ oil: vi.fn() }));
vi.mock("../src/pages/DashboardPage", () => ({ DashboardPage: () => <div>Dashboard</div> }));
vi.mock("../src/pages/OilNewsPage", () => ({
  OilNewsPage: () => { mounts.oil(); return <div>Oil page mounted</div>; }
}));
vi.mock("../src/components/FloatingWatchPanel", () => ({ FloatingWatchPanel: () => null }));
vi.mock("../src/pages/SqueezeArbitragePage", () => ({ SqueezeArbitragePage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/NegativeBasisMonitorPage", () => ({ NegativeBasisMonitorPage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/OpportunityRadarPage", () => ({ OpportunityRadarPage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/SymbolSpreadPage", () => ({ SymbolSpreadPage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/FatFingerBacktestPage", () => ({ FatFingerBacktestPage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/GateTwapPage", () => ({ GateTwapPage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/SecondLevelSamplingPage", () => ({ SecondLevelSamplingPage: () => <div>Enabled page</div> }));
vi.mock("../src/pages/FundingResearchPage", () => ({ FundingResearchPage: () => <div>Enabled page</div> }));

import { AppShell } from "../src/components/AppShell";

const features = [
  ["squeeze", "squeeze-arbitrage", "挤仓结构观察"],
  ["negative_basis", "negative-basis", "负基差埋伏"],
  ["oil_news", "oil-news", "原油新闻"],
  ["opportunity_radar", "opportunity-radar", "机会雷达"],
  ["symbol_spread", "symbol-spread", "跨所价差"],
  ["fat_finger", "fat-finger", "乌龙回测"],
  ["gate_twap", "gate-twap", "Gate 定时减仓"],
  ["second_level_sampling", "second-sampling", "1s 采样"],
  ["funding_research", "funding-research", "资金研究"]
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
  mounts.oil.mockClear();
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
    const order = JSON.stringify(["oil-news", "dashboard"]);
    window.localStorage.setItem("taoli1:navigation-order.v1", order);
    mockFeatures(features.map(([key]) => key));
    render(<AppShell />);
    await screen.findByText("Dashboard");
    await waitFor(() => {
      for (const [, , label] of features) expect(screen.queryByRole("menuitem", { name: new RegExp(label) })).toBeNull();
    });
    expect(JSON.parse(window.localStorage.getItem("taoli1:navigation-order.v1")!)[0]).toBe("oil-news");
  });

  it.each([false, true])("shows all menus with empty flags or failure: %s", async (fail) => {
    const fetchMock = mockFeatures([], fail);
    window.history.replaceState({}, "", "/?page=oil-news");
    render(<AppShell />);
    await screen.findByText("Oil page mounted");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/features"))).toBe(true);
    for (const [, , label] of features) expect(screen.getByRole("menuitem", { name: new RegExp(label) })).toBeTruthy();
  });

  it("does not mount a direct page while flags are pending or after disabling", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => String(input).includes("/features")
      ? pending : Promise.resolve({ ok: true, json: async () => ({ symbols: [], pair_ids: [] }) })));
    window.history.replaceState({}, "", "/?page=oil-news");
    render(<AppShell />);
    await act(async () => { await Promise.resolve(); });
    expect(mounts.oil).not.toHaveBeenCalled();
    await act(async () => resolve({ ok: true, json: async () => ({ disabled: ["oil_news"] }) }));
    expect(await screen.findByText("该功能已停用")).toBeTruthy();
    expect(mounts.oil).not.toHaveBeenCalled();
    window.history.pushState({}, "", "/?page=dashboard");
    fireEvent(window, new PopStateEvent("popstate"));
    expect(await screen.findByText("Dashboard")).toBeTruthy();
    window.history.pushState({}, "", "/?page=oil-news");
    fireEvent(window, new Event("taoli1:navigate"));
    expect(await screen.findByText("该功能已停用")).toBeTruthy();
  });
});
