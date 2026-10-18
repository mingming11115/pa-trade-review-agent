import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LogicalRange, MouseEventParams } from 'lightweight-charts';
import type { ChartOverlayContext } from '../chartOverlay';
import type { Bar } from '../types';
import { createChartSync } from './chartSync';

const epoch = (time: string) => Date.parse(time) / 1000;
const bar = (timestamp: string, close = 100): Bar => ({ timestamp, close, open: close, high: close, low: close, volume: 1 });
function chart(initial = { from: 0, to: 10 }) {
  const crosshairs = new Set<(event: MouseEventParams) => void>();
  const ranges = new Set<(range: LogicalRange | null) => void>();
  let visible = initial;
  const emitCrosshair = (time?: number) => crosshairs.forEach(listener => listener({ time } as MouseEventParams));
  const emitRange = (range: { from: number; to: number } | null) => {
    if (range) visible = range;
    ranges.forEach(listener => listener(range as LogicalRange | null));
  };
  const scale = {
    getVisibleLogicalRange: () => visible,
    setVisibleLogicalRange: vi.fn((range: LogicalRange) => emitRange(range)),
    subscribeVisibleLogicalRangeChange: (listener: (range: LogicalRange | null) => void) => ranges.add(listener),
    unsubscribeVisibleLogicalRangeChange: (listener: (range: LogicalRange | null) => void) => ranges.delete(listener),
  };
  const api = {
    timeScale: () => scale,
    subscribeCrosshairMove: (listener: (event: MouseEventParams) => void) => crosshairs.add(listener),
    unsubscribeCrosshairMove: (listener: (event: MouseEventParams) => void) => crosshairs.delete(listener),
    setCrosshairPosition: vi.fn((_price: number, time: number) => emitCrosshair(time)),
    clearCrosshairPosition: vi.fn(() => emitCrosshair()),
  };
  const series = {};
  return { api, scale, series, emitCrosshair, emitRange, crosshairs, ranges, context: { chart: api, series } as unknown as ChartOverlayContext };
}

