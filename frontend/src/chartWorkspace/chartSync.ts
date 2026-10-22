import type { ChartOverlayContext } from '../chartOverlay';
import type { LogicalRange, MouseEventParams, Time, UTCTimestamp } from 'lightweight-charts';
import { barCloseAtMs } from '../tradingHours';
import { PERIOD_MINUTES } from '../feed';
import type { Bar, Period } from '../types';

interface Entry {
  context: ChartOverlayContext;
  period: Period;
  times: number[];
  byTime: Map<number, Bar>;
  expectedRange?: LogicalRange;
  unsubscribe(): void;
}

function utcTime(time: Time | undefined): number | null {
  if (time === undefined) return null;
  if (typeof time === 'number') return Number.isFinite(time) ? time : null;
  const value = typeof time === 'string'
    ? Date.parse(time) / 1000
    : Date.UTC(time.year, time.month - 1, time.day) / 1000;
  return Number.isFinite(value) ? value : null;
}

function loadBars(entry: Entry, period: Period, bars: Bar[]) {
  entry.period = period;
  entry.byTime = new Map();
  for (const bar of bars) {
    const time = Math.floor(Date.parse(bar.timestamp) / 1000);
    if (Number.isFinite(time)) entry.byTime.set(time, bar);
  }
  entry.times = [...entry.byTime.keys()].sort((a, b) => a - b);
}

/** Interpolate only between loaded bars; whitespace outside the data clamps. */
function timeAtLogical(times: number[], logical: number): number | null {
  if (!times.length) return null;
  const index = Math.max(0, Math.min(times.length - 1, logical));
  const left = Math.floor(index), right = Math.ceil(index);
  return times[left] + (times[right] - times[left]) * (index - left);
}

function logicalAtTime(times: number[], time: number): number {
  if (time <= times[0]) return 0;
  if (time >= times[times.length - 1]) return times.length - 1;
  let left = 0, right = times.length - 1;
  while (right - left > 1) {
    const middle = Math.floor((left + right) / 2);
    if (times[middle] <= time) left = middle;
    else right = middle;
  }
  return left + (time - times[left]) / (times[right] - times[left]);
}

function equalRange(a: LogicalRange, b: LogicalRange) {
  return Math.abs(a.from - b.from) < 1e-6 && Math.abs(a.to - b.to) < 1e-6;
}

/** Coordinates chart instances only; each chart owns its data and zoom level. */
export function createChartSync() {
  const entries = new Map<string, Entry>();
  let options = { crosshair: true, time: true };
  let disposed = false;
  let applying = false;
  let frame: number | null = null;
  let pending: { source: Entry; anchor: number } | null = null;

  const cancelPending = () => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    pending = null;
  };

  const scheduleTime = (source: Entry, range: LogicalRange | null) => {
    if (disposed || applying || !options.time || !range) return;
    // Lightweight Charts can notify range changes after the setter returns.
    if (source.expectedRange && equalRange(source.expectedRange, range)) return;
    source.expectedRange = undefined;
    const anchor = timeAtLogical(source.times, (range.from + range.to) / 2);
    if (anchor === null || !Number.isFinite(anchor)) return;
    pending = { source, anchor };
    if (frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      const work = pending;
      pending = null;
      if (!work || disposed || !options.time || ![...entries.values()].includes(work.source)) return;
      applying = true;
      try {
        for (const target of entries.values()) {
          if (target === work.source || !target.times.length) continue;
          const scale = target.context.chart.timeScale();
          const visible = scale.getVisibleLogicalRange();
          if (!visible) continue;
          const span = visible.to - visible.from;
          if (!Number.isFinite(span) || span <= 0) continue;
          const center = logicalAtTime(target.times, work.anchor);
          const next = { from: center - span / 2, to: center + span / 2 } as LogicalRange;
          if (equalRange(visible, next)) continue;
          target.expectedRange = next;
          scale.setVisibleLogicalRange(next);
        }
      } finally { applying = false; }
    });
  };

  const syncCrosshair = (source: Entry, event: MouseEventParams) => {
    if (disposed || applying || !options.crosshair) return;
    const time = utcTime(event.time);
    applying = true;
    try {
      for (const target of entries.values()) {
        if (target === source) continue;
        const seconds = PERIOD_MINUTES[target.period] * 60;
        const rth = target.times.length && target.byTime.get(target.times[0])?.trading_hours === 'RTH';
        const bucket = time === null ? null : rth
          ? target.times.find(open => open <= time && time * 1000 < barCloseAtMs(target.byTime.get(open)!, target.period)) ?? null
          : Math.floor(time / seconds) * seconds;
        const bar = bucket === null ? undefined : target.byTime.get(bucket);
        if (bar && Number.isFinite(bar.close)) {
          target.context.chart.setCrosshairPosition(bar.close, bucket as UTCTimestamp, target.context.series);
        } else {
          target.context.chart.clearCrosshairPosition();
        }
      }
    } finally { applying = false; }
  };

  return {
    register(id: string, context: ChartOverlayContext, period: Period, bars: Bar[]) {
      if (disposed) return () => {};
      entries.get(id)?.unsubscribe();
      const entry: Entry = { context, period, times: [], byTime: new Map(), unsubscribe() {} };
      loadBars(entry, period, bars);
      const crosshairListener = (event: MouseEventParams) => syncCrosshair(entry, event);
      const rangeListener = (range: LogicalRange | null) => scheduleTime(entry, range);
      const scale = context.chart.timeScale();
      context.chart.subscribeCrosshairMove(crosshairListener);
      scale.subscribeVisibleLogicalRangeChange(rangeListener);
      entry.unsubscribe = () => {
        context.chart.unsubscribeCrosshairMove(crosshairListener);
        scale.unsubscribeVisibleLogicalRangeChange(rangeListener);
        if (pending?.source === entry) cancelPending();
      };
      entries.set(id, entry);
      return () => {
        if (entries.get(id) !== entry) return;
        entry.unsubscribe();
        entries.delete(id);
      };
    },
    update(id: string, period: Period, bars: Bar[]) {
      const entry = entries.get(id);
      if (entry) {
        if (pending?.source === entry) cancelPending();
        loadBars(entry, period, bars);
        entry.expectedRange = undefined;
      }
    },
    setOptions(next: { crosshair: boolean; time: boolean }) {
      const clearCrosshair = options.crosshair && !next.crosshair;
      options = { ...next };
      if (!options.time) cancelPending();
      if (clearCrosshair) {
        applying = true;
        try { entries.forEach(entry => entry.context.chart.clearCrosshairPosition()); }
        finally { applying = false; }
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelPending();
      entries.forEach(entry => entry.unsubscribe());
      entries.clear();
    },
  };
}
