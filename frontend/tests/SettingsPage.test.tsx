import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../src/pages/SettingsPage";

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/risk") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 1000000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: ["AIUSDT"]
          });
        }
        if (url.includes("/alerts/rules") && init?.method === "POST") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/alerts/rules")) {
          return Response.json([]);
        }
        return Response.json([]);
      })
    );
  });

  it("loads risk settings and can submit an alert rule", async () => {
    render(<SettingsPage />);

    expect(await screen.findByDisplayValue("1000000")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("规则名称"), "FF 价差");
    await userEvent.clear(screen.getByLabelText("开仓阈值"));
    await userEvent.type(screen.getByLabelText("开仓阈值"), "0.5");
    await userEvent.click(screen.getByRole("button", { name: "新增规则" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/alerts/rules"),
        expect.objectContaining({ method: "POST" })
      );
    });
  });
});
