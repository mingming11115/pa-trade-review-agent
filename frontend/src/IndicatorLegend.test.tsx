import { cleanup, fireEvent, render, screen, act } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { IChartApi, MouseEventParams, Time } from 'lightweight-charts';
import { IndicatorLegend } from './IndicatorLegend';
import type { createChartIndicators } from './chartIndicators';
import { DEFAULT_INDICATOR_SETTINGS } from './indicatorSettings';

const rect = (top: number, height: number) => ({ top, bottom: top + height, left: 0, right: 500, width: 500, height, x: 0, y: top, toJSON() {} });
let resize: () => void;
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { resize = callback; } observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});
afterEach(() => { cleanup(); document.body.innerHTML = ''; vi.unstubAllGlobals(); });
function setup(pendingPane = false) {
  const stage = document.createElement('div'); document.body.appendChild(stage);
  const container = document.createElement('div'); stage.appendChild(container);
  const quote = document.createElement('div'); quote.className = 'chart-quick-trade'; stage.appendChild(quote);
  let quoteTop = 42, quoteHeight = 70, secondTop = 305;
  container.getBoundingClientRect = () => rect(100, 450);
  quote.getBoundingClientRect = () => rect(100 + quoteTop, quoteHeight);
  const nativePanes = [document.createElement('div'), document.createElement('div')];
  nativePanes[0].getBoundingClientRect = () => rect(100, 300);
  nativePanes[1].getBoundingClientRect = () => rect(100 + secondTop, 140);
  let crosshair: ((event: MouseEventParams<Time>) => void) | undefined;
  const chart = {
    panes: () => nativePanes.map((element, index) => ({ getHTMLElement: () => pendingPane && index === 1 ? null : element, getHeight: () => index === 0 ? 300 : 140 })),
    paneSize: (index: number) => { if (pendingPane && index === 1) throw new Error('Value is undefined'); return { width: 500, height: 300 }; },
    subscribeCrosshairMove: vi.fn((callback) => { crosshair = callback; }), unsubscribeCrosshairMove: vi.fn(),
  };
  const renderer = { update: vi.fn(), getLegendRows: (time?: Time) => [
    { key: 'ema', paneIndex: 0, visible: true, values: [{ label: 'EMA', value: time === 1 ? 15 : 20, color: '#123456' }] },
    { key: 'rsi', paneIndex: 1, visible: true, values: [{ label: 'RSI', value: 50, color: '#123456' }] },
  ] };
  const settings = structuredClone(DEFAULT_INDICATOR_SETTINGS); settings.rsi.enabled = true;
  const props = { chart: chart as unknown as IChartApi, renderer: renderer as unknown as ReturnType<typeof createChartIndicators>, container, bars: [], settings, period: '1h' as const, identity: 'ES:normal', onChange: vi.fn(), onSettings: vi.fn() };
  const view = render(<IndicatorLegend {...props} />, { container: stage.appendChild(document.createElement('div')) });
  return { ...view, props, chart, crosshair: (event: { time?: Time; point?: { x: number; y: number } }) => crosshair?.(event as MouseEventParams<Time>), resizeQuote: (top: number, height: number) => { quoteTop = top; quoteHeight = height; act(() => { resize(); window.dispatchEvent(new Event('resize')); }); }, resizePane: (top: number) => { secondTop = top; act(() => { resize(); window.dispatchEvent(new Event('resize')); }); } };
}
it('does not read native widget dimensions before a new pane is mounted', () => {
  expect(() => setup(true)).not.toThrow();
});
it('follows measured replay quote bounds and native pane offsets after resizing', () => {
  const view = setup();
  expect(view.container.querySelector('[data-pane="0"]')).toHaveStyle({ top: '118px' });
  expect(view.container.querySelector('[data-pane="1"]')).toHaveStyle({ top: '313px' });
  view.resizeQuote(80, 100); expect(view.container.querySelector('[data-pane="0"]')).toHaveStyle({ top: '186px' });
  view.resizePane(250); expect(view.container.querySelector('[data-pane="1"]')).toHaveStyle({ top: '258px' });
});
it('prevents the main legend from spilling into another pane when quotes consume its available height', () => {
  const view = setup(); view.resizeQuote(270, 70);
  const main = view.container.querySelector('[data-pane="0"]');
  if (main) expect(main).not.toBeVisible();
  expect(screen.getByRole('group', { name: 'RSI 指标图例' })).toBeVisible();
});
it('clears hovered readings when changing chart identity within the same period', () => {
  const view = setup(); act(() => view.crosshair({ time: 1 as Time, point: { x: 20, y: 20 } }));
  expect(screen.getByLabelText('EMA EMA')).toHaveTextContent('15');
  view.rerender(<IndicatorLegend {...view.props} identity="NQ:normal" />);
  expect(screen.getByLabelText('EMA EMA')).toHaveTextContent('20');
});
it('shows gaps over empty chart space, restores latest readings on leaving, and unsubscribes', () => {
  const view = setup(); act(() => view.crosshair({ time: 1 as Time, point: { x: 20, y: 20 } })); expect(screen.getByLabelText('EMA EMA')).toHaveTextContent('15');
  act(() => view.crosshair({ point: { x: 490, y: 20 } })); expect(screen.getByLabelText('EMA EMA')).toHaveTextContent('—');
  act(() => view.crosshair({})); expect(screen.getByLabelText('EMA EMA')).toHaveTextContent('20');
  view.unmount(); expect(view.chart.unsubscribeCrosshairMove).toHaveBeenCalledWith(view.chart.subscribeCrosshairMove.mock.calls[0][0]);
});
it('exposes accessible settings, hide and delete actions', () => {
  const view = setup(); fireEvent.click(screen.getByRole('button', { name: '设置 EMA' })); expect(view.props.onSettings).toHaveBeenCalledWith('ema');
  fireEvent.click(screen.getByRole('button', { name: '隐藏 EMA' })); expect(view.props.onChange.mock.calls[0][0].ema).toMatchObject({ enabled: true, visible: false });
  fireEvent.click(screen.getByRole('button', { name: '删除 EMA' })); expect(view.props.onChange.mock.calls[1][0].ema.enabled).toBe(false);
});
