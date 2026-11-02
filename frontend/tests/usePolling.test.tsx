import { StrictMode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePolling } from "../src/utils/usePolling";

function setVisibility(value: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
  act(() => document.dispatchEvent(new Event("visibilitychange")));
}

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}

describe("usePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });

  afterEach(() => {
    cleanup();
    Reflect.deleteProperty(document, "visibilityState");
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("waits for an async callback to finish before starting the next interval", async () => {
    const pending = deferred();
    const callback = vi.fn().mockReturnValueOnce(pending.promise);
    renderHook(() => usePolling(callback, 1000));
    expect(callback).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    await advance(10_000);
    expect(callback).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve());
    await advance(999);
    expect(callback).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(callback).toHaveBeenCalledTimes(2);
  });

  it("clears timers while hidden and refreshes once on becoming visible", async () => {
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000));
    await advance(0);
    setVisibility("hidden");
    expect(vi.getTimerCount()).toBe(0);
    await advance(60_000);
    expect(callback).toHaveBeenCalledTimes(1);
    setVisibility("visible");
    expect(callback).toHaveBeenCalledTimes(2);
    // Duplicate events without a visibility transition must not refresh again.
    setVisibility("visible");
    await advance(999);
    expect(callback).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it("does not start when mounted hidden, even with immediate enabled", async () => {
    setVisibility("hidden");
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000));
    expect(vi.getTimerCount()).toBe(0);
    await advance(60_000);
    expect(callback).not.toHaveBeenCalled();
    setVisibility("visible");
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("removes timers and listeners when disabled and restarts when enabled", async () => {
    const callback = vi.fn();
    const add = vi.spyOn(document, "addEventListener");
    const remove = vi.spyOn(document, "removeEventListener");
    const { rerender } = renderHook(({ enabled }) => usePolling(callback, 1000, { enabled }), {
      initialProps: { enabled: false }
    });
    expect(add.mock.calls.filter(([event]) => event === "visibilitychange")).toHaveLength(0);
    await advance(2000);
    expect(callback).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await advance(0);
    expect(callback).toHaveBeenCalledTimes(1);
    const listener = add.mock.calls.find(([event]) => event === "visibilitychange")?.[1];
    rerender({ enabled: false });
    expect(remove).toHaveBeenCalledWith("visibilitychange", listener);
    expect(vi.getTimerCount()).toBe(0);
    setVisibility("hidden");
    setVisibility("visible");
    await advance(60_000);
    expect(callback).toHaveBeenCalledTimes(1);
    rerender({ enabled: true });
    expect(callback).toHaveBeenCalledTimes(2);
  });

  it("does not reschedule or respond to visibility after unmount during a request", async () => {
    const pending = deferred();
    const callback = vi.fn(() => pending.promise);
    const { unmount } = renderHook(() => usePolling(callback, 1000));
    unmount();
    await act(async () => pending.resolve());
    setVisibility("hidden");
    setVisibility("visible");
    expect(vi.getTimerCount()).toBe(0);
    await advance(60_000);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("continues silently after synchronous throws and rejected promises", async () => {
    const log = vi.spyOn(console, "error");
    const callback = vi.fn()
      .mockImplementationOnce(() => { throw new Error("sync"); })
      .mockRejectedValueOnce(new Error("async"));
    renderHook(() => usePolling(callback, 1000));
    await advance(2000);
    expect(callback).toHaveBeenCalledTimes(3);
    expect(log).not.toHaveBeenCalled();
  });

  it("reschedules with a new interval and uses the latest callback without resetting on renders", async () => {
    const first = vi.fn();
    const latest = vi.fn();
    const { rerender } = renderHook(({ callback, interval }) => usePolling(callback, interval, { immediate: false }), {
      initialProps: { callback: first, interval: 1000 }
    });
    await advance(500);
    rerender({ callback: latest, interval: 2000 });
    await advance(1999);
    expect(first).not.toHaveBeenCalled();
    expect(latest).not.toHaveBeenCalled();
    await advance(1);
    expect(latest).toHaveBeenCalledTimes(1);
    await advance(500);
    rerender({ callback: first, interval: 2000 });
    await advance(1500);
    expect(first).toHaveBeenCalledTimes(1);
  });

  it("waits for the new interval without an extra immediate call when it changes", async () => {
    const callback = vi.fn();
    const { rerender } = renderHook(({ interval }) => usePolling(callback, interval), {
      initialProps: { interval: 1000 }
    });
    await advance(500);
    rerender({ interval: 2000 });
    await advance(1999);
    expect(callback).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(callback).toHaveBeenCalledTimes(2);
  });

  it("keeps polling hidden when pauseWhenHidden is false", async () => {
    setVisibility("hidden");
    const callback = vi.fn();
    renderHook(() => usePolling(callback, 1000, { pauseWhenHidden: false }));
    await advance(2000);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it("does not overlap or queue duplicate resumes across visibility and enabled changes", async () => {
    const pending = deferred();
    const callback = vi.fn().mockReturnValueOnce(pending.promise);
    const { rerender } = renderHook(({ enabled, interval }) => usePolling(callback, interval, { enabled }), {
      initialProps: { enabled: true, interval: 1000 }
    });
    setVisibility("hidden");
    setVisibility("visible");
    setVisibility("hidden");
    setVisibility("visible");
    rerender({ enabled: false, interval: 1000 });
    rerender({ enabled: true, interval: 2000 });
    await advance(10_000);
    expect(callback).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve());
    expect(callback).toHaveBeenCalledTimes(2);
    await advance(1999);
    expect(callback).toHaveBeenCalledTimes(2);
    await advance(1);
    expect(callback).toHaveBeenCalledTimes(3);
  });

  it.each([false, true])("calls only once before the first full interval in StrictMode (async: %s)", async slow => {
    const pending = deferred();
    const callback = vi.fn().mockReturnValueOnce(slow ? pending.promise : undefined);
    renderHook(() => usePolling(callback, 1000), { wrapper: StrictMode });
    await advance(0);
    expect(callback).toHaveBeenCalledTimes(1);
    if (slow) {
      await advance(5000);
      expect(callback).toHaveBeenCalledTimes(1);
      await act(async () => pending.resolve());
    }
    await advance(999);
    expect(callback).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(callback).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(1);
  });

  it("serializes triggered refreshes with polling and restarts the full interval after completion", async () => {
    const pending = deferred();
    const first = vi.fn().mockReturnValueOnce(pending.promise);
    const latest = vi.fn();
    const { result, rerender } = renderHook(({ callback }) => usePolling(callback, 1000), {
      initialProps: { callback: first }
    });
    rerender({ callback: latest });
    act(() => { result.current(); result.current(); });
    await advance(5000);
    expect(first).toHaveBeenCalledTimes(1);
    expect(latest).not.toHaveBeenCalled();
    await act(async () => pending.resolve());
    expect(latest).toHaveBeenCalledTimes(1);
    await advance(999);
    expect(latest).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(latest).toHaveBeenCalledTimes(2);
  });

  it("allows explicit refreshes with polling disabled, respects current visibility and stops after unmount", async () => {
    const callback = vi.fn();
    const { result, unmount } = renderHook(() => usePolling(callback, 1000, { enabled: false }));
    const trigger = result.current;
    setVisibility("hidden");
    act(() => trigger());
    expect(callback).not.toHaveBeenCalled();
    setVisibility("visible");
    act(() => trigger());
    await advance(5000);
    expect(callback).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
    unmount();
    act(() => trigger());
    expect(callback).toHaveBeenCalledTimes(1);
  });
});
