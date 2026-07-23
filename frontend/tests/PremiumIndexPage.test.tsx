import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PremiumIndexPage } from "../src/pages/PremiumIndexPage";

const observedAt = "2026-07-12T02:00:00Z";

function premiumResult(symbol: string, hours: number, intervalMinutes: number) {
  return {
    exchange: "binance",
    symbol,
    hours,
    interval_minutes: intervalMinutes,
    observed_at: observedAt,
    point_count: 1,
    first_seen_at: observedAt,
    last_seen_at: observedAt,
    premium_pct: {
      min: 0.1,
      max: 0.1,
      mean: 0.1,
      current: 0.1
    },
    current: {
      observed_at: observedAt,
      exchange: "binance",
      symbol,
      raw_symbol: `${symbol}USDT`,
      mark_price: 101,
      index_price: 100,
      mid_price: 100.5,
      last_price: 101,
      premium_pct: 1,
      mid_premium_pct: 0.5,
      funding_rate_pct: 0.01,
      funding_next_rate_pct: null,
      funding_next_time: null,
      source: "mark_index"
    },
    points: [
      {
        bucket_at: observedAt,
        premium_pct: 0.1,
        mark_price: 101,
        index_price: 100,
        source: "test"
      }
    ],
    warnings: []
  };
}

function bybitPremiumResult() {
  return {
    exchange: "bybit",
    symbol: "LAUSDT",
    hours: 4,
    interval_minutes: 60,
    observed_at: "2026-07-21T05:40:00Z",
    point_count: 2,
    first_seen_at: "2026-07-21T04:00:00Z",
    last_seen_at: "2026-07-21T05:00:00Z",
    premium_pct: {
      min: -2,
      max: -2,
      mean: -2,
      current: -2
    },
    current: {
      observed_at: "2026-07-21T05:40:00Z",
      exchange: "bybit",
      symbol: "LAUSDT",
      raw_symbol: "LAUSDT",
      mark_price: 0.06318,
      index_price: 0.06451,
      mid_price: 0.06313,
      last_price: 0.06313,
      premium_pct: -2,
      mid_premium_pct: -2.1392,
      funding_rate_pct: -2,
      funding_next_rate_pct: null,
      funding_next_time: "2026-07-21T08:00:00Z",
      funding_interval_hours: 4,
      funding_rate_upper_pct: 2,
      funding_rate_lower_pct: -2,
      source: "bybit_premium_index"
    },
    points: [
      {
        bucket_at: "2026-07-21T04:00:00Z",
        premium_pct: -2,
        mark_price: null,
        index_price: null,
        source: "bybit_premium_index"
      },
      {
        bucket_at: "2026-07-21T05:00:00Z",
        premium_pct: -2,
        mark_price: null,
        index_price: null,
        source: "bybit_premium_index"
      }
    ],
    warnings: []
  };
}

function okxPremiumResult() {
  return {
    exchange: "okx",
    symbol: "OUSDT",
    hours: 4,
    interval_minutes: 60,
    observed_at: "2026-07-21T05:40:00Z",
    point_count: 2,
    first_seen_at: "2026-07-21T04:00:00Z",
    last_seen_at: "2026-07-21T05:00:00Z",
    premium_pct: {
      min: -1.2,
      max: -1.2,
      mean: -1.2,
      current: -1.2
    },
    current: {
      observed_at: "2026-07-21T05:40:00Z",
      exchange: "okx",
      symbol: "OUSDT",
      raw_symbol: "O-USDT-SWAP",
      mark_price: 0.6279,
      index_price: 0.63684,
      mid_price: 0.628,
      last_price: 0.628,
      premium_pct: -1.2,
      mid_premium_pct: -1.3881,
      funding_rate_pct: -1,
      funding_next_rate_pct: null,
      funding_next_time: "2026-07-21T08:00:00Z",
      funding_interval_hours: 4,
      funding_rate_upper_pct: null,
      funding_rate_lower_pct: null,
      source: "okx_premium_index"
    },
    points: [
      {
        bucket_at: "2026-07-21T04:00:00Z",
        premium_pct: -1.2,
        mark_price: null,
        index_price: null,
        source: "okx_premium_index"
      },
      {
        bucket_at: "2026-07-21T05:00:00Z",
        premium_pct: -1.2,
        mark_price: null,
        index_price: null,
        source: "okx_premium_index"
      }
    ],
    warnings: []
  };
}

