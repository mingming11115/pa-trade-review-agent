import type { Period } from './types';

export const INDICATOR_PERIODS: Period[] = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];
type Visibility = { enabled: boolean; visible: boolean; timeframes: Period[] };
type LineSettings = Visibility & { period: number; color: string; lineWidth: 1 | 2 | 3 | 4 };
export interface IndicatorSettings {
  ma: LineSettings;
  ema: LineSettings;
  boll: LineSettings & { multiplier: number; upperColor: string; lowerColor: string };
  macd: Visibility & { fast: number; slow: number; signal: number; difColor: string; deaColor: string; positiveColor: string; negativeColor: string; lineWidth: 1 | 2 | 3 | 4 };
  rsi: LineSettings;
  volume: Visibility & { upColor: string; downColor: string };
}
export type IndicatorKey = keyof IndicatorSettings;
const visibility = (enabled: boolean): Visibility => ({ enabled, visible: true, timeframes: [...INDICATOR_PERIODS] });
export const DEFAULT_INDICATOR_SETTINGS: IndicatorSettings = {
  ma: { ...visibility(false), period: 20, color: '#4d83c8', lineWidth: 2 },
  ema: { ...visibility(true), period: 20, color: '#f59e0b', lineWidth: 2 },
  boll: { ...visibility(false), period: 20, multiplier: 2, color: '#8b5cf6', upperColor: '#8b5cf6', lowerColor: '#8b5cf6', lineWidth: 2 },
  macd: { ...visibility(false), fast: 12, slow: 26, signal: 9, difColor: '#4d83c8', deaColor: '#e8a13a', positiveColor: '#2c9b79', negativeColor: '#dd6b76', lineWidth: 2 },
  rsi: { ...visibility(false), period: 14, color: '#8b5cf6', lineWidth: 2 },
  volume: { ...visibility(true), upColor: '#089981', downColor: '#f23645' },
};
const STORAGE_KEY = 'pa.chart.indicators.v2';
const LEGACY_KEY = 'pa.chart.indicators.v1';
const defaults = (): IndicatorSettings => structuredClone(DEFAULT_INDICATOR_SETTINGS);
const period = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 500;
const color = (value: unknown) => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
function shape(value: unknown, keys: string[]): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
const keys: IndicatorKey[] = ['ma', 'ema', 'boll', 'macd', 'rsi', 'volume'];
function validParameters(value: Record<string, unknown>, key: IndicatorKey) {
  if (typeof value.enabled !== 'boolean') return false;
  if (key === 'volume') return true;
  if (key === 'macd') return period(value.fast) && period(value.slow) && period(value.signal) && (value.fast as number) < (value.slow as number);
  return period(value.period) && color(value.color) && (key !== 'boll' || (typeof value.multiplier === 'number' && value.multiplier > 0 && value.multiplier <= 10));
}
export function isIndicatorSettings(value: unknown): value is IndicatorSettings {
  if (!shape(value, keys)) return false;
  return keys.every(key => {
    const entry = value[key];
    if (!shape(entry, Object.keys(DEFAULT_INDICATOR_SETTINGS[key])) || !validParameters(entry, key)) return false;
    if (typeof entry.visible !== 'boolean' || !Array.isArray(entry.timeframes)
      || !entry.timeframes.every(item => INDICATOR_PERIODS.includes(item)) || new Set(entry.timeframes).size !== entry.timeframes.length) return false;
    if (key !== 'volume' && ![1, 2, 3, 4].includes(entry.lineWidth as number)) return false;
    if (key === 'boll') return color(entry.upperColor) && color(entry.lowerColor);
    if (key === 'macd') return ['difColor', 'deaColor', 'positiveColor', 'negativeColor'].every(field => color(entry[field]));
    if (key === 'volume') return color(entry.upColor) && color(entry.downColor);
    return true;
  });
}
function migrateLegacy(value: unknown): IndicatorSettings | null {
  if (!shape(value, keys)) return null;
  const legacyKeys = { ma: ['enabled', 'period', 'color'], ema: ['enabled', 'period', 'color'], boll: ['enabled', 'period', 'multiplier', 'color'], macd: ['enabled', 'fast', 'slow', 'signal'], rsi: ['enabled', 'period', 'color'], volume: ['enabled'] };
  if (!keys.every(key => shape(value[key], legacyKeys[key]) && validParameters(value[key] as Record<string, unknown>, key))) return null;
  const result = defaults();
  for (const key of keys) Object.assign(result[key], value[key]);
  result.boll.upperColor = result.boll.color;
  result.boll.lowerColor = result.boll.color;
  return result;
}
export function isIndicatorVisible(entry: Visibility, chartPeriod?: Period | ''): boolean {
  return entry.enabled && entry.visible && (!chartPeriod || !INDICATOR_PERIODS.includes(chartPeriod) || entry.timeframes.includes(chartPeriod));
}
export function loadIndicatorSettings(): IndicatorSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw !== null) { const value: unknown = JSON.parse(raw); return isIndicatorSettings(value) ? value : defaults(); }
    const legacy = window.localStorage.getItem(LEGACY_KEY);
    return legacy ? migrateLegacy(JSON.parse(legacy)) ?? defaults() : defaults();
  } catch { return defaults(); }
}
export function saveIndicatorSettings(settings: IndicatorSettings): void {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); }
  catch { /* A storage restriction must not block chart interaction. */ }
}
