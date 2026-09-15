import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MinuteSignalPage } from "../src/pages/MinuteSignalPage";

const savedSettings = {
  hours: 6,
  max_symbols: 20,
  min_volume_24h_usdt: 300_000,
  alert_cooldown_minutes: 30,
  max_entry_basis_bps: 10,
  require_negative_premium_when_spot_above: true,
  max_premium_when_spot_above_bps: -15
};

function scanResult(urlText: string) {
  const url = new URL(urlText);
  return {
    observed_at: "2026-07-27T01:00:00Z",
    hours: Number(url.searchParams.get("hours") ?? 6),
    max_symbols: Number(url.searchParams.get("max_symbols") ?? 20),
    min_volume_24h_usdt: Number(url.searchParams.get("min_volume_24h_usdt") ?? 300_000),
    alert_cooldown_minutes: Number(url.searchParams.get("alert_cooldown_minutes") ?? 30),
    max_entry_basis_bps: Number(url.searchParams.get("max_entry_basis_bps") ?? 10),
    require_negative_premium_when_spot_above:
      url.searchParams.get("require_negative_premium_when_spot_above") !== "false",
    max_premium_when_spot_above_bps: Number(
      url.searchParams.get("max_premium_when_spot_above_bps") ?? -15
    ),
    universe_count: 5,
    eligible_count: 2,
    filtered_by_basis_count: 1,
    filtered_by_premium_count: 2,
    scanned_count: 2,
    signal_count: 0,
    error_count: 0,
    candidates: [],
    warnings: ["测试扫描范围"]
  };
}

describe("MinuteSignalPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/minute-signals") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/minute-signals")) {
          return Response.json(savedSettings);
        }
        if (url.includes("/minute-signals/scan-all")) {
          return Response.json(scanResult(url));
        }
        return Response.json({});
      })
    );
  });

  it("loads, saves, and scans with editable basis and premium filters", async () => {
    render(<MinuteSignalPage />);

    expect(await screen.findByDisplayValue("10")).toBeTruthy();
    expect(screen.getByDisplayValue("-15")).toBeTruthy();
    expect(await screen.findByText("basis 过滤 1 个")).toBeTruthy();
    expect(screen.getByText("premium 过滤 2 个")).toBeTruthy();

    const basisLimit = screen.getByLabelText("入场 basis 上限");
    await userEvent.clear(basisLimit);
    await userEvent.type(basisLimit, "0");
    await userEvent.click(screen.getByRole("button", { name: /保存参数/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/minute-signals"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"max_entry_basis_bps":0')
        })
      );
    });
    expect(
      vi.mocked(fetch).mock.calls.some(([input]) => {
        const url = String(input);
        return url.includes("/minute-signals/scan-all") && url.includes("max_entry_basis_bps=0");
      })
    ).toBe(true);
  }, 15000);
});
