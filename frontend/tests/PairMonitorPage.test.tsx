import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PairMonitorPage } from "../src/pages/PairMonitorPage";

const observedAt = "2026-07-24T02:00:00Z";

function normalizedMockSymbol(exchange: string, symbol: string) {
  const text = symbol.trim().toUpperCase();
  if (exchange === "binance_alpha") {
    return text;
  }
  return text.endsWith("USDT") ? text : `${text}USDT`;
}

function pairSpreadResult(params?: URLSearchParams) {
  const leg1MarketType = params?.get("leg1_market_type") === "spot" ? "spot" : "future";
  const leg2MarketType = params?.get("leg2_market_type") === "spot" ? "spot" : "future";
  const leg1Exchange = params?.get("leg1_exchange") ?? "bitget";
  const leg2Exchange = params?.get("leg2_exchange") ?? "bitget";
  const requestedLeg1Symbol = params?.get("leg1_symbol") ?? "SKHYUSDT";
  const requestedLeg2Symbol = params?.get("leg2_symbol") ?? "SKHYNIXUSDT";
  const leg1Symbol = normalizedMockSymbol(leg1Exchange, requestedLeg1Symbol);
  const leg2Symbol = normalizedMockSymbol(leg2Exchange, requestedLeg2Symbol);
  const leg2Multiplier = Number(params?.get("leg2_multiplier") ?? 1);
  const intervalSeconds = Number(params?.get("interval_seconds") ?? 5);
  const intervalMinutes = Number(params?.get("interval_minutes") ?? Math.max(1, Math.round(intervalSeconds / 60)));
  const hours = Number(params?.get("hours") ?? 4);
  const includeCurrent = params?.get("include_current") !== "false";
  const requestedEndAt = params?.get("end_at");
  const baseObservedAt = requestedEndAt ?? observedAt;
  const compareDayOffset = requestedEndAt ? Math.max(0, Math.round((Date.parse(observedAt) - Date.parse(requestedEndAt)) / 86_400_000)) : 0;
  const compareSpreadBias = compareDayOffset * 0.18;
  const largePriceGap = leg2Multiplier === 0.1;
  const currentTailMismatch = requestedLeg1Symbol.trim().toUpperCase() === "TAIL";
  const leg1Price = largePriceGap ? 1218.6 : 100;
  const leg2Price = largePriceGap ? 1618.3 : currentTailMismatch ? 99.94 : 101;
  const currentSpreadAbs = largePriceGap ? 399.7 : currentTailMismatch ? -0.06 : 1;
  const currentSpreadPct = largePriceGap ? 28.18 : currentTailMismatch ? -0.06 : 0.5;
  const fundingHistory =
    leg1MarketType === "future" && leg2MarketType === "future"
      ? [
          {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            funding_time: "2026-07-24T00:00:00Z",
            funding_rate_pct: -0.1
          },
          {
            exchange: leg2Exchange,
            symbol: leg2Symbol,
            funding_time: "2026-07-24T00:00:40Z",
            funding_rate_pct: -0.07
          },
          {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            funding_time: "2026-07-23T16:00:00Z",
            funding_rate_pct: 0.01
          },
          {
            exchange: leg2Exchange,
            symbol: leg2Symbol,
            funding_time: "2026-07-23T16:00:00Z",
            funding_rate_pct: 0.01
          },
          {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            funding_time: "2026-07-23T08:00:00Z",
            funding_rate_pct: -0.1
          }
        ]
      : [];
  const realtimeFunding =
    includeCurrent && leg1MarketType === "future" && leg2MarketType === "future"
      ? [
          {
            bucket_at: "2026-07-24T01:59:50Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.015,
            net_rate_pct: 0.005,
            source: "current"
          },
          {
            bucket_at: "2026-07-24T01:59:55Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.02,
            net_rate_pct: 0.01,
            source: "current"
          },
          {
            bucket_at: "2026-07-24T02:00:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.025,
            net_rate_pct: 0.015,
            source: "current"
          }
        ]
      : [];
  const points = currentTailMismatch
    ? [
        {
          bucket_at: "2026-07-24T01:58:00Z",
          leg1_close: 100,
          leg2_close: 98.8,
          spread_abs: -1.2,
          spread_pct: -1.21
        },
        {
          bucket_at: "2026-07-24T01:59:00Z",
          leg1_close: 100,
          leg2_close: 98.5,
          spread_abs: -1.5,
          spread_pct: -1.51
        }
      ]
    : largePriceGap
      ? [
        {
          bucket_at: "2026-07-24T02:00:00Z",
          leg1_close: 1200,
          leg2_close: 1600,
          spread_abs: 400,
          spread_pct: 28.5
        },
        {
          bucket_at: "2026-07-24T02:01:00Z",
          leg1_close: 1210,
          leg2_close: 1590,
          spread_abs: 380,
          spread_pct: 27.2
        },
        {
          bucket_at: "2026-07-24T02:02:00Z",
          leg1_close: leg1Price,
          leg2_close: leg2Price,
          spread_abs: 399.7,
          spread_pct: 28.18
        }
      ]
    : [
        {
          bucket_at: baseObservedAt,
          leg1_close: 100,
          leg2_close: 101,
          spread_abs: 1,
          spread_pct: 0.5
        }
      ];
  const historicalPoints = includeCurrent
    ? points
    : points.map((point, index) => ({
        ...point,
        bucket_at: new Date(Date.parse(baseObservedAt) - (points.length - index - 1) * intervalSeconds * 1000).toISOString(),
        spread_abs: Number((point.spread_abs + compareSpreadBias).toFixed(4)),
        spread_pct: Number((point.spread_pct + compareSpreadBias).toFixed(4))
      }));
  return {
    leg1: {
      exchange: leg1Exchange,
      symbol: leg1Symbol,
      market_type: leg1MarketType
    },
    leg2: {
      exchange: leg2Exchange,
      symbol: leg2Symbol,
      market_type: leg2MarketType
    },
    hours,
    interval_minutes: intervalMinutes,
    interval_seconds: intervalSeconds,
    leg2_multiplier: leg2Multiplier,
    observed_at: baseObservedAt,
    point_count: historicalPoints.length,
    first_seen_at: historicalPoints[0]?.bucket_at ?? baseObservedAt,
    last_seen_at: historicalPoints[historicalPoints.length - 1]?.bucket_at ?? baseObservedAt,
    spread_abs: { min: 1, max: 1, mean: 1, current: 1 },
    spread_pct: { min: 0.5, max: 0.5, mean: 0.5, current: 0.5 },
    current: includeCurrent
      ? {
          observed_at: observedAt,
          leg1: {
            exchange: leg1Exchange,
            symbol: leg1Symbol,
            market_type: leg1MarketType,
            raw_symbol: leg1Symbol,
            price: leg1Price,
            price_field: "mid_price" as const,
            mark_price: null,
            index_price: null,
            mid_price: leg1Price,
            last_price: leg1Price,
            funding_rate_pct: leg1MarketType === "spot" ? null : 0.01,
            funding_next_rate_pct: null,
            funding_next_time: null,
            funding_interval_hours: null,
            funding_rate_upper_pct: null,
            funding_rate_lower_pct: null,
            timestamp: observedAt
          },
          leg2: {
            exchange: leg2Exchange,
            symbol: leg2Symbol,
            market_type: leg2MarketType,
            raw_symbol: leg2Symbol,
            price: leg2Price,
            price_field: leg2MarketType === "spot" ? "last_price" as const : "mark_price" as const,
            mark_price: leg2MarketType === "spot" ? null : leg2Price,
            index_price: leg2MarketType === "spot" ? null : 100,
            mid_price: leg2Price,
            last_price: leg2Price,
            funding_rate_pct: leg2MarketType === "spot" ? null : 0.01,
            funding_next_rate_pct: null,
            funding_next_time: null,
            funding_interval_hours: 8,
            funding_rate_upper_pct: null,
            funding_rate_lower_pct: null,
            timestamp: observedAt
          },
          spread_abs: currentSpreadAbs,
          spread_pct: currentSpreadPct
        }
      : null,
    points: historicalPoints,
    funding_history: includeCurrent ? fundingHistory : [],
    realtime_funding: realtimeFunding,
    warnings: []
  };
}

