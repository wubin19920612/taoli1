export type TimeScalePoint = { bucket_at: string };

export type TimeScaleGap = {
  startMs: number;
  endMs: number;
  /** Input point index immediately after the gap. */
  afterIndex: number;
};

export type ChartTimeScale = {
  xOf: (timestampMs: number) => number;
  tOf: (plotX: number) => number;
  gaps: TimeScaleGap[];
  nearestIndex: (timestampMs: number) => number | null;
};

/**
 * Maps epoch milliseconds to plot-local x coordinates (without SVG padding).
 * A single timestamp stays centered; empty input has a zero time domain.
 * Nearest-point ties select the earlier timestamp, then the first input point.
 */
export function buildTimeScale(points: readonly TimeScalePoint[], width: number): ChartTimeScale {
  const plotWidth = Number.isFinite(width) ? Math.max(0, width) : 0;
  const ordered = points
    .map((point, index) => ({ ms: Date.parse(point.bucket_at), index }))
    .filter((point) => Number.isFinite(point.ms))
    .sort((left, right) => left.ms - right.ms || left.index - right.index);
  // Duplicate timestamps neither shorten the median interval nor change tie handling.
  const samples = ordered.filter((point, index) => index === 0 || point.ms !== ordered[index - 1].ms);
  const startMs = samples[0]?.ms ?? 0;
  const endMs = samples[samples.length - 1]?.ms ?? startMs;
  const spanMs = endMs - startMs;
  const intervals = samples.slice(1).map((point, index) => point.ms - samples[index].ms).sort((a, b) => a - b);
  const middle = Math.floor(intervals.length / 2);
  const medianMs = intervals.length === 0
    ? 0
    : intervals.length % 2 === 0
      ? (intervals[middle - 1] + intervals[middle]) / 2
      : intervals[middle];
  const gaps: TimeScaleGap[] = [];
  samples.slice(1).forEach((point, index) => {
    const previous = samples[index];
    if (point.ms - previous.ms > 3 * medianMs) {
      gaps.push({ startMs: previous.ms, endMs: point.ms, afterIndex: point.index });
    }
  });

  return {
    xOf: (timestampMs) => spanMs === 0 ? plotWidth / 2 : (timestampMs - startMs) / spanMs * plotWidth,
    tOf: (plotX) => plotWidth === 0 ? startMs : startMs + plotX / plotWidth * spanMs,
    gaps,
    nearestIndex: (timestampMs) => {
      if (!samples.length || !Number.isFinite(timestampMs)) return null;
      let low = 0;
      let high = samples.length;
      while (low < high) {
        const mid = Math.floor((low + high) / 2);
        if (samples[mid].ms < timestampMs) low = mid + 1;
        else high = mid;
      }
      if (low === 0) return samples[0].index;
      if (low === samples.length) return samples[low - 1].index;
      const before = samples[low - 1];
      const after = samples[low];
      return timestampMs - before.ms <= after.ms - timestampMs ? before.index : after.index;
    }
  };
}
