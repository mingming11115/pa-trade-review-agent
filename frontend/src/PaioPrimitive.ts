import type {
  IChartApi,
  ISeriesApi,
  ISeriesPrimitive,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

import type { PaioGapBox, PaioLabel, PaioModel } from "./paio";

export interface PaioVisibility {
  gaps: boolean;
  barIndex: boolean;
  hl: boolean;
}

const EMPTY_MODEL: PaioModel = { gaps: [], barIndexLabels: [], hlLabels: [] };

class PaioRenderer implements IPrimitivePaneRenderer {
  private gaps: PaioGapBox[] = [];
  private labels: PaioLabel[] = [];
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;

  setData(
    gaps: PaioGapBox[],
    labels: PaioLabel[],
    chart: IChartApi | null,
    series: ISeriesApi<"Candlestick"> | null,
  ) {
    this.gaps = gaps;
    this.labels = labels;
    this.chart = chart;
    this.series = series;
  }

  draw(target: CanvasRenderingTarget2D) {
    const chart = this.chart;
    const series = this.series;
    if (!chart || !series) return;

    const timeScale = chart.timeScale();
    const barSpacing = timeScale.options().barSpacing;

    target.useMediaCoordinateSpace(({ context }) => {
      for (const gap of this.gaps) {
        const x1 = timeScale.timeToCoordinate(gap.timeLeft as Time);
        const x2 = timeScale.timeToCoordinate(gap.timeRight as Time);
        const y1 = series.priceToCoordinate(gap.priceHigh);
        const y2 = series.priceToCoordinate(gap.priceLow);
        if (x1 == null || x2 == null || y1 == null || y2 == null) continue;
        const left = Math.min(x1, x2) - barSpacing * 0.15;
        const right = Math.max(x1, x2) + barSpacing * 0.15;
        const top = Math.min(y1, y2);
        const bottom = Math.max(y1, y2);
        context.fillStyle = gap.color;
        context.fillRect(left, top, Math.max(1, right - left), Math.max(1, bottom - top));
      }

      context.textAlign = "center";
      context.textBaseline = "middle";
      context.font = '600 10px -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif';

      for (const label of this.labels) {
        if (label.price == null) continue;
        const x = timeScale.timeToCoordinate(label.time as Time);
        const y = series.priceToCoordinate(label.price);
        if (x == null || y == null) continue;
        context.fillStyle = label.color;
        context.fillText(label.text, x, y);
      }
    });
  }
}

class PaioPaneView implements IPrimitivePaneView {
  private readonly _renderer = new PaioRenderer();
  private readonly z: "bottom" | "normal" | "top";

  constructor(z: "bottom" | "normal" | "top") {
    this.z = z;
  }

  zOrder() {
    return this.z;
  }

  renderer() {
    return this._renderer;
  }

  update(
    gaps: PaioGapBox[],
    labels: PaioLabel[],
    chart: IChartApi | null,
    series: ISeriesApi<"Candlestick"> | null,
  ) {
    this._renderer.setData(gaps, labels, chart, series);
  }
}

/** Canvas overlay for PAIO gaps + text labels. */
export class PaioPrimitive implements ISeriesPrimitive<Time> {
  private readonly gapView = new PaioPaneView("bottom");
  private readonly labelView = new PaioPaneView("top");
  private model: PaioModel = EMPTY_MODEL;
  private visibility: PaioVisibility = { gaps: true, barIndex: true, hl: true };
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private barLookup = new Map<number, { high: number; low: number }>();

  attached(param: SeriesAttachedParameter<Time, "Candlestick">) {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = param.requestUpdate;
    this.syncViews();
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  updateAllViews() {
    this.syncViews();
  }

  paneViews() {
    return [this.gapView, this.labelView];
  }

  setModel(model: PaioModel, barsByTime?: Map<number, { high: number; low: number }>) {
    this.model = model;
    if (barsByTime) this.barLookup = barsByTime;
    this.syncViews();
    this.requestUpdate?.();
  }

  setVisibility(visibility: PaioVisibility) {
    this.visibility = visibility;
    this.syncViews();
    this.requestUpdate?.();
  }

  private syncViews() {
    const gaps = this.visibility.gaps ? this.model.gaps : [];
    const labels: PaioLabel[] = [];

    if (this.visibility.barIndex) {
      for (const label of this.model.barIndexLabels) {
        const bar = this.barLookup.get(label.time as number);
        if (!bar) continue;
        const span = Math.max(bar.high - bar.low, Math.abs(bar.low) * 0.0008);
        labels.push({ ...label, anchor: "price", price: bar.low - span * 0.35 });
      }
    }
    if (this.visibility.hl) labels.push(...this.model.hlLabels);

    this.gapView.update(gaps, [], this.chart, this.series);
    this.labelView.update([], labels, this.chart, this.series);
  }
}

export function buildBarLookup(bars: { timestamp: string; high: number; low: number }[]) {
  const map = new Map<number, { high: number; low: number }>();
  for (const bar of bars) {
    const time = Math.floor(new Date(bar.timestamp).getTime() / 1000);
    map.set(time, { high: bar.high, low: bar.low });
  }
  return map;
}
