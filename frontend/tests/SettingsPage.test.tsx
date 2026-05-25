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
        if (url.includes("/settings/alert-message-template") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/alert-message-template")) {
          return Response.json({
            include_trigger_summary: true,
            include_rule_details: true,
            include_pair: true,
            include_spread: true,
            include_funding: true,
            include_volume: true,
            include_risk: true,
            include_observations: true,
            include_dashboard_link: true,
            observation_limit: 5
          });
        }
        if (url.includes("/settings/astro-card") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/astro-card")) {
          return Response.json({
            max_trade_usdt: 25,
            leverage: 2,
            min_notional: 10,
            max_notional: 25,
            close_position_buffer_pct: 0.1,
            unfavorable_funding_weight: 1,
            close_position_floor_pct: 0
          });
        }
        if (url.includes("/settings/live-pilot") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/live-pilot/preview")) {
          return Response.json({
            settings: {
              enabled: false,
              max_symbols: 10,
              notional_per_symbol_usdt: 100,
              min_next_funding_edge_pct: -0.05,
              prefer_hyperliquid: true,
              exclude_ss: true,
              create_cards_enabled: true
            },
            total_opportunities: 3,
            eligible_symbols: 2,
            selected_symbols: 2,
            skipped_negative_funding: 1,
            skipped_type: 1,
            skipped_risk: 4,
            budget_usdt: 200,
            items: [
              {
                opportunity_id: "btc-hyper",
                symbol: "BTCUSDT",
                type: "FF",
                route: "hyperliquid future -> okx future",
                buy_exchange: "hyperliquid",
                sell_exchange: "okx",
                uses_hyperliquid: true,
                open_spread_pct: 0.5,
                fee_adjusted_open_pct: 0.35,
                next_funding_edge_pct: 0.02,
                combined_open_edge_pct: 0.37,
                volume_24h_usdt: 10000000,
                notional_usdt: 100,
                risk_labels: []
              },
              {
                opportunity_id: "eth",
                symbol: "ETHUSDT",
                type: "FF",
                route: "binance future -> okx future",
                buy_exchange: "binance",
                sell_exchange: "okx",
                uses_hyperliquid: false,
                open_spread_pct: 0.4,
                fee_adjusted_open_pct: 0.2,
                next_funding_edge_pct: -0.01,
                combined_open_edge_pct: 0.19,
                volume_24h_usdt: 8000000,
                notional_usdt: 100,
                risk_labels: ["FUNDING_AGAINST"]
              }
            ]
          });
        }
        if (url.includes("/settings/live-pilot")) {
          return Response.json({
            enabled: false,
            max_symbols: 10,
            notional_per_symbol_usdt: 100,
            min_next_funding_edge_pct: -0.05,
            prefer_hyperliquid: true,
            exclude_ss: true,
            create_cards_enabled: true
          });
        }
        if (url.includes("/astro/status")) {
          return Response.json({
            configured: true,
            dry_run_only: true,
            base_url: "http://astro.local",
            admin_prefix: "",
            api_key_configured: true,
            list_path: "/pairs",
            pair_path: "/pairs",
            message_path: "/message",
            message: null
          });
        }
        if (url.includes("/alerts/rules") && init?.method === "POST") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/alerts/rules")) {
          return Response.json([]);
        }
        if (url.includes("/admin/service-control")) {
          return Response.json({
            enabled: true,
            environment: "development",
            message: null,
            services: ["frontend", "backend"],
            details: [
              { name: "frontend", available: true, container_name: "taoli1-frontend-1", state: "running" },
              { name: "backend", available: true, container_name: "taoli1-backend-1", state: "running" }
            ]
          });
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
    expect(screen.getByText("综合开仓阈值")).toBeTruthy();
    expect(screen.getAllByText(/正资金费率会加分，逆风资金费率会扣分/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/同一机会需要连续满足多少轮才触发/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/同一机会触发后，多少秒内不重复发送/).length).toBeGreaterThan(0);
    expect(screen.getByText("包含标的")).toBeTruthy();
    expect(
      screen.getByText("排除标的直接继承实时机会页隐藏的黑名单，无需单独填写。")
    ).toBeTruthy();
    expect(screen.queryByLabelText("排除标的")).toBeNull();
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
  }, 15000);

  it("saves global alert message template field choices", async () => {
    render(<SettingsPage />);

    expect(await screen.findByText("告警内容模板")).toBeTruthy();
    await userEvent.click(screen.getByLabelText("资金费率"));
    await userEvent.click(screen.getByRole("button", { name: /保存告警模板/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/alert-message-template"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"include_funding":false')
        })
      );
    });
    const preview = document.querySelector(".template-preview pre");
    expect(preview).not.toBeNull();
    expect(preview?.textContent).toContain("价差对：BTCUSDT | binance future -> okx future");
    expect(preview?.textContent).not.toContain("资金费率差");
  }, 15000);

  it("loads and saves Astro card defaults", async () => {
    render(<SettingsPage />);

    expect(await screen.findByText("Astro card defaults")).toBeTruthy();
    const positionValueInput = await screen.findByLabelText("Position value USDT");
    expect((positionValueInput as HTMLInputElement).value).toBe("25");

    await userEvent.clear(positionValueInput);
    await userEvent.type(positionValueInput, "80");
    await userEvent.click(screen.getByRole("button", { name: /Save Astro card defaults/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/astro-card"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"max_trade_usdt":80')
        })
      );
    });
  }, 15000);

  it("loads and saves Live Pilot settings", async () => {
    render(<SettingsPage />);

    expect(await screen.findByText("实盘灰度")).toBeTruthy();
    expect(screen.getByText("1000 USDT")).toBeTruthy();
    expect(await screen.findByText("当前候选 2/2")).toBeTruthy();
    expect(screen.getByText("BTCUSDT")).toBeTruthy();
    expect(screen.getAllByText("FF 合约-合约").length).toBeGreaterThan(0);
    expect(screen.getByText("hyperliquid future -> okx future")).toBeTruthy();
    expect(screen.getByText("Hyper")).toBeTruthy();
    expect(screen.getByText("强负资金跳过 1")).toBeTruthy();
    expect(screen.getByText("类型跳过 1")).toBeTruthy();
    expect(screen.getByText("风险跳过 4")).toBeTruthy();
    expect(screen.getByText(/Astro dry-run 当前开启/)).toBeTruthy();

    await userEvent.click(screen.getByLabelText("启用实盘灰度"));
    const symbolLimit = screen.getByLabelText("最多标的数");
    await userEvent.clear(symbolLimit);
    await userEvent.type(symbolLimit, "7");
    const notional = screen.getByLabelText("每标的资金 USDT");
    await userEvent.clear(notional);
    await userEvent.type(notional, "125");
    const fundingFloor = screen.getByLabelText("强负资金跳过阈值");
    await userEvent.clear(fundingFloor);
    await userEvent.type(fundingFloor, "-0.03");
    await userEvent.click(screen.getByRole("button", { name: /保存实盘灰度/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/live-pilot"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"notional_per_symbol_usdt":125')
        })
      );
    });
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => {
        return init?.method === "PUT" && String(init.body).includes('"enabled":true');
      })
    ).toBe(true);
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => {
        return init?.method === "PUT" && String(init.body).includes('"min_next_funding_edge_pct":-0.03');
      })
    ).toBe(true);
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => {
        return init?.method === "PUT" && String(init.body).includes('"exclude_ss":true');
      })
    ).toBe(true);
  }, 15000);

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
        if (url.includes("/admin/service-control")) {
          return Response.json({
            enabled: true,
            environment: "development",
            message: null,
            services: ["frontend", "backend"],
            details: [
              { name: "frontend", available: true, container_name: "taoli1-frontend-1", state: "running" },
              { name: "backend", available: true, container_name: "taoli1-backend-1", state: "running" }
            ]
          });
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
  }, 15000);

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
  }, 15000);

  it("saves signal validity risk settings", async () => {
    render(<SettingsPage />);

    const slippageInput = await screen.findByLabelText(/Signal slippage buffer pct/);
    await userEvent.clear(slippageInput);
    await userEvent.type(slippageInput, "0.2");
    await userEvent.click(screen.getByRole("button", { name: /保存风险参数/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"signal_slippage_buffer_pct":0.2')
        })
      );
    });
  }, 15000);

  it("shows signal strategy minimum notional default and saves it", async () => {
    render(<SettingsPage />);

    expect(await screen.findByText("Signal strategy（信号策略）")).toBeTruthy();
    expect(screen.getByText(/信号策略用于判断告警机会是否真的适合创建 Astro 卡片/)).toBeTruthy();
    expect(screen.getByText(/系统会在创建卡片前拉取两边交易所的多档 order book/)).toBeTruthy();
    expect(screen.getByText(/默认 1000 USDT/)).toBeTruthy();
    const minimumInput = await screen.findByLabelText("最小盘口验证金额 USDT (Minimum validation notional USDT)");
    const strategyNotes = await screen.findByLabelText("信号策略备注 / 后续自定义规则 (Signal strategy notes)");
    expect((minimumInput as HTMLInputElement).value).toBe("1000");

    await userEvent.clear(minimumInput);
    await userEvent.type(minimumInput, "1500");
    await userEvent.type(strategyNotes, "Depth must survive the intended card size.");
    await userEvent.click(screen.getByRole("button", { name: /保存风险参数/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"signal_validation_notional_usdt":1500')
        })
      );
    });
    expect(
      vi.mocked(fetch).mock.calls.some(([, init]) => {
        return (
          init?.method === "PUT" &&
          String(init.body).includes('"signal_strategy_notes":"Depth must survive the intended card size."')
        );
      })
    ).toBe(true);
  }, 15000);

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
  }, 15000);

  it("shows service restart controls and queues selected service restart", async () => {
    const restartCalls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
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
        if (url.includes("/alerts/rules")) {
          return Response.json([]);
        }
        if (url.includes("/admin/service-control") && init?.method === "POST") {
          restartCalls.push(url);
          return Response.json({ service: "frontend", status: "queued", message: "restart queued" });
        }
        if (url.includes("/admin/service-control")) {
          return Response.json({
            enabled: true,
            environment: "development",
            message: null,
            services: ["frontend", "backend"],
            details: [
              { name: "frontend", available: true, container_name: "taoli1-frontend-1", state: "running" },
              { name: "backend", available: true, container_name: "taoli1-backend-1", state: "running" }
            ]
          });
        }
        return Response.json([]);
      })
    );

    render(<SettingsPage />);

    await userEvent.click(await screen.findByRole("button", { name: "重启前端" }, { timeout: 10000 }));

    await waitFor(() => {
      expect(restartCalls.some((url) => url.includes("/admin/service-control/frontend/restart"))).toBe(true);
    });
    expect(screen.getByRole("button", { name: "重启后端" })).toBeTruthy();
    expect(screen.getByText(/taoli1-frontend-1/)).toBeTruthy();
  }, 15000);
});
