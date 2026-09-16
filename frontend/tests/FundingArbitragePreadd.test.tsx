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
        return Response.json({ warnings: [], total_matches: 1, items: [{
          id: "eth-bitget-binance", symbol: "ETHUSDT", buy_exchange: "binance",
          sell_exchange: "bitget", signal_exchange: "bitget", signal_type: "funding",
          signal_value_pct: 0.8, funding_source: "predicted", funding_interval_hours: 8,
          live_spread_pct: -0.2, observed_at: "2026-09-16T02:00:00Z"
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
    expect(screen.getByText("自动监测关闭")).toBeTruthy();

    const fundingInput = screen.getByLabelText("资金费绝对值（单次结算）");
    await user.clear(fundingInput);
    await user.type(fundingInput, "1.2");
    await user.click(screen.getByRole("button", { name: /保存预建规则/ }));
    await waitFor(() => {
      const saved = vi.mocked(fetch).mock.calls.find(([url, init]) =>
        String(url).includes("/astro/preadd/settings") && init?.method === "PUT"
      );
      expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({
        exchanges: ["bitget", "binance"], funding_threshold_pct: 1.2
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
