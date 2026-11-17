import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { getLiveExec, type LiveExecResult, type LiveResource, type LiveResources, type LiveSummary, type LiveTrade } from "../../api/liveExec";
import { isPageHidden } from "../../utils/pageVisibility";
import { usePollingHealth } from "./usePollingHealth";

type Results = { [K in LiveResource]?: LiveExecResult<LiveResources[K]> };
const resources: LiveResource[] = ["summary", "positions", "trades", "stats"];

function mergeTrades(previous: LiveTrade[], next: LiveTrade[]): LiveTrade[] {
  const rows = new Map(previous.map(row => [row.id, row]));
  next.forEach(row => rows.set(row.id, row));
  return [...rows.values()].sort((a, b) => (b.closed_at ?? "").localeCompare(a.closed_at ?? ""));
}

export function useLiveExec() {
  const { health, record, invalidate, writesBlocked } = usePollingHealth(resources);
  const [results, setResults] = useState<Results>({});
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const loadMoreRef = useRef<(before: string) => void>(() => undefined);
  const refreshSummaryRef = useRef<() => Promise<LiveSummary | null>>(async () => null);
  const refreshSummary = useCallback(() => refreshSummaryRef.current(), []);

  useEffect(() => {
    let active = true;
    let paginated = false;
    const pending = new Map<LiveResource, AbortController>();
    const load = async <K extends LiveResource>(resource: K, before?: string, force = false): Promise<LiveResources[K] | null> => {
      if (!active || isPageHidden() || (pending.has(resource) && !force)) return null;
      pending.get(resource)?.abort();
      const controller = new AbortController();
      pending.set(resource, controller);
      if (force) {
        setResults(previous => ({ ...previous, [resource]: undefined }));
        invalidate([resource]);
      }
      if (before) { paginated = true; setLoadingMore(true); }
      const result = await getLiveExec(resource, controller.signal, before);
      const accepted = active && !controller.signal.aborted && !isPageHidden();
      if (accepted) {
        const publish = () => {
          record(resource, result.available ? undefined : result.error);
          setResults(previous => {
            if (!result.available) return previous;
            if (resource !== "trades") return { ...previous, [resource]: result };
            const incoming = (result.data ?? []) as LiveTrade[];
            const old = previous.trades?.available ? previous.trades.data ?? [] : [];
            return { ...previous, trades: { available: true, data: mergeTrades(old, incoming) } };
          });
        };
        // A write preflight reads the latest disabled ref after this promise resolves.
        // Commit the forced result and its health before returning that evidence.
        if (force) flushSync(publish);
        else publish();
        if (resource === "trades" && result.available) {
          const rows = (result.data ?? []) as LiveTrade[];
          // A cursor must move backwards; an empty/repeated page ends pagination.
          if (before) setHasMore(rows.some(row => row.closed_at && row.closed_at < before));
          else if (!paginated) setHasMore(rows.length > 0);
        }
      }
      if (pending.get(resource) === controller) {
        pending.delete(resource);
        if (active && before) setLoadingMore(false);
      }
      return accepted && result.available ? result.data : null;
    };
    const refresh = (keys: LiveResource[]) => keys.forEach(key => { void load(key); });
    loadMoreRef.current = before => { void load("trades", before); };
    refreshSummaryRef.current = () => load("summary", undefined, true);
    refresh(resources);
    const fast = window.setInterval(() => refresh(["summary", "positions"]), 5000);
    const slow = window.setInterval(() => refresh(["trades", "stats"]), 30000);
    const onVisibility = () => {
      if (isPageHidden()) {
        pending.forEach(controller => controller.abort());
        pending.clear();
        setLoadingMore(false);
      } else refresh(resources);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      active = false;
      refreshSummaryRef.current = async () => null;
      window.clearInterval(fast); window.clearInterval(slow);
      pending.forEach(controller => controller.abort());
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [record, invalidate]);

  const error = resources.map(key => health[key]).find(state => state.failures >= 2)?.error ?? null;
  const data = <K extends LiveResource>(key: K): LiveResources[K] | null => {
    const result = results[key];
    return result?.available ? result.data : null;
  };
  const trades = data("trades") ?? [];
  const cursor = trades[trades.length - 1]?.closed_at;
  return { summary: data("summary"), positions: data("positions") ?? [], trades, stats: data("stats"),
    error, health, writesBlocked, loadingMore, refreshSummary, canLoadMore: hasMore && Boolean(cursor) && !health.trades.unsafe,
    loadMore: () => { if (cursor) loadMoreRef.current(cursor); } };
}
