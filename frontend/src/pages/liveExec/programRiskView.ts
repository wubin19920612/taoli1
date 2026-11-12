import type { LiveProgramIncome, LiveRemotePositions, LiveSummary } from "../../api/liveExec";
import { remoteSnapshotFresh } from "./remotePositionView";

// Keep upstream decimal strings intact, including the side of a precise boundary.
// Bound exponents and input size before constructing BigInts from remote data.
function decimal(value: unknown): { units: bigint; scale: number; text: string } | null {
  if (typeof value !== "string" || value.length > 512) return null;
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(value);
  if (!match) return null;
  const exponent = Number(match[5] ?? 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return null;
  const fraction = match[3] ?? match[4] ?? "";
  return { units: BigInt(`${match[1] === "-" ? "-" : ""}${match[2] ?? "0"}${fraction}`),
    scale: fraction.length - exponent, text: value };
}

export function programRiskView(income: LiveProgramIncome | null | undefined, snapshot: LiveRemotePositions | null,
  summary: LiveSummary | null, now: number) {
  const total = decimal(income?.program_total);
  const captured = income?.captured_at;
  const verified = income?.verified === true && income.scope === "program" && total !== null
    && remoteSnapshotFresh(snapshot, now) && income.snapshot_version === snapshot?.snapshot_version
    && typeof captured === "number" && Number.isFinite(captured) && 0 <= now - captured * 1000 && now - captured * 1000 <= 10000
    && income.day === new Date(now).toISOString().slice(0, 10)
    && Number.isSafeInteger(income.revision) && income.revision! >= 0
    && Number.isSafeInteger(income.equity_revision) && income.equity_revision! >= 0
    && (summary?.equity_baseline_revision == null || income.equity_revision === summary.equity_baseline_revision)
    && typeof income.program_journal === "string" && income.program_journal.length > 0;
  const configured = summary?.effective_risk_limits?.max_equity_drop_usd;
  const cap = summary?.caps?.equity_drop_max_usd;
  const validLimit = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value > 0;
  const limit = (configured != null || cap != null) && (configured == null || validLimit(configured))
    && (cap == null || validLimit(cap)) ? Math.min(configured ?? 150, cap ?? 150, 150) : null;
  const threshold = typeof limit === "number" && Number.isFinite(limit) && limit > 0 ? decimal(String(-limit)) : null;
  let reached: boolean | null = null;
  if (verified && total && threshold) {
    const scale = Math.max(total.scale, threshold.scale);
    reached = total.units * 10n ** BigInt(scale - total.scale) <= threshold.units * 10n ** BigInt(scale - threshold.scale);
  }
  return { verified, reached, limit: threshold ? limit : null, total: verified ? total!.text : null,
    daily: verified ? decimal(income?.program_daily)?.text ?? null : null,
    funding: verified ? decimal(income?.program_funding_total)?.text ?? null : null,
    upnl: verified ? decimal(income?.program_upnl)?.text ?? null : null };
}
