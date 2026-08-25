import type { UTCTimestamp } from "lightweight-charts";

import type { Bar } from "./types";

export type PaioGapKind = "open" | "wick" | "body";

export interface PaioGapBox {
  kind: PaioGapKind;
  timeLeft: UTCTimestamp;
  timeRight: UTCTimestamp;
  priceHigh: number;
  priceLow: number;
  color: string;
}

export interface PaioLabel {
  time: UTCTimestamp;
  /** Absolute price when set; otherwise placed relative to the bar. */
  price?: number;
  anchor: "price" | "belowBar" | "aboveBar";
  text: string;
  color: string;
}

export interface PaioModel {
  gaps: PaioGapBox[];
  barIndexLabels: PaioLabel[];
  hlLabels: PaioLabel[];
}

export interface PaioComputeOptions {
  /**
   * @deprecated Bar Index is always daily-from-open; kept for call-site compat.
   */
  analysisBars?: Bar[];
  /** Show every Nth daily index label (1 always shown). Default 1 = every bar. */
  barIndexStep?: number;
  hlIntervalMultiplier?: number;
}

const OPEN_UP = "rgba(8, 153, 129, 0.60)";
const OPEN_DOWN = "rgba(242, 54, 69, 0.60)";
const WICK_UP = "rgba(8, 153, 129, 0.40)";
const WICK_DOWN = "rgba(242, 54, 69, 0.40)";
const BODY_UP = "rgba(8, 153, 129, 0.20)";
const BODY_DOWN = "rgba(242, 54, 69, 0.20)";
const BAR_INDEX_COLOR = "#f59e0b";
const H_COLOR = "#089981";
const L_COLOR = "#F23645";

function toTime(bar: Bar): UTCTimestamp {
  return Math.floor(new Date(bar.timestamp).getTime() / 1000) as UTCTimestamp;
}

function bodyHigh(bar: Bar): number {
  return Math.max(bar.open, bar.close);
}

function bodyLow(bar: Bar): number {
  return Math.min(bar.open, bar.close);
}

/**
 * PAIO-style Bar Index: each trading day resets at open, first bar = 1.
 * Input must be chronological (oldest → newest).
 */
export function buildDailyBarIndexMap(
  barsChronological: Bar[],
): Map<string, number> {
  return new Map(
    barsChronological
      .filter((bar): bar is Bar & { day_index: number } => bar.day_index != null)
      .map((bar) => [bar.timestamp, bar.day_index]),
  );
}

/** @deprecated Prefer buildDailyBarIndexMap. Kept for older tests/imports. */
export function buildAnalysisSeqMap(barsChronological: Bar[]): Map<string, number> {
  return buildDailyBarIndexMap(barsChronological);
}

function buildBarIndexLabels(
  chartBars: Bar[],
  seqByTs: Map<string, number>,
  step: number,
): PaioLabel[] {
  const labels: PaioLabel[] = [];
  for (const bar of chartBars) {
    const seq = seqByTs.get(bar.timestamp);
    if (seq == null) continue;
    if (seq !== 1 && step > 1 && seq % step !== 0) continue;
    labels.push({
      time: toTime(bar),
      anchor: "belowBar",
      text: String(seq),
      color: BAR_INDEX_COLOR,
    });
  }
  return labels;
}