describe('cross-timeframe chart sync', () => {
  let frames: Map<number, FrameRequestCallback>;
  let nextFrame: number;
  const flush = () => { const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(0)); };
  beforeEach(() => {
    frames = new Map(); nextFrame = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('maps 10:07 UTC into each target bucket using its own timestamp and close without feedback', () => {
    const sync = createChartSync();
    const source = chart(), five = chart(), hour = chart();
    sync.register('source', source.context, '1m', []);
    sync.register('five', five.context, '5m', [bar('2026-10-18T18:05:00+08:00', 205)]);
    sync.register('hour', hour.context, '1h', [bar('2026-10-18T10:00:00Z', 300)]);
    source.emitCrosshair(epoch('2026-10-18T10:07:00Z'));
    expect(five.api.setCrosshairPosition).toHaveBeenCalledExactlyOnceWith(205, 1792317900, five.series);
    expect(hour.api.setCrosshairPosition).toHaveBeenCalledExactlyOnceWith(300, 1792317600, hour.series);
    expect(source.api.setCrosshairPosition).not.toHaveBeenCalled();
    sync.dispose();
  });

  it('clears missing buckets and pointer exit instead of snapping across data gaps', () => {
    const sync = createChartSync();
    const source = chart(), target = chart();
    sync.register('source', source.context, '1m', []);
    sync.register('target', target.context, '5m', [bar('2026-10-18T10:00:00Z'), bar('2026-10-18T10:10:00Z')]);
    source.emitCrosshair(epoch('2026-10-18T10:07:00Z'));
    expect(target.api.setCrosshairPosition).not.toHaveBeenCalled();
    expect(target.api.clearCrosshairPosition).toHaveBeenCalledTimes(1);
    source.emitCrosshair();
    expect(target.api.clearCrosshairPosition).toHaveBeenCalledTimes(2);
    sync.update('target', '15m', [bar('2026-10-18T10:00:00Z', 700)]);
    source.emitCrosshair(epoch('2026-10-18T10:07:00Z'));
    expect(target.api.setCrosshairPosition).toHaveBeenLastCalledWith(700, 1792317600, target.series);
    sync.dispose();
  });

  it('moves the UTC center anchor while preserving each target logical span', () => {
    const sync = createChartSync();
    const source = chart(), target = chart({ from: -1, to: 1 });
    sync.register('source', source.context, '1m', [bar('2026-10-18T10:00:00Z'), bar('2026-10-18T10:10:00Z')]);
    sync.register('target', target.context, '5m', [bar('2026-10-18T09:55:00Z'), bar('2026-10-18T10:00:00Z'), bar('2026-10-18T10:05:00Z'), bar('2026-10-18T10:10:00Z')]);
    source.emitRange({ from: 0, to: 1 });
    flush();
    expect(target.scale.setVisibleLogicalRange).toHaveBeenCalledExactlyOnceWith({ from: 1, to: 3 });
    expect(source.scale.setVisibleLogicalRange).not.toHaveBeenCalled();
    // A zoom changes source span, but only its center anchor is propagated.
    source.emitRange({ from: 0.5, to: 1 });
    flush();
    expect(target.scale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 1.5, to: 3.5 });
    expect(frames.size).toBe(0);
    sync.dispose();
  });

  it('clamps anchors to loaded data bounds and leaves charts with no data alone', () => {
    const sync = createChartSync();
    const source = chart(), target = chart({ from: -2, to: 2 }), empty = chart();
    sync.register('source', source.context, '1m', [bar('2026-10-18T10:00:00Z'), bar('2026-10-18T10:10:00Z')]);
    sync.register('target', target.context, '5m', [bar('2026-10-18T09:00:00Z'), bar('2026-10-18T09:05:00Z')]);
    sync.register('empty', empty.context, '1h', []);
    source.emitRange({ from: 100, to: 110 }); flush();
    expect(target.scale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -1, to: 3 });
    expect(empty.scale.setVisibleLogicalRange).not.toHaveBeenCalled();
    sync.dispose();
  });

  it('coalesces pan events, independently disables syncing, and cancels pending work on teardown', () => {
    const sync = createChartSync();
    const source = chart(), target = chart();
    const unregister = sync.register('source', source.context, '1m', [bar('2026-10-18T10:00:00Z')]);
    sync.register('target', target.context, '5m', [bar('2026-10-18T10:00:00Z')]);
    source.emitRange({ from: 0, to: 2 }); source.emitRange({ from: 1, to: 3 });
    expect(frames.size).toBe(1);
    sync.setOptions({ crosshair: false, time: false }); flush();
    source.emitCrosshair(epoch('2026-10-18T10:00:00Z'));
    expect(target.scale.setVisibleLogicalRange).not.toHaveBeenCalled();
    expect(target.api.setCrosshairPosition).not.toHaveBeenCalled();
    sync.setOptions({ crosshair: true, time: true });
    source.emitRange({ from: 0, to: 2 });
    unregister(); flush();
    expect(source.crosshairs.size + source.ranges.size).toBe(0);
    expect(target.scale.setVisibleLogicalRange).not.toHaveBeenCalled();
    target.emitRange({ from: 0, to: 2 });
    sync.dispose();
    expect(frames.size).toBe(0);
    expect(target.crosshairs.size + target.ranges.size).toBe(0);
    sync.dispose();
  });

  it('ignores deferred programmatic range notifications while allowing later user pans', () => {
    const sync = createChartSync();
    const source = chart({ from: -1, to: 1 }), target = chart({ from: -1, to: 1 });
    const bars = [bar('2026-10-18T10:00:00Z'), bar('2026-10-18T10:10:00Z')];
    sync.register('source', source.context, '1m', bars);
    sync.register('target', target.context, '5m', bars);
    source.emitRange({ from: 0, to: 2 }); flush();
    target.emitRange({ from: 0, to: 2 });
    expect(frames.size).toBe(0);
    target.emitRange({ from: -0.5, to: 1.5 }); flush();
    expect(source.scale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -0.5, to: 1.5 });
    sync.dispose();
  });

  it('unregisters replaced chart instances and does not let old cleanup remove the replacement', () => {
    const sync = createChartSync();
    const old = chart(), replacement = chart(), target = chart();
    const oldCleanup = sync.register('source', old.context, '1m', []);
    sync.register('source', replacement.context, '1m', []);
    sync.register('target', target.context, '5m', [bar('2026-10-18T10:00:00Z')]);
    oldCleanup();
    expect(old.crosshairs.size + old.ranges.size).toBe(0);
    replacement.emitCrosshair(epoch('2026-10-18T10:00:00Z'));
    expect(target.api.setCrosshairPosition).toHaveBeenCalledTimes(1);
    sync.setOptions({ crosshair: true, time: false });
    replacement.emitCrosshair(epoch('2026-10-18T10:00:00Z'));
    expect(target.api.setCrosshairPosition).toHaveBeenCalledTimes(2);
    sync.dispose();
    expect(replacement.crosshairs.size + replacement.ranges.size).toBe(0);
  });
});
