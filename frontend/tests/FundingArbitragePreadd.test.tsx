import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FundingArbitragePage } from "../src/pages/FundingArbitragePage";

const preaddSettings = {
  enabled: false,
  exchanges: ["bitget", "binance"],
  funding_threshold_pct: 0.6,
  premium_threshold_pct: 1,
  open_spread_threshold_pct: 0.9,
  min_volume_24h_usdt: 0,
  scan_interval_seconds: 60,
  max_routes_per_run: 5,
  stale_after_seconds: 30
};

describe("Astro preadd controls", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/astro/preadd/exchanges")) {
        return Response.json(["bitget", "binance", "okx"]);
      }
      if (url.includes("/astro/preadd/settings")) {
        return Response.json(init?.method === "PUT" ? JSON.parse(String(init.body)) : preaddSettings);
      }
      if (url.includes("/astro/preadd/preview")) {
        return Response.json({ warnings: [], total_matches: 2, items: [{
          id: "eth-bitget-binance", symbol: "ETHUSDT", buy_exchange: "binance",
          sell_exchange: "bitget", signal_exchange: "bitget", signal_type: "funding",
          signal_value_pct: 0.8, funding_source: "predicted", funding_interval_hours: 8,
          entry_mode: "convergence", card_open_spread_pct: 0.9,
          buy_leg: { exchange: "binance", premium_index_pct: -0.12, funding_rate_pct: 0.01,
            funding_interval_hours: 8, volume_24h_usdt: 12_500_000 },
          sell_leg: { exchange: "bitget", premium_index_pct: 1.25, funding_rate_pct: 0.08,
            funding_interval_hours: 4, volume_24h_usdt: 8_400_000 },
          live_spread_pct: -0.2, observed_at: "2026-09-16T02:00:00Z"
        }, {
          id: "zil-binance-bybit", symbol: "ZILUSDT", buy_exchange: "binance",
          sell_exchange: "bybit", signal_exchange: "bybit", signal_type: "funding",
          signal_value_pct: 0.75, funding_source: "current", funding_interval_hours: 8,
          entry_mode: "bybit_funding_reverse", card_open_spread_pct: -0.9,
          buy_leg: { exchange: "binance", premium_index_pct: 0, funding_rate_pct: 0.01,
            funding_interval_hours: 8, volume_24h_usdt: 5_000_000 },
          sell_leg: { exchange: "bybit", premium_index_pct: -1.2, funding_rate_pct: 0.75,
            funding_interval_hours: 8, volume_24h_usdt: 6_000_000 },
          live_spread_pct: -1.1, observed_at: "2026-09-16T02:00:00Z"
        }] });
      }
      if (url.includes("/astro/preadd/run")) {
        return Response.json({ attempted: 1, created: 1, skipped: 0, failed: 0,
          warnings: [], results: ["ETHUSDT 已预建暂停卡片"] });
      }
      if (url.includes("/funding-arbitrage/settings")) {
        return Response.json({ enabled: false });
      }
      if (url.includes("/funding-arbitrage/preview")) {
        return Response.json({ candidates: [], total_pairs_evaluated: 0, displayed_candidates: 0 });
      }
      return Response.json({});
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it("saves thresholds and only submits after explicit confirmation", async () => {
    const user = userEvent.setup();
    render(<FundingArbitragePage />);
    expect(await screen.findByText("Astro 交易对预建")).toBeTruthy();
    expect(await screen.findByText("bitget 下期 +0.800% / 8h")).toBeTruthy();
    expect(screen.getByText("溢价 -0.120%")).toBeTruthy();
    expect(screen.getAllByText("资金费 +0.0100% / 8h")).toHaveLength(2);
    expect(screen.getByText("24h 12.50M USDT")).toBeTruthy();
    expect(screen.getByText("溢价 +1.250%")).toBeTruthy();
    expect(screen.getByText("资金费 +0.0800% / 4h")).toBeTruthy();
    expect(screen.getByText("24h 8.40M USDT")).toBeTruthy();
    expect(screen.getByText("自动监测关闭")).toBeTruthy();
    expect(screen.getByText("候选判定说明")).toBeTruthy();
    expect(screen.getByText(/资金费和溢价是“或”关系/)).toBeTruthy();
    expect(screen.getByText(/Bybit 自身达到资金费阈值时只按收资金费方向/)).toBeTruthy();
    expect(screen.getByText(/普通候选写入正阈值/)).toBeTruthy();
    expect(screen.getByText("反向开仓")).toBeTruthy();
    expect(screen.getByText("仅做 Bybit 资金费")).toBeTruthy();
    expect(screen.getByText("卡片 -0.900%")).toBeTruthy();

    const fundingInput = screen.getByLabelText("资金费绝对值（单次结算）");
    const volumeInput = screen.getByLabelText("单边24h最低成交量");
    await user.clear(fundingInput);
    await user.type(fundingInput, "1.2");
    await user.clear(volumeInput);
    await user.type(volumeInput, "500000");
    await user.click(screen.getByRole("button", { name: /保存预建规则/ }));
    await waitFor(() => {
      const saved = vi.mocked(fetch).mock.calls.find(([url, init]) =>
        String(url).includes("/astro/preadd/settings") && init?.method === "PUT"
      );
      expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({
        exchanges: ["bitget", "binance"],
        funding_threshold_pct: 1.2,
        min_volume_24h_usdt: 500_000
      });
    });
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/astro/preadd/run"))).toBe(false);
    await user.click(screen.getByRole("button", { name: /立即预建/ }));
    expect(await screen.findByText("卡片将以暂停、禁开状态创建；不会开启仓位。")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "确认预建" }));
    await waitFor(() => {
      const run = vi.mocked(fetch).mock.calls.find(([url, init]) =>
        String(url).includes("/astro/preadd/run") && init?.method === "POST"
      );
      expect(JSON.parse(String(run?.[1]?.body))).toEqual({ candidate_ids: null });
    });
  });
});