function fundingFollowPremiumResult(
  exchange: "binance" | "gate" | "hyperliquid",
  options?: {
    fundingIntervalHours?: number;
    fundingLimitPct?: number;
    fundingRatePct?: number;
    hours?: number;
    intervalMinutes?: number;
    nextFundingTime?: string;
    symbol?: string;
  }
) {
  const symbol = options?.symbol ?? "IOUSDT";
  const fundingIntervalHours = options?.fundingIntervalHours ?? 4;
  const fundingLimitPct = options?.fundingLimitPct ?? 1;
  const fundingRatePct = options?.fundingRatePct ?? -1;
  const hours = options?.hours ?? 4;
  const intervalMinutes = options?.intervalMinutes ?? 60;
  const nextFundingTime = options?.nextFundingTime ?? "2026-07-21T08:00:00Z";
  return {
    exchange,
    symbol,
    hours,
    interval_minutes: intervalMinutes,
    observed_at: "2026-07-21T05:40:00Z",
    point_count: 2,
    first_seen_at: "2026-07-21T04:00:00Z",
    last_seen_at: "2026-07-21T05:00:00Z",
    premium_pct: {
      min: -1.2,
      max: -1.2,
      mean: -1.2,
      current: -1.2
    },
    current: {
      observed_at: "2026-07-21T05:40:00Z",
      exchange,
      symbol,
      raw_symbol: exchange === "gate" ? "IO_USDT" : symbol,
      mark_price: 0.6279,
      index_price: 0.63684,
      mid_price: 0.628,
      last_price: 0.628,
      premium_pct: -1.2,
      mid_premium_pct: -1.3881,
      funding_rate_pct: fundingRatePct,
      funding_next_rate_pct: null,
      funding_next_time: nextFundingTime,
      funding_interval_hours: fundingIntervalHours,
      funding_rate_upper_pct: fundingLimitPct,
      funding_rate_lower_pct: -fundingLimitPct,
      source: `${exchange}_premium_index`
    },
    points: [
      {
        bucket_at: "2026-07-21T04:00:00Z",
        premium_pct: -1.2,
        mark_price: null,
        index_price: null,
        source: `${exchange}_premium_index`
      },
      {
        bucket_at: "2026-07-21T05:00:00Z",
        premium_pct: -1.2,
        mark_price: null,
        index_price: null,
        source: `${exchange}_premium_index`
      }
    ],
    warnings: []
  };
}

