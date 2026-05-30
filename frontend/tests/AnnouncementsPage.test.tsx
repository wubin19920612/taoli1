import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnnouncementsPage } from "../src/pages/AnnouncementsPage";

describe("AnnouncementsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/settings/announcements")) {
          if (method === "PUT") {
            return Response.json(JSON.parse(String(init?.body)));
          }
          return Response.json({
            enabled: true,
            poll_interval_seconds: 120,
            record_exchanges: ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
            alert_exchanges: ["bybit"],
            bootstrap_alerts_enabled: false
          });
        }
        if (url.includes("/announcements/exchanges")) {
          return Response.json([
            { label: "Binance", value: "binance" },
            { label: "OKX", value: "okx" },
            { label: "Bybit", value: "bybit" },
            { label: "Gate", value: "gate" },
            { label: "Bitget", value: "bitget" },
            { label: "Hyperliquid", value: "hyperliquid" }
          ]);
        }
        if (url.includes("/announcements")) {
          return Response.json([
            {
              id: "ann-1",
              exchange: "bybit",
              announcement_id: "new-listing",
              kind: "listing",
              title: "New listing: WDCUSDT Perpetual Contract",
              url: "https://announcements.bybit.com/en-US/article/new-listing/",
              source: "bybit-v5-announcements",
              category: "new_crypto",
              published_at: "2026-05-30T08:00:00Z",
              fetched_at: "2026-05-30T08:01:00Z",
              alert_status: "sent"
            }
          ]);
        }
        return Response.json([]);
      })
    );
  });

  it("loads announcement settings and renders recorded listing announcements", async () => {
    render(<AnnouncementsPage />);

    expect(await screen.findByText("上币/下币公告监控")).toBeTruthy();
    expect(await screen.findByText("New listing: WDCUSDT Perpetual Contract")).toBeTruthy();
    expect(screen.getAllByText("Bybit").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("上币")).toBeTruthy();
    expect(screen.getByText("sent")).toBeTruthy();

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
      expect(calls.some((url) => url.includes("/settings/announcements"))).toBe(true);
      expect(calls.some((url) => url.includes("/announcements/exchanges"))).toBe(true);
      expect(calls.some((url) => url.includes("/announcements?"))).toBe(true);
    });
  });

  it("saves announcement monitoring settings", async () => {
    render(<AnnouncementsPage />);

    await screen.findByText("New listing: WDCUSDT Perpetual Contract");
    await userEvent.click(screen.getByRole("button", { name: /保存公告监控/ }));

    await waitFor(() => {
      const putCall = vi
        .mocked(fetch)
        .mock.calls.find((call) => String(call[0]).includes("/settings/announcements") && call[1]?.method === "PUT");
      expect(putCall).toBeTruthy();
      expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({
        enabled: true,
        poll_interval_seconds: 120,
        record_exchanges: ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"],
        alert_exchanges: ["bybit"],
        bootstrap_alerts_enabled: false
      });
    });
  });
});
