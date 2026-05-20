import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AlertHistoryPage } from "../src/pages/AlertHistoryPage";

describe("AlertHistoryPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
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
                "资金费率差：当前 -0.03% / 预测 0.01%\n" +
                "【连续监测】\n" +
                "1. 01:59:44 | 价差 0.720% | 净估算 0.520% | 资金差 0.01% | 综合 0.530%",
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
});
