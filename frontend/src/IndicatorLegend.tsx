import { useEffect, useRef, useState } from 'react';
import type { IChartApi, MouseEventParams, Time } from 'lightweight-charts';
import type { createChartIndicators, IndicatorLegendRow } from './chartIndicators';
import type { IndicatorKey, IndicatorSettings } from './indicatorSettings';
import type { Bar, Period } from './types';
import './indicatorLegend.css';

const names: Record<IndicatorKey, string> = { ma: 'MA', ema: 'EMA', boll: 'BOLL', macd: 'MACD', rsi: 'RSI', volume: 'VOL' };
function parameters(key: IndicatorKey, settings: IndicatorSettings) {
  if (key === 'volume') return '';
  if (key === 'macd') return `${settings.macd.fast} ${settings.macd.slow} ${settings.macd.signal}`;
  if (key === 'boll') return `${settings.boll.period} ${settings.boll.multiplier}`;
  return String(settings[key].period);
}

export function IndicatorActionIcon({ kind }: { kind: 'eye' | 'hidden' | 'settings' | 'remove' }) {
  return <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
    {kind === 'eye' || kind === 'hidden' ? <><path d="M2 10s3-5 8-5 8 5 8 5-3 5-8 5-8-5-8-5Z" /><circle cx="10" cy="10" r="2.2" />{kind === 'hidden' && <path d="m3 3 14 14" />}</>
      : kind === 'remove' ? <path d="m5 5 10 10M15 5 5 15" />
        : <><path d="m8 2-1 2-2 .5-2 3 1 2-.2 2 1.7 3 2 .5 1.5 2h3l1.5-2 2-.5 1.7-3-.2-2 1-2-2-3-2-.5-1-2Z" /><circle cx="10" cy="9.5" r="2.5" /></>}
  </svg>;
}

type Props = {
  chart: IChartApi;
  renderer: ReturnType<typeof createChartIndicators>;
  container: HTMLDivElement;
  bars: Bar[];
  settings: IndicatorSettings;
  period?: Period | '';
  identity?: string;
  onChange: (settings: IndicatorSettings) => void;
  onSettings: (key: IndicatorKey) => void;
};
type PaneLayout = { top: number; width: number; height: number };

