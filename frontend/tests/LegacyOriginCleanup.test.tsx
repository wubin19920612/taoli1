// @vitest-environment-options {"url":"http://legacy.test:3000/"}
import { act, render } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AuthGate } from "../src/components/AuthGate";

it("cleans the old HTTP 3000 origin on first load without a login request", async () => {
  expect(location.origin).toBe("http://legacy.test:3000");
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  try {
    for (const storage of [localStorage, sessionStorage]) {
      storage.setItem("dashboard_password", "synthetic-obsolete-value");
      storage.setItem("preference", "keep");
    }
    await act(async () => { render(<AuthGate><div>业务页面</div></AuthGate>); });
    for (const storage of [localStorage, sessionStorage]) {
      expect(storage.getItem("dashboard_password")).toBeNull();
      expect(storage.getItem("preference")).toBe("keep");
      storage.clear();
    }
    expect(fetchMock).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllGlobals();
  }
});
