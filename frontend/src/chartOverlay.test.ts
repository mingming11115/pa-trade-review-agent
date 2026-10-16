import type { ChartOptions, IChartApi, ISeriesApi, ISeriesPrimitive } from 'lightweight-charts';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createChartOverlayContext } from './chartOverlay';

function setup(options: Pick<ChartOptions, 'handleScroll' | 'handleScale'> = { handleScroll: true, handleScale: true }) {
  const chart = { options: () => options, applyOptions: vi.fn() };
  const series = { attachPrimitive: vi.fn(), detachPrimitive: vi.fn() };
  const container = document.createElement('div');
  const bridge = createChartOverlayContext({
    chart: chart as unknown as IChartApi,
    series: series as unknown as ISeriesApi<'Candlestick'>,
    container,
  });
  const primitive = () => series.attachPrimitive.mock.calls[0][0] as ISeriesPrimitive;
  return { ...bridge, chart, series, container, primitive };
}

describe('chart overlay bridge', () => {
  let resize: ResizeObserverCallback;
  let observe: ReturnType<typeof vi.fn>;
  let disconnect: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    vi.useFakeTimers();
    observe = vi.fn();
    disconnect = vi.fn();
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: ResizeObserverCallback) { resize = callback; }
      observe = observe;
      disconnect = disconnect;
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('notifies price and time viewport redraws once per frame without an idle animation loop', () => {
    const bridge = setup();
    const listener = vi.fn();
    const unsubscribe = bridge.context.subscribeViewportChange(listener);
    bridge.primitive().updateAllViews?.();
    bridge.primitive().updateAllViews?.();
    expect(listener).not.toHaveBeenCalled();
    vi.advanceTimersToNextFrame();
    expect(listener).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(1000);
    expect(listener).toHaveBeenCalledOnce();
    bridge.primitive().updateAllViews?.();
    vi.advanceTimersToNextFrame();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    bridge.primitive().updateAllViews?.();
    vi.advanceTimersToNextFrame();
    expect(listener).toHaveBeenCalledTimes(2);
    bridge.dispose();
  });

  it('observes container resize and coalesces it with chart redraws', () => {
    const bridge = setup();
    const listener = vi.fn();
    bridge.context.subscribeViewportChange(listener);
    expect(observe).toHaveBeenCalledWith(bridge.container);
    resize([], {} as ResizeObserver);
    bridge.primitive().updateAllViews?.();
    vi.advanceTimersToNextFrame();
    expect(listener).toHaveBeenCalledOnce();
    bridge.dispose();
  });

  it('restores the complete gesture options after repeated locks despite in-place chart option changes', () => {
    const options = {
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: false, axisPressedMouseMove: { time: false, price: true }, axisDoubleClickReset: { time: true, price: false } },
    };
    const bridge = setup(options);
    bridge.context.setInteractionLocked(true);
    bridge.context.setInteractionLocked(true);
    expect(bridge.chart.applyOptions).toHaveBeenCalledExactlyOnceWith({ handleScroll: false, handleScale: false });
    options.handleScale.axisPressedMouseMove.price = false;
    options.handleScroll.pressedMouseMove = false;
    bridge.context.setInteractionLocked(false);
    expect(bridge.chart.applyOptions).toHaveBeenLastCalledWith({
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: true, pinch: false, axisPressedMouseMove: { time: false, price: true }, axisDoubleClickReset: { time: true, price: false } },
    });
    bridge.context.setInteractionLocked(false);
    expect(bridge.chart.applyOptions).toHaveBeenCalledTimes(2);
    bridge.dispose();
  });

  it('captures current boolean gesture options for each new drag and restores them on disposal', () => {
    const options = { handleScroll: false, handleScale: true };
    const bridge = setup(options);
    bridge.context.setInteractionLocked(true);
    bridge.context.setInteractionLocked(false);
    expect(bridge.chart.applyOptions).toHaveBeenLastCalledWith({ handleScroll: false, handleScale: true });
    options.handleScroll = true;
    options.handleScale = false;
    bridge.context.setInteractionLocked(true);
    bridge.dispose();
    expect(bridge.chart.applyOptions).toHaveBeenLastCalledWith({ handleScroll: true, handleScale: false });
    bridge.context.setInteractionLocked(true);
    bridge.dispose();
    expect(bridge.chart.applyOptions).toHaveBeenCalledTimes(4);
  });

  it('cancels pending notifications and releases observers and primitive before chart removal', () => {
    const bridge = setup();
    const listener = vi.fn();
    bridge.context.subscribeViewportChange(listener);
    const primitive = bridge.primitive();
    primitive.updateAllViews?.();
    bridge.dispose();
    bridge.dispose();
    expect(disconnect).toHaveBeenCalledOnce();
    expect(bridge.series.detachPrimitive).toHaveBeenCalledExactlyOnceWith(primitive);
    resize([], {} as ResizeObserver);
    primitive.updateAllViews?.();
    bridge.context.subscribeViewportChange(listener);
    vi.advanceTimersByTime(1000);
    expect(listener).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
