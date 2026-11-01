import { describe, expect, it } from "vitest";

import { buildTimeScale } from "../src/utils/chartTimeScale";

const startMs = Date.parse("2026-11-01T00:00:00Z");
const at = (seconds: number) => startMs + seconds * 1000;
const pointsAt = (...seconds: number[]) => seconds.map((value) => ({ bucket_at: new Date(at(value)).toISOString() }));

describe("buildTimeScale", () => {
  it("maps irregular timestamps linearly and inverts plot coordinates", () => {
    const scale = buildTimeScale(pointsAt(0, 10, 30, 40), 400);

    expect([0, 10, 30, 40].map((seconds) => scale.xOf(at(seconds)))).toEqual([0, 100, 300, 400]);
    expect(scale.tOf(250)).toBe(at(25));
    expect(scale.tOf(scale.xOf(at(17)))).toBe(at(17));
    expect(scale.gaps).toEqual([]);
  });

  it("marks only intervals strictly greater than three times the median", () => {
    const scale = buildTimeScale(pointsAt(0, 10, 20, 60, 100, 110, 120, 150), 1500);

    expect(scale.gaps).toEqual([
      { startMs: at(20), endMs: at(60), afterIndex: 3 },
      { startMs: at(60), endMs: at(100), afterIndex: 4 }
    ]);
    expect(scale.xOf(at(60)) - scale.xOf(at(20))).toBe(400);
  });

  it.each([75, 100])("averages an even count of sampling intervals, last interval=%s", (lastInterval) => {
    const end = 70 + lastInterval;
    const scale = buildTimeScale(pointsAt(0, 10, 30, 70, end), end);

    // Middle intervals: 20, 40; median = 30, threshold = 90.
    expect(scale.gaps).toEqual(lastInterval > 90 ? [{ startMs: at(70), endMs: at(end), afterIndex: 4 }] : []);
  });

  it("finds the nearest point by time inside a gap, with the earlier point winning ties", () => {
    const scale = buildTimeScale(pointsAt(0, 10, 20, 90, 100), 1000);

    expect(scale.nearestIndex(scale.tOf(600))).toBe(3);
    expect(scale.nearestIndex(at(54))).toBe(2);
    expect(scale.nearestIndex(at(55))).toBe(2);
    expect(scale.nearestIndex(at(56))).toBe(3);
    expect(scale.nearestIndex(at(-10))).toBe(0);
    expect(scale.nearestIndex(at(110))).toBe(4);
  });

  it("ignores duplicate intervals when estimating sampling cadence", () => {
    const scale = buildTimeScale(pointsAt(0, 0, 0, 10, 20, 60, 70), 700);

    expect(scale.gaps).toEqual([{ startMs: at(20), endMs: at(60), afterIndex: 5 }]);
    expect(scale.nearestIndex(at(0))).toBe(0);
    expect(scale.nearestIndex(at(5))).toBe(0);
  });

  it("preserves input order and returns original indices when timestamps arrive out of order", () => {
    const points = Object.freeze(pointsAt(20, 0, 10));
    const scale = buildTimeScale(points, 200);

    expect(scale.xOf(at(0))).toBe(0);
    expect(scale.xOf(at(20))).toBe(200);
    expect(scale.nearestIndex(at(15))).toBe(2);
    expect(points.map((point) => point.bucket_at)).toEqual(pointsAt(20, 0, 10).map((point) => point.bucket_at));
  });

  it("handles a single timestamp, empty input and zero width without invalid geometry", () => {
    const single = buildTimeScale(pointsAt(10), 400);
    expect(single.xOf(at(10))).toBe(200);
    expect(single.tOf(0)).toBe(at(10));
    expect(single.tOf(400)).toBe(at(10));
    expect(single.nearestIndex(at(50))).toBe(0);
    expect(single.gaps).toEqual([]);

    const empty = buildTimeScale([], 400);
    expect(empty.xOf(at(10))).toBe(200);
    expect(empty.tOf(200)).toBe(0);
    expect(empty.nearestIndex(at(10))).toBeNull();
    expect(empty.gaps).toEqual([]);

    const collapsed = buildTimeScale(pointsAt(0, 10), 0);
    expect(collapsed.xOf(at(5))).toBe(0);
    expect(collapsed.tOf(0)).toBe(at(0));
  });
});
