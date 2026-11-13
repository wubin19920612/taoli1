import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AlertHistoryPage } from "../src/pages/AlertHistoryPage";

describe("AlertHistoryPage", () => {
  it.each([["1 天", 24], ["7 天", 168], ["永久", null]] as const)("mutes a historical route for %s with its original market context", async (label, hours) => {
    const routeMessage = "【大价差提醒】ANTUSDT FF\n买 hyperliquid 合约 xyz:ANT（DEX xyz, 价格倍率 2） → 卖 gate 合约 ANT_USDT\n盘口价差：开仓 5%";
    vi.stubGlobal("fetch", vi.fn(async (input, init) => {
      if (String(input).includes("/big-spread-mutes")) return Response.json([]);
      return Response.json([
        { id: "big", rule_id: "big_spread", symbol: "ANTUSDT", message: routeMessage, created_at: "2026-11-13T03:09:32Z", status: "sent" },
        { id: "ordinary", rule_id: "rule-1", symbol: "BTCUSDT", message: "ordinary", created_at: "2026-11-13T03:09:32Z", status: "sent" }
      ]);
    }));
    render(<AlertHistoryPage />);
    expect((await screen.findAllByRole("button", { name: "屏蔽此路线" })).length).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "屏蔽此路线" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/DEX xyz, 价格倍率 2/)).toBeTruthy();
    expect((within(dialog).getByLabelText("屏蔽标的") as HTMLInputElement).value).toBe("ANTUSDT");
    fireEvent.click(within(dialog).getByLabelText(label));
    fireEvent.change(within(dialog).getByLabelText("屏蔽备注"), { target: { value: "规格不同" } });
    fireEvent.click(within(dialog).getByLabelText("同时屏蔽反向"));
    fireEvent.click(within(dialog).getByRole("button", { name: "确认屏蔽" }));
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).includes("/big-spread-mutes") && init?.method === "POST");
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        symbol: "ANTUSDT", buy_exchange: "hyperliquid", sell_exchange: "gate", both_directions: false, note: "规格不同",
        ...(hours ? { duration_hours: hours } : { until: null })
      });
    });
    expect(await screen.findByText("已屏蔽此路线")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps a failed mutation open and allows retry", async () => {
    let fails = true;
    vi.stubGlobal("fetch", vi.fn(async (input) => {
      if (String(input).includes("/big-spread-mutes")) return fails
        ? Response.json({ detail: "屏蔽保存失败" }, { status: 500 }) : Response.json([]);
      return Response.json([{ id: "big", rule_id: "big_spread", symbol: "IMXUSDT", message: "买 binance 合约 IMXUSDT → 卖 gate 合约 IMXUSDT", status: "sent", created_at: "2026-11-13T03:09:32Z" }]);
    }));
    render(<AlertHistoryPage />);
    fireEvent.click(await screen.findByRole("button", { name: "屏蔽此路线" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "确认屏蔽" }));
    expect(await screen.findByText(/屏蔽保存失败/)).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    fails = false;
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认屏蔽" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("disables muting when the historical route is missing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json([{ id: "big", rule_id: "big_spread", symbol: "IMXUSDT", message: "结果未知", status: "unknown", created_at: "2026-11-13T03:09:32Z" }])));
    render(<AlertHistoryPage />);
    expect((await screen.findByRole("button", { name: "屏蔽此路线" }) as HTMLButtonElement).disabled).toBe(true);
  });

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
