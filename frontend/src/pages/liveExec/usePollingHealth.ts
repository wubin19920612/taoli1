import { useCallback, useEffect, useState } from "react";

type Observation = { updatedAt: number | null; since: number; failures: number; error?: string };
export type PollingHealth = { unsafe: boolean; stale: boolean; ageSeconds: number; failures: number; error?: string };

export function usePollingHealth<K extends string>(keys: readonly K[]) {
  const [observations, setObservations] = useState(() => Object.fromEntries(keys.map(key =>
    [key, { updatedAt: null, since: Date.now(), failures: 0 }])) as Record<K, Observation>);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const record = useCallback((key: K, error?: string, at = Date.now()) => {
    setObservations(old => ({ ...old, [key]: error !== undefined
      ? { ...old[key], failures: old[key].failures + 1, error: error || "request_failed" }
      : { updatedAt: at, since: at, failures: 0 } }));
    setNow(Date.now());
  }, []);
  const invalidate = useCallback((invalid: readonly K[]) => {
    setObservations(old => {
      const next = { ...old };
      for (const key of invalid) next[key] = { ...old[key], updatedAt: null };
      return next;
    });
  }, []);
  const health = Object.fromEntries(keys.map(key => {
    const observation = observations[key];
    const age = Math.max(0, now - (observation.updatedAt ?? observation.since));
    return [key, { unsafe: observation.updatedAt === null || observation.failures > 0 || age > 30000,
      stale: observation.failures >= 2 || age > 30000, ageSeconds: Math.floor(age / 1000),
      failures: observation.failures, error: observation.error }];
  })) as Record<K, PollingHealth>;
  return { health, record, invalidate, writesBlocked: keys.some(key => health[key].unsafe) };
}
