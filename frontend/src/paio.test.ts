import { describe, expect, it } from "vitest";

import { buildDailyBarIndexMap, computePaio } from "./paio";
import type { Bar } from "./types";

function bar(partial: Partial<Bar> & Pick<Bar, "timestamp" | "open" | "high" | "low" | "close">): Bar {
  return { volume: 1, ...partial };
}

describe("computePaio", () => {
  it("labels bars with daily index from session open", () => {
    const bars: Bar[] = [
      bar({ timestamp: "2024-01-02T15:00:00.000Z", day_index: 1, open: 10, high: 11, low: 9, close: 10.5 }),
      bar({ timestamp: "2024-01-02T16:00:00.000Z", day_index: 2, open: 13, high: 14, low: 12.5, close: 13.5 }),
      bar({ timestamp: "2024-01-02T17:00:00.000Z", day_index: 3, open: 13.2, high: 13.8, low: 12.8, close: 13 }),
    ];
    const model = computePaio(bars);
    expect(model.barIndexLabels.map((label) => label.text)).toEqual(["1", "2", "3"]);
  });

  it("resets daily index when trading day changes", () => {
    // America/Chicago: 2024-01-02 23:00 UTC = Jan 2 evening CT; 2024-01-03 06:00 UTC = Jan 3 morning CT
    const bars: Bar[] = [
      bar({ timestamp: "2024-01-02T15:00:00.000Z", day_index: 1, open: 10, high: 11, low: 9, close: 10.5 }),
      bar({ timestamp: "2024-01-02T20:00:00.000Z", day_index: 2, open: 13, high: 14, low: 12.5, close: 13.5 }),
      bar({ timestamp: "2024-01-03T15:00:00.000Z", day_index: 1, open: 13.2, high: 13.8, low: 12.8, close: 13 }),
      bar({ timestamp: "2024-01-03T16:00:00.000Z", day_index: 2, open: 10, high: 10.5, low: 9.5, close: 9.8 }),
    ];
    const model = computePaio(bars);
    expect(model.barIndexLabels.map((label) => label.text)).toEqual(["1", "2", "1", "2"]);
    expect(buildDailyBarIndexMap(bars).get("2024-01-03T15:00:00.000Z")).toBe(1);
  });

  it("numbers all visible chart bars (not only analysis snapshot)", () => {
    const analysisBars: Bar[] = [
      bar({ timestamp: "2024-01-02T16:00:00.000Z", day_index: 2, open: 13, high: 14, low: 12.5, close: 13.5 }),
      bar({ timestamp: "2024-01-02T17:00:00.000Z", day_index: 3, open: 13.2, high: 13.8, low: 12.8, close: 13 }),
    ];
    const chartBars: Bar[] = [
      bar({ timestamp: "2024-01-02T15:00:00.000Z", day_index: 1, open: 10, high: 11, low: 9, close: 10.5 }),
      ...analysisBars,
    ];
    const model = computePaio(chartBars, { analysisBars });
    expect(model.barIndexLabels.map((label) => label.text)).toEqual(["1", "2", "3"]);
  });

  it("detects opening gaps", () => {
    const bars: Bar[] = [
      bar({ timestamp: "2024-01-02T15:00:00.000Z", open: 10, high: 11, low: 9, close: 10.5 }),
      bar({ timestamp: "2024-01-02T16:00:00.000Z", open: 13, high: 14, low: 12.5, close: 13.5 }),
      bar({ timestamp: "2024-01-02T17:00:00.000Z", open: 13.2, high: 13.8, low: 12.8, close: 13 }),
      bar({ timestamp: "2024-01-02T18:00:00.000Z", open: 10, high: 10.5, low: 9.5, close: 9.8 }),
    ];
    const model = computePaio(bars);
    expect(model.gaps.some((gap) => gap.kind === "open" && gap.color.includes("8, 153, 129"))).toBe(true);
    expect(model.gaps.some((gap) => gap.kind === "open" && gap.color.includes("242, 54, 69"))).toBe(true);
  });

  it("emits H/L counts after a pullback break", () => {
    const bars: Bar[] = [
      bar({ timestamp: "2024-01-02T15:00:00.000Z", open: 10, high: 12, low: 10, close: 11 }),
      bar({ timestamp: "2024-01-02T16:00:00.000Z", open: 11, high: 11.5, low: 9, close: 9.5 }),
      bar({ timestamp: "2024-01-02T17:00:00.000Z", open: 9.5, high: 13, low: 9.4, close: 12.5 }),
      bar({ timestamp: "2024-01-02T18:00:00.000Z", open: 12.5, high: 14, low: 12, close: 13 }),
      bar({ timestamp: "2024-01-02T19:00:00.000Z", open: 13, high: 13.5, low: 11, close: 11.5 }),
    ];
    const model = computePaio(bars);
    expect(model.hlLabels.map((label) => label.text)).toEqual(["H1", "L1"]);
  });
});
