import { act, cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/pages/DashboardPage", () => ({
  DashboardPage: () => <div>Dashboard</div>
}));
vi.mock("../src/pages/GateTwapPage", () => ({ GateTwapPage: () => <div>Gate TWAP</div> }));
vi.mock("../src/pages/LiveExecPage", () => ({ LiveExecPage: () => <div>实盘页面测试</div> }));

import { AppShell } from "../src/components/AppShell";

const navigationOrderStorageKey = "taoli1:navigation-order.v1";
const defaultNavigationOrder = [
  "dashboard",
  "funding",
  "instrument",
  "pair-monitor",
  "premium-index",
  "gate-twap",
  "index-components",
  "shadow-ledger",
  "live-exec",
  "announcements",
  "account-connections",
  "settings"
];
const retiredPages = [
  ["fat-finger", "乌龙回测"],
  ["funding-research", "资金研究"],
  ["negative-basis", "负基差埋伏"],
  ["oil-news", "原油新闻"],
  ["opportunity-radar", "机会雷达"],
  ["second-sampling", "1s 采样"],
  ["squeeze-arbitrage", "挤仓结构观察"],
  ["symbol-spread", "跨所价差"],
  ["spread-events", "价差事件榜"],
  ["tradfi-perp", "TradFi 价差"],
  ["fs-monitor", "负费率 FS 观察"]
];

function visibleMenuLabels(): string[] {
  return screen.getAllByRole("menuitem").map((item) => item.textContent?.trim() ?? "");
}

async function openNavigationDialog(): Promise<HTMLElement> {
  await userEvent.click(screen.getByRole("button", { name: "调整菜单顺序" }));
  return screen.getByRole("dialog", { name: "调整菜单顺序" });
}

function dragNavigationItem(
  dialog: HTMLElement,
  sourceLabel: string,
  sourceKey: string,
  targetLabel: string,
  clientY: number
): void {
  const source = within(dialog).getByText(sourceLabel).closest("li");
  const target = within(dialog).getByText(targetLabel).closest("li");
  if (!source || !target) throw new Error("Navigation row not found");
  vi.spyOn(target, "getBoundingClientRect").mockReturnValue({
    top: 100,
    bottom: 140,
    height: 40,
    left: 0,
    right: 400,
    width: 400,
    x: 0,
    y: 100,
    toJSON: () => ({})
  });
  const dataTransfer = {
    effectAllowed: "none",
    dropEffect: "none",
    setData: vi.fn(),
    getData: vi.fn(() => sourceKey)
  };
  fireEvent.dragStart(source, { dataTransfer });
  fireEvent.dragOver(target, { dataTransfer, clientY });
  const dropEvent = createEvent.drop(target, { dataTransfer });
  Object.defineProperty(dropEvent, "clientY", { value: clientY });
  fireEvent(target, dropEvent);
}

