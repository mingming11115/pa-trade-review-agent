import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useChartReplay } from './useChartReplay';
import type { Bar } from './types';
const bars: Bar[] = [0, 1, 2].map(hour => ({ timestamp: `2026-01-01T0${hour}:00:00Z`, open: 1, high: 2, low: 1, close: 2, volume: 1 }));
describe('chart replay', () => {
  afterEach(() => vi.useRealTimers());
  it('random replay starts in 2023, retries gaps and hides later candles', async () => {
    const random = vi.spyOn(Math, 'random').mockReturnValue(0);
    const first = { ...bars[0], timestamp: '2023-01-01T00:00:00Z' };
    const later = { ...first, timestamp: '2023-01-01T01:00:00Z' };
    const load = vi.fn().mockResolvedValueOnce([]).mockResolvedValue([first, later]);
    try {
      const { result } = renderHook(() => useChartReplay('random', '1h', 480, load));
      await act(async () => { await result.current.startRandom(); });
      expect(result.current.bars).toEqual([first]);
      expect(result.current.playing).toBe(false);
      await act(async () => { await result.current.next(); });
      expect(result.current.bars).toEqual([first, later]);
    } finally { random.mockRestore(); }
  });
  it('stops random retries after five empty windows', async () => {
    const load = vi.fn().mockResolvedValue([]);
    const { result } = renderHook(() => useChartReplay('random', '1h', 0, load));
    await act(async () => { await result.current.startRandom(); });
    expect(result.current.error).toContain('随机');
    expect(result.current.busy).toBe(false);
    expect(result.current.bars).toBeNull();
    expect(load).toHaveBeenCalledTimes(5);
  });
  it('cancels random loading without retrying', async () => {
    let signal!: AbortSignal;
    const load = vi.fn((_start: string, _end: string, incoming: AbortSignal) => {
      signal = incoming;
      return new Promise<Bar[]>(() => {});
    });
    const { result } = renderHook(() => useChartReplay('random', '1h', 0, load));
    let pending!: Promise<unknown>;
    act(() => { pending = result.current.startRandom(); });
    act(() => result.current.exit());
    await act(async () => { await pending; });
    expect(signal.aborted).toBe(true);
    expect(result.current.bars).toBeNull();
    expect(result.current.error).toBe('');
    expect(result.current.busy).toBe(false);
    expect(load).toHaveBeenCalledTimes(1);
  });
  it('aborts pending network work on exit and leaves no loading state', async () => {
    let signal: AbortSignal | undefined;
    const load = vi.fn((_start: string, _end: string, incoming: AbortSignal) => {
      signal = incoming;
      return new Promise<Bar[]>(() => {});
    });
    const { result } = renderHook(() => useChartReplay('a', '1h', 0, load));
    let pending!: Promise<void>;
    act(() => { pending = result.current.start('2026-01-01T01:00'); });
    act(() => result.current.exit());
    expect(signal?.aborted).toBe(true);
    await act(async () => { await pending; });
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toBe('');
  });
  it('releases loading after timeout even if a loader never settles', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useChartReplay('a', '1h', 0, () => new Promise<Bar[]>(() => {})));
    let pending!: Promise<void>;
    act(() => { pending = result.current.start('2026-01-01T01:00'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(150000); await pending; });
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toContain('超时');
  });
  it('distinguishes malformed input from a future time and limits the initial download', async () => {
    const load = vi.fn().mockResolvedValue(bars);
    const { result } = renderHook(() => useChartReplay('a', '1h', 480, load));
    await act(async () => { await result.current.start('broken'); });
    expect(result.current.error).toContain('完整');
    await act(async () => { await result.current.start('2999-01-01T00:00'); });
    expect(result.current.error).toContain('晚于');
    await act(async () => { await result.current.start('2026-01-01T09:00'); });
    expect(load.mock.lastCall?.[0]).toBe('2025-12-27T21:00:00.000Z');
    expect(load.mock.lastCall?.[1]).toBe('2026-01-01T21:00:00.000Z');
  });
  it('uses chart timezone, hides future bars, steps back and restores on exit', async () => {
    const { result } = renderHook(() => useChartReplay('a', '1h', 480, async () => bars));
    await act(async () => { await result.current.start('2026-01-01T09:00'); });
    expect(result.current.bars?.map(b => b.timestamp)).toEqual([bars[0].timestamp, bars[1].timestamp]);
    await act(async () => { await result.current.next(); });
    expect(result.current.bars).toHaveLength(3);
    act(() => result.current.back());
    expect(result.current.bars).toHaveLength(2);
    act(() => result.current.exit());
    expect(result.current.bars).toBeNull();
  });
  it('does not advance when paused while an automatic step loads more bars', async () => {
    let resolve!: (bars: Bar[]) => void;
    const load = vi.fn().mockResolvedValueOnce(bars.slice(0, 2)).mockImplementationOnce(() => new Promise<Bar[]>(done => { resolve = done; }));
    const { result } = renderHook(() => useChartReplay('pause', '1h', 0, load));
    await act(async () => { await result.current.start('2026-01-01T01:00'); });
    act(() => { result.current.setPlaying(true); });
    await waitFor(() => expect(result.current.busy).toBe(true), { timeout: 2000 });
    act(() => result.current.setPlaying(false));
    await act(async () => { resolve(bars.slice(1)); });
    expect(result.current.bars).toHaveLength(2);
    await act(async () => { await result.current.next(); });
    expect(result.current.bars).toHaveLength(3);
  });
  it('rejects empty history and surfaces request errors', async () => {
    const load = vi.fn().mockResolvedValueOnce([]).mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(() => useChartReplay('a', '1h', 0, load));
    await act(async () => { await result.current.start('2026-01-01T01:00'); });
    expect(result.current.error).toBeTruthy();
    expect(result.current.bars).toBeNull();
    await act(async () => { await result.current.start('2026-01-01T01:00'); });
    expect(result.current.error).toContain('offline');
  });
  it('ignores a pending request after exit', async () => {
    let resolve!: (bars: Bar[]) => void;
    const load = () => new Promise<Bar[]>(r => { resolve = r; });
    const { result } = renderHook(() => useChartReplay('a', '1h', 0, load));
    let pending!: Promise<void>;
    act(() => { pending = result.current.start('2026-01-01T01:00'); });
    act(() => result.current.exit());
    await act(async () => { resolve(bars); await pending; });
    expect(result.current.bars).toBeNull();
  });
  it('loads the next chunk without duplicates and preserves the cursor on failure', async () => {
    const load = vi.fn().mockResolvedValueOnce(bars.slice(0, 2)).mockResolvedValueOnce(bars.slice(1)).mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(() => useChartReplay('a', '1h', 0, load));
    await act(async () => { await result.current.start('2026-01-01T01:00'); });
    await act(async () => { await result.current.next(); });
    expect(result.current.bars).toEqual(bars);
    await act(async () => { await result.current.next(); });
    expect(result.current.bars).toEqual(bars);
    expect(result.current.error).toContain('offline');
    expect(result.current.busy).toBe(false);
  });
  it('crosses an empty session gap before stopping playback', async () => {
    const later = { ...bars[0], timestamp: '2026-01-04T01:00:00Z' };
    const load = vi.fn(async (start: string, end: string) => [bars[0], later].filter(bar => Date.parse(bar.timestamp) >= Date.parse(start) && Date.parse(bar.timestamp) <= Date.parse(end)));
    const { result } = renderHook(() => useChartReplay('a', '1m', 0, load));
    await act(async () => { await result.current.start('2026-01-01T00:00'); });
    await act(async () => { await result.current.next(); });
    expect(result.current.bars).toEqual([bars[0], later]);
  });
  it('rejects invalid and future dates without fetching', async () => {
    const load = vi.fn();
    const { result } = renderHook(() => useChartReplay('a', '1h', 0, load));
    await act(async () => { await result.current.start(''); });
    expect(result.current.error).toBeTruthy();
    await act(async () => { await result.current.start('2999-01-01T01:00'); });
    expect(result.current.error).toBeTruthy();
    expect(load).not.toHaveBeenCalled();
  });
  it('plays, stops at the data boundary and resets when chart identity changes', async () => {
    const load = vi.fn().mockResolvedValueOnce(bars).mockResolvedValue([]);
    const { result, rerender } = renderHook(({ id }) => useChartReplay(id, '1h', 0, load), { initialProps: { id: 'a' } });
    await act(async () => { await result.current.start('2026-01-01T01:00'); });
    act(() => { result.current.setPlaying(true); });
    await waitFor(() => expect(result.current.playing).toBe(false), { timeout: 3000 });
    expect(result.current.bars).toHaveLength(3);
    expect(result.current.error).toBeTruthy();
    rerender({ id: 'b' });
    expect(result.current.bars).toBeNull();
  });
});
