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
            ticker_collision_symbols: ["AIUSDT"],
            excluded_symbols: [],
            ignored_exchanges: []
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

    expect(await screen.findByDisplayValue("1000")).toBeTruthy();
    expect(screen.getAllByText(/低成交额.*LOW_VOLUME/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/数据过期.*STALE_DATA/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/异常大价差.*HUGE_SPREAD_VERIFY/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/开平价差宽/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SF=现货买入 \/ 永续卖出/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/info=仅记录，warning=普通告警，critical=强提醒/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/同一机会需要连续满足多少轮才触发/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/同一机会触发后，多少秒内不重复发送/).length).toBeGreaterThan(0);
    expect(screen.getByText("包含标的")).toBeTruthy();
    expect(screen.getByText("排除标的")).toBeTruthy();
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

  it("uses saved risk volume as the default alert rule volume", async () => {
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: ["AIUSDT"],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/alerts/rules") && init?.method === "POST") {
          posts.push(String(init.body));
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/alerts/rules")) {
          return Response.json([]);
        }
        return Response.json([]);
      })
    );

    render(<SettingsPage />);

    await userEvent.type(await screen.findByLabelText("规则名称"), "TRADOOR");
    await userEvent.click(screen.getByRole("button", { name: "新增规则" }));
    await userEvent.clear(await screen.findByLabelText("规则名称"));
    await userEvent.type(await screen.findByLabelText("规则名称"), "TRADOOR2");
    await userEvent.click(screen.getByRole("button", { name: "新增规则" }));

    await waitFor(() => {
      expect(posts).toHaveLength(2);
    });
    expect(posts.every((body) => body.includes('"min_volume_24h_usdt":100000'))).toBe(true);
  });

  it("saves editable risk thresholds and converts K volume back to USDT", async () => {
    render(<SettingsPage />);

    const volumeInput = await screen.findByLabelText("低成交额阈值 (LOW_VOLUME)");
    await userEvent.clear(volumeInput);
    await userEvent.type(volumeInput, "2500");
    await userEvent.click(screen.getByRole("button", { name: /保存风险参数/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"min_volume_24h_usdt":2500000')
        })
      );
    });
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => {
        return init?.method === "PUT" && String(init.body).includes("min_volume_24h_k");
      })
    ).toBe(false);
  });

  it("saves symbol blacklist and ignored exchanges in risk settings", async () => {
    render(<SettingsPage />);

    const blacklist = await screen.findByLabelText("黑名单标的");
    await userEvent.click(blacklist);
    await userEvent.type(blacklist, "BADUSDT");
    await userEvent.keyboard("{Enter}");

    const ignored = await screen.findByLabelText("忽略交易所");
    await userEvent.click(ignored);
    await userEvent.click(screen.getByText("gate"));

    await userEvent.click(screen.getByRole("button", { name: /保存风险参数/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"excluded_symbols":["BADUSDT"]')
        })
      );
    });
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => {
        return init?.method === "PUT" && String(init.body).includes('"ignored_exchanges":["gate"]');
      })
    ).toBe(true);
  });
});
