import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IndexComponentChangesPage } from "../src/pages/IndexComponentChangesPage";

describe("IndexComponentChangesPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/index-components/snapshots")) {
          if (url.includes("symbol=ESPORTS")) {
            if (url.includes("exchange=gate")) {
              return Response.json([]);
            }
            return Response.json([
              {
                exchange: "binance",
                symbol: "ESPORTSUSDT",
                component_hash: "esports-hash",
                source: "binance-fapi-constituents",
                observed_at: "2026-05-27T08:07:00Z",
                components: [
                  { source: "binance_future", symbol: "ESPORTSUSDT", weight: 0.3 },
                  { source: "gateio", symbol: "ESPORTS_USDT", weight: 0.2 },
                  { source: "pancakeswapv3", symbol: "ESPORTS_WBNB", weight: 0.5 }
                ]
              }
            ]);
          }
          return Response.json([
              {
                exchange: "binance",
                symbol: "VANRYUSDT",
                component_hash: "snapshot-hash",
                source: "binance-fapi-constituents",
                observed_at: "2026-05-27T08:06:00Z",
                components: [
                  { source: "binance", symbol: "VANRYUSDT", weight: 0.5 },
                  { source: "bybit", symbol: "VANRYUSDT", weight: 0.5 }
                ]
              }
            ]);
        }
        if (url.includes("/markets")) {
          if (url.includes("symbol=ESPORTS")) {
            return Response.json([
              {
                symbol: "ESPORTSUSDT",
                base: "ESPORTS",
                quote: "USDT",
                exchange: "gate",
                market_type: "future",
                bid: 0.04,
                ask: 0.041,
                volume_24h_usdt: 1000000,
                funding_rate_pct: 0.16,
                funding_next_rate_pct: 0.16,
                funding_interval_hours: 8,
                funding_next_time: "2026-05-27T16:00:00Z",
                mark_price: 0.041,
                index_price: 0.04,
                timestamp: "2026-05-27T08:06:00Z",
                raw_symbol: "ESPORTS_USDT"
              },
              {
                symbol: "ESPORTSUSDT",
                base: "ESPORTS",
                quote: "USDT",
                exchange: "bitget",
                market_type: "future",
                bid: 0.039,
                ask: 0.04,
                volume_24h_usdt: 1000000,
                funding_rate_pct: 1.4,
                funding_next_rate_pct: null,
                funding_interval_hours: 8,
                funding_next_time: null,
                mark_price: 0.0413,
                index_price: 0.04,
                timestamp: "2026-05-27T08:06:00Z",
                raw_symbol: "ESPORTSUSDT"
              }
            ]);
          }
          return Response.json([
            {
              symbol: "VANRYUSDT",
              base: "VANRY",
              quote: "USDT",
              exchange: "binance",
              market_type: "future",
              bid: 1,
              ask: 1.01,
              volume_24h_usdt: 1000000,
              funding_rate_pct: 0.01,
              funding_next_rate_pct: 0.04,
              funding_interval_hours: 8,
              funding_next_time: "2026-05-27T16:00:00Z",
              mark_price: 1.02,
              index_price: 1,
              timestamp: "2026-05-27T08:06:00Z",
              raw_symbol: "VANRYUSDT"
            },
            {
              symbol: "VANRYUSDT",
              base: "VANRY",
              quote: "USDT",
              exchange: "okx",
              market_type: "future",
              bid: 0.99,
              ask: 1,
              volume_24h_usdt: 1000000,
              funding_rate_pct: -0.01,
              funding_next_rate_pct: -0.02,
              funding_interval_hours: 8,
              funding_next_time: "2026-05-27T16:00:00Z",
              mark_price: 0.99,
              index_price: 1,
              timestamp: "2026-05-27T08:06:00Z",
              raw_symbol: "VANRY-USDT-SWAP"
            }
          ]);
        }
        if (url.includes("/index-components/changes")) {
          return Response.json([
            {
              id: "change-1",
              exchange: "binance",
              symbol: "VANRYUSDT",
              old_hash: "old-hash",
              new_hash: "new-hash",
              old_components: [
                { source: "binance", symbol: "VANRYUSDT", weight: 0.7 },
                { source: "gate", symbol: "VANRYUSDT", weight: 0.3 }
              ],
              new_components: [
                { source: "binance", symbol: "VANRYUSDT", weight: 0.5 },
                { source: "bybit", symbol: "VANRYUSDT", weight: 0.5 }
              ],
              added_components: [{ source: "bybit", symbol: "VANRYUSDT", weight: 0.5 }],
              removed_components: [{ source: "gate", symbol: "VANRYUSDT", weight: 0.3 }],
              changed_components: [{ source: "binance", symbol: "VANRYUSDT", weight: 0.5 }],
              source: "binance-fapi-constituents",
              alert_status: "sent",
              created_at: "2026-05-27T08:05:00Z"
            }
          ]);
        }
        if (url.includes("/index-components/watchlist")) {
          if (method === "DELETE") {
            return new Response(null, { status: 204 });
          }
          if (method === "POST") {
            return Response.json({ id: "watch-1", symbol: "ESPORTS", note: "重点", created_at: "2026-05-28T08:00:00Z" });
          }
          if (method === "GET") {
            return Response.json([{ id: "delete-me", symbol: "ESPORTS", note: "重点", created_at: "2026-05-28T08:00:00Z" }]);
          }
        }
        return Response.json([]);
      })
    );
  });

  it("renders index component change records with readable weight diffs and short source", async () => {
    render(<IndexComponentChangesPage />);

    expect(await screen.findByText("VANRYUSDT")).toBeTruthy();
    expect(screen.getAllByText("binance").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Binance (VANRYUSDT): 权重 70.00% ↓→ 50.00%")).toBeTruthy();
    expect(screen.getByText("Bybit (VANRYUSDT): 权重 0.00% ↑→ 50.00%")).toBeTruthy();
    expect(screen.getByText("Gate (VANRYUSDT): 权重 30.00% ↓→ 0.00%")).toBeTruthy();
    expect(screen.queryByText("binance-fapi-constituents")).toBeNull();
    expect(screen.queryByText("新增 1 / 移除 1 / 变化 1")).toBeNull();
    expect(screen.queryByText(/旧:/)).toBeNull();
    expect(screen.queryByText(/新:/)).toBeNull();
    expect(screen.getByText("sent")).toBeTruthy();
  });

  it("renders a symbol component chart with market funding comparison", async () => {
    render(<IndexComponentChangesPage />);

    await userEvent.type(await screen.findByPlaceholderText("标的模糊匹配"), "vanry");
    await userEvent.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("指数成分与资金费率")).toBeTruthy();
    expect(screen.getByTestId("index-component-market-chart")).toBeTruthy();
    expect(screen.getByText("当前资金差 0.020%")).toBeTruthy();
    expect(screen.getByText("下期资金差 0.060%")).toBeTruthy();
    expect(screen.getByText("预计扩大")).toBeTruthy();
    expect(screen.getAllByText("binance 50.00%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("bybit 50.00%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("当前 0.010%")).toBeTruthy();
    expect(screen.getByText("下期 0.040%")).toBeTruthy();

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining("/index-components/snapshots?"),
        expect.anything()
      );
    });
    const calls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(calls.some((url) => url.includes("/markets?") && url.includes("symbol=VANRY"))).toBe(true);
  });

  it("uses referenced components when an exchange-specific snapshot is missing", async () => {
    render(<IndexComponentChangesPage />);

    await userEvent.type(await screen.findByPlaceholderText("标的模糊匹配"), "esports");
    await userEvent.click(screen.getByRole("button", { name: /查询/ }));

    expect((await screen.findAllByText("gateio 20.00%")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("参考 binance 指数成分").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("pancakeswapv3 50.00%").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("binance future 30.00%").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("参考篮子包含 gateio 20.00%")).toBeTruthy();
    expect(screen.getByText("参考篮子未包含 bitget")).toBeTruthy();
  });

  it("keeps reference snapshots available for the chart when exchange filter is selected", async () => {
    render(<IndexComponentChangesPage />);

    await userEvent.type(await screen.findByPlaceholderText("标的模糊匹配"), "esports");
    const exchangeSelector = document.querySelector(".index-exchange-select .ant-select-selector");
    expect(exchangeSelector).not.toBeNull();
    fireEvent.mouseDown(exchangeSelector as Element);
    await userEvent.click(await screen.findByText("Gate"));
    await userEvent.click(screen.getByRole("button", { name: /查询/ }));

    expect((await screen.findAllByText("gateio 20.00%")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("参考 binance 指数成分").length).toBeGreaterThanOrEqual(1);
    const calls = vi.mocked(fetch).mock.calls.map((call) => String(call[0]));
    expect(
      calls.some(
        (url) =>
          url.includes("/index-components/snapshots?") &&
          url.includes("symbol=ESPORTS") &&
          !url.includes("exchange=gate")
      )
    ).toBe(true);
  });

  it("expands a row to show detailed component weight diffs", async () => {
    render(<IndexComponentChangesPage />);

    await userEvent.click(await screen.findByLabelText("Expand row"));

    expect(await screen.findByText("成分变更")).toBeTruthy();
    expect(screen.getAllByText("Binance (VANRYUSDT): 权重 70.00% ↓→ 50.00%").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Bybit (VANRYUSDT): 权重 0.00% ↑→ 50.00%").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Gate (VANRYUSDT): 权重 30.00% ↓→ 0.00%").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("旧成分")).toBeNull();
    expect(screen.queryByText("新成分")).toBeNull();
    expect(screen.queryByText(/hash/)).toBeNull();
  });

  it("sends symbol and exchange filters when refreshing", async () => {
    render(<IndexComponentChangesPage />);

    await userEvent.type(await screen.findByPlaceholderText("标的模糊匹配"), "vanry");
    const exchangeSelector = document.querySelector(".index-exchange-select .ant-select-selector");
    expect(exchangeSelector).not.toBeNull();
    fireEvent.mouseDown(exchangeSelector as Element);
    await userEvent.click(await screen.findByText("Binance"));
    await userEvent.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(fetch).toHaveBeenLastCalledWith(
        expect.stringContaining("symbol=VANRY"),
        expect.anything()
      );
    });
    const calls = vi.mocked(fetch).mock.calls;
    expect(String(calls[calls.length - 1][0])).toContain("exchange=binance");
  });

  it("uses a fixed exchange selector instead of a free text exchange input", async () => {
    render(<IndexComponentChangesPage />);

    expect(await screen.findByText("VANRYUSDT")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "交易所" })).toBeNull();
    expect(document.querySelector(".index-exchange-select .ant-select-selector")).not.toBeNull();
  });

  it("manages monitored symbols for index component alerts", async () => {
    render(<IndexComponentChangesPage />);

    expect(await screen.findByText("监控标的")).toBeTruthy();
    expect(screen.getByText("ESPORTS")).toBeTruthy();
    await userEvent.type(screen.getByPlaceholderText("新增监控标的"), "esports");
    await userEvent.click(screen.getByRole("button", { name: /加入监控/ }));
    await userEvent.click(screen.getByRole("button", { name: /删除/ }));

    const calls = vi.mocked(fetch).mock.calls.map((call) => [String(call[0]), call[1]?.method ?? "GET"]);
    expect(calls.some(([url, method]) => url.includes("/index-components/watchlist") && method === "POST")).toBe(true);
    expect(calls.some(([url, method]) => url.includes("/index-components/watchlist/delete-me") && method === "DELETE")).toBe(true);
  });
});
