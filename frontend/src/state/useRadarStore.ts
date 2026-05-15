import { useCallback, useEffect, useMemo, useState } from "react";

import { getHealth, listOpportunities } from "../api/client";
import type { HealthStatus, Opportunity, OpportunityFilters } from "../api/types";

interface RadarState {
  opportunities: Opportunity[];
  health: HealthStatus | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
}

export function useRadarStore(filters: OpportunityFilters): RadarState {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const stableFilters = useMemo(() => filters, [JSON.stringify(filters)]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextHealth, rows] = await Promise.all([
        getHealth(),
        listOpportunities(stableFilters)
      ]);
      setHealth(nextHealth);
      setOpportunities(rows);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [stableFilters]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 8000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { opportunities, health, loading, error, refresh };
}
