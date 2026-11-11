import { Empty } from "antd";
import { createChart, HistogramSeries, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { LiveStats } from "../../api/liveExec";

export function ProfitHistogram({ bins }: { bins: LiveStats["histogram"] | undefined }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current || !bins?.length) return;
    // The time axis is a discrete bucket index; labels always show bp ranges.
    const base = 946684800;
    const label = (time: number) => {
      const bin = bins[Math.round((time - base) / 86400)];
      return bin ? `${bin.lo}～${bin.hi} bp` : "";
    };
    const chart = createChart(container.current, {
      height: 260, autoSize: true,
      localization: { locale: "zh-CN", timeFormatter: (time: Time) => label(Number(time)) },
      timeScale: { tickMarkFormatter: (time: Time) => label(Number(time)) },
      rightPriceScale: { minimumWidth: 45 },
    });
    const series = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "custom", formatter: (value: number) => `${value.toFixed(0)} 笔`, minMove: 1 },
      lastValueVisible: false, priceLineVisible: false,
    });
    series.setData(bins.map((bin, index) => ({
      time: (base + index * 86400) as UTCTimestamp, value: bin.count,
      color: bin.hi <= 0 ? "#cf1322" : "#389e0d",
    })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bins]);
  return bins?.length ? <div ref={container} aria-label="净收益基点直方图" /> : <Empty description="暂无利润分布" />;
}