describe("PremiumIndexPage", () => {
  const requests: string[] = [];

  beforeEach(() => {
    requests.length = 0;
    window.localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const urlText = String(input);
        requests.push(urlText);
        const url = new URL(urlText, "http://localhost");
        if (url.pathname.includes("/premium-index/query")) {
          const symbol = url.searchParams.get("symbol") ?? "BTC";
          const hours = Number(url.searchParams.get("hours") ?? "12");
          const intervalMinutes = Number(url.searchParams.get("interval_minutes") ?? "1");
          return Response.json(premiumResult(symbol, hours, intervalMinutes));
        }
        if (url.pathname.includes("/premium-index/current")) {
          const symbol = url.searchParams.get("symbol") ?? "BTC";
          return Response.json(premiumResult(symbol, 12, 1).current);
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("saves premium index presets and queries a saved preset", async () => {
    const user = userEvent.setup();
    render(<PremiumIndexPage />);

    const symbolInput = screen.getByPlaceholderText("BTC");
    await user.clear(symbolInput);
    await user.type(symbolInput, "eth");
    await user.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByText("Binance ETH")).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem("taoli1.premiumIndex.presets.v1") ?? "[]")[0]).toMatchObject({
      exchange: "binance",
      symbol: "ETH",
      hours: 12,
      intervalMinutes: 1,
      samplingIntervalSeconds: 8
    });

    await user.clear(symbolInput);
    await user.type(symbolInput, "btc");
    await user.click(screen.getByText("Binance ETH"));

    await waitFor(() => {
      expect(
        requests.some(
          (request) => request.includes("symbol=ETH") && request.includes("hours=12") && request.includes("interval_minutes=1")
        )
      ).toBe(true);
    });
    expect(await screen.findByText("当前溢价指数")).toBeTruthy();
  });

  it("uses and persists the selected real-time sampling interval", async () => {
    const intervalSpy = vi.spyOn(window, "setInterval");
    const user = userEvent.setup();
    render(<PremiumIndexPage />);

    await user.click(screen.getByRole("combobox", { name: "实时采样间隔" }));
    await user.click(await screen.findByText("采样 3 秒"));
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("3s 实时采样")).toBeTruthy();
    await waitFor(() => {
      expect(window.localStorage.getItem("taoli1.premiumIndex.samplingIntervalSeconds.v1")).toBe("3");
      expect(intervalSpy.mock.calls.some((call) => call[1] === 3_000)).toBe(true);
    });

    intervalSpy.mockRestore();
  });

  it("shows the weighted period premium and remaining average needed to hit the funding limit", async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      requests.push(String(input));
      if (url.pathname.includes("/premium-index/query")) {
        return Response.json(bybitPremiumResult());
      }
      if (url.pathname.includes("/premium-index/current")) {
        return Response.json(bybitPremiumResult().current);
      }
      return Response.json({});
    });
    const user = userEvent.setup();
    render(<PremiumIndexPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    const weightedCard = (await screen.findByText("本周期加权溢价指数")).parentElement;
    expect(weightedCard?.textContent).toContain("-2.0000%");
    const requiredCard = screen.getByText("剩余拉满所需溢价指数").parentElement;
    expect(requiredCard?.textContent).toContain("-2.0714%");
    expect(requiredCard?.textContent).toContain("≤ 该值");
    expect(requiredCard?.textContent).not.toContain("-2.1392%");

    await user.click(screen.getByRole("button", { name: /最新/ }));
    await waitFor(() => expect(weightedCard?.textContent).toContain("2点"));
  });

  it("estimates the remaining OKX premium needed when the funding rate looks capped", async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      requests.push(String(input));
      if (url.pathname.includes("/premium-index/query")) {
        return Response.json(okxPremiumResult());
      }
      if (url.pathname.includes("/premium-index/current")) {
        return Response.json(okxPremiumResult().current);
      }
      return Response.json({});
    });
    const user = userEvent.setup();
    render(<PremiumIndexPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    const weightedCard = (await screen.findByText("本周期加权溢价指数")).parentElement;
    expect(weightedCard?.textContent).toContain("-1.2000%");
    const requiredCard = screen.getByText("剩余拉满所需溢价指数").parentElement;
    expect(requiredCard?.textContent).toContain("-2.4143%");
    expect(requiredCard?.textContent).toContain("拉满下限 -1.0000%");
    expect(await screen.findByText("资金费跟随估算（OKX）")).toBeTruthy();
    expect(screen.getByText(/当前交易所返回值疑似触及/)).toBeTruthy();
  });

  it.each([
    {
      exchange: "binance" as const,
      title: "资金费跟随估算（Binance）",
      expectedRequired: "-2.4143%",
      options: undefined
    },
    {
      exchange: "gate" as const,
      title: "资金费跟随估算（Gate）",
      expectedRequired: "-2.4143%",
      options: undefined
    },
    {
      exchange: "hyperliquid" as const,
      title: "资金费跟随估算（Hyperliquid）",
      expectedRequired: "-47.4750%",
      options: {
        fundingIntervalHours: 1,
        fundingLimitPct: 4,
        fundingRatePct: -0.25,
        hours: 1,
        intervalMinutes: 15,
        nextFundingTime: "2026-07-21T06:00:00Z"
      }
    }
  ])("shows remaining premium needed for $exchange funding limit", async ({ exchange, title, expectedRequired, options }) => {
    const response = fundingFollowPremiumResult(exchange, options);
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      requests.push(String(input));
      if (url.pathname.includes("/premium-index/query")) {
        return Response.json(response);
      }
      if (url.pathname.includes("/premium-index/current")) {
        return Response.json(response.current);
      }
      return Response.json({});
    });
    const user = userEvent.setup();
    render(<PremiumIndexPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText(title)).toBeTruthy();
    const requiredCard = screen.getByText("剩余拉满所需溢价指数").parentElement;
    expect(requiredCard?.textContent).toContain(expectedRequired);
    expect(requiredCard?.textContent).toContain(`拉满下限 -${response.current.funding_rate_upper_pct.toFixed(4)}%`);
    expect(requiredCard?.textContent).not.toContain("暂时无法反推");
  });
});
