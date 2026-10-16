import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TradingChart } from './TradingChart';
import type { ChartOverlayContext } from './chartOverlay';
import type { Bar, Stage1Result, Stage2Result } from './types';
// Canvas rendering is outside jsdom; capture the real component's chart boundary.
const chart = vi.hoisted(() => {
  const pane = { setStretchFactor() {}, paneIndex: () => 0, getHTMLElement: () => null, getHeight: () => 300 };
  const series = () => ({ getPane: () => pane, setData: vi.fn(), update: vi.fn(), applyOptions: vi.fn(), attachPrimitive: vi.fn(), detachPrimitive: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn() });
  return { pane, candles: series(), volume: series(), ema: series(), markers: vi.fn(), remove: vi.fn() };
});
vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles', HistogramSeries: 'volume', LineSeries: 'ema',
  ColorType: { Solid: 'solid' }, CrosshairMode: { Normal: 0 },
  createSeriesMarkers: () => ({ setMarkers: chart.markers }),
  createChart: () => ({
    panes: () => [chart.pane], subscribeCrosshairMove() {}, unsubscribeCrosshairMove() {},
    addSeries: (kind: 'candles' | 'volume' | 'ema') => chart[kind],
    priceScale: () => ({ applyOptions() {} }), applyOptions() {}, remove: chart.remove,
    options: () => ({ handleScroll: true, handleScale: true }),
    paneSize: () => ({ width: 400, height: 300 }),
    timeScale: () => ({ fitContent() {}, setVisibleLogicalRange() {}, getVisibleLogicalRange: () => null, subscribeVisibleLogicalRangeChange() {}, unsubscribeVisibleLogicalRangeChange() {}, coordinateToLogical: (x: number) => x / 100, timeToCoordinate: (time: number) => (time - Date.parse('2026-01-01T00:00:00Z') / 1000) / 36 }),
  }),
}));
const bars: Bar[] = [0, 1, 2].map(hour => ({ timestamp: `2026-01-01T0${hour}:00:00Z`, open: 1, high: 2, low: 1, close: 2, volume: 1 }));
describe('replay chart controls', () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value) });
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  });
  afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
  it('previews the selected bar and starts server replay exactly at its opening instant', () => {
    const start = vi.fn();
    render(<TradingChart bars={bars} chartKey="pick-bar" period="1h" timezoneOffsetMinutes={480} onStartTradingReplay={start} />);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    expect(screen.getByRole('button', { name: '随机回放' })).toHaveAttribute('title', '从当前品种已存的本地行情中随机选择回放起点');
    fireEvent.click(screen.getByRole('button', { name: '选择 K 线' }));
    const picker = screen.getByRole('region', { name: 'K 线回放起点选择区' });
    fireEvent.mouseMove(picker, { clientX: 105, clientY: 150 });
    expect(screen.getByRole('status')).toHaveTextContent('2026-01-01 09:00');
    expect(picker.querySelector('.replay-bar-picker-line')).toHaveStyle({ left: '100px' });
    expect(picker.querySelector('.replay-bar-picker-future')).toHaveStyle({ left: '100px' });
    expect(start).not.toHaveBeenCalled();
    fireEvent.click(picker, { clientX: 105, clientY: 150 });
    fireEvent.click(picker, { clientX: 105, clientY: 150 });
    expect(start).toHaveBeenCalledExactlyOnceWith('2026-01-01T01:00:00.000Z');
    expect(screen.queryByRole('region', { name: 'K 线回放起点选择区' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('回放开始时间')).toHaveValue('2026-01-01T09:00');
  });
  it('waits for confirmation after choosing a trading replay start so settings can be reviewed', () => {
    const start = vi.fn();
    render(<TradingChart bars={bars} chartKey="confirm-bar" period="1h" onStartTradingReplay={start} replaySetup={<p>初始资金</p>} />);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    fireEvent.click(screen.getByRole('button', { name: '选择 K 线' }));
    fireEvent.click(screen.getByRole('region', { name: 'K 线回放起点选择区' }), { clientX: 100, clientY: 100 });
    expect(start).not.toHaveBeenCalled();
    expect(screen.getByLabelText('回放开始时间')).toHaveValue('2026-01-01T01:00');
    expect(screen.queryByRole('region', { name: 'K 线回放起点选择区' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '开始回放' }));
    expect(start).toHaveBeenCalledExactlyOnceWith('2026-01-01T01:00:00.000Z');
  });
  it('ignores blank future space, price/time axes, and outside the plot when selecting a bar', () => {
    const start = vi.fn();
    render(<TradingChart bars={bars} chartKey="invalid-bar" period="1h" onStartTradingReplay={start} />);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    fireEvent.click(screen.getByRole('button', { name: '选择 K 线' }));
    const picker = screen.getByRole('region', { name: 'K 线回放起点选择区' });
    fireEvent.mouseMove(picker, { clientX: 100, clientY: 100 });
    expect(picker.querySelector('.replay-bar-picker-line')).not.toBeNull();
    for (const [clientX, clientY] of [[350, 100], [-5, 100], [420, 100], [100, 320]]) {
      fireEvent.mouseMove(picker, { clientX, clientY });
      fireEvent.click(picker, { clientX, clientY });
      expect(picker.querySelector('.replay-bar-picker-line')).toBeNull();
    }
    expect(start).not.toHaveBeenCalled();
  });
  it('cancels selection on Escape, explicit cancellation, closing replay, and changing chart identity or period', () => {
    const start = vi.fn();
    const props = { bars, chartKey: 'cancel-bar', period: '1h' as const, onStartTradingReplay: start };
    const view = render(<TradingChart {...props} />);
    const pick = () => fireEvent.click(screen.getByRole('button', { name: '选择 K 线' }));
    const expectClosed = () => expect(screen.queryByRole('region', { name: 'K 线回放起点选择区' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    pick();
    fireEvent.keyDown(document, { key: 'Escape' });
    expectClosed();
    pick();
    fireEvent.click(screen.getByRole('button', { name: '取消选择' }));
    expectClosed();
    pick();
    view.rerender(<TradingChart {...props} period="5m" />);
    expectClosed();
    pick();
    view.rerender(<TradingChart {...props} period="5m" chartKey="other-symbol" />);
    expectClosed();
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    pick();
    fireEvent.click(screen.getByRole('button', { name: '退出回放' }));
    expectClosed();
    expect(start).not.toHaveBeenCalled();
  });
  it('disables bar selection without data or while creating replay, and cancels an active picker when creation starts', () => {
    const props = { bars, chartKey: 'busy-bar', period: '1h' as const, onStartTradingReplay: vi.fn() };
    const view = render(<TradingChart {...props} bars={[]} />);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    expect(screen.getByRole('button', { name: '选择 K 线' })).toBeDisabled();
    view.rerender(<TradingChart {...props} />);
    fireEvent.click(screen.getByRole('button', { name: '选择 K 线' }));
    expect(screen.getByRole('region', { name: 'K 线回放起点选择区' })).toBeInTheDocument();
    view.rerender(<TradingChart {...props} replayStarting />);
    expect(screen.getByRole('button', { name: '选择 K 线' })).toBeDisabled();
    expect(screen.queryByRole('region', { name: 'K 线回放起点选择区' })).not.toBeInTheDocument();
    expect(props.onStartTradingReplay).not.toHaveBeenCalled();
  });
  it('uses the same selected opening instant for the local replay loader in a non-UTC chart', async () => {
    render(<TradingChart bars={bars} chartKey="local-bar" period="1h" timezoneOffsetMinutes={-300} onReplayLoad={async () => bars} />);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    fireEvent.click(screen.getByRole('button', { name: '选择 K 线' }));
    fireEvent.click(screen.getByRole('region', { name: 'K 线回放起点选择区' }), { clientX: 100, clientY: 100 });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
      expect(chart.candles.setData.mock.lastCall?.[0]).toHaveLength(2);
      expect(screen.getByLabelText('回放开始时间')).toHaveValue('2025-12-31T20:00');
    });
  });
  it('renders a stable overlay context next to the canvas after chart initialization', () => {
    const contexts: ChartOverlayContext[] = [];
    const renderOverlay = (context: ChartOverlayContext) => {
      contexts.push(context);
      return <div role="status">图上持仓</div>;
    };
    const view = render(<TradingChart bars={bars} chartKey="overlay" renderOverlay={renderOverlay} />);
    const overlay = screen.getByRole('status');
    const context = contexts[0];
    expect(context.series).toBe(chart.candles);
    expect(overlay.parentElement).toContainElement(context.container);
    expect(overlay.parentElement).toHaveClass('trading-chart-stage');
    view.rerender(<TradingChart bars={bars.slice(0, 2)} chartKey="overlay" renderOverlay={renderOverlay} />);
    expect(contexts.every(item => item === context)).toBe(true);
    view.unmount();
    expect(chart.candles.detachPrimitive).toHaveBeenCalledOnce();
    expect(chart.candles.detachPrimitive.mock.invocationCallOrder[0]).toBeLessThan(chart.remove.mock.invocationCallOrder[0]);
  });
  it('adds and removes the optional bridge without recreating the chart or clearing independent price lines', () => {
    const props = { bars, chartKey: 'toggle-overlay', stage1: { support_levels: [1], resistance_levels: [2] } as Stage1Result };
    const view = render(<TradingChart {...props} />);
    expect(chart.candles.attachPrimitive).toHaveBeenCalledTimes(1);
    view.rerender(<TradingChart {...props} renderOverlay={() => <div role="status">图上持仓</div>} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(chart.candles.attachPrimitive).toHaveBeenCalledTimes(2);
    const independentPriceLine = chart.candles.createPriceLine();
    view.rerender(<TradingChart {...props} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(chart.candles.detachPrimitive).toHaveBeenCalledOnce();
    expect(chart.candles.removePriceLine.mock.calls.map(([line]) => line)).not.toContain(independentPriceLine);
    expect(chart.remove).not.toHaveBeenCalled();
  });
  it('shows trading creation progress and routes exit to its cancellation handler', () => {
    const cancel = vi.fn();
    render(<TradingChart bars={bars} chartKey="starting" period="1h" onStartTradingReplay={()=>{}} replayStarting replayStartingSeconds={12} onCancelTradingReplay={cancel}/>);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    expect(screen.getByRole('status')).toHaveTextContent('正在创建回放');
    expect(screen.getByRole('status')).toHaveTextContent('12 秒');
    fireEvent.click(screen.getByRole('button', { name: '取消创建' }));
    expect(cancel).toHaveBeenCalledOnce();
  });
  it('starts random replay and updates the picker to the actual start in chart timezone', async () => {
    const first = { ...bars[0], timestamp: '2023-01-01T00:00:00Z' };
    vi.spyOn(Math, 'random').mockReturnValue(0);
    try {
      render(<TradingChart bars={bars} chartKey="random" period="1h" timezoneOffsetMinutes={480} onReplayLoad={async () => [first]} />);
      fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
      fireEvent.click(screen.getByRole('button', { name: '随机回放' }));
      await waitFor(() => {
        expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
        expect(chart.candles.setData.mock.lastCall?.[0]).toHaveLength(1);
        expect(screen.getByLabelText('回放开始时间')).toHaveValue('2023-01-01T08:00');
      });
      expect(screen.queryByRole('button', { name: '上一根' })).not.toBeInTheDocument();
    } finally { vi.restoreAllMocks(); }
  });
  it('converts the picker when choosing Beijing time without moving the replay instant', async () => {
    const load = vi.fn().mockResolvedValue(bars);
    render(<TradingChart bars={bars} chartKey="timezone" period="1h" timezoneOffsetMinutes={-300} onReplayLoad={load} />);
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    fireEvent.change(screen.getByLabelText('回放开始时间'), { target: { value: '2025-12-31T20:00' } });
    fireEvent.change(screen.getByLabelText('回放输入时区'), { target: { value: '480' } });
    expect(screen.getByLabelText('回放开始时间')).toHaveValue('2026-01-01T09:00');
    fireEvent.click(screen.getByRole('button', { name: '从此处回放' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
      expect(chart.candles.setData.mock.lastCall?.[0]).toHaveLength(2);
    });
  });
  it('disables future selections in chart timezone and advances the limit with time', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-02T02:00:30Z'));
    const view = render(<TradingChart bars={bars} chartKey="limit" period="1h" timezoneOffsetMinutes={-300} onReplayLoad={async () => bars} />);
    try {
      fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
      const input = screen.getByLabelText('回放开始时间') as HTMLInputElement;
      expect(input.max).toBe('2026-10-01T21:00');
      fireEvent.change(input, { target: { value: '2026-10-01T21:01' } });
      expect(input.validity.rangeOverflow).toBe(true);
      expect(screen.getByRole('button', { name: '从此处回放' })).toBeDisabled();
      act(() => { vi.advanceTimersByTime(60000); });
      expect(input.max).toBe('2026-10-01T21:01');
      expect(screen.getByRole('button', { name: '从此处回放' })).toBeEnabled();
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
  it('cuts candles and future markers, removes analysis lines and restores the original snapshot on exit', async () => {
    render(<TradingChart bars={bars} analysisBars={bars} chartKey="a" period="1h" onReplayLoad={async () => bars}
      stage1={{ support_levels: [1], resistance_levels: [2] } as Stage1Result}
      stage2={{ decision: { entry_price: 1.5 } } as Stage2Result}
      markers={[{ timestamp: bars[2].timestamp, position: 'aboveBar', shape: 'arrowDown', color: 'red', text: 'future' }]} />);
    expect(chart.markers.mock.lastCall?.[0]).toHaveLength(1);
    expect(screen.queryByRole('button', { name: '上一根' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^回放$/ }));
    fireEvent.change(screen.getByLabelText('回放开始时间'), { target: { value: '2026-01-01T01:00' } });
    chart.candles.createPriceLine.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '从此处回放' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument();
      expect(chart.candles.setData.mock.lastCall?.[0]).toHaveLength(2);
      expect(chart.volume.setData.mock.lastCall?.[0]).toHaveLength(2);
      expect(chart.markers.mock.lastCall?.[0]).toHaveLength(0);
    });
    expect(chart.candles.createPriceLine).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '下一根' }));
    expect(chart.markers.mock.lastCall?.[0]).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '退出回放' }));
    expect(chart.candles.setData.mock.lastCall?.[0]).toHaveLength(3);
    expect(chart.candles.createPriceLine).toHaveBeenCalled();
  });
});
