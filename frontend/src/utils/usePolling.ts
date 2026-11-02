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
  // Keep the in-flight guard across effect restarts (including StrictMode).
  const running = useRef(false);
  const onSettled = useRef<(() => void) | null>(null);
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
    let pending = false;
    let timer: number | undefined;
    const clearTimer = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      if (!active || !enabled || hidden || running.current) return;
      clearTimer();
      timer = window.setTimeout(() => void run(), intervalMs);
    };
    const run = async () => {
      // Explicit triggers also work while disabled, when no visibility listener is installed.
      if (!active || (pauseWhenHidden && document.visibilityState === "hidden")) return;
      clearTimer();
      if (running.current) {
        pending = true;
        return;
      }
      pending = false;
      running.current = true;
      try {
        await callbackRef.current();
      } catch {
        // The caller owns error presentation; failures must not stop polling.
      } finally {
        running.current = false;
        onSettled.current?.();
      }
    };
    const settled = () => {
      if (pending) void run();
      else schedule();
    };
    onSettled.current = settled;
    const triggerRun = () => { void run(); };
    triggerRef.current = triggerRun;

    const handleVisibility = () => {
      const nextHidden = document.visibilityState === "hidden";
      if (nextHidden === hidden) return;
      hidden = nextHidden;
      clearTimer();
      if (hidden) pending = false;
      else void run();
    };
    if (enabled && pauseWhenHidden) document.addEventListener("visibilitychange", handleVisibility);
    // StrictMode replays setup without a new refresh intent. Let the first call
    // settle and schedule instead of queuing a second immediate invocation.
    if (enabled && immediate && !intervalChanged && !replay) void run();
    else schedule();

    return () => {
      active = false;
      pending = false;
      clearTimer();
      if (onSettled.current === settled) onSettled.current = null;
      if (triggerRef.current === triggerRun) triggerRef.current = null;
      if (enabled && pauseWhenHidden) document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [enabled, immediate, intervalMs, pauseWhenHidden]);

  return trigger;
}
