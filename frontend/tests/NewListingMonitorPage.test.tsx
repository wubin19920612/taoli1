import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NewListingMonitorPage } from "../src/pages/NewListingMonitorPage";

describe("NewListingMonitorPage", () => {
  beforeEach(() => {
    let enabled = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/new-listing-monitor/settings") && init?.method === "PUT") {
          enabled = Boolean(JSON.parse(String(init.body)).enabled);
          return Response.json({ enabled });
        }
        if (url.includes("/new-listing-monitor/status")) {
          return Response.json({
            enabled,
            running: true,
            watch_count: 1,
            enabled_watch_count: 1,
            active_watch_count: enabled ? 1 : 0,
            sample_count: 0,
            event_count: 0,
            latest_error: null,
            watchlist: [],
            latest_samples: [],
            latest_events: []
          });
        }
        if (url.includes("/new-listing-monitor/exchanges")) {
          return Response.json(["bybit", "gate"]);
        }
        return Response.json([]);
      })
    );
  });

  it("persists the master switch and reports monitoring as disabled", async () => {
    render(<NewListingMonitorPage />);

    const masterSwitch = await screen.findByRole("switch", { name: "新币极速总开关" });
    expect(masterSwitch.getAttribute("aria-checked")).toBe("true");

    await userEvent.click(masterSwitch);

    await waitFor(() => {
      const putCall = vi.mocked(fetch).mock.calls.find(
        ([input, init]) =>
          String(input).includes("/new-listing-monitor/settings") && init?.method === "PUT"
      );
      expect(putCall).toBeTruthy();
      expect(JSON.parse(String(putCall?.[1]?.body))).toEqual({ enabled: false });
    });
    expect((await screen.findAllByText("新币极速总开关已关闭")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("自动采样、公告预热和新币自动建卡均已停止；已有标的配置会保留。")).toBeTruthy();
  });
});
