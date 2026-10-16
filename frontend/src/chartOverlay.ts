import type { ChartOptions, IChartApi, ISeriesApi, ISeriesPrimitive } from 'lightweight-charts';

export interface ChartOverlayContext {
  chart: IChartApi;
  series: ISeriesApi<'Candlestick'>;
  container: HTMLDivElement;
  subscribeViewportChange(listener: () => void): () => void;
  setInteractionLocked(locked: boolean): void;
}

type InteractionOptions = Pick<ChartOptions, 'handleScroll' | 'handleScale'>;

export function createChartOverlayContext({ chart, series, container }: Pick<ChartOverlayContext, 'chart' | 'series' | 'container'>) {
  const listeners = new Set<() => void>();
  let frame: number | null = null;
  let disposed = false;
  let originalInteraction: InteractionOptions | null = null;

  const scheduleViewportChange = () => {
    if (disposed || frame !== null || listeners.size === 0) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (!disposed) listeners.forEach(listener => listener());
    });
  };
  // Primitive updates include price-axis dragging and autoscale changes, which
  // are not reported by time-scale range subscriptions.
  const primitive: ISeriesPrimitive = { updateAllViews: scheduleViewportChange };
  const resizeObserver = new ResizeObserver(scheduleViewportChange);
  series.attachPrimitive(primitive);
  resizeObserver.observe(container);

  const context: ChartOverlayContext = {
    chart,
    series,
    container,
    subscribeViewportChange(listener) {
      if (disposed) return () => {};
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    setInteractionLocked(locked) {
      if (disposed || locked === (originalInteraction !== null)) return;
      if (locked) {
        const options = chart.options();
        // Chart options are mutable; retain nested price/time-axis preferences.
        originalInteraction = structuredClone({ handleScroll: options.handleScroll, handleScale: options.handleScale });
        chart.applyOptions({ handleScroll: false, handleScale: false });
      } else if (originalInteraction) {
        chart.applyOptions(originalInteraction);
        originalInteraction = null;
      }
    },
  };

  return {
    context,
    dispose() {
      if (disposed) return;
      disposed = true;
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      listeners.clear();
      resizeObserver.disconnect();
      series.detachPrimitive(primitive);
      if (originalInteraction) chart.applyOptions(originalInteraction);
      originalInteraction = null;
    },
  };
}
