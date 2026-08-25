import { describe, expect, it } from "vitest";

import { closedBars, formatBarCountdown, formatChartTime, isBarPrepend, mergeBars, remainingToBucketCloseMs, sourceTimezone } from "./feed";
import type { Bar } from "./types";

function bar(timestamp: string): Bar {
  return { timestamp, open: 1, high: 2, low: 1, close: 1.5, volume: 1 };
}

describe("history helpers", () => {
  it("detects prepended bars", () => {
    const prev = [bar("2024-01-02T02:00:00.000Z"), bar("2024-01-02T03:00:00.000Z")];
    const next = [bar("2024-01-02T01:00:00.000Z"), ...prev];
    expect(isBarPrepend(prev, next)).toBe(true);
    expect(isBarPrepend(prev, [...prev, bar("2024-01-02T04:00:00.000Z")])).toBe(false);
  });

  it("merges overlapping history chunks", () => {
    const left = [bar("2024-01-02T01:00:00.000Z"), bar("2024-01-02T02:00:00.000Z")];
    const right = [bar("2024-01-02T02:00:00.000Z"), bar("2024-01-02T03:00:00.000Z")];
    expect(mergeBars(left, right).map((item) => item.timestamp)).toEqual([
      "2024-01-02T01:00:00.000Z",
      "2024-01-02T02:00:00.000Z",
      "2024-01-02T03:00:00.000Z",
    ]);
  });
});

describe("source timezone helpers", () => {
  it.each([
    ["2026-08-11T01:05:00Z", 0, "UTC"],
    ["2026-08-11T01:05:00+00:00", 0, "UTC"],
    ["2026-08-11T09:05:00+08:00", 480, "UTC+8"],
    ["2026-08-10T20:35:00-04:30", -270, "UTC-4:30"],
  ])("reads the offset from %s", (timestamp, offsetMinutes, label) => {
    expect(sourceTimezone(timestamp)).toEqual({ offsetMinutes, label });
  });

  it.each([undefined, "", "2026-08-11T01:05:00", "not-a-time"])("falls back to UTC for %s", (timestamp) => {
    expect(sourceTimezone(timestamp)).toEqual({ offsetMinutes: 0, label: "UTC" });
  });

  it("formats epoch seconds in the supplied offset instead of local time", () => {
    const epochSeconds = Date.parse("2026-08-11T01:05:00Z") / 1000;
    expect(formatChartTime(epochSeconds, 480, "full")).toBe("2026-08-11 09:05");
    expect(formatChartTime(epochSeconds, -270, "axis")).toBe("08-10 20:35");
  });
});

describe("closed bar selection", () => {
  it("excludes the currently forming period bucket", () => {
    const bars = [
      bar("2026-08-11T01:00:00.000Z"),
      bar("2026-08-11T01:05:00.000Z"),
      bar("2026-08-11T01:10:00.000Z"),
    ];

    expect(closedBars(bars, "5m", new Date("2026-08-11T01:11:00Z")).map((item) => item.timestamp)).toEqual([
      "2026-08-11T01:00:00.000Z",
      "2026-08-11T01:05:00.000Z",
    ]);
  });
});

describe("bar close countdown", () => {
  it("counts down to the end of the forming 5m bucket", () => {
    const now = Date.parse("2026-08-11T01:11:30.000Z");
    expect(remainingToBucketCloseMs("5m", now)).toBe(210_000);
    expect(formatBarCountdown(210_000)).toBe("03:30");
  });

  it("uses h:mm:ss for long periods", () => {
    expect(formatBarCountdown(3_661_000)).toBe("1:01:01");
  });

  it("starts a full period when now sits on a bucket boundary", () => {
    const now = Date.parse("2026-08-11T01:15:00.000Z");
    expect(remainingToBucketCloseMs("5m", now)).toBe(300_000);
  });
});
