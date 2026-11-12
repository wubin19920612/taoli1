import { useCallback, useEffect, useRef, useState } from "react";
import { getLiveExecAdmin, type LiveAdminResource, type LiveAdminResources } from "../../api/liveExec";
import { isPageHidden } from "../../utils/pageVisibility";

type Data = { [K in LiveAdminResource]: LiveAdminResources[K] | null };
const resources: LiveAdminResource[] = ["config", "universe", "mappings", "config/audit", "external-positions"];

export function useLiveExecAdmin() {
  const [data, setData] = useState<Data>({ config: null, universe: null, mappings: null, "config/audit": null, "external-positions": null });
  const [errors, setErrors] = useState<Partial<Record<LiveAdminResource, string>>>({});
  const current = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const refresh = useCallback(async (force = false) => {
    if (!mounted.current || isPageHidden() || (current.current && !force)) return;
    current.current?.abort();
    const controller = new AbortController();
    current.current = controller;
    try {
      // The upstream has a bounded read slot; serialize the management reads.
      for (const resource of resources) {
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
    const timer = window.setInterval(() => void refresh(), 30000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      mounted.current = false; current.current?.abort(); current.current = null;
      window.clearInterval(timer); document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);
  return { data, errors, refresh };
}
