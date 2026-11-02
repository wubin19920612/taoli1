export function isPageHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/** Registers a callback fired when the page goes from hidden to visible. Returns an unsubscribe function. */
export function onPageVisible(callback: () => void): () => void {
  if (typeof document === "undefined") return () => undefined;
  const handler = () => {
    if (document.visibilityState === "visible") callback();
  };
  document.addEventListener("visibilitychange", handler);
  return () => document.removeEventListener("visibilitychange", handler);
}
