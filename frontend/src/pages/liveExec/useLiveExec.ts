import { useEffect, useRef, useState } from "react";
import { getLiveExec, type LiveExecResult, type LiveResource, type LiveResources, type LiveTrade } from "../../api/liveExec";
import { isPageHidden } from "../../utils/pageVisibility";

type Results = { [K in LiveResource]?: LiveExecResult<LiveResources[K]> };
const resources: LiveResource[] = ["summary", "positions", "trades", "stats"];

function mergeTrades(previous: LiveTrade[], next: LiveTrade[]): LiveTrade[] {
  const rows = new Map(previous.map(row => [row.id, row]));
  next.forEach(row => rows.set(row.id, row));
  return [...rows.values()].sort((a, b) => (b.closed_at ?? "").localeCompare(a.closed_at ?? ""));
}

export function useLiveExec() {
  const [results, setResults] = useState<Results>({});
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const loadMoreRef = useRef<(before: string) => void>(() => undefined);

  useEffect(() => {
    let active = true;
    let paginated = false;
    const pending = new Map<LiveResource, AbortController>();
    const load = async (resource: LiveResource, before?: string) => {
      if (!active || isPageHidden() || pending.has(resource)) return;
      const controller = new AbortController();
      pending.set(resource, controller);
      if (before) { paginated = true; setLoadingMore(true); }
      const result = await getLiveExec(resource, controller.signal, before);
      if (active && !controller.signal.aborted && !isPageHidden()) {
        setResults(previous => {
          if (resource !== "trades" || !result.available) return { ...previous, [resource]: result };
          const incoming = (result.data ?? []) as LiveTrade[];
          const old = previous.trades?.available ? previous.trades.data ?? [] : [];
          return { ...previous, trades: { available: true, data: mergeTrades(old, incoming) } };
        });
        if (resource === "trades" && result.available) {
          const rows = (result.data ?? []) as LiveTrade[];
          // A cursor must move backwards; an empty/repeated page ends pagination.
          if (before) setHasMore(rows.some(row => row.closed_at && row.closed_at < before));
          else if (!paginated) setHasMore(rows.length > 0);
        } else if (resource === "trades") {
          paginated = false;
          setHasMore(true);
        }
      }
      if (pending.get(resource) === controller) {
        pending.delete(resource);
        if (active && before) setLoadingMore(false);
      }
    };
    const refresh = (keys: LiveResource[]) => keys.forEach(key => { void load(key); });
    loadMoreRef.current = before => { void load("trades", before); };
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
      window.clearInterval(fast); window.clearInterval(slow);
      pending.forEach(controller => controller.abort());
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const failure = resources.map(key => results[key]).find(result => result?.available === false);
  const error = failure && !failure.available ? failure.error : null;
  const data = <K extends LiveResource>(key: K): LiveResources[K] | null => {
    const result = results[key];
    return !error && result?.available ? result.data : null;
  };
  const trades = data("trades") ?? [];
  const cursor = trades[trades.length - 1]?.closed_at;
  return { summary: data("summary"), positions: data("positions") ?? [], trades, stats: data("stats"),
    error, loadingMore, canLoadMore: hasMore && Boolean(cursor) && !error,
    loadMore: () => { if (cursor) loadMoreRef.current(cursor); } };
}