/** HTML controls follow native pane geometry; indicator values use the same data as the drawn series. */
export function IndicatorLegend({ chart, renderer, container, bars, settings, period, identity, onChange, onSettings }: Props) {
  const [rows, setRows] = useState<IndicatorLegendRow[]>([]);
  const [layout, setLayout] = useState<PaneLayout[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const hovered = useRef<Time | undefined>(undefined);
  const emptyCrosshair = useRef(false);

  useEffect(() => {
    hovered.current = undefined;
    emptyCrosshair.current = false;
  }, [period, identity]);

  useEffect(() => {
    renderer.update(bars, settings, period);
    const next = renderer.getLegendRows(hovered.current);
    setRows(emptyCrosshair.current ? next.map(row => ({ ...row, values: row.values.map(value => ({ ...value, value: null })) })) : next);
  }, [renderer, bars, settings, period, identity]);

  useEffect(() => {
    const move = (event: MouseEventParams<Time>) => {
      hovered.current = event.time;
      emptyCrosshair.current = !!event.point && event.time === undefined;
      const next = renderer.getLegendRows(event.time);
      setRows(emptyCrosshair.current ? next.map(row => ({ ...row, values: row.values.map(value => ({ ...value, value: null })) })) : next);
    };
    chart.subscribeCrosshairMove(move);
    return () => chart.unsubscribeCrosshairMove(move);
  }, [chart, renderer]);

  useEffect(() => {
    const stage = container.parentElement;
    let frame = 0;
    const measure = () => {
      const origin = container.getBoundingClientRect();
      let fallbackTop = 0;
      const next = chart.panes().map((pane, index) => {
        const element = pane.getHTMLElement();
        const rect = element?.getBoundingClientRect();
        const paneTop = rect?.height ? rect.top - origin.top : fallbackTop;
        const height = pane.getHeight();
        fallbackTop = paneTop + height + 1;
        let top = paneTop + 8;
        if (index === 0 && stage) {
          // Replay quotes can wrap or move around position labels. Reserve their
          // actual bounds so the legend never covers the trading controls.
          for (const element of stage.querySelectorAll('.chart-quick-trade')) {
            const bounds = element.getBoundingClientRect();
            if (bounds.height) top = Math.max(top, bounds.bottom - origin.top + 6);
          }
        }
        // The pane model exists immediately after addSeries; its widget is
        // created on the next chart draw. paneSize throws until that happens.
        return { top, width: element ? chart.paneSize(index).width : origin.width, height: Math.max(0, paneTop + height - top - 4) };
      });
      setLayout(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    const schedule = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(measure); };
    measure();
    const resize = new ResizeObserver(schedule);
    resize.observe(container);
    chart.panes().forEach(pane => { const element = pane.getHTMLElement(); if (element) resize.observe(element); });
    const mutation = new MutationObserver(schedule);
    if (stage) mutation.observe(stage, { subtree: true, childList: true, attributes: true, attributeFilter: ['style', 'hidden'] });
    window.addEventListener('resize', schedule);
    return () => { cancelAnimationFrame(frame); resize.disconnect(); mutation.disconnect(); window.removeEventListener('resize', schedule); };
  }, [chart, container, settings.macd.enabled, settings.rsi.enabled]);

  const panes = [...new Set(rows.map(row => row.paneIndex))];
  return <div className="indicator-legends">
    {panes.map(pane => {
      const paneRows = rows.filter(row => row.paneIndex === pane);
      const position = layout[pane];
      return <div key={pane} className="indicator-legend-pane" data-pane={pane} style={{ top: position?.top ?? 8, maxWidth: Math.max(0, (position?.width ?? 400) - 20), maxHeight: position?.height, display: position?.height === 0 ? 'none' : undefined }}>
        {!(pane === 0 && collapsed) && paneRows.map(row => <div key={row.key} role="group" aria-label={`${names[row.key]} 指标图例`} className={`indicator-legend-row${row.visible ? '' : ' is-hidden'}`} onPointerDown={event => event.stopPropagation()} onContextMenu={event => { event.preventDefault(); event.stopPropagation(); }}>
          <button type="button" className="indicator-legend-name" aria-label={`${names[row.key]} 参数`} title="双击打开指标设置" onDoubleClick={() => onSettings(row.key)}>{names[row.key]} <span>{parameters(row.key, settings)}</span></button>
          <span className="indicator-legend-values">{row.visible ? row.values.map(value => <span key={value.label} aria-label={`${names[row.key]} ${value.label}`} title={value.label} style={{ color: value.color }}>{value.value === null ? '—' : value.value.toLocaleString('en-US', { maximumFractionDigits: row.key === 'volume' ? 0 : 2 })}</span>) : <span className="indicator-legend-hidden">{settings[row.key].visible ? '当前周期隐藏' : '已隐藏'}</span>}</span>
          <span className="indicator-legend-actions">
            <button type="button" aria-label={`${settings[row.key].visible ? '隐藏' : '显示'} ${names[row.key]}`} title={settings[row.key].visible ? '隐藏指标' : '显示指标'} onClick={() => onChange({ ...settings, [row.key]: { ...settings[row.key], visible: !settings[row.key].visible } })}><IndicatorActionIcon kind={settings[row.key].visible ? 'eye' : 'hidden'} /></button>
            <button type="button" aria-label={`设置 ${names[row.key]}`} title="设置" onClick={() => onSettings(row.key)}><IndicatorActionIcon kind="settings" /></button>
            <button type="button" aria-label={`删除 ${names[row.key]}`} title="删除" onClick={() => onChange({ ...settings, [row.key]: { ...settings[row.key], enabled: false } })}><IndicatorActionIcon kind="remove" /></button>
          </span>
        </div>)}
        {pane === 0 && paneRows.length > 1 && <button type="button" className="indicator-legend-collapse" aria-label={collapsed ? '展开指标图例' : '收起指标图例'} onClick={() => setCollapsed(value => !value)}>{collapsed ? `⌄ ${paneRows.length}` : '⌃'}</button>}
      </div>;
    })}
  </div>;
}
