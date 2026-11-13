// Shape and units from crossex-exec 7d63c7d, remote_view.py / round thirteen contract.
export const remoteIdentity = { route: "crossex", exchange_type: "HYPERLIQUID", business: "FUTURE",
  symbol: "HYPERLIQUID_FUTURE_io:OAI_USDC", quote: "USDC", dex: "io" };
export const remoteAt = "2026-11-12T00:00:00Z";
export const remotePosition = { ...remoteIdentity,
  position_key: `crossex|${remoteIdentity.symbol}|long`, side: "long", quantity: "2.000000000000000001",
  identity_verified: true, entry_price: "20.25", mark_price: "19.125",
  unrealized_pnl_native: "-2.25", unrealized_pnl_usd: "-2.254500000000000001",
  valuation: "public_spot_lower_bound", valuation_verified: true, valuation_error: null,
  ownership: "unregistered", snapshot_at: remoteAt, snapshot_version: 17, stale: false };
export const remoteSnapshot = { available: true, snapshot_at: remoteAt, snapshot_version: 17,
  stale: false, positions: [remotePosition] };
export const remoteRegistry = { status: "x1:" + "a".repeat(64), contracts: [
  { identity: { ...remoteIdentity, symbol: "HYPERLIQUID_FUTURE_xyz:OAI_USDC", dex: "xyz" },
    raw_base: "xyz:OAI", registered: true, forbidden: true, revision: 1, registered_at: 1794441600, updated_at: 1794441600 },
] };