/** Port of PAIO Gap Series + daily Bar Index + H/L Count. */
export function computePaio(bars: Bar[], options: PaioComputeOptions = {}): PaioModel {
  const barIndexStep = options.barIndexStep ?? 1;
  const hlIntervalMultiplier = options.hlIntervalMultiplier ?? 1;
  const seqByTs = buildDailyBarIndexMap(bars);

  const gaps: PaioGapBox[] = [];
  const barIndexLabels = buildBarIndexLabels(bars, seqByTs, barIndexStep);
  const hlLabels: PaioLabel[] = [];

  let hCount = 0;
  let hLastL: number | null = null;
  let bullPb = false;
  let bullPbL: number | null = null;

  let lCount = 0;
  let lLastH: number | null = null;
  let bearPb = false;
  let bearPbH: number | null = null;

  const ranges: number[] = [];

  for (let i = 0; i < bars.length; i += 1) {
    const bar = bars[i];
    const prev = i > 0 ? bars[i - 1] : null;
    const prev2 = i > 1 ? bars[i - 2] : null;

    let openUp = false;
    let openDown = false;
    let wickUp = false;
    let wickDown = false;

    if (prev) {
      if (bar.low > prev.high) {
        openUp = true;
        gaps.push({
          kind: "open",
          timeLeft: toTime(prev),
          timeRight: toTime(bar),
          priceHigh: bar.low,
          priceLow: prev.high,
          color: OPEN_UP,
        });
      } else if (bar.high < prev.low) {
        openDown = true;
        gaps.push({
          kind: "open",
          timeLeft: toTime(prev),
          timeRight: toTime(bar),
          priceHigh: prev.low,
          priceLow: bar.high,
          color: OPEN_DOWN,
        });
      }
    }

    let prevOpenUp = false;
    let prevOpenDown = false;
    if (prev && prev2) {
      prevOpenUp = prev.low > prev2.high;
      prevOpenDown = prev.high < prev2.low;
    }

    if (prev2) {
      if (bar.low > prev2.high) {
        wickUp = true;
        if (!(openUp || prevOpenUp)) {
          gaps.push({
            kind: "wick",
            timeLeft: toTime(prev2),
            timeRight: toTime(bar),
            priceHigh: bar.low,
            priceLow: prev2.high,
            color: WICK_UP,
          });
        }
      } else if (bar.high < prev2.low) {
        wickDown = true;
        if (!(openDown || prevOpenDown)) {
          gaps.push({
            kind: "wick",
            timeLeft: toTime(prev2),
            timeRight: toTime(bar),
            priceHigh: prev2.low,
            priceLow: bar.high,
            color: WICK_DOWN,
          });
        }
      }

      const bh = bodyHigh(bar);
      const bl = bodyLow(bar);
      const bh2 = bodyHigh(prev2);
      const bl2 = bodyLow(prev2);
      if (bl > bh2) {
        if (!(openUp || prevOpenUp) && !wickUp) {
          gaps.push({
            kind: "body",
            timeLeft: toTime(prev2),
            timeRight: toTime(bar),
            priceHigh: bl,
            priceLow: bh2,
            color: BODY_UP,
          });
        }
      } else if (bh < bl2) {
        if (!(openDown || prevOpenDown) && !wickDown) {
          gaps.push({
            kind: "body",
            timeLeft: toTime(prev2),
            timeRight: toTime(bar),
            priceHigh: bl2,
            priceLow: bh,
            color: BODY_DOWN,
          });
        }
      }
    }

    ranges.push(bar.high - bar.low);
    const smaWindow = ranges.length >= 100 ? ranges.slice(-100) : ranges;
    const interval =
      (smaWindow.reduce((sum, value) => sum + value, 0) / smaWindow.length) * hlIntervalMultiplier;

    if (prev && bar.low < prev.low) {
      bullPb = true;
      if (bullPbL == null || bullPbL > bar.low) bullPbL = bar.low;
    }
    if (prev && bar.high > prev.high && bullPb) {
      hCount += 1;
      if (hLastL == null || (bullPbL != null && bullPbL > hLastL)) hCount = 1;
      hLastL = bullPbL;
      bullPbL = null;
      bullPb = false;
      hlLabels.push({
        time: toTime(bar),
        price: bar.high + interval,
        anchor: "price",
        text: `H${hCount}`,
        color: H_COLOR,
      });
    }

    if (prev && bar.high > prev.high) {
      bearPb = true;
      if (bearPbH == null || bearPbH < bar.high) bearPbH = bar.high;
    }
    if (prev && bar.low < prev.low && bearPb) {
      lCount += 1;
      if (lLastH == null || (bearPbH != null && bearPbH < lLastH)) lCount = 1;
      lLastH = bearPbH;
      bearPbH = null;
      bearPb = false;
      hlLabels.push({
        time: toTime(bar),
        price: bar.low - interval,
        anchor: "price",
        text: `L${lCount}`,
        color: L_COLOR,
      });
    }
  }

  return { gaps, barIndexLabels, hlLabels };
}
