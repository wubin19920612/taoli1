import { useCallback, useEffect, useRef, useState } from "react";
import { getLiveExecAdmin, hasLiveExecAdminSession, type LiveAdminResource, type LiveAdminResources } from "../../api/liveExec";
import { AUTH_REQUIRED_EVENT } from "../../api/auth";
import { isPageHidden } from "../../utils/pageVisibility";
import { usePollingHealth } from "./usePollingHealth";
import { remoteSnapshotFresh } from "./remotePositionView";

type Data = { [K in LiveAdminResource]: LiveAdminResources[K] | null };
const resources: LiveAdminResource[] = ["config", "universe", "mappings", "config/audit", "external-positions", "remote-positions", "external-liabilities"];
const healthKeys = [...resources, "session"] as const;
const snapshotResources: LiveAdminResource[] = ["external-positions", "remote-positions", "external-liabilities"];

export function useLiveExecAdmin() {
  const { health, record, invalidate, writesBlocked } = usePollingHealth(healthKeys);
  const [data, setData] = useState<Data>({ config: null, universe: null, mappings: null, "config/audit": null,
    "external-positions": null, "remote-positions": null, "external-liabilities": null });
  const [errors, setErrors] = useState<Partial<Record<LiveAdminResource, string>>>({});
  const [hasAdminSession, setHasAdminSession] = useState(false);
  const current = useRef<AbortController | null>(null);
  const queued = useRef(new Set<LiveAdminResource>());
  const mounted = useRef(false);
  const refresh = useCallback(async (force = false, keys = resources): Promise<void> => {
    if (!mounted.current || isPageHidden()) return;
    if (current.current && !force) {
      keys.forEach(key => queued.current.add(key));
      return;
    }
    // Coalesce missed requests; a partial poll must never replace a queued full read.
    const requested = new Set([...keys, ...queued.current]);
    queued.current.clear();
    if (force || snapshotResources.some(key => requested.has(key)))
      snapshotResources.forEach(key => requested.add(key));
    keys = resources.filter(key => requested.has(key));
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    // An explicit post-write/conflict refresh must revoke old action evidence immediately.
    if (force) {
      setData(old => ({ ...old, ...Object.fromEntries(keys.map(key => [key, null])),
        "external-positions": null, "remote-positions": null, "external-liabilities": null }));
      invalidate([...keys, "external-positions", "remote-positions", "external-liabilities"]);
    }
    try {
      const hasSession = await hasLiveExecAdminSession(controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      if (hasSession !== null) setHasAdminSession(hasSession);
      record("session", hasSession === true ? undefined : "session_unverified");
      const nextData: Partial<Data> = {};
      const nextErrors: Partial<Record<LiveAdminResource, string | undefined>> = {};
      const successes: { resource: LiveAdminResource; at: number }[] = [];
      // The upstream has a bounded read slot; serialize the management reads.
      for (const resource of keys) {
        const result = await getLiveExecAdmin(resource, controller.signal);
        if (controller.signal.aborted || !mounted.current) return;
        if (result.available) Object.assign(nextData, { [resource]: result.data });
        nextErrors[resource] = result.available ? undefined : result.error;
        if (!result.available) record(resource, result.error); // Revoke writes as soon as a read fails.
        else if (resource === "remote-positions"
          && !remoteSnapshotFresh(result.data as LiveAdminResources["remote-positions"], Date.now()))
          record(resource, "remote_snapshot_invalid");
        else successes.push({ resource, at: Date.now() });
      }
      // Publish this round once so income proof never renders with the previous snapshot.
      // Failed resources keep their previous observations; consumers still verify versions.
      setData(old => ({ ...old, ...nextData }));
      setErrors(old => ({ ...old, ...nextErrors }));
      successes.forEach(({ resource, at }) => record(resource, undefined, at));
    } finally {
      if (current.current === controller) {
        current.current = null;
        if (queued.current.size > 0 && mounted.current && !isPageHidden())
          await refresh(false, []);
      }
    }
  }, [record, invalidate]);
  useEffect(() => {
    mounted.current = true;
    const visible = () => {
      if (isPageHidden()) { current.current?.abort(); current.current = null; queued.current.clear(); invalidate(healthKeys); }
      else void refresh();
    };
    const expired = () => {
      setHasAdminSession(false);
      invalidate(["session"]);
      current.current?.abort(); current.current = null;
      queued.current.clear();
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, expired);
    visible();
    // Income is bound to the current ten-second snapshot. Refresh position evidence
    // and liability observations every five seconds in the same serial lane.
    let tick = 0;
    const timer = window.setInterval(() => {
      void refresh(false, ++tick % 6 === 0 ? resources : ["external-positions", "remote-positions", "external-liabilities"]);
    }, 5000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false; current.current?.abort(); current.current = null;
      queued.current.clear();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener(AUTH_REQUIRED_EVENT, expired);
    };
  }, [refresh, invalidate]);
  const snapshotFresh = remoteSnapshotFresh(data["remote-positions"], Date.now());
  const remoteHealth = health["remote-positions"];
  return { data, errors, health: { ...health, "remote-positions": { ...remoteHealth,
    unsafe: remoteHealth.unsafe || !snapshotFresh,
    stale: remoteHealth.stale || (data["remote-positions"] !== null && !snapshotFresh) } },
    writesBlocked: writesBlocked || !hasAdminSession || !snapshotFresh, refresh, hasAdminSession };
}
