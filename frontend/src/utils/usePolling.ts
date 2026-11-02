import { useCallback, useEffect, useRef } from "react";

type PollingOptions = {
  enabled?: boolean;
  immediate?: boolean;
  pauseWhenHidden?: boolean;
};

export function usePolling(
  callback: () => void | Promise<unknown>,
  intervalMs: number,
  { enabled = true, immediate = true, pauseWhenHidden = true }: PollingOptions = {}
) {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;
  // Refresh intents and the in-flight guard survive configuration changes and
  // StrictMode effect replay. Only automatic intents may be cancelled by options.
  const queue = useRef({ running: false, trigger: false, poll: false, batching: false });
  const drainRef = useRef<(() => void) | null>(null);
  const triggerRef = useRef<(() => void) | null>(null);
  const previous = useRef<{
    enabled: boolean; immediate: boolean; intervalMs: number; pauseWhenHidden: boolean;
  } | null>(null);
  // Explicit refreshes share the polling guard, even when periodic polling is disabled.
  const trigger = useCallback(() => { triggerRef.current?.(); }, []);

  useEffect(() => {
    const intervalChanged = previous.current?.enabled === enabled
      && previous.current.intervalMs !== intervalMs;
    const replay = previous.current?.enabled === enabled
      && previous.current.immediate === immediate
      && previous.current.intervalMs === intervalMs
      && previous.current.pauseWhenHidden === pauseWhenHidden;
    previous.current = { enabled, immediate, intervalMs, pauseWhenHidden };

    let active = true;
    let hidden = pauseWhenHidden && document.visibilityState === "hidden";
    const state = queue.current;
    let listening = false;
    let timer: number | undefined;
    const isHidden = () => pauseWhenHidden && document.visibilityState === "hidden";
    const clearTimer = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      clearTimer();
      if (!active || !enabled || isHidden() || state.running || state.trigger || state.batching) return;
      timer = window.setTimeout(() => {
        timer = undefined;
        state.poll = true;
        drain();
      }, intervalMs);
    };
    const syncListener = () => {
      const needed = active && pauseWhenHidden && (enabled || state.trigger);
      if (needed === listening) return;
      listening = needed;
      if (needed) {
        // Visibility may have changed while disabled with no pending trigger.
        hidden = isHidden();
        document.addEventListener("visibilitychange", handleVisibility);
      } else document.removeEventListener("visibilitychange", handleVisibility);
    };
    const drain = () => {
      if (!active) return;
      if (!enabled || isHidden()) state.poll = false;
      syncListener();
      if (isHidden() || state.running || state.batching) return;
      if (state.trigger || state.poll) void run();
      else schedule();
    };
    const run = async () => {
      clearTimer();
      state.trigger = false;
      state.poll = false;
      state.running = true;
      syncListener();
      try {
        await callbackRef.current();
      } catch {
        // The caller owns error presentation; failures must not stop polling.
      } finally {
        state.running = false;
        drainRef.current?.();
      }
    };
    const triggerRun = () => {
      state.trigger = true;
      clearTimer();
      syncListener();
      // Batch effect replay and other same-tick triggers before starting work.
      // Resolve through the current effect so option changes cannot lose work.
      if (!state.batching) {
        state.batching = true;
        void Promise.resolve().then(() => {
          state.batching = false;
          drainRef.current?.();
        });
      }
    };
    drainRef.current = drain;
    triggerRef.current = triggerRun;

    const handleVisibility = () => {
      const nextHidden = document.visibilityState === "hidden";
      if (nextHidden === hidden) return;
      hidden = nextHidden;
      clearTimer();
      state.poll = enabled && !hidden;
      drain();
    };
    // StrictMode replays setup without a new refresh intent. Let the first call
    // settle and schedule instead of queuing a second immediate invocation.
    if (enabled && immediate && !intervalChanged && !replay) state.poll = true;
    drain();

    return () => {
      active = false;
      state.poll = false;
      clearTimer();
      if (drainRef.current === drain) drainRef.current = null;
      if (triggerRef.current === triggerRun) triggerRef.current = null;
      syncListener();
    };
  }, [enabled, immediate, intervalMs, pauseWhenHidden]);

  return trigger;
}
