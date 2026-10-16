import type { UTCTimestamp } from 'lightweight-charts';
import type { Bar } from './types';

export type IndicatorPoint = { time: UTCTimestamp; value: number };

function validPeriod(period: number): boolean {
  return Number.isInteger(period) && period >= 1 && period <= 500;
}

function point(bar: Bar, value: number): IndicatorPoint {
  return { time: Math.floor(Date.parse(bar.timestamp) / 1000) as UTCTimestamp, value };
}

export function computeSma(bars: Bar[], period: number): IndicatorPoint[] {
  if (!validPeriod(period) || bars.length < period) return [];
  const result: IndicatorPoint[] = [];
  let sum = 0;
  for (let index = 0; index < bars.length; index++) {
    sum += bars[index].close;
    if (index >= period) sum -= bars[index - period].close;
    if (index >= period - 1) result.push(point(bars[index], sum / period));
  }
  return result;
}

/** EMA is seeded by the first complete period's SMA. */
function emaPoints(points: IndicatorPoint[], period: number): IndicatorPoint[] {
  if (points.length < period) return [];
  let value = 0;
  for (let index = 0; index < period; index++) value += points[index].value;
  value /= period;
  const result: IndicatorPoint[] = [{ time: points[period - 1].time, value }];
  const alpha = 2 / (period + 1);
  for (let index = period; index < points.length; index++) {
    value += alpha * (points[index].value - value);
    result.push({ time: points[index].time, value });
  }
  return result;
}

export function computeEma(bars: Bar[], period: number): IndicatorPoint[] {
  if (!validPeriod(period) || bars.length < period) return [];
  return emaPoints(bars.map(bar => point(bar, bar.close)), period);
}

export function computeBollinger(bars: Bar[], period: number, multiplier: number): {
  middle: IndicatorPoint[]; upper: IndicatorPoint[]; lower: IndicatorPoint[];
} {
  const empty = { middle: [], upper: [], lower: [] };
  if (!validPeriod(period) || !Number.isFinite(multiplier) || multiplier <= 0 || multiplier > 10) return empty;
  const middle = computeSma(bars, period);
  const upper: IndicatorPoint[] = [];
  const lower: IndicatorPoint[] = [];
  middle.forEach((center, offset) => {
    // Centered differences avoid cancellation when prices are large and variance is small.
    let squaredDeviation = 0;
    for (let index = offset; index < offset + period; index++) {
      squaredDeviation += (bars[index].close - center.value) ** 2;
    }
    const width = multiplier * Math.sqrt(squaredDeviation / period);
    upper.push({ time: center.time, value: center.value + width });
    lower.push({ time: center.time, value: center.value - width });
  });
  return { middle, upper, lower };
}

export function computeMacd(bars: Bar[], fast: number, slow: number, signal: number): {
  dif: IndicatorPoint[]; dea: IndicatorPoint[]; histogram: IndicatorPoint[];
} {
  if (!validPeriod(fast) || !validPeriod(slow) || !validPeriod(signal) || fast >= slow) {
    return { dif: [], dea: [], histogram: [] };
  }
  const fastEma = computeEma(bars, fast);
  const slowEma = computeEma(bars, slow);
  const dif = slowEma.map((entry, index) => ({
    time: entry.time, value: fastEma[index + slow - fast].value - entry.value,
  }));
  const dea = emaPoints(dif, signal);
  // Common Chinese-market convention: the MACD histogram is twice DIF minus DEA.
  const histogram = dea.map((entry, index) => ({
    time: entry.time, value: (dif[index + signal - 1].value - entry.value) * 2,
  }));
  return { dif, dea, histogram };
}

export function computeRsi(bars: Bar[], period: number): IndicatorPoint[] {
  if (!validPeriod(period) || bars.length <= period) return [];
  let gain = 0;
  let loss = 0;
  for (let index = 1; index <= period; index++) {
    const change = bars[index].close - bars[index - 1].close;
    gain += Math.max(change, 0);
    loss += Math.max(-change, 0);
  }
  gain /= period;
  loss /= period;
  const value = () => gain + loss === 0 ? 50 : 100 * gain / (gain + loss);
  const result: IndicatorPoint[] = [point(bars[period], value())];
  for (let index = period + 1; index < bars.length; index++) {
    const change = bars[index].close - bars[index - 1].close;
    gain = (gain * (period - 1) + Math.max(change, 0)) / period;
    loss = (loss * (period - 1) + Math.max(-change, 0)) / period;
    result.push(point(bars[index], value()));
  }
  return result;
}