function pairFundingRecordStatus(watched: boolean) {
  return {
    watched,
    item: watched
      ? {
          pair_key: "bitget|future|SKHYUSDT|bitget|future|SKHYNIXUSDT|1|60",
          leg1: { exchange: "bitget", symbol: "SKHYUSDT", market_type: "future" },
          leg2: { exchange: "bitget", symbol: "SKHYNIXUSDT", market_type: "future" },
          leg2_multiplier: 1,
          interval_seconds: 60,
          created_at: "2026-07-24T01:58:00Z",
          updated_at: "2026-07-24T02:02:00Z",
          sample_count: 3,
          latest_sample_at: "2026-07-24T02:02:00Z"
        }
      : null,
    samples: watched
      ? [
          {
            bucket_at: "2026-07-24T02:00:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.015,
            net_rate_pct: 0.005,
            source: "minute_record"
          },
          {
            bucket_at: "2026-07-24T02:01:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.02,
            net_rate_pct: 0.01,
            source: "minute_record"
          },
          {
            bucket_at: "2026-07-24T02:02:00Z",
            left_rate_pct: 0.01,
            right_rate_pct: 0.025,
            net_rate_pct: 0.015,
            source: "minute_record"
          }
        ]
      : [],
    warnings: []
  };
}

describe("PairMonitorPage", () => {
  const requests: string[] = [];
  let fundingRecordWatched = false;

  beforeEach(() => {
    requests.length = 0;
    fundingRecordWatched = false;
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlText = String(input);
        requests.push(urlText);
        const url = new URL(urlText, "http://localhost");
        if (url.pathname.includes("/pair-spread/query")) {
          return Response.json(pairSpreadResult(url.searchParams));
        }
        if (url.pathname.includes("/pair-spread/funding-records/status")) {
          return Response.json(pairFundingRecordStatus(fundingRecordWatched));
        }
        if (url.pathname.includes("/pair-spread/funding-records/watch")) {
          fundingRecordWatched = init?.method !== "DELETE";
          return Response.json(pairFundingRecordStatus(fundingRecordWatched));
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("sends the selected spot market type with the pair spread query", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[1]);
    const spotOptions = await screen.findAllByText("现货");
    await user.click(spotOptions[spotOptions.length - 1]);
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("leg1_market_type=spot") &&
            request.includes("leg2_market_type=future")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("Bitget · 现货 · SKHYUSDT")).length).toBeGreaterThan(0);
  });

  it("loads old saved presets as future contracts", () => {
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([
        {
          id: "legacy",
          leg1_exchange: "bybit",
          leg1_symbol: "DEXE",
          leg2_exchange: "bitget",
          leg2_symbol: "DEXE",
          leg2_multiplier: 1,
          hours: 24,
          intervalMinutes: 5,
          savedAt: observedAt
        }
      ])
    );

    render(<PairMonitorPage />);

    expect(screen.getByText("bybit 合约 DEXE / bitget 合约 DEXE")).toBeTruthy();
  });

  it("uses 5-second pair spread queries by default", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("interval_seconds=5") &&
            request.includes("interval_minutes=1")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("5秒")).length).toBeGreaterThan(0);
  });

  it("loads same-time day comparison with historical minute queries", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    await user.click(screen.getByRole("switch", { name: /同时段/ }));

    await waitFor(() => {
      expect(requests.filter((request) => request.includes("include_current=false"))).toHaveLength(2);
    });
    const compareRequests = requests
      .map((request) => new URL(request, "http://localhost"))
      .filter((url) => url.searchParams.get("include_current") === "false");
    expect(compareRequests.every((url) => url.searchParams.get("interval_seconds") === "60")).toBe(true);
    expect(compareRequests.every((url) => url.searchParams.get("interval_minutes") === "1")).toBe(true);
    expect(compareRequests.map((url) => url.searchParams.get("end_at") ?? "")[0]).toContain("2026-07-23T02:00:00");
    expect(compareRequests.map((url) => url.searchParams.get("end_at") ?? "")[1]).toContain("2026-07-22T02:00:00");

    await waitFor(() => {
      expect(document.querySelector(".pair-day-compare-chart")).toBeTruthy();
      expect(document.querySelectorAll(".pair-day-compare-line").length).toBeGreaterThan(1);
    });
    const cachedState = JSON.parse(window.sessionStorage.getItem("taoli1.pairSpread.lastState.v1") ?? "{}");
    expect(cachedState.showDayCompare).toBe(true);
    expect(cachedState.dayCompareDays).toBe(3);
  });

  it("loads same-time comparison for a custom Beijing time window", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    await user.click(screen.getByRole("switch", { name: /同时段/ }));
    await waitFor(() => {
      expect(requests.filter((request) => request.includes("include_current=false"))).toHaveLength(2);
    });
    requests.length = 0;

    await user.click(screen.getByText("指定时间"));
    fireEvent.change(screen.getByLabelText("同时段开始时间"), { target: { value: "09:00" } });
    fireEvent.change(screen.getByLabelText("同时段结束时间"), { target: { value: "15:00" } });

    await new Promise((resolve) => window.setTimeout(resolve, 20));
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);

    await user.click(screen.getByRole("button", { name: /查询同时段/ }));

    await waitFor(() => {
      expect(requests.filter((request) => request.includes("include_current=false"))).toHaveLength(3);
    });
    const compareRequests = requests
      .map((request) => new URL(request, "http://localhost"))
      .filter((url) => url.searchParams.get("include_current") === "false");
    expect(compareRequests.every((url) => url.searchParams.get("interval_seconds") === "60")).toBe(true);
    expect(compareRequests.every((url) => url.searchParams.get("interval_minutes") === "1")).toBe(true);
    expect(compareRequests.every((url) => url.searchParams.get("hours") === "6")).toBe(true);
    expect(compareRequests.map((url) => url.searchParams.get("end_at"))).toEqual([
      "2026-07-23T07:00:00.000Z",
      "2026-07-22T07:00:00.000Z",
      "2026-07-21T07:00:00.000Z"
    ]);

    await waitFor(() => {
      expect(document.querySelector(".pair-day-compare-chart")).toBeTruthy();
      expect(screen.getByText("09:00-15:00")).toBeTruthy();
    });
    const cachedState = JSON.parse(window.sessionStorage.getItem("taoli1.pairSpread.lastState.v1") ?? "{}");
    expect(cachedState.dayCompareMode).toBe("custom");
    expect(cachedState.dayCompareStartTime).toBe("09:00");
    expect(cachedState.dayCompareEndTime).toBe("15:00");

    await user.click(screen.getByRole("button", { name: /保存/ }));
    const savedPreset = JSON.parse(window.localStorage.getItem("taoli1.pairSpread.presets.v1") ?? "[]")[0];
    expect(savedPreset.dayCompareMode).toBe("custom");
    expect(savedPreset.dayCompareStartTime).toBe("09:00");
    expect(savedPreset.dayCompareEndTime).toBe("15:00");
  });

  it("sends a custom second interval when selected", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[comboboxes.length - 1]);
    await user.click(await screen.findByText("自定义"));
    const customInput = screen.getByRole("spinbutton", { name: /自定义秒/ });
    await user.clear(customInput);
    await user.type(customInput, "7");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("interval_seconds=7"))).toBe(true);
    });
    expect(new URLSearchParams(window.location.search).get("interval_seconds")).toBe("7");
  });

  it("does not auto-query or reset form values when editing hours", async () => {
    const user = userEvent.setup();
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=bitget&leg1_market_type=future&leg1_symbol=SKHY" +
        "&leg2_exchange=bitget&leg2_market_type=future&leg2_symbol=SKHYNIX&hours=1&interval_seconds=5"
    );
    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-card")).toBeTruthy();
    });
    requests.length = 0;

    const leftSymbolInput = screen.getByPlaceholderText("SKHY") as HTMLInputElement;
    await user.clear(leftSymbolInput);
    await user.type(leftSymbolInput, "CXMTUSDT");
    const hoursInput = document.querySelector(".pair-query-hours input") as HTMLInputElement;
    fireEvent.change(hoursInput, { target: { value: "12" } });

    await new Promise((resolve) => window.setTimeout(resolve, 20));

    expect(leftSymbolInput.value).toBe("CXMTUSDT");
    expect(hoursInput.value).toBe("12");
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });

  it("fills a saved preset without querying immediately", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(
      "taoli1.pairSpread.presets.v1",
      JSON.stringify([
        {
          id: "cxmt",
          leg1_exchange: "aster",
          leg1_market_type: "future",
          leg1_symbol: "CXMTUSDT",
          leg2_exchange: "gate",
          leg2_market_type: "future",
          leg2_symbol: "CXMTUSDT",
          leg2_multiplier: 1,
          hours: 12,
          intervalSeconds: 60,
          showDayCompare: true,
          dayCompareDays: 4,
          savedAt: observedAt
        }
      ])
    );
    render(<PairMonitorPage />);
    requests.length = 0;

    const savedTag = document.querySelector(".pair-saved-tag") as HTMLElement;
    await user.click(savedTag);

    expect((screen.getByPlaceholderText("SKHY") as HTMLInputElement).value).toBe("CXMTUSDT");
    expect((screen.getByPlaceholderText("SKHYNIX") as HTMLInputElement).value).toBe("CXMTUSDT");
    expect((document.querySelector(".pair-query-hours input") as HTMLInputElement).value).toBe("12");
    expect((screen.getByRole("spinbutton", { name: /同时段对比天数/ }) as HTMLInputElement).value).toBe("4");
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });

  it("shows the funding rate difference table", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("资金费率差")).toBeTruthy();
    expect(screen.getAllByText("净费率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.0300%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.1000%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+0.0000%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Bitget 费率").length).toBeGreaterThan(0);
    const fundingLayout = Array.from(document.querySelector(".pair-funding-grid")?.children ?? []).map(
      (element) => (element as HTMLElement).className
    );
    expect(fundingLayout[0]).toContain("pair-funding-diff-card");
    expect(fundingLayout).toHaveLength(1);
    expect(await screen.findByText("未记录分钟费率")).toBeTruthy();
    expect(screen.getByRole("button", { name: /开始记录/ })).toBeTruthy();
    expect(document.querySelector(".pair-funding-diff-chart")).toBeNull();
    expect(document.querySelector(".pair-funding-raw-card")).toBeNull();
    const pageText = document.body.textContent ?? "";
    expect(pageText).not.toContain("分钟资金费率差图");
    const pageLayout = Array.from(document.querySelector(".pair-monitor-page")?.children ?? []).map((element) =>
      (element as HTMLElement).className.toString()
    );
    const spreadChartIndex = pageLayout.findIndex((className) => className.includes("pair-chart-card"));
    const priceChartIndex = pageLayout.findIndex((className) => className.includes("pair-price-card"));
    const fundingGridIndex = pageLayout.findIndex((className) => className.includes("pair-funding-grid"));
    expect(spreadChartIndex).toBeGreaterThan(-1);
    expect(priceChartIndex).toBe(spreadChartIndex + 1);
    expect(fundingGridIndex).toBeGreaterThan(priceChartIndex);
    expect(pageText.indexOf("标的价格")).toBeLessThan(pageText.indexOf("资金费率差"));
  });

  it("starts minute funding recording before showing the funding rate difference chart", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    await user.click(await screen.findByRole("button", { name: /开始记录/ }));

    await waitFor(() => {
      expect(requests.some((request) => request.includes("/pair-spread/funding-records/watch"))).toBe(true);
    });
    expect(await screen.findByText("分钟资金费率差图")).toBeTruthy();
    expect(screen.getByText("分钟记录样本")).toBeTruthy();
    expect(screen.getByText("分钟记录已开启")).toBeTruthy();
    expect(document.querySelector(".pair-funding-diff-chart")).toBeTruthy();
    expect(document.querySelector(".pair-minute-funding-point")).toBeTruthy();
    expect(document.querySelector(".pair-minute-funding-turning-dot")).toBeTruthy();
    expect((document.querySelector(".pair-funding-diff-chart .pair-funding-chart-line") as SVGPathElement).getAttribute("d")).toContain("L ");
  });

  it("uses the realtime current spread as the chart tail point", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=aster&leg1_market_type=future&leg1_symbol=TAIL" +
        "&leg2_exchange=gate&leg2_market_type=future&leg2_symbol=TAIL&hours=6&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(document.querySelector(".pair-chart-current-point")).toBeTruthy();
    });
    const currentPointTitle = document.querySelector(".pair-chart-current-point title")?.textContent ?? "";
    expect(currentPointTitle).toContain("-0.06%");
    expect((document.querySelector(".pair-spread-chart .pair-chart-line") as SVGPathElement).getAttribute("d")).toContain(
      "L "
    );
  });

  it("auto-runs a Binance Alpha spread query from URL parameters", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=binance&leg1_market_type=future&leg1_symbol=AKEUSDT" +
        "&leg2_exchange=binance_alpha&leg2_market_type=spot&leg2_symbol=ALPHA_331USDT" +
        "&leg2_multiplier=1&hours=4&interval_seconds=5"
    );

    render(<PairMonitorPage />);

    await waitFor(() => {
      expect(
        requests.some(
          (request) =>
            request.includes("leg1_exchange=binance") &&
            request.includes("leg2_exchange=binance_alpha") &&
            request.includes("leg2_symbol=ALPHA_331USDT") &&
            request.includes("interval_seconds=5") &&
            request.includes("interval_minutes=1") &&
            request.includes("hours=4")
        )
      ).toBe(true);
    });
    expect((await screen.findAllByText("Binance Alpha · 现货 · ALPHA_331USDT")).length).toBeGreaterThan(0);
  });

  it("switches the price chart to indexed trend view when leg prices are far apart", async () => {
    window.history.pushState(
      {},
      "",
      "/?page=pair-monitor&leg1_exchange=hyperliquid&leg1_market_type=future&leg1_symbol=SKHXUSDT" +
        "&leg2_exchange=hyperliquid&leg2_market_type=future&leg2_symbol=SKHYUSDT" +
        "&leg2_multiplier=0.1&hours=6&interval_seconds=60"
    );

    render(<PairMonitorPage />);

    expect(await screen.findByText("自动：相对走势 · 首点=100")).toBeTruthy();
    expect(screen.getByText(/原始最新价：左 1218.60 \/ 右 1618.30/)).toBeTruthy();
  });

  it("opens the premium index page for a futures leg from the spread result", async () => {
    const user = userEvent.setup();
    render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    const premiumButtons = await screen.findAllByRole("button", { name: /查看溢价指数/ });
    await user.click(premiumButtons[0]);

    const params = new URLSearchParams(window.location.search);
    expect(params.get("page")).toBe("premium-index");
    expect(params.get("from")).toBe("pair-monitor");
    expect(params.get("exchange")).toBe("bitget");
    expect(params.get("symbol")).toBe("SKHYUSDT");
    expect(params.get("leg1_exchange")).toBe("bitget");
    expect(params.get("leg1_market_type")).toBe("future");
    expect(params.get("leg1_symbol")).toBe("SKHYUSDT");
    expect(params.get("leg2_exchange")).toBe("bitget");
    expect(params.get("leg2_market_type")).toBe("future");
    expect(params.get("leg2_symbol")).toBe("SKHYNIXUSDT");
    expect(params.get("leg2_multiplier")).toBe("1");
    expect(params.get("hours")).toBe("4");
    expect(params.get("interval_minutes")).toBe("1");
    expect(params.get("interval_seconds")).toBe("5");
  });

  it("restores the last spread result from session storage after returning from another page", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<PairMonitorPage />);

    await user.click(screen.getByRole("button", { name: /查询/ }));
    expect((await screen.findAllByText("Bitget · 合约 · SKHYUSDT")).length).toBeGreaterThan(0);

    window.history.pushState(
      {},
      "",
      "/?page=premium-index&exchange=bitget&symbol=SKHYUSDT&hours=4&interval_minutes=5"
    );
    unmount();
    requests.length = 0;

    render(<PairMonitorPage />);

    expect(screen.getAllByText("+0.50%").length).toBeGreaterThan(0);
    expect((screen.getByPlaceholderText("SKHY") as HTMLInputElement).value).toBe("SKHYUSDT");
    expect((await screen.findAllByText("Bitget · 合约 · SKHYUSDT")).length).toBeGreaterThan(0);
    expect(requests.some((request) => request.includes("/pair-spread/query"))).toBe(false);
  });
});
