import { useEffect, useRef } from "react";

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
  const previous = useRef<{ enabled: boolean; intervalMs: number } | null>(null);

  useEffect(() => {
    const intervalChanged = previous.current?.enabled === enabled
      && previous.current.intervalMs !== intervalMs;
    previous.current = { enabled, intervalMs };
    if (!enabled) return;

    let active = true;
    let hidden = pauseWhenHidden && document.visibilityState === "hidden";
    let pending = false;
    let timer: number | undefined;
    const clearTimer = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
    };
    const schedule = () => {
      if (!active || hidden || running.current) return;
      clearTimer();
      timer = window.setTimeout(() => void run(), intervalMs);
    };
    const run = async () => {
      if (!active || hidden) return;
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

    const handleVisibility = () => {
      const nextHidden = document.visibilityState === "hidden";
      if (nextHidden === hidden) return;
      hidden = nextHidden;
      clearTimer();
      if (hidden) pending = false;
      else void run();
    };
    if (pauseWhenHidden) document.addEventListener("visibilitychange", handleVisibility);
    if (immediate && !intervalChanged) void run();
    else schedule();

    return () => {
      active = false;
      pending = false;
      clearTimer();
      if (onSettled.current === settled) onSettled.current = null;
      if (pauseWhenHidden) document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [enabled, immediate, intervalMs, pauseWhenHidden]);
}
