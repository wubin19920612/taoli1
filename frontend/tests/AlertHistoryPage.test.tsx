import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AlertHistoryPage } from "../src/pages/AlertHistoryPage";

describe("AlertHistoryPage", () => {
  it("identifies big spread alerts by their source", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([{
      id: "big-1", rule_id: "big_spread", opportunity_id: "imx", symbol: "IMXUSDT",
      status: "sent", message: "【大价差提醒】盘口深度薄", created_at: "2026-11-13T03:09:32Z"
    }])));
    render(<AlertHistoryPage />);
    expect(await screen.findByText("大价差提醒")).toBeTruthy();
    expect(screen.getByText("【大价差提醒】盘口深度薄")).toBeTruthy();
  });

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

  it("translates actual delivery statuses and preserves unknown values", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(
      ["sent", "failed", "muted", "unknown", "future-status", "toString"].map((status, index) => ({
        id: `evt-${index}`,
        symbol: "BTCUSDT",
        status,
        message: "status test",
        created_at: "2026-05-15T02:00:00Z"
      }))
    )));
    render(<AlertHistoryPage />);
    for (const label of ["已发送", "失败", "已静默", "结果未知", "future-status", "toString"]) {
      expect(await screen.findByText(label)).toBeTruthy();
    }
  });

  it("labels refresh and reloads history on demand", async () => {
    render(<AlertHistoryPage />);
    await screen.findByText("BTCUSDT");
    await userEvent.click(screen.getByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining("/alerts/events?limit=200"), expect.anything());
  });

  it("does not load while its embedded tab is inactive", async () => {
    const { rerender } = render(<AlertHistoryPage active={false} />);
    expect(fetch).not.toHaveBeenCalled();
    rerender(<AlertHistoryPage active />);
    expect(await screen.findByText("BTCUSDT")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
    rerender(<AlertHistoryPage active={false} />);
    expect(screen.getByText("BTCUSDT")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
