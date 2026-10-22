import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { TradingChart } from './TradingChart';
import type { Bar } from './types';

// Only the canvas boundary is replaced; settings, calculations and replay are real.
const chart = vi.hoisted(() => {
  const records: Array<{ kind: string; pane: number; options: Record<string, unknown>; data: Array<{ time: number; value?: number }>; removed: boolean; api: Record<string, unknown> }> = [];
  const scale = { fitContent: vi.fn(), setVisibleLogicalRange: vi.fn(), getVisibleLogicalRange: () => null, timeToCoordinate: () => null, subscribeVisibleLogicalRangeChange() {}, unsubscribeVisibleLogicalRangeChange() {} };
  type Pane = { setStretchFactor: ReturnType<typeof vi.fn>; paneIndex: () => number; getHTMLElement: () => null; getHeight: () => number };
  const panes: Pane[] = [];
  const makePane = () => {
    const pane: Pane = { setStretchFactor: vi.fn(), paneIndex: () => panes.indexOf(pane), getHTMLElement: () => null, getHeight: () => 140 };
    return pane;
  };
  const crosshair = new Set<(event: { time?: number; point?: { x: number; y: number } }) => void>();
  return { records, scale, panes, makePane, crosshair, remove: vi.fn() };
});
vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candle', HistogramSeries: 'histogram', LineSeries: 'line',
  ColorType: { Solid: 'solid' }, CrosshairMode: { Normal: 0 }, LineStyle: { Dashed: 2 },
  createSeriesMarkers: () => ({ setMarkers() {} }),
  createChart: () => {
    chart.panes.push(chart.makePane());
    return {
      addSeries(kind: string, options: Record<string, unknown>, pane = 0) {
        if (!chart.panes[pane]) chart.panes[pane] = chart.makePane();
        const record = { kind, pane, options, data: [] as Array<{ time: number; value?: number }>, removed: false, api: {} };
        record.api = {
          setData(data: typeof record.data) { record.data = data; },
          update(point: typeof record.data[number]) { record.data = [...record.data.filter(item => item.time !== point.time), point]; },
          applyOptions(next: Record<string, unknown>) { record.options = { ...record.options, ...next }; },
          priceToCoordinate: () => null,
          getPane: () => chart.panes[record.pane], attachPrimitive() {}, detachPrimitive() {}, createPriceLine() { return {}; }, removePriceLine() {},
        };
        chart.records.push(record);
        return record.api;
      },
      removeSeries(api: Record<string, unknown>) {
        const record = chart.records.find(item => item.api === api)!;
        record.removed = true;
        if (record.pane > 0 && !chart.records.some(item => !item.removed && item.pane === record.pane)) {
          chart.panes.splice(record.pane, 1);
          chart.records.filter(item => !item.removed && item.pane > record.pane).forEach(item => item.pane--);
        }
      },
      panes: () => chart.panes, paneSize: () => ({ width: 640, height: 140 }),
      subscribeCrosshairMove: (listener: typeof chart.crosshair extends Set<infer T> ? T : never) => chart.crosshair.add(listener),
      unsubscribeCrosshairMove: (listener: typeof chart.crosshair extends Set<infer T> ? T : never) => chart.crosshair.delete(listener),
      priceScale: () => ({ applyOptions() {} }), applyOptions() {}, remove: chart.remove,
      timeScale: () => chart.scale,
    };
  },
}));

const bars: Bar[] = [1, 3, 2, 6, 4, 8].map((close, hour) => ({ timestamp: `2026-01-01T0${hour}:00:00Z`, open: close, high: close + 1, low: close - 1, close, volume: hour + 1 }));
const settings = () => ({
  ma: { enabled: true, period: 2, color: '#4d83c8' },
  ema: { enabled: true, period: 2, color: '#f59e0b' },
  boll: { enabled: true, period: 2, multiplier: 2, color: '#8b5cf6' },
  macd: { enabled: true, fast: 2, slow: 3, signal: 2 },
  rsi: { enabled: true, period: 2, color: '#8b5cf6' },
  volume: { enabled: true },
});
const active = (title: string) => chart.records.find(item => !item.removed && item.options.title === title)!;
beforeEach(() => {
  // Vitest 3 running on Node 26 inherits Node's disabled storage global.
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), clear: () => values.clear() });
  chart.records.length = 0;
  chart.panes.length = 0;
  chart.crosshair.clear();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

