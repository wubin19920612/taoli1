import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getHealth, listOpportunities } from "../api/client";
import type { HealthStatus, Opportunity, OpportunityFilters } from "../api/types";

interface RadarState {
  opportunities: Opportunity[];
  health: HealthStatus | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
}

export function useRadarStore(filters: OpportunityFilters, enabled = true): RadarState {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const requestIdRef = useRef(0);
  const stableFilters = useMemo(() => filters, [JSON.stringify(filters)]);

  const refresh = useCallback(async () => {
    if (!enabled) {
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
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
    } catch (exc) {
      if (requestId !== requestIdRef.current) {
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      if (requestId === requestIdRef.current) {
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
    void refresh();
    const timer = window.setInterval(() => void refresh(), 8000);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);

  return { opportunities, health, loading, error, refresh };
}
