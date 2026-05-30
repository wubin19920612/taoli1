import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listOpportunities } from "../src/api/client";
import { DashboardPage } from "../src/pages/DashboardPage";
import type { Opportunity } from "../src/api/types";

const baseOpportunity: Opportunity = {
  id: "opp-1",
  type: "FF",
  symbol: "BTCUSDT",
  buy_exchange: "binance",
  buy_market_type: "future",
  sell_exchange: "okx",
  sell_market_type: "future",
  open_spread_pct: 0.78,
  close_spread_pct: 0.42,
  fee_adjusted_open_pct: 0.62,
  spread_width_pct: 0.36,
  buy_bid: 99900,
  buy_ask: 100000,
  sell_bid: 100780,
  sell_ask: 100850,
  buy_volume_24h_usdt: 10000000,
  sell_volume_24h_usdt: 12000000,
  funding_rate_buy_pct: 0.01,
  funding_rate_sell_pct: -0.02,
  funding_next_rate_buy_pct: 0.015,
  funding_next_rate_sell_pct: 0.025,
  funding_next_time_buy: "2026-05-15T08:00:00Z",
  funding_next_time_sell: "2026-05-15T08:00:00Z",
  net_funding_pct: -0.03,
  net_funding_next_pct: 0.01,
  buy_funding_interval_hours: 8,
  sell_funding_interval_hours: 8,
  net_funding_hourly_pct: -0.00375,
  net_funding_daily_pct: -0.09,
  net_funding_next_hourly_pct: 0.00125,
  net_funding_next_daily_pct: 0.03,
  mark_index_diff_buy_pct: 0.01,
  mark_index_diff_sell_pct: 0.02,
  risk_labels: [],
  last_seen_at: "2026-05-15T02:00:00Z"
};

