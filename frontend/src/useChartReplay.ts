import { useEffect, useMemo, useRef, useState } from 'react';
import { lookbackDurationMs, mergeBars, tradingRangeLimitMs } from './feed';
import { inTradingHours } from './tradingHours';
import type { Bar, Period, TradingHours } from './types';

export type ReplayLoader = (start: string, end: string, signal: AbortSignal) => Promise<Bar[]>;

export function useChartReplay(key: string, period: Period | '' | undefined, offset: number, load?: ReplayLoader, tradingHours: TradingHours = "ETH", symbol = "") {
  const [data, setData] = useState<Bar[] | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlayingState] = useState(false);
  const playingRef = useRef(false);
  function setPlaying(value: boolean) {
    playingRef.current = value;
    setPlayingState(value);
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const requestRef = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!busy) return;
    setElapsedSeconds(0);
    const started = Date.now();
    const timer = window.setInterval(() => setElapsedSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  async function request(start: string, end: string): Promise<Bar[]> {
    if (!load) return [];
    const controller = new AbortController();
    requestRef.current = controller;
    const timer = window.setTimeout(() => controller.abort(new Error('行情加载超时，请重试或选择已有历史数据的时间')), 150000);
    let onAbort: () => void = () => {};
    try {
      return await Promise.race([
        load(start, end, controller.signal),
        new Promise<never>((_resolve, reject) => {
          onAbort = () => reject(controller.signal.reason);
          controller.signal.addEventListener('abort', onAbort, { once: true });
          if (controller.signal.aborted) onAbort();
        }),
      ]);
    } finally {
      window.clearTimeout(timer);
      controller.signal.removeEventListener('abort', onAbort);
      if (requestRef.current === controller) requestRef.current = null;
    }
  }
  const generation = useRef(0);
  const inflight = useRef(false);
  function exit() {
    generation.current += 1;
    requestRef.current?.abort();
    inflight.current = false;
    setData(null); setPlaying(false); setBusy(false); setError('');
  }
  useEffect(() => {
    exit();
    return () => { generation.current += 1; requestRef.current?.abort(); };
  }, [key]);
  async function start(value: string) {
    const time = Date.parse(`${value}Z`) - offset * 60000;
    if (!Number.isFinite(time)) {
      setError('请填写完整的日期和时间'); return;
    }
    if (time > Date.now()) {
      setError('所选时间晚于当前时间，请检查输入时区'); return;
    }
    await startAt(() => time);
  }
  async function startRandom() {
    const earliest = Date.UTC(2023, 0, 1);
    const latest = Date.now();
    return startAt(() => earliest + Math.floor(Math.random() * (latest - earliest)), earliest);
  }
  async function startAt(pickTime: () => number, earliest?: number) {
    if (!period || !load) return;
    const token = ++generation.current;
    requestRef.current?.abort();
    inflight.current = true;
    setBusy(true); setPlaying(false); setError('');
    try {
      for (let attempt = 0; attempt < (earliest === undefined ? 1 : 5); attempt += 1) {
        const time = pickTime();
        const beforeSpan = lookbackDurationMs(period, 100, tradingHours);
        const afterSpan = lookbackDurationMs(period, 20, tradingHours);
        const rangeLimit = tradingHours === "RTH" ? tradingRangeLimitMs(period) : Infinity;
        const loaded = mergeBars([], await request(
          new Date(time - Math.min(beforeSpan, rangeLimit * 0.8)).toISOString(),
          new Date(Math.min(Date.now(), time + Math.min(afterSpan, rangeLimit * 0.2))).toISOString(),
        ));
        if (token !== generation.current) return;
        const before = loaded.filter(bar => Date.parse(bar.timestamp) <= time);
        const nextIndex = tradingHours === "RTH" && !inTradingHours(new Date(time).toISOString(), symbol, tradingHours)
          ? loaded.findIndex(bar => Date.parse(bar.timestamp) >= time)
          : before.length - 1;
        const tip = loaded[nextIndex];
        if (!tip || (earliest !== undefined && Date.parse(tip.timestamp) < earliest)) continue;
        setData(loaded); setIndex(nextIndex);
        return Date.parse(tip.timestamp);
      }
      setError(earliest === undefined
        ? '所选时间附近没有可用行情，请选择其他时间'
        : '随机尝试的区间暂无可用行情，请重新随机或手动选择时间');
    } catch (caught) {
      if (token === generation.current) setError((caught as Error)?.message || '加载回放行情失败，请重试');
    } finally {
      if (token === generation.current) { inflight.current = false; setBusy(false); }
    }
  }
  async function next(automatic = false) {
    if (!data || inflight.current) return;
    if (index < data.length - 1) { setIndex(index + 1); return; }
    if (!load || !period) { setPlaying(false); return; }
    const token = generation.current;
    inflight.current = true; setBusy(true); setError('');
    try {
      const last = Date.parse(data[data.length - 1].timestamp);
      // Search bounded, expanding windows so weekends do not trap playback.
      let cursor = last;
      let span = lookbackDurationMs(period, 50, tradingHours);
      const horizon = Math.min(Date.now(), last + Math.max(span, 7 * 24 * 60 * 60 * 1000));
      let later: Bar[] = [];
      for (let attempt = 0; attempt < 5 && cursor < horizon; attempt += 1) {
        const end = Math.min(horizon, cursor + span);
        const loaded = await request(new Date(cursor).toISOString(), new Date(end).toISOString());
        if (token !== generation.current) return;
        later = loaded.filter(bar => Date.parse(bar.timestamp) > last);
        if (later.length) break;
        cursor = end;
        span *= 4;
      }
      if (!later.length) { setPlaying(false); setError('后续区间暂无行情，可稍后重试或重新选择时间'); return; }
      setData(mergeBars(data, later));
      if (!automatic || playingRef.current) setIndex(index + 1);
    } catch (caught) {
      if (token === generation.current) { setPlaying(false); setError((caught as Error)?.message || '加载后续行情失败，请重试'); }
    } finally {
      if (token === generation.current) { inflight.current = false; setBusy(false); }
    }
  }
  useEffect(() => {
    if (!playing || busy || !data) return;
    const timer = window.setTimeout(() => { void next(true); }, 1000);
    return () => window.clearTimeout(timer);
  }, [playing, busy, data, index]);
  const bars = useMemo(() => data?.slice(0, index + 1) ?? null, [data, index]);
  return {
    bars, playing, setPlaying, busy, error, elapsedSeconds,
    start, startRandom, next, exit, canBack: index > 0,
    back() { setPlaying(false); setIndex(current => Math.max(0, current - 1)); },
  };
}
