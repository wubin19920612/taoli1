import type { MarketType } from "../api/types";

function normalizeMarketSymbol(value: string): string {
  return value.toUpperCase().replace(/[-_/]/g, "");
}

export function isBitgetRTokenSpot(
  exchange: string,
  marketType: MarketType | string | null | undefined,
  rawSymbol?: string | null,
  canonicalSymbol?: string | null
): boolean {
  if (
    exchange.trim().toLowerCase() !== "bitget" ||
    marketType !== "spot" ||
    !rawSymbol ||
    !canonicalSymbol
  ) {
    return false;
  }

  const raw = normalizeMarketSymbol(rawSymbol);
  const canonical = normalizeMarketSymbol(canonicalSymbol);
  return raw.length > 1 && raw.startsWith("R") && raw.slice(1) === canonical;
}

export function marketTypeText(
  exchange: string,
  marketType: MarketType | string | null | undefined,
  rawSymbol?: string | null,
  canonicalSymbol?: string | null
): string {
  if (isBitgetRTokenSpot(exchange, marketType, rawSymbol, canonicalSymbol)) {
    return "股票现货";
  }
  return marketType === "spot" ? "现货" : "合约";
}