const hover = (time?: number, point?: { x: number; y: number }) => act(() => chart.crosshair.forEach(listener => listener({ time, point })));
const group = (name: string) => screen.getByRole('group', { name: `${name} 指标图例` });

it.each([false, true])('manages chart display switches inside the indicator library (locked=%s)', locked => {
  const view = render(<TradingChart bars={bars} chartKey="indicator-controls" period="5m" locked={locked} />);
  expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '指标' }));
  const panel = screen.getByRole('dialog', { name: '指标库' });
  for (const name of ['EMA20', 'Gap', 'K#', 'H/L']) {
    const toggle = within(panel).getByRole('checkbox', { name });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
  }
  expect(active('EMA20').options.visible).toBe(false);
  expect(JSON.parse(window.localStorage.getItem('pa.chart.indicators.v2')!).ema.visible).toBe(false);
  fireEvent.click(within(panel).getByRole('button', { name: '关闭指标库' }));
  expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  expect(within(group('EMA')).getByRole('button', { name: '显示 EMA' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '指标' }));
  for (const name of ['EMA20', 'Gap', 'K#', 'H/L']) expect(screen.getByRole('checkbox', { name })).not.toBeChecked();
  view.unmount();
  render(<TradingChart bars={bars} chartKey="indicator-controls-restored" period="5m" locked={locked} />);
  fireEvent.click(screen.getByRole('button', { name: '指标' }));
  expect(screen.getByRole('checkbox', { name: 'EMA20' })).not.toBeChecked();
});

