import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { IChartApi, UTCTimestamp } from "lightweight-charts";

type Range = { start: UTCTimestamp; end: UTCTimestamp };
type Box = { left: number; width: number; height: number };

export function useRangeSelection(host: RefObject<HTMLDivElement>, chartRef: RefObject<IChartApi | null>,
  enabled: boolean, onSelect: (start: number, end: number) => void, onClear: () => void) {
  const callbacks = useRef({ enabled, onSelect, onClear });
  callbacks.current = { enabled, onSelect, onClear };
  const selected = useRef<Range | null>(null);
  const dragging = useRef<Range | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const clear = useCallback(() => {
    selected.current = null;
    dragging.current = null;
    setBox(null);
    callbacks.current.onClear();
  }, []);

  useEffect(() => {
    const chart = chartRef.current, element = host.current;
    if (!chart || !element) return;
    const scale = chart.timeScale();
    let startX = 0;
    const draw = () => {
      const range = dragging.current ?? selected.current;
      if (!range) { setBox(null); return; }
      const a = scale.timeToCoordinate(range.start), b = scale.timeToCoordinate(range.end);
      if (a === null || b === null) { setBox(null); return; }
      const left = Math.max(0, Math.min(scale.width(), Math.min(a, b)));
      const right = Math.max(0, Math.min(scale.width(), Math.max(a, b)));
      setBox({ left: chart.priceScale("left").width() + left, width: right - left, height: chart.panes()[0].getHeight() });
    };
    const xOf = (event: MouseEvent) => event.clientX - element.getBoundingClientRect().left - chart.priceScale("left").width();
    const timeOf = (x: number) => {
      const time = scale.coordinateToTime(Math.max(0, Math.min(scale.width() - 1, x)));
      return typeof time === "number" ? time as UTCTimestamp : null;
    };
    const stop = (event: MouseEvent) => { event.preventDefault(); event.stopPropagation(); };
    const down = (event: MouseEvent) => {
      if (event.button !== 0 || (!event.shiftKey && !callbacks.current.enabled)) return;
      const x = xOf(event), y = event.clientY - element.getBoundingClientRect().top;
      if (x < 0 || x > scale.width() || y < 0 || y > chart.panes()[0].getHeight()) return;
      const time = timeOf(x);
      if (time === null) return;
      stop(event);
      startX = x;
      dragging.current = { start: time, end: time };
      draw();
    };
    const move = (event: MouseEvent) => {
      if (!dragging.current) return;
      stop(event);
      const time = timeOf(xOf(event));
      if (time !== null) dragging.current.end = time;
      draw();
    };
    const up = (event: MouseEvent) => {
      if (!dragging.current || event.button !== 0) return;
      move(event);
      const range = dragging.current;
      dragging.current = null;
      if (Math.abs(xOf(event) - startX) >= 8 && range.start !== range.end) {
        selected.current = { start: Math.min(range.start, range.end) as UTCTimestamp, end: Math.max(range.start, range.end) as UTCTimestamp };
        callbacks.current.onSelect(selected.current.start * 1000, selected.current.end * 1000);
      }
      draw();
    };
    const cancel = () => { dragging.current = null; draw(); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") clear(); };
    element.addEventListener("mousedown", down, true);
    window.addEventListener("mousemove", move, true);
    window.addEventListener("mouseup", up, true);
    window.addEventListener("keydown", key);
    window.addEventListener("blur", cancel);
    scale.subscribeVisibleLogicalRangeChange(draw);
    scale.subscribeSizeChange(draw);
    return () => {
      element.removeEventListener("mousedown", down, true);
      window.removeEventListener("mousemove", move, true);
      window.removeEventListener("mouseup", up, true);
      window.removeEventListener("keydown", key);
      window.removeEventListener("blur", cancel);
      scale.unsubscribeVisibleLogicalRangeChange(draw);
      scale.unsubscribeSizeChange(draw);
    };
  }, [host, chartRef, clear]);
  return { box, clear };
}
