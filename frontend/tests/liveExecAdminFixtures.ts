export const mappingId = "0123456789abcdef01234567";
export const markets = ["binance|future|SKHYNIXUSDT", "bitget|future|SKHYNIXUSDT"];
export const universeStatus = "u1:" + "a".repeat(64);
export const fixtures = {
  config: { version: 2, fallback: false, values: { order_usd_max: 50, min_venues: 3 }, schema: {
    order_usd_max: { default: 50, min: 5, max: 150, integer: false },
    min_venues: { default: 3, min: 3, max: 6, integer: true },
  } },
  "config/audit": [{ version: 2, at: "2026-11-12T00:00:00Z", source: "admin_api", old: { order_usd_max: 40 }, new: { order_usd_max: 50 } }],
  universe: [{ base: "SKHYNIX", status: universeStatus, category: "unconfirmed", excluded: false, underlying_market: null,
    markets: markets.map((market_key, i) => ({ market_key, raw_symbol: "SKHYNIXUSDT", exchange: i ? "bitget" : "binance",
      category: "listed_equity", effective_category: "listed_equity", underlying_market: null,
      volume_usd: i ? null : 3000000, volume_basis: i ? "base_times_low24h_lower_bound" : "quote_volume",
      at: "2026-11-12T00:00:00Z", selected: false, eligible: false, needs_classification: true })) }],
  mappings: [{ id: mappingId, base: "PEPE", status: "pending", multiplier: "1000", at: "2026-11-12T00:00:00Z", ratio: 1000,
    legs: [{ market_key: "binance|future|1000PEPEUSDT", raw_base: "1000PEPE", quantity_multiplier: 1000 },
      { market_key: "bitget|future|PEPEUSDT", raw_base: "PEPE", quantity_multiplier: 1 }] }],
};
