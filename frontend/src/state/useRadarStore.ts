import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getHealth, listOpportunities } from "../api/client";
import type { HealthStatus, Opportunity, OpportunityFilters } from "../api/types";

interface RadarState {
  opportunities: Opportunity[];
  health: HealthStatus | null;
  loading: boolean;
  error: string;
  refresh: (options?: RefreshOptions) => Promise<void>;
}

interface RefreshOptions {
  force?: boolean;
  showLoading?: boolean;
}

interface RadarStoreOptions {
  autoRefresh?: boolean;
  refreshIntervalMs?: number;
}

export function useRadarStore(
  filters: OpportunityFilters,
  enabled = true,
  options: RadarStoreOptions = {}
): RadarState {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const hasLoadedRef = useRef(false);
  const inFlightRef = useRef(false);
  const requestIdRef = useRef(0);
  const autoRefresh = options.autoRefresh ?? true;
  const refreshIntervalMs = Math.max(options.refreshIntervalMs ?? 15000, 5000);
  const filterKey = useMemo(() => JSON.stringify(filters), [filters]);
  const stableFilters = useMemo(() => filters, [filterKey]);

  const refresh = useCallback(async (refreshOptions: RefreshOptions = {}) => {
    if (!enabled) {
      return;
    }
    if (inFlightRef.current && !refreshOptions.force) {
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    inFlightRef.current = true;
    const showLoading = refreshOptions.showLoading ?? !hasLoadedRef.current;
    if (showLoading) {
      setLoading(true);
    }
    setError("");
    try {
      const [nextHealth, rows] = await Promise.all([
        getHealth(),
        listOpportunities(stableFilters)
      ]);
      if (requestId !== requestIdRef.current) {
        return;
      }
      setHealth(nextHealth);
      setOpportunities(rows);
      hasLoadedRef.current = true;
    } catch (exc) {
      if (requestId !== requestIdRef.current) {
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (requestId === requestIdRef.current) {
        inFlightRef.current = false;
        setLoading(false);
      }
    }
  }, [enabled, stableFilters]);

  useEffect(() => {
    if (!enabled) {
      requestIdRef.current += 1;
      setLoading(false);
      return undefined;
    }
    void refresh({ showLoading: !hasLoadedRef.current });
    if (!autoRefresh) {
      return undefined;
    }
    const refreshIfVisible = () => {
      if (document.visibilityState !== "hidden") {
        void refresh({ showLoading: false });
      }
    };
    const timer = window.setInterval(refreshIfVisible, refreshIntervalMs);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [autoRefresh, enabled, refresh, refreshIntervalMs]);

  return { opportunities, health, loading, error, refresh };
}
