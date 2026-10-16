import { describe, expect, it } from 'vitest';
import type { Bar } from './types';
import {
  computeSma, computeEma, computeBollinger, computeMacd, computeRsi,
  type IndicatorPoint,
} from './indicators';

function bars(closes: number[]): Bar[] {
  return closes.map((close, index) => ({
    timestamp: new Date((1_700_000_000 + index * 60) * 1000).toISOString(),
    open: close, high: close, low: close, close, volume: 100,
  }));
}

function expectPoints(actual: IndicatorPoint[], startIndex: number, values: number[]) {
  expect(actual).toHaveLength(values.length);
  values.forEach((value, index) => {
    expect(actual[index].time).toBe(1_700_000_000 + (startIndex + index) * 60);
    expect(actual[index].value).toBeCloseTo(value, 10);
  });
}

describe('built-in indicator calculations', () => {
  it('truncates millisecond timestamps to chart seconds for every indicator', () => {
    const input = bars([1, 2, 4, 3]);
    input.forEach((bar, index) => {
      bar.timestamp = `2026-01-01T00:0${index}:00.123Z`;
    });
    expect(computeSma(input, 3)[0].time).toBe(1_767_225_720);
    expect(computeEma(input, 3)[0].time).toBe(1_767_225_720);
    const boll = computeBollinger(input, 3, 2);
    for (const points of Object.values(boll)) expect(points[0].time).toBe(1_767_225_720);
    const macd = computeMacd(input, 2, 3, 2);
    expect(macd.dif[0].time).toBe(1_767_225_720);
    expect(macd.dea[0].time).toBe(1_767_225_780);
    expect(macd.histogram[0].time).toBe(1_767_225_780);
    expect(computeRsi(input, 3)[0].time).toBe(1_767_225_780);
  });

  it('SMA averages only the trailing period and starts at the first full window', () => {
    expectPoints(computeSma(bars([2, 4, 6, 10, 5]), 3), 2, [4, 20 / 3, 7]);
  });

  it('EMA starts with an SMA seed and then applies alpha 2/(period+1)', () => {
    expectPoints(computeEma(bars([2, 4, 6, 10, 5]), 3), 2, [4, 7, 6]);
  });

  it('BOLL uses the population standard deviation of each trailing window', () => {
    const result = computeBollinger(bars([1, 2, 3, 4]), 3, 2);
    expectPoints(result.middle, 2, [2, 3]);
    expectPoints(result.upper, 2, [3.632993161855452, 4.632993161855452]);
    expectPoints(result.lower, 2, [0.367006838144548, 1.367006838144548]);
  });

  it('MACD aligns fast and slow SMA-seeded EMAs and doubles the DIF-DEA histogram', () => {
    const result = computeMacd(bars([1, 2, 4, 3, 6, 2]), 2, 3, 2);
    expectPoints(result.dif, 2, [5 / 6, 7 / 18, 37 / 54, -13 / 81]);
    expectPoints(result.dea, 3, [11 / 18, 107 / 162, 55 / 486]);
    expectPoints(result.histogram, 3, [-4 / 9, 4 / 81, -133 / 243]);
  });

  it('MACD exposes warmed DIF while waiting for the signal seed', () => {
    const result = computeMacd(bars([1, 2, 4]), 2, 3, 2);
    expectPoints(result.dif, 2, [5 / 6]);
    expect(result.dea).toEqual([]);
    expect(result.histogram).toEqual([]);
  });

  it('RSI starts after period changes and uses Wilder smoothing thereafter', () => {
    expectPoints(computeRsi(bars([10, 12, 11, 14, 12, 15]), 3), 3,
      [250 / 3, 500 / 9, 4700 / 63]);
  });

  it.each([
    [[1, 2, 3, 4, 5], 100],
    [[5, 4, 3, 2, 1], 0],
    [[3, 3, 3, 3, 3], 50],
  ])('RSI handles one-sided or flat changes: %j', (closes, value) => {
    expectPoints(computeRsi(bars(closes), 3), 3, [value, value]);
  });

  it('constant prices keep averages and BOLL constant and MACD zero', () => {
    const input = bars([7, 7, 7, 7, 7]);
    expectPoints(computeSma(input, 3), 2, [7, 7, 7]);
    expectPoints(computeEma(input, 3), 2, [7, 7, 7]);
    const boll = computeBollinger(input, 3, 2);
    for (const points of Object.values(boll)) expectPoints(points, 2, [7, 7, 7]);
    const macd = computeMacd(input, 2, 3, 2);
    expectPoints(macd.dif, 2, [0, 0, 0]);
    expectPoints(macd.dea, 3, [0, 0]);
    expectPoints(macd.histogram, 3, [0, 0]);
  });

  it.each([{ closes: [] }, { closes: [1, 2] }])('returns empty series before the price seed is available: $closes', ({ closes }) => {
    const input = bars(closes);
    expect(computeSma(input, 3)).toEqual([]);
    expect(computeEma(input, 3)).toEqual([]);
    expect(computeBollinger(input, 3, 2)).toEqual({ middle: [], upper: [], lower: [] });
    expect(computeMacd(input, 2, 3, 2)).toEqual({ dif: [], dea: [], histogram: [] });
    expect(computeRsi(input, 3)).toEqual([]);
  });

  it('RSI needs one more bar than its period', () => {
    expect(computeRsi(bars([1, 2, 3]), 3)).toEqual([]);
  });

  it.each([0, -1, 1.5, 501, NaN, Infinity])('rejects invalid period %s', (period) => {
    const input = bars([1, 2, 3, 4]);
    expect(computeSma(input, period)).toEqual([]);
    expect(computeEma(input, period)).toEqual([]);
    expect(computeBollinger(input, period, 2)).toEqual({ middle: [], upper: [], lower: [] });
    expect(computeRsi(input, period)).toEqual([]);
    for (const parameters of [[period, 3, 2], [2, period, 2], [2, 3, period]]) {
      expect(computeMacd(input, ...parameters as [number, number, number]))
        .toEqual({ dif: [], dea: [], histogram: [] });
    }
  });

  it.each([0, -1, 10.1, NaN, Infinity])('rejects invalid BOLL multiplier %s', (multiplier) => {
    expect(computeBollinger(bars([1, 2, 3]), 3, multiplier))
      .toEqual({ middle: [], upper: [], lower: [] });
  });

  it.each([[3, 3], [4, 3]])('rejects fast period %s greater than or equal to slow %s', (fast, slow) => {
    expect(computeMacd(bars([1, 2, 3, 4]), fast, slow, 2))
      .toEqual({ dif: [], dea: [], histogram: [] });
  });

  it('accepts period one and the upper period and multiplier boundaries', () => {
    const input = bars([2, 4, 6]);
    expectPoints(computeSma(input, 1), 0, [2, 4, 6]);
    expectPoints(computeEma(input, 1), 0, [2, 4, 6]);
    expectPoints(computeRsi(input, 1), 1, [100, 100]);
    const longInput = bars(Array(501).fill(7));
    expectPoints(computeSma(longInput, 500), 499, [7, 7]);
    expectPoints(computeEma(longInput, 500), 499, [7, 7]);
    expectPoints(computeRsi(longInput, 500), 500, [50]);
    const boll = computeBollinger(longInput, 500, 10);
    for (const points of Object.values(boll)) expectPoints(points, 499, [7, 7]);
    expectPoints(computeMacd(longInput, 1, 500, 1).histogram, 499, [0, 0]);
  });

  it('replay prefixes retain every already-visible value without using future bars or mutating input', () => {
    const input = bars([1, 2, 4, 3, 6, 2, 8, 1]);
    const original = structuredClone(input);
    const calculations = [
      (data: Bar[]) => ({ points: computeSma(data, 3) }),
      (data: Bar[]) => ({ points: computeEma(data, 3) }),
      (data: Bar[]) => computeBollinger(data, 3, 2),
      (data: Bar[]) => computeMacd(data, 2, 3, 2),
      (data: Bar[]) => ({ points: computeRsi(data, 3) }),
    ];
    for (const calculate of calculations) {
      const full = calculate(input);
      for (let length = 1; length <= input.length; length++) {
        const prefix = calculate(input.slice(0, length));
        for (const [key, points] of Object.entries(prefix)) {
          const fullPoints = (full as Record<string, IndicatorPoint[]>)[key];
          expect(points).toEqual(fullPoints.filter(point => point.time <= 1_700_000_000 + (length - 1) * 60));
        }
      }
    }
    expect(input).toEqual(original);
  });
});
