import type { LiveExternalIdentity, LiveExternalPositions, LiveRemotePosition, LiveRemotePositions } from "../../api/liveExec";
import type { ExternalPositionRow } from "./LiveExternalPositions";

const identityFields = ["route", "exchange_type", "business", "symbol", "quote", "dex"] as const;
export const identityKey = (identity: LiveExternalIdentity) => JSON.stringify(identityFields.map(key => identity[key]));
const decimal = (value: unknown): string | null => typeof value === "string"
  && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) ? value : null;

// Match the existing registration whitelist, not ticker aliases or a guessed DEX.
function registrableIdentity(row: Partial<LiveExternalIdentity>): LiveExternalIdentity | null {
  if (!identityFields.every(key => typeof row[key] === "string") || row.route !== "crossex" || row.business !== "FUTURE") return null;
  const match = /^([A-Z][A-Z0-9]*)_FUTURE_([^|\s_]+)_(USDT|USDC)$/.exec(row.symbol!);
  if (!match || row.symbol!.length > 160) return null;
  const [, exchange, raw, quote] = match;
  const parts = raw.split(":");
  if (parts.length > 1 && (exchange !== "HYPERLIQUID" || parts.length !== 2 || parts.some(part => !part))) return null;
  if (/[\u0000-\u001f]/.test(raw) || row.exchange_type !== exchange || row.quote !== quote || row.dex !== (parts.length === 2 ? parts[0] : "")) return null;
  return Object.fromEntries(identityFields.map(key => [key, row[key]])) as unknown as LiveExternalIdentity;
}

export function remoteSnapshotFresh(snapshot: LiveRemotePositions | null | undefined, now: number): boolean {
  const at = typeof snapshot?.snapshot_at === "string" ? Date.parse(snapshot.snapshot_at) : NaN;
  return snapshot?.available === true && snapshot.stale === false && Number.isSafeInteger(snapshot.snapshot_version)
    && snapshot.snapshot_version! > 0 && Number.isFinite(at) && now >= at && now - at <= 10000;
}

function rowEvidence(row: LiveRemotePosition, snapshot: LiveRemotePositions): boolean {
  return row.stale === false && row.snapshot_at === snapshot.snapshot_at && row.snapshot_version === snapshot.snapshot_version
    && ["long", "short"].includes(row.side) && row.position_key === `${row.route}|${row.symbol}|${row.side}`
    && decimal(row.quantity) !== null && Number(row.quantity) > 0;
}

export function remotePositionView(snapshot: LiveRemotePositions | null, registry: LiveExternalPositions | null, now: number) {
  const fresh = remoteSnapshotFresh(snapshot, now);
  const records = registry?.contracts ?? [];
  const rows: ExternalPositionRow[] | null = snapshot?.available === true ? snapshot.positions.map((row, index) => {
    const identity = row.identity_verified === true ? registrableIdentity(row as Partial<LiveExternalIdentity>) : null;
    // Registry and snapshot are separate HTTP reads. Conflicting observations must not enable a write.
    const registered = records.some(record => record.registered && record.identity.route === row.route && record.identity.symbol === row.symbol);
    const usd = row.valuation_verified === true ? decimal(row.unrealized_pnl_usd) : null;
    return { key: `${row.position_key}:${index}`, identity, route: row.route, exchange: row.exchange_type ?? null,
      symbol: row.symbol, dex: row.dex ?? null, side: row.side, quantity: decimal(row.quantity),
      ownership: row.ownership === "program" ? "program" : row.ownership === "external_registered" ? "external"
        : row.ownership === "unregistered" ? "unregistered" : "unknown",
      nativeUpnl: decimal(row.unrealized_pnl_native), pnlCurrency: row.quote ?? null, usdUpnl: usd,
      conversion: row.valuation_verified === false ? "折算不可用" : usd === null ? null
        : row.valuation === "public_spot_lower_bound" ? "公开现货保守下界"
          : row.valuation === "native_usd" ? "原生 USD 报告口径" : null,
      action: identity && row.ownership === "unregistered" && !registered && rowEvidence(row, snapshot) ? "register" : undefined,
    };
  }) : null;
  // A complete snapshot proves absence only if no raw row (either direction, even
  // with unverifiable remaining identity fields) names this exact route + symbol.
  const cancellable: ExternalPositionRow[] = fresh ? records.flatMap(record => {
    const identity = registrableIdentity(record.identity);
    if (!record.registered || !identity || snapshot!.positions.some(row => row.route === identity.route && row.symbol === identity.symbol)) return [];
    return [{ key: `registry:${identityKey(identity)}`, identity, route: identity.route, exchange: identity.exchange_type,
      symbol: identity.symbol, dex: identity.dex, side: null, quantity: null, ownership: "external",
      nativeUpnl: null, pnlCurrency: null, usdUpnl: null, conversion: null, action: "unregister" }];
  }) : [];
  return { rows, cancellable, fresh };
}
