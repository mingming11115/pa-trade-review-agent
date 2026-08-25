import { describe, expect, it } from "vitest";

import { barSummaryDisplay, formatBarRange, formatBarRef } from "./barDisplay";
import type { BarRef } from "./types";

const ref = (dayIndex: number, timestamp: string, timeframe = "5m"): BarRef => ({
  bar_timestamp: timestamp,
  timeframe,
  session: "CME",
  day_index: dayIndex,
});

describe("structured bar display", () => {
  it("renders the authoritative session index, open time, and timeframe", () => {
    expect(formatBarRef(ref(42, "2026-08-11T13:20:00Z"))).toBe("#42 · 08:20 · 5m");
    expect(formatBarRef(ref(42, "2026-01-11T13:20:00Z"))).toBe("#42 · 07:20 · 5m");
  });

  it("renders structured ranges without relative K conversion", () => {
    expect(formatBarRange({
      start: ref(38, "2026-08-11T13:00:00Z"),
      end: ref(42, "2026-08-11T13:20:00Z"),
    })).toBe("#38 · 08:00 · 5m → #42 · 08:20 · 5m");
  });

  it("sorts summaries by the authoritative day index", () => {
    expect(barSummaryDisplay({ bar_ref: ref(42, "2026-08-11T13:20:00Z") })).toEqual({
      label: "#42 · 08:20 · 5m",
      sequence: 42,
    });
  });
});
