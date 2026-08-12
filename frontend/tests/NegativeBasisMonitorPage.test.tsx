import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NegativeBasisMonitorPage } from "../src/pages/NegativeBasisMonitorPage";

describe("NegativeBasisMonitorPage", () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    scrollIntoView.mockReset();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView
    });
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/negative-basis-monitor/exchanges")) {
          return Response.json({
            spot: ["binance"],
            future: ["gate"]
          });
        }
        if (url.includes("/negative-basis-monitor/status")) {
          return Response.json({
            running: true,
            auto_scan_enabled: true,
            auto_scan_settings: {
              enabled: true,
              blocked_exchanges: [],
              blocked_symbols: [],
              blocked_exchange_symbols: [],
              updated_at: "2026-08-12T00:00:00Z"
            },
            auto_scan_last_at: "2026-08-12T00:00:00Z",
            auto_scan_error: null,
            auto_candidate_count: 0,
            auto_candidates: [],
            watch_count: 0,
            enabled_watch_count: 0,
            sample_count: 0,
            event_count: 0,
            latest_error: null,
            watchlist: [],
            latest_samples: [],
            latest_events: []
          });
        }
        return Response.json({});
      })
    );
  });

  it("opens and scrolls to the advanced configuration from the toolbar", async () => {
    render(<NegativeBasisMonitorPage />);

    await screen.findByText("自动发现候选");
    await userEvent.click(screen.getByRole("button", { name: /^plus 高级配置$/ }));

    expect(await screen.findByText("监控参数")).toBeTruthy();
    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "smooth",
        block: "start"
      });
    });
  });
});
