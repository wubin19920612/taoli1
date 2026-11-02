import { afterEach, describe, expect, it, vi } from "vitest";

import { isPageHidden, onPageVisible } from "../src/utils/pageVisibility";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

afterEach(() => {
  Reflect.deleteProperty(document, "visibilityState");
});

describe("pageVisibility", () => {
  it("reports hidden state from document.visibilityState", () => {
    setVisibility("visible");
    expect(isPageHidden()).toBe(false);
    setVisibility("hidden");
    expect(isPageHidden()).toBe(true);
  });

  it("fires the callback only when the page becomes visible", () => {
    const callback = vi.fn();
    setVisibility("visible");
    const off = onPageVisible(callback);
    setVisibility("hidden");
    expect(callback).not.toHaveBeenCalled();
    setVisibility("visible");
    expect(callback).toHaveBeenCalledTimes(1);
    off();
    setVisibility("hidden");
    setVisibility("visible");
    expect(callback).toHaveBeenCalledTimes(1);
  });
});
