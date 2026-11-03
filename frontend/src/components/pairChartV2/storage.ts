export type ChartVersion = "v1" | "v2";
export type Thresholds = { open: number | null; close: number | null };
const VERSION_KEY = "pair-chart-version";
const THRESHOLDS_KEY = "pair-chart-thresholds";
const finite = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

export function readVersion(): ChartVersion {
  try { return localStorage.getItem(VERSION_KEY) === "v1" ? "v1" : "v2"; }
  catch { return "v2"; }
}

export function writeVersion(version: ChartVersion): void {
  try { localStorage.setItem(VERSION_KEY, version === "v1" ? "v1" : "v2"); } catch { /* Storage may be unavailable. */ }
}

export function readThresholds(): Thresholds {
  try {
    const value = JSON.parse(localStorage.getItem(THRESHOLDS_KEY) ?? "null");
    return { open: finite(value?.open), close: finite(value?.close) };
  } catch { return { open: null, close: null }; }
}

export function writeThresholds(value: Thresholds): void {
  try {
    localStorage.setItem(THRESHOLDS_KEY, JSON.stringify({ open: finite(value.open), close: finite(value.close) }));
  } catch { /* Keep the in-memory settings usable. */ }
}
