import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readThresholds, readVersion, writeThresholds, writeVersion } from "./storage";

beforeEach(() => localStorage.clear());
afterEach(() => { localStorage.clear(); });
describe("v2 chart preferences", () => {
  it("defaults invalid/missing versions to v2 and stores v1/v2 using the specified key", () => {
    expect(readVersion()).toBe("v2");
    localStorage.setItem("pair-chart-version", "wrong");
    expect(readVersion()).toBe("v2");
    writeVersion("v1"); expect(localStorage.getItem("pair-chart-version")).toBe("v1"); expect(readVersion()).toBe("v1");
    writeVersion("v2"); expect(readVersion()).toBe("v2");
  });
  it("round trips empty and zero thresholds and rejects malformed/non-numeric values", () => {
    const empty = { open: null, close: null };
    expect(readThresholds()).toEqual(empty);
    for (const value of ["bad", "null", "[]", '{"open":"2","close":{}}']) {
      localStorage.setItem("pair-chart-thresholds", value); expect(readThresholds()).toEqual(empty);
    }
    writeThresholds({ open: 0, close: -0.2 }); expect(readThresholds()).toEqual({ open: 0, close: -0.2 });
    writeThresholds({ open: Infinity, close: NaN }); expect(readThresholds()).toEqual(empty);
    writeThresholds(empty); expect(JSON.parse(localStorage.getItem("pair-chart-thresholds")!)).toEqual(empty);
  });
  it("survives denied reads and writes", () => {
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(readVersion()).toBe("v2"); expect(readThresholds()).toEqual({ open: null, close: null });
    expect(() => writeVersion("v1")).not.toThrow();
    expect(() => writeThresholds({ open: 1, close: null })).not.toThrow();
    read.mockRestore(); write.mockRestore();
  });
});
