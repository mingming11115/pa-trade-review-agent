import { HistogramSeries, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from 'lightweight-charts';
import { computeBollinger, computeEma, computeMacd, computeRsi, computeSma, type IndicatorPoint } from './indicators';
import { isIndicatorVisible, type IndicatorKey, type IndicatorSettings } from './indicatorSettings';
import type { Bar, Period } from './types';

export type IndicatorLegendRow = {
  key: IndicatorKey;
  paneIndex: number;
  visible: boolean;
  values: { label: string; value: number | null; color: string }[];
};
type LegendValue = { label: string; color: string; points: Map<number, number>; pointColors?: Map<number, string>; negativeColor?: string };
type LegendSource = { key: IndicatorKey; series: ISeriesApi<'Line'> | ISeriesApi<'Histogram'>; visible: boolean; values: LegendValue[] };
const barTime = (bar: Bar): UTCTimestamp => Math.floor(Date.parse(bar.timestamp) / 1000) as UTCTimestamp;
function timestamp(time: Time): number {
  if (typeof time === 'number') return time;
  if (typeof time === 'string') return Date.parse(time) / 1000;
  return Date.UTC(time.year, time.month - 1, time.day) / 1000;
}
function legendValue(label: string, color: string, data: IndicatorPoint[], negativeColor?: string): LegendValue {
  return { label, color, points: new Map(data.map(point => [Number(point.time), point.value])), negativeColor };
}

/** Owns indicator series only. The chart owns their final teardown. */
export function createChartIndicators(chart: IChartApi) {
  const lines = new Map<string, ISeriesApi<'Line'>>();
  let volume: ISeriesApi<'Histogram'> | null = null;
  let macd: { dif: ISeriesApi<'Line'>; dea: ISeriesApi<'Line'>; histogram: ISeriesApi<'Histogram'> } | null = null;
  let rsi: ISeriesApi<'Line'> | null = null;
  let paneCount = 0;
  let latestTime: number | null = null;
  let legends: LegendSource[] = [];

  function line(key: string, enabled: boolean, title: string, color: string, lineWidth: 1 | 2 | 3 | 4, visible: boolean, data: IndicatorPoint[]) {
    let series = lines.get(key);
    if (!enabled) {
      if (series) { chart.removeSeries(series); lines.delete(key); }
      return null;
    }
    const options = { title, color, lineWidth, visible, priceLineVisible: false, lastValueVisible: true };
    if (!series) { series = chart.addSeries(LineSeries, options); lines.set(key, series); }
    else series.applyOptions(options);
    series.setData(data);
    return series;
  }

  return {
    update(bars: Bar[], settings: IndicatorSettings, period?: Period | '') {
      latestTime = bars.length ? barTime(bars[bars.length - 1]) : null;
      legends = [];
      for (const key of ['ma', 'ema'] as const) {
        const entry = settings[key];
        const visible = isIndicatorVisible(entry, period);
        const data = entry.enabled ? (key === 'ma' ? computeSma : computeEma)(bars, entry.period) : [];
        const series = line(key, entry.enabled, `${key.toUpperCase()}${entry.period}`, entry.color, entry.lineWidth, visible, data);
        if (series) legends.push({ key, series, visible, values: [legendValue(key.toUpperCase(), entry.color, data)] });
      }
      const { boll } = settings;
      const bands = boll.enabled ? computeBollinger(bars, boll.period, boll.multiplier) : { middle: [], upper: [], lower: [] };
      const bollVisible = isIndicatorVisible(boll, period);
      const middle = line('boll-middle', boll.enabled, `BOLL${boll.period}`, boll.color, boll.lineWidth, bollVisible, bands.middle);
      line('boll-upper', boll.enabled, 'BOLL 上轨', boll.upperColor, boll.lineWidth, bollVisible, bands.upper);
      line('boll-lower', boll.enabled, 'BOLL 下轨', boll.lowerColor, boll.lineWidth, bollVisible, bands.lower);
      if (middle) legends.push({ key: 'boll', series: middle, visible: bollVisible, values: [legendValue('中轨', boll.color, bands.middle), legendValue('上轨', boll.upperColor, bands.upper), legendValue('下轨', boll.lowerColor, bands.lower)] });

      if (settings.volume.enabled) {
        const entry = settings.volume;
        const visible = isIndicatorVisible(entry, period);
        if (!volume) {
          volume = chart.addSeries(HistogramSeries, { title: 'VOL', priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false, visible });
          chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        } else volume.applyOptions({ visible });
        const data = bars.filter(bar => bar.volume != null).map(bar => ({ time: barTime(bar), value: bar.volume!, color: `${bar.close >= bar.open ? entry.upColor : entry.downColor}47` }));
        volume.setData(data);
        legends.push({ key: 'volume', series: volume, visible, values: [{ ...legendValue('VOL', entry.upColor, data), pointColors: new Map(data.map(point => [Number(point.time), point.color.slice(0, 7)])) }] });
      } else if (volume) { chart.removeSeries(volume); volume = null; }

      // Removing the final series removes its pane and reindexes remaining panes.
      // Hidden indicators retain their series and pane; only deletion removes them.
      if (!settings.macd.enabled && macd) {
        chart.removeSeries(macd.dif); chart.removeSeries(macd.dea); chart.removeSeries(macd.histogram); macd = null;
      }
      if (!settings.rsi.enabled && rsi) { chart.removeSeries(rsi); rsi = null; }
      if (settings.macd.enabled) {
        const entry = settings.macd;
        const visible = isIndicatorVisible(entry, period);
        const difOptions = { title: 'DIF', color: entry.difColor, lineWidth: entry.lineWidth, visible, priceLineVisible: false };
        const deaOptions = { title: 'DEA', color: entry.deaColor, lineWidth: entry.lineWidth, visible, priceLineVisible: false };
        if (!macd) {
          const pane = chart.panes().length;
          macd = { histogram: chart.addSeries(HistogramSeries, { title: 'MACD', visible, priceLineVisible: false }, pane), dif: chart.addSeries(LineSeries, difOptions, pane), dea: chart.addSeries(LineSeries, deaOptions, pane) };
        } else {
          macd.dif.applyOptions(difOptions); macd.dea.applyOptions(deaOptions); macd.histogram.applyOptions({ visible });
        }
        const data = computeMacd(bars, entry.fast, entry.slow, entry.signal);
        macd.dif.setData(data.dif); macd.dea.setData(data.dea);
        macd.histogram.setData(data.histogram.map(point => ({ ...point, color: point.value >= 0 ? entry.positiveColor : entry.negativeColor })));
        legends.push({ key: 'macd', series: macd.dif, visible, values: [legendValue('DIF', entry.difColor, data.dif), legendValue('DEA', entry.deaColor, data.dea), legendValue('MACD', entry.positiveColor, data.histogram, entry.negativeColor)] });
      }
      if (settings.rsi.enabled) {
        const entry = settings.rsi;
        const visible = isIndicatorVisible(entry, period);
        const options = { title: `RSI${entry.period}`, color: entry.color, lineWidth: entry.lineWidth, visible, priceLineVisible: false };
        if (!rsi) {
          rsi = chart.addSeries(LineSeries, { ...options, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) }, chart.panes().length);
          for (const price of [30, 70]) rsi.createPriceLine({ price, title: String(price), color: '#a0a3ab', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true });
        } else rsi.applyOptions(options);
        const data = computeRsi(bars, entry.period);
        rsi.setData(data);
        legends.push({ key: 'rsi', series: rsi, visible, values: [legendValue('RSI', entry.color, data)] });
      }
      const nextPaneCount = Number(settings.macd.enabled) + Number(settings.rsi.enabled);
      if (nextPaneCount !== paneCount) {
        chart.panes().forEach((pane, index) => pane.setStretchFactor(index === 0 ? 3 : 1));
        paneCount = nextPaneCount;
      }
    },
    getLegendRows(time?: Time): IndicatorLegendRow[] {
      const target = time === undefined ? latestTime : timestamp(time);
      return legends.map(source => ({
        key: source.key, paneIndex: source.series.getPane().paneIndex(), visible: source.visible,
        values: source.values.map(item => {
          const value = target === null ? null : item.points.get(target) ?? null;
          return { label: item.label, value, color: (target === null ? undefined : item.pointColors?.get(target)) ?? (value !== null && value < 0 && item.negativeColor ? item.negativeColor : item.color) };
        }),
      }));
    },
  };
}