describe("AppShell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("places live execution after the shadow ledger for saved menus and lazy-loads it", async () => {
    localStorage.setItem(navigationOrderStorageKey, JSON.stringify(defaultNavigationOrder.filter(key => key !== "live-exec")));
    render(<AppShell />);
    const labels = visibleMenuLabels();
    expect(labels[labels.indexOf("影子账本") + 1]).toBe("实盘");
    await userEvent.click(screen.getByRole("menuitem", { name: "实盘" }));
    expect(await screen.findByText("实盘页面测试")).toBeTruthy();
    expect(new URLSearchParams(window.location.search).get("page")).toBe("live-exec");
  });

  it("switches to the quiet palette and persists the selection", async () => {
    const { container, unmount } = render(<AppShell />);

    expect(container.querySelector(".app-shell-standard")).toBeTruthy();
    await userEvent.click(screen.getByText("低调"));

    expect(container.querySelector(".app-shell-quiet")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "数据工作台" })).toBeTruthy();
    expect(window.localStorage.getItem("taoli1:appearance-mode")).toBe("quiet");

    unmount();
    const secondRender = render(<AppShell />);
    await waitFor(() => {
      expect(secondRender.container.querySelector(".app-shell-quiet")).toBeTruthy();
    });
  });

  it("restores the original palette from the same control", async () => {
    window.localStorage.setItem("taoli1:appearance-mode", "quiet");
    const { container } = render(<AppShell />);

    await userEvent.click(screen.getByText("原配色"));

    expect(container.querySelector(".app-shell-standard")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "CEX 套利雷达" })).toBeTruthy();
    expect(window.localStorage.getItem("taoli1:appearance-mode")).toBe("standard");
  });

  it("manually reorders navigation items and restores the saved order", async () => {
    const firstRender = render(<AppShell />);

    const dialog = await openNavigationDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "下移 实时机会" }));
    await userEvent.click(screen.getByRole("button", { name: "保存菜单顺序" }));

    expect(visibleMenuLabels().slice(0, 2)).toEqual(["资金费率套利", "实时机会"]);
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]").slice(0, 2)).toEqual([
      "funding",
      "dashboard"
    ]);

    firstRender.unmount();
    render(<AppShell />);
    expect(visibleMenuLabels().slice(0, 2)).toEqual(["资金费率套利", "实时机会"]);
  });

  it("reorders navigation by dropping before or after a target row", async () => {
    render(<AppShell />);
    const dialog = await openNavigationDialog();

    dragNavigationItem(dialog, "实时机会", "dashboard", "资金费率套利", 130);
    expect(within(dialog).getAllByRole("listitem").slice(0, 2).map((item) => item.textContent)).toEqual([
      expect.stringContaining("资金费率套利"),
      expect.stringContaining("实时机会")
    ]);

    dragNavigationItem(dialog, "标的查询", "instrument", "资金费率套利", 110);
    expect(within(dialog).getAllByRole("listitem").slice(0, 3).map((item) => item.textContent)).toEqual([
      expect.stringContaining("标的查询"),
      expect.stringContaining("资金费率套利"),
      expect.stringContaining("实时机会")
    ]);

    await userEvent.click(screen.getByRole("button", { name: "保存菜单顺序" }));
    expect(visibleMenuLabels().slice(0, 3)).toEqual(["标的查询", "资金费率套利", "实时机会"]);
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]").slice(0, 3)).toEqual([
      "instrument",
      "funding",
      "dashboard"
    ]);
  });

  it("restores the complete default navigation order after confirmation", async () => {
    window.localStorage.setItem(navigationOrderStorageKey, JSON.stringify(["history", "settings", "dashboard"]));
    render(<AppShell />);
    await openNavigationDialog();

    await userEvent.click(screen.getByRole("button", { name: "恢复默认菜单顺序" }));
    await userEvent.click(screen.getByRole("button", { name: "保存菜单顺序" }));

    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]")).toEqual(defaultNavigationOrder);
    expect(visibleMenuLabels().slice(0, 3)).toEqual(["实时机会", "资金费率套利", "标的查询"]);
    expect(visibleMenuLabels().slice(-2)).toEqual(["账户连接", "参数与告警"]);
    expect(visibleMenuLabels()).not.toContain("告警历史");
  });

  it("discards draft changes when canceled or closed", async () => {
    render(<AppShell />);
    let dialog = await openNavigationDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "下移 实时机会" }));
    await userEvent.click(screen.getByRole("button", { name: "取消调整菜单顺序" }));

    expect(visibleMenuLabels().slice(0, 2)).toEqual(["实时机会", "资金费率套利"]);
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]")).toEqual(defaultNavigationOrder);

    dialog = await openNavigationDialog();
    expect(within(dialog).getAllByRole("listitem")[0].textContent).toContain("实时机会");
    await userEvent.click(within(dialog).getByRole("button", { name: "下移 实时机会" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    expect(visibleMenuLabels().slice(0, 2)).toEqual(["实时机会", "资金费率套利"]);
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]")).toEqual(defaultNavigationOrder);
  });

  it("ignores invalid saved entries and appends newly available navigation items", () => {
    window.localStorage.setItem(navigationOrderStorageKey, JSON.stringify([
      "history", "dashboard", "minute-signals", "new-listing", "removed-page"
    ]));
    render(<AppShell />);

    const labels = visibleMenuLabels();
    expect(labels.slice(0, 2)).toEqual(["实时机会", "资金费率套利"]);
    expect(labels).not.toContain("告警历史");
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]")).toEqual(defaultNavigationOrder);
    expect(labels).toHaveLength(defaultNavigationOrder.length);
    expect(labels).not.toContain("1 分钟价差信号");
    expect(labels).not.toContain("新币极速");
    expect(labels).toContain("账户连接");
    expect(labels).toContain("Gate 定时减仓");
    expect(labels).toContain("参数与告警");
  });

  it("removes all retired pages from menus and saved order while preserving retained ordering", async () => {
    window.localStorage.setItem(navigationOrderStorageKey, JSON.stringify([
      ...retiredPages.map(([key]) => key), "settings", "dashboard", "settings", "unknown-page"
    ]));
    render(<AppShell />);

    expect(visibleMenuLabels().slice(0, 2)).toEqual(["参数与告警", "实时机会"]);
    expect(visibleMenuLabels()).toHaveLength(defaultNavigationOrder.length);
    expect(screen.getByText("Dashboard")).toBeTruthy();
    const dialog = await openNavigationDialog();
    for (const [key, label] of retiredPages) {
      expect(screen.queryByRole("menuitem", { name: label })).toBeNull();
      expect(within(dialog).queryByText(label)).toBeNull();
      expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]")).not.toContain(key);
    }
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]")).toEqual([
      "settings", "dashboard", ...defaultNavigationOrder.filter((key) => key !== "settings" && key !== "dashboard")
    ]);
  });

  it.each([...retiredPages.map(([key]) => key), "minute-signals", "new-listing"])("opens the default page for retired URL %s", (page) => {
    window.history.replaceState({}, "", `/?page=${page}`);
    render(<AppShell />);

    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /实时机会/ }).classList.contains("ant-menu-item-selected")).toBe(true);
    expect(visibleMenuLabels()).toHaveLength(defaultNavigationOrder.length);
  });

  it.each(["popstate", "taoli1:navigate"])("falls back from every retired URL through %s after another page mounts", async (eventName) => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ disabled: [], symbols: [], pair_ids: [] })));
    render(<AppShell />);

    for (const [key] of retiredPages) {
      act(() => {
        window.history.pushState({}, "", "/?page=gate-twap");
        window.dispatchEvent(new Event(eventName));
      });
      expect(await screen.findByText("Gate TWAP")).toBeTruthy();
      act(() => {
        window.history.pushState({}, "", `/?page=${key}`);
        window.dispatchEvent(new Event(eventName));
      });
      expect(screen.getByText("Dashboard")).toBeTruthy();
      expect(screen.getByRole("menuitem", { name: /实时机会/ }).classList.contains("ant-menu-item-selected")).toBe(true);
    }
  });

  it("opens the settings history tab for a legacy history bookmark", async () => {
    window.localStorage.setItem("settings-active-tab", "risk");
    window.history.replaceState({}, "", "/?page=history&symbol=BTCUSDT#bookmark");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
    render(<AppShell />);

    const historyTab = await screen.findByRole("tab", { name: "告警历史" }, { timeout: 10000 });
    expect(historyTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("menuitem", { name: /参数与告警/ }).classList.contains("ant-menu-item-selected")).toBe(true);
    expect(visibleMenuLabels()).not.toContain("告警历史");
    expect(window.localStorage.getItem("settings-active-tab")).toBe("history");
    expect(window.location.search).toContain("symbol=BTCUSDT");
    expect(window.location.hash).toBe("#bookmark");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/alerts/events"))).toBe(true));
  }, 15000);

  it.each(["popstate", "taoli1:navigate"])("handles legacy history through %s while settings is already mounted", async (eventName) => {
    window.history.replaceState({}, "", "/?page=settings");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([])));
    render(<AppShell />);
    await screen.findByRole("tab", { name: "告警规则" }, { timeout: 10000 });
    const ruleName = screen.getByLabelText("告警规则名称") as HTMLInputElement;
    await waitFor(() => expect(ruleName.disabled).toBe(false));
    await userEvent.type(ruleName, "Preserved draft");
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/alerts/events"))).toBe(false);

    act(() => {
      window.history.pushState({}, "", "/?page=history");
      window.dispatchEvent(new Event(eventName));
    });
    await waitFor(() => expect(screen.getByRole("tab", { name: "告警历史" }).getAttribute("aria-selected")).toBe("true"));
    await userEvent.click(screen.getByRole("tab", { name: "告警规则" }));
    expect(screen.getByLabelText("告警规则名称")).toBe(ruleName);
    expect(ruleName.value).toBe("Preserved draft");
  }, 15000);

  it("renders only the dedicated watch panel for the standalone window URL", async () => {
    window.history.replaceState({}, "", "/?floating_watch=standalone");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ symbols: [], pair_ids: [] })));
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);

    const { container } = render(<AppShell />);

    expect(screen.getByRole("complementary", { name: "独立关注窗口" })).not.toBeNull();
    expect(container.querySelector(".floating-watch-standalone-shell")).not.toBeNull();
    expect(screen.queryByRole("heading", { name: "CEX 套利雷达" })).toBeNull();
    expect(screen.queryByRole("menu")).toBeNull();

    window.localStorage.setItem("taoli1:floating-watch-visible.v1", "0");
    window.dispatchEvent(new CustomEvent("taoli1:floating-watch-updated", {
      detail: { symbols: [], pair_ids: [] }
    }));
    expect(window.localStorage.getItem("taoli1:floating-watch-visible.v1")).toBe("0");

    await userEvent.click(screen.getByRole("button", { name: "关闭独立关注窗口" }));
    await waitFor(() => {
      expect(container.querySelector(".floating-watch-standalone-shell")).toBeNull();
    });
    expect(screen.getByRole("heading", { name: "CEX 套利雷达" })).not.toBeNull();
    expect(new URLSearchParams(window.location.search).get("floating_watch")).toBeNull();
    expect(new URLSearchParams(window.location.search).get("page")).toBe("dashboard");
    close.mockRestore();
  });

  it("dispatches navigation for consecutive same-page selections from the dedicated watch window", async () => {
    render(<AppShell />);
    const navigations: string[] = [];
    const recordNavigation = () => navigations.push(window.location.search);
    window.addEventListener("taoli1:navigate", recordNavigation);

    window.dispatchEvent(new MessageEvent("message", {
      origin: window.location.origin,
      data: {
        type: "taoli1:floating-watch-navigate",
        destination: "/?page=dashboard&symbol=BTCUSDT"
      }
    }));
    window.dispatchEvent(new MessageEvent("message", {
      origin: window.location.origin,
      data: {
        type: "taoli1:floating-watch-navigate",
        destination: "/?page=dashboard&symbol=ETHUSDT"
      }
    }));

    await waitFor(() => {
      expect(new URLSearchParams(window.location.search).get("symbol")).toBe("ETHUSDT");
    });
    expect(new URLSearchParams(window.location.search).get("page")).toBe("dashboard");
    expect(navigations).toHaveLength(2);
    window.removeEventListener("taoli1:navigate", recordNavigation);
  });

  it("does not reopen the embedded watch after a delayed initial settings response", async () => {
    let resolveSettings: ((response: Response) => void) | undefined;
    const settingsResponse = new Promise<Response>((resolve) => {
      resolveSettings = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(() => settingsResponse));
    render(<AppShell />);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    window.localStorage.setItem("taoli1:floating-watch-visible.v1", "0");
    await act(async () => {
      resolveSettings?.(Response.json({ symbols: ["BTCUSDT"], pair_ids: [] }));
      await settingsResponse;
    });
    expect(window.localStorage.getItem("taoli1:floating-watch-visible.v1")).toBe("0");
    expect(screen.queryByRole("complementary", { name: "关注浮窗" })).toBeNull();
  });
});
