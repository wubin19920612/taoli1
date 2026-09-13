import { cleanup, createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/pages/DashboardPage", () => ({
  DashboardPage: () => <div>Dashboard</div>
}));

import { AppShell } from "../src/components/AppShell";

const navigationOrderStorageKey = "taoli1:navigation-order.v1";
const defaultNavigationOrder = [
  "dashboard",
  "funding",
  "funding-research",
  "opportunity-radar",
  "instrument",
  "pair-monitor",
  "symbol-spread",
  "premium-index",
  "minute-signals",
  "negative-basis",
  "new-listing",
  "second-sampling",
  "fat-finger",
  "tradfi-perp",
  "gate-twap",
  "index-components",
  "announcements",
  "oil-news",
  "settings",
  "history"
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

    dragNavigationItem(dialog, "资金研究", "funding-research", "资金费率套利", 110);
    expect(within(dialog).getAllByRole("listitem").slice(0, 3).map((item) => item.textContent)).toEqual([
      expect.stringContaining("资金研究"),
      expect.stringContaining("资金费率套利"),
      expect.stringContaining("实时机会")
    ]);

    await userEvent.click(screen.getByRole("button", { name: "保存菜单顺序" }));
    expect(visibleMenuLabels().slice(0, 3)).toEqual(["资金研究", "资金费率套利", "实时机会"]);
    expect(JSON.parse(window.localStorage.getItem(navigationOrderStorageKey) ?? "[]").slice(0, 3)).toEqual([
      "funding-research",
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
    expect(visibleMenuLabels().slice(0, 3)).toEqual(["实时机会", "资金费率套利", "资金研究"]);
    expect(visibleMenuLabels().slice(-2)).toEqual(["参数与告警", "告警历史"]);
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
    window.localStorage.setItem(navigationOrderStorageKey, JSON.stringify(["history", "dashboard", "removed-page"]));
    render(<AppShell />);

    const labels = visibleMenuLabels();
    expect(labels.slice(0, 2)).toEqual(["告警历史", "实时机会"]);
    expect(labels).toHaveLength(20);
    expect(labels).toContain("参数与告警");
  });
});