describe("DashboardPage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
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
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );
  });

  it("hides non-actionable risk rows by default", async () => {
    render(<DashboardPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("include_risky=false"),
        expect.anything()
      );
    });
  });

  it("sends adjustable hidden risk labels and risk-settings min volume in K", async () => {
    render(<DashboardPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("hidden_risk_labels=LOW_VOLUME%2CSTALE_DATA%2CHUGE_SPREAD_VERIFY"),
        expect.anything()
      );
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("min_volume_24h_k=100"),
        expect.anything()
      );
    });
  });

  it("sends excluded opportunity types to the opportunities query", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        calls.push(String(input));
        return Response.json([]);
      })
    );

    await listOpportunities({ exclude_types: ["SF", "SS"] });

    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain("exclude_types=SF%2CSS");
  });

  it("keeps excluded opportunity types after the dashboard remounts", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    const firstRender = render(<DashboardPage />);

    const typeExcludeSelector = document.querySelector(".type-exclude-select .ant-select-selector");
    expect(typeExcludeSelector).not.toBeNull();
    fireEvent.mouseDown(typeExcludeSelector as Element);
    const ssOptions = await screen.findAllByText("SS");
    await userEvent.click(ssOptions[ssOptions.length - 1]);
    await waitFor(() => {
      expect(calls.some((url) => url.includes("exclude_types=SS"))).toBe(true);
    });

    firstRender.unmount();
    calls.length = 0;

    render(<DashboardPage />);

    await waitFor(() => {
      expect(calls.some((url) => url.includes("exclude_types=SS"))).toBe(true);
    });
  });

  it("waits for risk settings before requesting opportunities", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await waitFor(() => {
      expect(calls.some((url) => url.includes("/opportunities"))).toBe(true);
    });
    expect(calls.find((url) => url.includes("/opportunities"))).toContain("min_volume_24h_k=100");
  });

  it("adds a row symbol to the global blacklist from the table action", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/risk") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: ["OLDUSDT"],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 1,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "屏蔽 BTCUSDT" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"excluded_symbols":["OLDUSDT","BTCUSDT"]')
        })
      );
    });
  });

  it("removes a symbol from the global blacklist from the dashboard", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/settings/risk") && init?.method === "PUT") {
          return Response.json(JSON.parse(String(init.body)));
        }
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: ["BADUSDT"],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "取消屏蔽 BADUSDT" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/settings/risk"),
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"excluded_symbols":[]')
        })
      );
    });
  });

  it("opens an Astro dry-run preview for a selected opportunity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 1,
            exchange_errors: {}
          });
        }
        if (url.includes("/astro/preview/opp-1")) {
          return Response.json({
            opportunity_id: "opp-1",
            symbol: "BTCUSDT",
            mode: "dry_run",
            can_submit: true,
            pair: {
              name: "BTC",
              type: "FF",
              status: false,
              disableOpen: true,
              disableClose: false,
              openPosition: "0.007800",
              closePosition: "0.004200",
              maxTradeUSDT: "10",
              leverage: "1",
              buyEx: "binance",
              sellEx: "okx"
            },
            sdk_payload: {
              action: "add",
              pair: {
                name: "BTC",
                type: "FF",
                openPosition: "0.007800"
              }
            },
            blockers: [],
            warnings: ["Dry-run only"],
            assumptions: [
              {
                field: "openPosition",
                source: "open_spread_pct=0.78",
                assumed_value: "0.007800",
                note: "percent / 100",
                needs_verification: true
              }
            ]
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Astro BTCUSDT" }));

    expect((await screen.findAllByText("Astro dry-run")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/openPosition/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0.007800/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Dry-run only/)).toBeTruthy();
  }, 15000);

  it("opens spread history stats for a selected opportunity", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 1,
            exchange_errors: {}
          });
        }
        if (url.includes("/history/opportunities/stats")) {
          return Response.json({
            symbol: "BTCUSDT",
            opportunity_id: "opp-1",
            type: "FF",
            count: 4,
            first_seen_at: "2026-05-19T01:00:00Z",
            last_seen_at: "2026-05-19T01:03:00Z",
            latest: {
              ...baseOpportunity,
              observed_at: "2026-05-19T01:03:00Z"
            },
            open_spread_pct: {
              min: 0.1,
              max: 0.9,
              mean: 0.4,
              median: 0.3,
              p05: 0.115,
              p95: 0.825,
              current: 0.9,
              z_score: 1.51
            },
            close_spread_pct: {
              min: -0.1,
              max: 0.7,
              mean: 0.2,
              median: 0.1,
              p05: -0.085,
              p95: 0.625,
              current: 0.7,
              z_score: 1.51
            },
            fee_adjusted_open_pct: {
              min: 0,
              max: 0.8,
              mean: 0.3,
              median: 0.2,
              p05: 0.015,
              p95: 0.725,
              current: 0.8,
              z_score: 1.51
            },
            net_funding_pct: {
              min: 0.01,
              max: 0.01,
              mean: 0.01,
              median: 0.01,
              p05: 0.01,
              p95: 0.01,
              current: 0.01,
              z_score: null
            },
            net_funding_next_pct: {
              min: 0.01,
              max: 0.04,
              mean: 0.023333,
              median: 0.02,
              p05: 0.011,
              p95: 0.038,
              current: 0.04,
              z_score: 1.34
            },
            points: [
              {
                observed_at: "2026-05-19T01:00:00Z",
                open_spread_pct: 0.1,
                close_spread_pct: -0.1,
                fee_adjusted_open_pct: 0,
                net_funding_pct: 0.01,
                net_funding_next_pct: null
              },
              {
                observed_at: "2026-05-19T01:03:00Z",
                open_spread_pct: 0.9,
                close_spread_pct: 0.7,
                fee_adjusted_open_pct: 0.8,
                net_funding_pct: 0.01,
                net_funding_next_pct: 0.04
              }
            ]
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "价差历史 BTCUSDT" }));

    expect((await screen.findAllByText("价差历史统计")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("BTCUSDT").length).toBeGreaterThan(0);
    expect(screen.getByText("样本数")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("当前开仓差")).toBeTruthy();
    expect(screen.getByText("0.900%")).toBeTruthy();
    expect(screen.getAllByText("p95上边界").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0.825%").length).toBeGreaterThan(0);
    expect(screen.getByTestId("spread-history-chart")).toBeTruthy();
    expect(calls.some((url) => url.includes("opportunity_id=opp-1"))).toBe(true);
    expect(calls.some((url) => url.includes("type=FF"))).toBe(true);
  }, 15000);

  it("creates a paused Astro card from the preview modal", async () => {
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
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 1,
            exchange_errors: {}
          });
        }
        if (url.includes("/astro/opportunities/opp-1/card") && init?.method === "POST") {
          return Response.json({
            enabled: true,
            status: "created",
            action: "add",
            message: "已创建暂停卡片 BTC FF binance->okx，禁开=true",
            pair_name: "BTC",
            pair_type: "FF"
          });
        }
        if (url.includes("/astro/preview/opp-1")) {
          return Response.json({
            opportunity_id: "opp-1",
            symbol: "BTCUSDT",
            mode: "dry_run",
            can_submit: true,
            pair: {
              name: "BTC",
              type: "FF",
              status: false,
              disableOpen: true,
              openPosition: "0.007800",
              closePosition: "0.004200",
              buyEx: "binance",
              sellEx: "okx"
            },
            sdk_payload: { action: "add", pair: { name: "BTC" } },
            blockers: [],
            warnings: [],
            assumptions: []
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Astro BTCUSDT" }));
    await userEvent.click(await screen.findByRole("button", { name: "创建/更新暂停卡片" }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/astro/opportunities/opp-1/card"),
        expect.objectContaining({ method: "POST" })
      );
    });
    expect((await screen.findAllByText(/已创建暂停卡片 BTC FF/)).length).toBeGreaterThan(0);
  }, 15000);

  it("sends edited Astro sizing values and can save them as defaults", async () => {
    const createBodies: string[] = [];
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
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 1,
            exchange_errors: {}
          });
        }
        if (url.includes("/astro/opportunities/opp-1/card") && init?.method === "POST") {
          createBodies.push(String(init.body));
          return Response.json({
            enabled: true,
            status: "created",
            action: "add",
            message: "created paused card",
            pair_name: "BTC",
            pair_type: "FF"
          });
        }
        if (url.includes("/astro/preview/opp-1")) {
          return Response.json({
            opportunity_id: "opp-1",
            symbol: "BTCUSDT",
            mode: "dry_run",
            can_submit: true,
            pair: {
              name: "BTC",
              type: "FF",
              status: false,
              disableOpen: true,
              openPosition: "0.007800",
              closePosition: "0.000000",
              maxTradeUSDT: "25",
              leverage: "2",
              minNotional: "10",
              maxNotional: "25",
              buyEx: "binance",
              sellEx: "okx"
            },
            sdk_payload: { action: "add", pair: { name: "BTC" } },
            blockers: [],
            warnings: [],
            assumptions: []
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    await userEvent.click(await screen.findByRole("button", { name: "Astro BTCUSDT" }));
    const positionInput = await screen.findByLabelText("Position value USDT");
    expect((positionInput as HTMLInputElement).value).toBe("25");
    expect(screen.getByText("Generated openPosition")).toBeTruthy();
    expect(screen.getByText("0.007800")).toBeTruthy();
    expect(screen.getByText("Generated closePosition")).toBeTruthy();
    expect(screen.getByText("0.000000")).toBeTruthy();

    await userEvent.clear(positionInput);
    await userEvent.type(positionInput, "80");
    await userEvent.click(screen.getByLabelText("Save sizing as global default"));
    await userEvent.click(screen.getByRole("button", { name: /暂停卡片/ }));

    await waitFor(() => {
      expect(createBodies).toHaveLength(1);
    });
    expect(createBodies[0]).toContain('"max_trade_usdt":80');
    expect(createBodies[0]).toContain('"leverage":2');
    expect(createBodies[0]).toContain('"min_notional":10');
    expect(createBodies[0]).toContain('"max_notional":25');
    expect(createBodies[0]).toContain('"save_as_default":true');
  }, 15000);

  it("renders exchange states from the health payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 2,
            opportunities: 1,
            exchange_errors: {},
            exchange_states: {
              binance: {
                status: "healthy",
                last_success_at: "2026-05-21T04:00:00Z",
                last_error_at: null,
                consecutive_failures: 0,
                cooldown_until: null,
                next_due_at: "2026-05-21T04:00:08Z",
                in_flight: false
              },
              gate: {
                status: "cooling_down",
                last_success_at: "2026-05-21T03:59:00Z",
                last_error_at: "2026-05-21T04:00:00Z",
                consecutive_failures: 2,
                cooldown_until: "2026-05-21T04:01:00Z",
                next_due_at: "2026-05-21T04:01:00Z",
                in_flight: false
              }
            }
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([baseOpportunity]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    expect(await screen.findByText("Exchange states")).toBeTruthy();
    expect(screen.getByText("binance")).toBeTruthy();
    expect(screen.getByText("gate")).toBeTruthy();
    expect(screen.getByText("cooling_down")).toBeTruthy();
    expect(screen.getAllByText("05-21 04:00:00 UTC").length).toBeGreaterThan(0);
    expect(screen.getAllByText("05-21 04:01:00 UTC").length).toBeGreaterThan(0);
  });

  it("labels exchange errors as external exchange API link failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 1200,
            opportunities: 12,
            exchange_errors: {
              "binance:future":
                "ExchangeRequestError: GET https://fapi.binance.com/fapi/v1/ticker/bookTicker failed: ConnectTimeout"
            },
            exchange_states: {}
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    expect(await screen.findByText("交易所链路异常")).toBeTruthy();
    expect(screen.getByText("交易所外部 API 链路异常")).toBeTruthy();
    expect(screen.getByText("binance:future")).toBeTruthy();
    expect(screen.getByText(/fapi\.binance\.com/)).toBeTruthy();
    expect(screen.queryByText("Exchange errors")).toBeNull();
  });

  it("does not turn partial exchange states into healthy-looking values", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/settings/risk")) {
          return Response.json({
            min_volume_24h_usdt: 100000,
            stale_after_seconds: 30,
            huge_spread_pct: 10,
            wide_spread_pct: 3,
            mark_index_deviation_pct: 1,
            funding_against_pct: 0.01,
            ticker_collision_symbols: [],
            excluded_symbols: [],
            ignored_exchanges: []
          });
        }
        if (url.includes("/health")) {
          return Response.json({
            status: "ok",
            markets: 0,
            opportunities: 0,
            exchange_errors: {},
            exchange_states: {
              stale: {
                last_success_at: null,
                last_error_at: null,
                consecutive_failures: null,
                cooldown_until: null,
                next_due_at: null
              }
            }
          });
        }
        if (url.includes("/opportunities")) {
          return Response.json([]);
        }
        return Response.json({});
      })
    );

    render(<DashboardPage />);

    expect(await screen.findByText("stale")).toBeTruthy();
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
    expect(screen.getByText("n/a")).toBeTruthy();
    expect(screen.queryByText("no")).toBeNull();
  });
});
