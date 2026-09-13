import { mutateFloatingWatchItem } from "../api/client";
import type { FloatingWatchSettings } from "../api/types";

export const FLOATING_WATCH_UPDATED_EVENT = "taoli1:floating-watch-updated";

function announce(settings: FloatingWatchSettings): void {
  window.dispatchEvent(new CustomEvent<FloatingWatchSettings>(FLOATING_WATCH_UPDATED_EVENT, {
    detail: settings
  }));
}

async function mutate(
  action: "add" | "remove",
  itemType: "symbol" | "pair",
  value: string
): Promise<FloatingWatchSettings> {
  const saved = await mutateFloatingWatchItem(action, itemType, value);
  announce(saved);
  return saved;
}

export async function addFloatingWatchSymbol(symbol: string): Promise<FloatingWatchSettings> {
  return mutate("add", "symbol", symbol);
}

export async function addFloatingWatchPair(pairId: string): Promise<FloatingWatchSettings> {
  return mutate("add", "pair", pairId);
}

export async function removeFloatingWatchSymbol(symbol: string): Promise<FloatingWatchSettings> {
  return mutate("remove", "symbol", symbol);
}

export async function removeFloatingWatchPair(pairId: string): Promise<FloatingWatchSettings> {
  return mutate("remove", "pair", pairId);
}
