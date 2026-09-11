import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/pages/DashboardPage", () => ({
  DashboardPage: () => <div>Dashboard</div>
}));

import { AppShell } from "../src/components/AppShell";

describe("AppShell appearance mode", () => {
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
});
