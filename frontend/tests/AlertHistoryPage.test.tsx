import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AlertHistoryPage } from "../src/pages/AlertHistoryPage";

describe("AlertHistoryPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/alerts/test") && init?.method === "POST") {
          return Response.json({
            id: "evt-test",
            rule_id: "manual-test",
            opportunity_id: "manual-test",
            symbol: "TESTUSDT",
            status: "sent",
            message: "Manual alert test from dashboard",
            created_at: "2026-05-25T06:51:37Z"
          });
        }
        if (url.includes("/alerts/events")) {
          return Response.json([
            {
              id: "evt-1",
              rule_id: "rule-1",
              opportunity_id: "opp-1",
              symbol: "BTCUSDT",
              status: "sent",
              message:
                "价差对：BTCUSDT | binance future -> okx future\n" +
                "价差：开仓 0.800% / 平仓 0.500%\n" +
                "资金费率差（周期）：当前 -0.03% / 预测 0.01%\n" +
                "【连续监测】\n" +
                "1. 01:59:44 | 价差 0.720% | 净估算 0.520% | 资金差（周期） 0.01% | 综合 0.530%",
              created_at: "2026-05-15T02:00:00Z"
            }
          ]);
        }
        return Response.json([]);
      })
    );
  });

  it("renders alert messages as multiline history entries", async () => {
    render(<AlertHistoryPage />);

    const message = await screen.findByText(/价差对：BTCUSDT/);
    expect(message.textContent).toContain("\n");
    expect(message.getAttribute("style")).toContain("white-space: pre-wrap");
  });

  it("renders alert event creation time in UTC+8", async () => {
    render(<AlertHistoryPage />);

    expect(await screen.findByText("05-15 10:00:00")).toBeTruthy();
  });

  it("creates a test alert event from the toolbar", async () => {
    render(<AlertHistoryPage />);

    await userEvent.click(await screen.findByRole("button", { name: /测试告警/ }));

    expect(await screen.findByText("TESTUSDT")).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/alerts/test"),
      expect.objectContaining({ method: "POST" })
    );
  });
});
