import type { PollingHealth } from "./usePollingHealth";

export function PollingStatus({ health }: { health?: PollingHealth }) {
  return <span className="live-polling-status" role="status" style={{ display: "inline-block", width: 180,
    height: 22, lineHeight: "22px", marginLeft: 12, verticalAlign: "middle", fontSize: 12,
    fontWeight: "normal", color: "#ad6800", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap",
    visibility: health?.stale ? "visible" : "hidden" }}>
    {health?.stale ? `数据已过期 ${health.ageSeconds} 秒` : "\u00a0"}
  </span>;
}

export function oldestHealth(...values: (PollingHealth | undefined)[]): PollingHealth | undefined {
  return values.filter((value): value is PollingHealth => !!value)
    .sort((a, b) => Number(b.stale) - Number(a.stale) || b.ageSeconds - a.ageSeconds)[0];
}