it('adds an indicator from the search library and applies its settings without resetting the viewport', () => {
  render(<TradingChart bars={bars} chartKey="indicators" />);
  chart.scale.fitContent.mockClear();
  chart.scale.setVisibleLogicalRange.mockClear();
  fireEvent.click(screen.getByRole('button', { name: '指标' }));
  const library = screen.getByRole('dialog', { name: '指标库' });
  fireEvent.change(within(library).getByLabelText('搜索指标'), { target: { value: 'MA' } });
  fireEvent.click(within(library).getByRole('button', { name: '添加 MA' }));
  expect(within(library).getByRole('button', { name: 'MA 已添加' })).toBeDisabled();
  fireEvent.click(within(library).getByRole('button', { name: '关闭指标库' }));
  fireEvent.click(within(group('MA')).getByRole('button', { name: '设置 MA' }));
  const panel = screen.getByRole('dialog', { name: 'MA 设置' });
  fireEvent.change(within(panel).getByLabelText('MA 周期'), { target: { value: '2' } });
  fireEvent.click(within(panel).getByRole('button', { name: '应用' }));
  expect(active('MA2').data.map(item => item.value)).toEqual([2, 2.5, 4, 5, 6]);
  expect(JSON.parse(window.localStorage.getItem('pa.chart.indicators.v2')!).ma).toMatchObject({ enabled: true, period: 2 });
  expect(chart.scale.fitContent).not.toHaveBeenCalled();
  expect(chart.scale.setVisibleLogicalRange).not.toHaveBeenCalled();
  fireEvent.doubleClick(within(group('MA')).getByRole('button', { name: 'MA 参数' }));
  expect(screen.getByRole('dialog', { name: 'MA 设置' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
});

it('renders overlays on the price pane and MACD/RSI in separate panes using saved settings', () => {
  window.localStorage.setItem('pa.chart.indicators.v1', JSON.stringify(settings()));
  render(<TradingChart bars={bars} chartKey="all-indicators" />);
  expect(active('MA2').pane).toBe(0);
  expect(active('EMA2').pane).toBe(0);
  expect(active('BOLL 上轨').data[0].value).toBe(4);
  expect(active('BOLL 下轨').data[0].value).toBe(0);
  expect(active('DIF').pane).toBe(1);
  expect(active('DEA').pane).toBe(1);
  expect(active('MACD').pane).toBe(1);
  expect(active('RSI2').pane).toBe(2);
  expect(active('RSI2').data[0].value).toBeCloseTo(66.6666667);
  expect(chart.panes).toHaveLength(3);
});

it('recalculates same-length corrections, prepended history and empty data without retaining stale indicators', () => {
  window.localStorage.setItem('pa.chart.indicators.v1', JSON.stringify(settings()));
  const view = render(<TradingChart bars={bars.slice(1)} chartKey="updates" />);
  view.rerender(<TradingChart bars={bars} chartKey="updates" />);
  expect(active('MA2').data.map(item => item.value)).toEqual([2, 2.5, 4, 5, 6]);
  const corrected = bars.map((bar, index) => index === 1 ? { ...bar, close: 5 } : bar);
  view.rerender(<TradingChart bars={corrected} chartKey="updates" />);
  expect(active('MA2').data.map(item => item.value)).toEqual([3, 3.5, 4, 5, 6]);
  view.rerender(<TradingChart bars={[]} chartKey="updates" />);
  expect(chart.records.filter(item => !item.removed).every(item => item.data.length === 0)).toBe(true);
});

it('hides an indicator while retaining its legend and pane, then deletes it and reindexes RSI', () => {
  window.localStorage.setItem('pa.chart.indicators.v1', JSON.stringify(settings()));
  render(<TradingChart bars={bars} chartKey="disable" />);
  const candle = chart.records.find(item => item.kind === 'candle');
  const dif = active('DIF');
  fireEvent.click(within(group('MACD')).getByRole('button', { name: '隐藏 MACD' }));
  expect(chart.panes).toHaveLength(3);
  expect(dif.removed).toBe(false);
  expect(dif.options.visible).toBe(false);
  expect(group('MACD')).toHaveTextContent('已隐藏');
  fireEvent.click(within(group('MACD')).getByRole('button', { name: '显示 MACD' }));
  expect(dif.options.visible).toBe(true);
  fireEvent.click(within(group('MACD')).getByRole('button', { name: '删除 MACD' }));
  fireEvent.click(within(group('VOL')).getByRole('button', { name: '删除 VOL' }));
  expect(chart.panes).toHaveLength(2);
  expect(screen.queryByRole('group', { name: 'MACD 指标图例' })).not.toBeInTheDocument();
  expect(active('RSI2').pane).toBe(1);
  expect(group('RSI').parentElement).toHaveAttribute('data-pane', '1');
  expect(active('RSI2').data).toHaveLength(4);
  expect(chart.records.filter(item => !item.removed && item.kind === 'histogram')).toHaveLength(0);
  expect(candle?.removed).toBe(false);
  expect(chart.remove).not.toHaveBeenCalled();
});

it('shows exact hovered values, warmup and empty-space placeholders, and restores latest values on leave', () => {
  window.localStorage.setItem('pa.chart.indicators.v1', JSON.stringify(settings()));
  const view = render(<TradingChart bars={bars} chartKey="values" period="1h" />);
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('6');
  expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('6');
  hover(Date.parse(bars[2].timestamp) / 1000, { x: 20, y: 20 });
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('2.5');
  expect(screen.getByLabelText('RSI RSI')).toHaveTextContent('66.67');
  expect(screen.getByLabelText('MACD DEA')).toHaveTextContent('—');
  expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('3');
  hover(Date.parse(bars[0].timestamp) / 1000, { x: 10, y: 20 });
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('—');
  hover(undefined, { x: 630, y: 20 });
  expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('—');
  hover();
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('6');
  expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('6');
  hover(Date.parse(bars[2].timestamp) / 1000, { x: 20, y: 20 });
  view.rerender(<TradingChart bars={bars.slice(0, 2)} chartKey="other-values" period="1h" />);
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('2');
  expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('2');
  hover(Date.parse(bars[0].timestamp) / 1000, { x: 10, y: 20 });
  view.rerender(<TradingChart bars={bars} chartKey="other-values" period="5m" />);
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('6');
});

it('filters drawing by timeframe without deleting the legend or indicator pane', () => {
  const saved = { ...settings(), rsi: { ...settings().rsi } };
  window.localStorage.setItem('pa.chart.indicators.v1', JSON.stringify(saved));
  render(<TradingChart bars={bars} chartKey="timeframes" period="1h" />);
  fireEvent.click(within(group('RSI')).getByRole('button', { name: '设置 RSI' }));
  const panel = screen.getByRole('dialog', { name: 'RSI 设置' });
  fireEvent.click(within(panel).getByRole('tab', { name: '可见性' }));
  fireEvent.click(within(panel).getByRole('checkbox', { name: '1h' }));
  fireEvent.click(within(panel).getByRole('button', { name: '应用' }));
  expect(active('RSI2').options.visible).toBe(false);
  expect(chart.panes).toHaveLength(3);
  expect(group('RSI')).toHaveTextContent('当前周期隐藏');
  expect(JSON.parse(window.localStorage.getItem('pa.chart.indicators.v2')!).rsi.timeframes).not.toContain('1h');
});

it('uses only the current replay prefix, advances indicators and restores them on exit', async () => {
  window.localStorage.setItem('pa.chart.indicators.v1', JSON.stringify(settings()));
  render(<TradingChart bars={bars} chartKey="indicator-replay" period="1h" onReplayLoad={async () => bars} />);
  fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
  fireEvent.click(screen.getByRole('button', { name: '选择起点' }));
  fireEvent.click(screen.getByRole('menuitem', { name: '选择日期…' }));
  fireEvent.change(screen.getByLabelText('回放开始时间'), { target: { value: '2026-01-01T02:00' } });
  fireEvent.click(screen.getByRole('button', { name: '从此处回放' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument());
  expect(active('MA2').data.map(item => item.value)).toEqual([2, 2.5]);
  const tip = Date.parse(bars[2].timestamp) / 1000;
  expect(chart.records.filter(item => !item.removed).every(item => item.data.every(point => point.time <= tip))).toBe(true);
  expect(active('DEA').data).toHaveLength(0);
  await waitFor(() => {
    expect(screen.getByLabelText('MA MA')).toHaveTextContent('2.5');
    expect(screen.getByLabelText('MACD DEA')).toHaveTextContent('—');
    expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('3');
  });
  fireEvent.click(screen.getByRole('button', { name: '下一根' }));
  await waitFor(() => expect(active('MA2').data.map(item => item.value)).toEqual([2, 2.5, 4]));
  expect(active('DEA').data).toHaveLength(1);
  await waitFor(() => {
    expect(screen.getByLabelText('MA MA')).toHaveTextContent('4');
    expect(screen.getByLabelText('VOL VOL')).toHaveTextContent('4');
  });
  fireEvent.click(screen.getByRole('button', { name: '退出回放' }));
  expect(active('MA2').data).toHaveLength(5);
  expect(screen.getByLabelText('MA MA')).toHaveTextContent('6');
});


it('keeps indicator, replay and reset controls functional in a shared toolbar host', () => {
  const host = document.createElement('div');
  document.body.append(host);
  const view = render(<TradingChart bars={bars} chartKey="shared-toolbar" toolbarContainer={host} onStartTradingReplay={vi.fn()} />);
  expect(within(host).getAllByRole('button').map(button => button.textContent)).toEqual(['指标', '回放', '重置']);
  expect(view.container.querySelector('.chart-toolbar')).toBeNull();
  fireEvent.click(within(host).getByRole('button', { name: '指标' }));
  expect(screen.getByRole('dialog', { name: '指标库' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '关闭指标库' }));
  fireEvent.click(within(host).getByRole('button', { name: '回放' }));
  expect(screen.getByRole('group', { name: 'K 线回放' })).toBeVisible();
  chart.scale.fitContent.mockClear();
  fireEvent.click(within(host).getByRole('button', { name: '重置' }));
  expect(chart.scale.fitContent).toHaveBeenCalledOnce();
  view.rerender(<TradingChart bars={bars} chartKey="shared-toolbar" toolbarContainer={host} showToolbar={false} />);
  expect(within(host).queryAllByRole('button')).toHaveLength(0);
  view.unmount();
  host.remove();
});


it('keeps replay setup outside the chart pane and submits the selected start from the shared area', () => {
  const host = document.createElement('div');
  document.body.append(host);
  const start = vi.fn();
  const view = render(<TradingChart bars={bars} period="1h" chartKey="shared-replay-panel"
    replayPanelContainer={host} onStartTradingReplay={start}
    replaySetup={<label>初始资金 USD<input aria-label="初始资金 USD" defaultValue="100000" /></label>} />);
  fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
  const panel = screen.getByRole('group', { name: 'K 线回放' });
  expect(host).toContainElement(panel);
  expect(view.container).not.toContainElement(panel);
  fireEvent.click(within(host).getByRole('button', { name: '开始回放' }));
  expect(start).toHaveBeenCalledWith('2026-01-01T05:00:00.000Z');
  view.rerender(<TradingChart bars={bars} period="1h" chartKey="shared-replay-panel"
    replayPanelContainer={host} showToolbar={false} onStartTradingReplay={start} />);
  expect(host).toBeEmptyDOMElement();
  view.unmount();
  host.remove();
});
