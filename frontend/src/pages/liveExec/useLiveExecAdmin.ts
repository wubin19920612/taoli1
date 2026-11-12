import { useCallback, useEffect, useRef, useState } from "react";
import { getLiveExecAdmin, type LiveAdminResource, type LiveAdminResources } from "../../api/liveExec";
import { isPageHidden } from "../../utils/pageVisibility";

type Data = { [K in LiveAdminResource]: LiveAdminResources[K] | null };
const resources: LiveAdminResource[] = ["config", "universe", "mappings", "config/audit", "external-positions", "remote-positions"];

export function useLiveExecAdmin() {
  const [data, setData] = useState<Data>({ config: null, universe: null, mappings: null, "config/audit": null,
    "external-positions": null, "remote-positions": null });
  const [errors, setErrors] = useState<Partial<Record<LiveAdminResource, string>>>({});
  const current = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const refresh = useCallback(async (force = false, keys = resources) => {
    if (!mounted.current || isPageHidden() || (current.current && !force)) return;
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    // An explicit post-write/conflict refresh must revoke old action evidence immediately.
    if (force) setData(old => ({ ...old, "external-positions": null, "remote-positions": null }));
    try {
      // The upstream has a bounded read slot; serialize the management reads.
      for (const resource of keys) {
        const result = await getLiveExecAdmin(resource, controller.signal);
        if (controller.signal.aborted || !mounted.current) return;
        setData(old => ({ ...old, [resource]: result.available ? result.data : null }));
        setErrors(old => ({ ...old, [resource]: result.available ? undefined : result.error }));
      }
    } finally {
      if (current.current === controller) current.current = null;
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const visible = () => {
      if (isPageHidden()) { current.current?.abort(); current.current = null; }
      else void refresh();
    };
    visible();
    // Income is bound to the current ten-second snapshot. Read both every five
    // seconds in the same serial lane, with one copy of each at the 30s boundary.
    let tick = 0;
    const timer = window.setInterval(() => {
      void refresh(false, ++tick % 6 === 0 ? resources : ["external-positions", "remote-positions"]);
    }, 5000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false; current.current?.abort(); current.current = null;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  return { data, errors, refresh };
}
