import { vi } from "vitest";
import type { PairSpreadQueryResult } from "../../api/types";

export const chartMocks: ReturnType<typeof buildMockChart>[] = [];
function buildMockChart() {
  const series: ReturnType<typeof makeSeries>[] = [];
  function makeSeries() {
    return { setData: vi.fn(), applyOptions: vi.fn(), createPriceLine: vi.fn(() => ({ applyOptions: vi.fn() })), removePriceLine: vi.fn() };
  }
  const base = Date.parse("2026-11-03T00:00:00Z") / 1000;
  const scale = {
    fitContent: vi.fn(), width: () => 600,
    coordinateToTime: vi.fn((x: number) => base + Math.round(x)), timeToCoordinate: vi.fn((time: number) => time - base),
    subscribeVisibleLogicalRangeChange: vi.fn(), unsubscribeVisibleLogicalRangeChange: vi.fn(),
    subscribeSizeChange: vi.fn(), unsubscribeSizeChange: vi.fn()
  };
  const panes = [0, 1].map(() => ({ setHeight: vi.fn(), getHeight: () => 340 }));
  const chart = {
    series, scale, addSeries: vi.fn(() => { const item = makeSeries(); series.push(item); return item; }),
    applyOptions: vi.fn(), remove: vi.fn(), timeScale: () => scale, priceScale: () => ({ width: () => 80 }), panes: () => panes,
    subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn()
  };
  return chart;
}

export function createMockChart() {
  const chart = buildMockChart();
  chartMocks.push(chart);
  return chart;
}

export function chartFixture(): PairSpreadQueryResult {
  const stats = { min: 0, max: 1, mean: 0.5, current: 1 };
  return {
    leg1: { exchange: "binance", symbol: "BTCUSDT", market_type: "future" },
    leg2: { exchange: "bitget", symbol: "BTCUSDT", market_type: "future" },
    hours: 4, interval_minutes: 1, interval_seconds: 60, leg2_multiplier: 1,
    observed_at: "2026-11-03T00:10:00Z", first_seen_at: "2026-11-03T00:00:00Z", last_seen_at: "2026-11-03T00:10:00Z",
    point_count: 3, spread_abs: stats, spread_pct: stats, current: null, warnings: [],
    points: [0, 300, 600].map((seconds, index) => ({ bucket_at: new Date(Date.parse("2026-11-03T00:00:00Z") + seconds * 1000).toISOString(),
      leg1_close: 100 + index, leg2_close: 101 + index, spread_abs: 1, spread_pct: 1 - index / 100 })),
    funding_history: [
      { exchange: "binance", symbol: "BTCUSDT", funding_time: "2026-11-03T00:00:00Z", funding_rate_pct: 0.01 },
      { exchange: "bitget", symbol: "BTCUSDT", funding_time: "2026-11-03T00:00:00Z", funding_rate_pct: 0.03 }
    ],
    realtime_funding: [{ bucket_at: "2026-11-03T00:05:00Z", left_rate_pct: 0.02, right_rate_pct: 0.04, net_rate_pct: 0.02 }]
  };
}
