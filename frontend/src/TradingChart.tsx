import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import {
  FUTURE_CHUNK_BARS,
  HISTORY_CHUNK_BARS,
  HISTORY_EDGE_BARS,
  isBarPrepend,
  lookbackDurationMs,
  formatChartTime,
  formatBarCountdown,
  remainingToBucketCloseMs,
} from "./feed";
import { computePaio } from "./paio";
import { buildBarLookup, PaioPrimitive, type PaioVisibility } from "./PaioPrimitive";
import type { Bar, Period, Stage1Result, Stage2Result, TradeMarker } from "./types";

function useBarCloseCountdown(period: Period | "" | undefined, enabled: boolean): string | null {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !period) {
      setLabel(null);
      return;
    }
    const tick = () => {
      setLabel(formatBarCountdown(remainingToBucketCloseMs(period)));
    };
    tick();
    const timer = window.setInterval(tick, 250);
    return () => window.clearInterval(timer);
  }, [enabled, period]);

  return label;
}

function lastBarAnchor(
  chart: IChartApi | null,
  series: ISeriesApi<"Candlestick"> | null,
  bars: Bar[],
): { x: number; y: number } | null {
  const last = bars[bars.length - 1];
  if (!chart || !series || !last) return null;
  const time = Math.floor(new Date(last.timestamp).getTime() / 1000) as UTCTimestamp;
  const x = chart.timeScale().timeToCoordinate(time);
  const y = series.priceToCoordinate(last.close);
  if (x == null || y == null) return null;
  return { x: x + 14, y: y - 10 };
}

function computeEma(bars: Bar[], period: number): Array<{ time: UTCTimestamp; value: number }> {
  if (bars.length < period) return [];
  const multiplier = 2 / (period + 1);
  let sum = 0;
  for (let i = 0; i < period; i += 1) sum += bars[i].close;
  let ema = sum / period;
  const points: Array<{ time: UTCTimestamp; value: number }> = [{
    time: Math.floor(new Date(bars[period - 1].timestamp).getTime() / 1000) as UTCTimestamp,
    value: ema,
  }];
  for (let i = period; i < bars.length; i += 1) {
    ema = bars[i].close * multiplier + ema * (1 - multiplier);
    points.push({
      time: Math.floor(new Date(bars[i].timestamp).getTime() / 1000) as UTCTimestamp,
      value: ema,
    });
  }
  return points;
}

function resetRange(chart: IChartApi, barCount: number, locked: boolean) {
  if (locked) {
    chart.timeScale().fitContent();
    return;
  }
  if (barCount >= 40) {
    chart.timeScale().fitContent();
    return;
  }
  const padding = (40 - barCount) / 2;
  chart.timeScale().setVisibleLogicalRange({
    from: -padding,
    to: barCount - 1 + padding,
  });
}

function toCandle(bar: Bar) {
  return {
    time: Math.floor(new Date(bar.timestamp).getTime() / 1000) as UTCTimestamp,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  };
}

function toVolume(bar: Bar) {
  return {
    time: Math.floor(new Date(bar.timestamp).getTime() / 1000) as UTCTimestamp,
    value: bar.volume ?? 0,
    color: bar.close >= bar.open ? "rgba(8,153,129,.28)" : "rgba(242,54,69,.25)",
  };
}

function toMarkerShape(marker: TradeMarker): SeriesMarker<Time> {
  return {
    time: Math.floor(new Date(marker.timestamp).getTime() / 1000) as UTCTimestamp,
    position: marker.position,
    shape: marker.shape,
    color: marker.color,
    text: marker.text,
  };
}

function epochSeconds(time: Time): number {
  if (typeof time === "number") return time;
  if (typeof time === "string") return Date.parse(time) / 1000;
  return Date.UTC(time.year, time.month - 1, time.day) / 1000;
}

function timezoneFormat(time: Time, offsetMinutes: number, style: "axis" | "full"): string {
  return formatChartTime(epochSeconds(time), offsetMinutes, style);
}

function samePrefix(prev: Bar[], next: Bar[]): boolean {
  const n = Math.min(prev.length, next.length);
  for (let i = 0; i < n - 1; i += 1) {
    if (prev[i].timestamp !== next[i].timestamp) return false;
  }
  return true;
}

function barsAfterTip(bars: Bar[], tipTs: string): Bar[] {
  return bars.filter((bar) => bar.timestamp > tipTs);
}

function applySeriesData(
  series: ISeriesApi<"Candlestick">,
  volumeSeries: ISeriesApi<"Histogram">,
  ema20Series: ISeriesApi<"Line">,
  paio: PaioPrimitive,
  nextBars: Bar[],
  showEma: boolean,
  analysisBars?: Bar[],
) {
  series.setData(nextBars.map(toCandle));
  volumeSeries.setData(nextBars.filter((bar) => bar.volume != null).map(toVolume));
  ema20Series.setData(showEma ? computeEma(nextBars, 20) : []);
  paio.setModel(computePaio(nextBars, { analysisBars }), buildBarLookup(nextBars));
}

const DEFAULT_OVERLAYS = {
  ema20: true,
  gaps: true,
  barIndex: true,
  hl: true,
};

type OverlayKey = keyof typeof DEFAULT_OVERLAYS;

export function TradingChart({
  bars,
  chartKey,
  period,
  markers = [],
  locked = true,
  stage1,
  stage2,
  analysisBars,
  timezoneOffsetMinutes = 0,
  onRangeSelected,
  onNeedHistory,
  onNeedFuture,
}: {
  bars: Bar[];
  /** Identity for full setData; live merges keep the same key. */
  chartKey: string;
  period?: Period | "";
  markers?: TradeMarker[];
  locked?: boolean;
  stage1?: Stage1Result;
  stage2?: Stage2Result;
  /** Chronological analysis snapshot — chart starts here; play steps forward after its tip. */
  analysisBars?: Bar[];
  /** Fixed offset carried by the source K-line timestamps. */
  timezoneOffsetMinutes?: number;
  onRangeSelected?: (start: string, end: string) => void;
  onNeedHistory?: (start: string, end: string) => void | Promise<void>;
  /** Load bars after the analysis tip so「下一根」can continue. */
  onNeedFuture?: (start: string, end: string) => void | Promise<void>;
  onPeriodChange?: (period: Period) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const emaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const paioRef = useRef<PaioPrimitive | null>(null);
  const markersApiRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const prevKeyRef = useRef<string>("");
  const prevVisibleRef = useRef<Bar[]>([]);
  const allBarsRef = useRef<Bar[]>(bars);
  const historyInflightRef = useRef(false);
  const historyRequestedBeforeRef = useRef<string | null>(null);
  const futureInflightRef = useRef(false);
  const pendingForwardRef = useRef<number | null>(null);
  const forwardExtraRef = useRef(0);
  const afterBarsRef = useRef<Bar[]>([]);
  const [overlays, setOverlays] = useState(DEFAULT_OVERLAYS);
  const [forwardExtra, setForwardExtra] = useState(0);
  const [countdownAnchor, setCountdownAnchor] = useState<{ x: number; y: number } | null>(null);

  const analysisBarsRef = useRef(analysisBars);
  analysisBarsRef.current = analysisBars;
  allBarsRef.current = bars;
  forwardExtraRef.current = forwardExtra;

  const analysisTip = analysisBars?.length ? analysisBars[analysisBars.length - 1].timestamp : null;
  const analysisKey = analysisBars?.length
    ? `${analysisBars[0].timestamp}|${analysisTip}|${analysisBars.length}`
    : "";
  const afterBars = useMemo(
    () => (analysisTip ? barsAfterTip(bars, analysisTip) : []),
    [bars, analysisTip],
  );
  afterBarsRef.current = afterBars;

  const visibleBars = useMemo(() => {
    if (!analysisBars?.length) return bars;
    return [...analysisBars, ...afterBars.slice(0, forwardExtra)];
  }, [analysisBars, afterBars, bars, forwardExtra]);

  const markerSeriesData = useMemo(() => {
    const tip = visibleBars[visibleBars.length - 1]?.timestamp;
    const filtered = tip
      ? markers.filter((marker) => marker.timestamp <= tip)
      : markers;
    return filtered.map(toMarkerShape);
  }, [markers, visibleBars]);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 460,
      layout: {
        background: { type: ColorType.Solid, color: "#fbfcfd" },
        textColor: "#777b84",
        fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif',
        attributionLogo: false,
      },
      localization: {
        locale: "zh-CN",
        timeFormatter: (time: Time) => timezoneFormat(time, timezoneOffsetMinutes, "full"),
      },
      grid: {
        vertLines: { color: "#eef1f4" },
        horzLines: { color: "#eef1f4" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: "#e6e9ed",
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "#e6e9ed",
        timeVisible: true,
        secondsVisible: false,
        barSpacing: locked ? 9 : 11,
        minBarSpacing: 3,
        maxBarSpacing: 60,
        rightOffset: locked ? 2 : 3,
        lockVisibleTimeRangeOnResize: true,
        tickMarkFormatter: (time: Time) => timezoneFormat(time, timezoneOffsetMinutes, "axis"),
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: true,
        axisDoubleClickReset: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#089981",
      downColor: "#f23645",
      borderUpColor: "#089981",
      borderDownColor: "#f23645",
      wickUpColor: "#089981",
      wickDownColor: "#f23645",
      priceLineVisible: true,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const ema20Series = chart.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      title: "EMA20",
    });
    const paio = new PaioPrimitive();
    series.attachPrimitive(paio);

    chartRef.current = chart;
    candleRef.current = series;
    volumeRef.current = volumeSeries;
    emaRef.current = ema20Series;
    paioRef.current = paio;
    markersApiRef.current = createSeriesMarkers(series, []);

    return () => {
      markersApiRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      emaRef.current = null;
      paioRef.current = null;
      chartRef.current = null;
      prevKeyRef.current = "";
      prevVisibleRef.current = [];
      priceLinesRef.current = [];
      chart.remove();
    };
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions({
      localization: {
        timeFormatter: (time: Time) => timezoneFormat(time, timezoneOffsetMinutes, "full"),
      },
      timeScale: {
        tickMarkFormatter: (time: Time) => timezoneFormat(time, timezoneOffsetMinutes, "axis"),
      },
    });
  }, [timezoneOffsetMinutes]);

  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: {
        barSpacing: locked ? 9 : 11,
        rightOffset: locked ? 2 : 3,
      },
    });
  }, [locked]);

  // New analysis / chart identity → show exactly the analysis snapshot.
  useEffect(() => {
    setForwardExtra(0);
    pendingForwardRef.current = null;
  }, [chartKey, analysisKey]);

  // Finish a pending「下一根」once future bars arrive.
  useEffect(() => {
    const pending = pendingForwardRef.current;
    if (pending == null) return;
    if (afterBars.length >= pending) {
      setForwardExtra(pending);
      pendingForwardRef.current = null;
      return;
    }
    if (!futureInflightRef.current) {
      pendingForwardRef.current = null;
    }
  }, [afterBars]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleRef.current;
    const volumeSeries = volumeRef.current;
    const ema20Series = emaRef.current;
    const paio = paioRef.current;
    if (!chart || !series || !volumeSeries || !ema20Series || !paio) return;

    if (visibleBars.length === 0) {
      series.setData([]);
      volumeSeries.setData([]);
      ema20Series.setData([]);
      paio.setModel(computePaio([]), buildBarLookup([]));
      prevVisibleRef.current = [];
      prevKeyRef.current = chartKey;
      return;
    }

    const pinned = analysisBarsRef.current;
    const applyFull = (fit: boolean) => {
      applySeriesData(series, volumeSeries, ema20Series, paio, visibleBars, overlays.ema20, pinned);
      if (fit) resetRange(chart, visibleBars.length, locked);
    };

    const identityChanged = prevKeyRef.current !== chartKey;
    const prev = prevVisibleRef.current;

    if (identityChanged || prev.length === 0) {
      applyFull(true);
    } else if (isBarPrepend(prev, visibleBars)) {
      applyFull(false);
    } else if (visibleBars.length < prev.length || !samePrefix(prev, visibleBars)) {
      applyFull(false);
    } else if (visibleBars.length === prev.length) {
      const last = visibleBars[visibleBars.length - 1];
      const prevLast = prev[prev.length - 1];
      if (
        last.timestamp !== prevLast.timestamp
        || last.open !== prevLast.open
        || last.high !== prevLast.high
        || last.low !== prevLast.low
        || last.close !== prevLast.close
        || last.volume !== prevLast.volume
      ) {
        series.update(toCandle(last));
        if (last.volume != null) volumeSeries.update(toVolume(last));
        ema20Series.setData(overlays.ema20 ? computeEma(visibleBars, 20) : []);
        paio.setModel(computePaio(visibleBars, { analysisBars: pinned }), buildBarLookup(visibleBars));
      }
    } else if (visibleBars.length === prev.length + 1) {
      const last = visibleBars[visibleBars.length - 1];
      series.update(toCandle(last));
      if (last.volume != null) volumeSeries.update(toVolume(last));
      ema20Series.setData(overlays.ema20 ? computeEma(visibleBars, 20) : []);
      paio.setModel(computePaio(visibleBars, { analysisBars: pinned }), buildBarLookup(visibleBars));
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range && range.to < visibleBars.length - 1) {
        const width = range.to - range.from;
        chart.timeScale().setVisibleLogicalRange({
          from: visibleBars.length - 1 - width,
          to: visibleBars.length - 1 + 2,
        });
      }
    } else {
      applyFull(false);
    }

    prevKeyRef.current = chartKey;
    prevVisibleRef.current = visibleBars;
  }, [visibleBars, chartKey, locked, overlays.ema20, analysisBars]);

  useEffect(() => {
    const visibility: PaioVisibility = {
      gaps: overlays.gaps,
      barIndex: overlays.barIndex,
      hl: overlays.hl,
    };
    paioRef.current?.setVisibility(visibility);
  }, [overlays.gaps, overlays.barIndex, overlays.hl]);

  useEffect(() => {
    const ema20Series = emaRef.current;
    if (!ema20Series) return;
    ema20Series.setData(overlays.ema20 ? computeEma(visibleBars, 20) : []);
    ema20Series.applyOptions({ visible: overlays.ema20 });
  }, [overlays.ema20, visibleBars]);

  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;

    for (const line of priceLinesRef.current) series.removePriceLine(line);
    priceLinesRef.current = [];

    const addLine = (price: number, color: string, title: string, lineWidth: number, lineStyle: number) => {
      priceLinesRef.current.push(series.createPriceLine({
        price,
        color,
        lineWidth: lineWidth as 1 | 2 | 3 | 4,
        lineStyle,
        axisLabelVisible: true,
        title,
      }));
    };

    // S/R belong to the analysis snapshot tip — hide once replay steps past it.
    if (forwardExtra === 0) {
      stage1?.support_levels.forEach((price) => addLine(price, "#12846d", "支撑", 1, 2));
      stage1?.resistance_levels.forEach((price) => addLine(price, "#d94f5d", "阻力", 1, 2));
    }
    const decisionLines: Array<[number | null, string, string]> = [
      [stage2?.decision.entry_price ?? null, "#2563eb", "入场"],
      [stage2?.decision.stop_loss_price ?? null, "#dc2626", "止损"],
      [stage2?.decision.take_profit_price ?? null, "#16a34a", "止盈"],
      [stage2?.decision.take_profit_price_2 ?? null, "#059669", "目标 2"],
    ];
    decisionLines.forEach(([price, color, title]) => {
      if (price != null) addLine(price, color, title, 2, 0);
    });

    markersApiRef.current?.setMarkers(markerSeriesData);
  }, [markerSeriesData, stage1, stage2, forwardExtra]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleRef.current;
    if (!chart || !series || !onNeedHistory || !period) return;

    historyRequestedBeforeRef.current = null;
    let timer = 0;
    const onRange = () => {
      if (historyInflightRef.current) return;
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range) return;
      const info = series.barsInLogicalRange(range);
      if (!info || info.barsBefore >= HISTORY_EDGE_BARS) return;
      const first = allBarsRef.current[0];
      if (!first) return;
      if (historyRequestedBeforeRef.current === first.timestamp) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (historyInflightRef.current) return;
        const tip = allBarsRef.current[0];
        if (!tip || historyRequestedBeforeRef.current === tip.timestamp) return;
        const end = tip.timestamp;
        const start = new Date(Date.parse(end) - lookbackDurationMs(period, HISTORY_CHUNK_BARS)).toISOString();
        historyRequestedBeforeRef.current = end;
        historyInflightRef.current = true;
        Promise.resolve(onNeedHistory(start, end))
          .catch(() => {
            if (historyRequestedBeforeRef.current === end) historyRequestedBeforeRef.current = null;
          })
          .finally(() => {
            historyInflightRef.current = false;
          });
      }, 200);
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    return () => {
      window.clearTimeout(timer);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
    };
  }, [onNeedHistory, period, chartKey]);

  async function requestFutureChunk() {
    if (!onNeedFuture || !period || !analysisTip || futureInflightRef.current) return;
    const cached = afterBarsRef.current;
    const from = cached.length ? cached[cached.length - 1].timestamp : analysisTip;
    const end = new Date(Date.parse(from) + lookbackDurationMs(period, FUTURE_CHUNK_BARS)).toISOString();
    futureInflightRef.current = true;
    try {
      await onNeedFuture(from, end);
    } finally {
      futureInflightRef.current = false;
    }
  }

  async function stepForward() {
    if (!analysisBarsRef.current?.length) return;
    const target = forwardExtraRef.current + 1;
    if (afterBarsRef.current.length >= target) {
      setForwardExtra(target);
      return;
    }
    pendingForwardRef.current = target;
    await requestFutureChunk();
    window.setTimeout(() => {
      if (pendingForwardRef.current !== target) return;
      if (afterBarsRef.current.length >= target) {
        setForwardExtra(target);
      }
      pendingForwardRef.current = null;
    }, 120);
  }

  function stepBack() {
    pendingForwardRef.current = null;
    setForwardExtra((current) => Math.max(0, current - 1));
  }

  function zoom(factor: number) {
    const chart = chartRef.current;
    const range = chart?.timeScale().getVisibleLogicalRange();
    if (!chart || !range) return;
    const center = (range.from + range.to) / 2;
    const halfWidth = ((range.to - range.from) * factor) / 2;
    chart.timeScale().setVisibleLogicalRange({
      from: center - halfWidth,
      to: center + halfWidth,
    });
  }

  function selectVisibleRange() {
    if (!onRangeSelected) return;
    if (analysisBars?.length && visibleBars.length) {
      onRangeSelected(visibleBars[0].timestamp, visibleBars[visibleBars.length - 1].timestamp);
      return;
    }
    const range = chartRef.current?.timeScale().getVisibleRange();
    if (!range) return;
    const toIso = (value: unknown) => new Date(Number(value) * 1000).toISOString();
    onRangeSelected(toIso(range.from), toIso(range.to));
  }

  function toggleOverlay(key: OverlayKey) {
    setOverlays((current) => ({ ...current, [key]: !current[key] }));
  }

  const showForwardControls = Boolean(locked && analysisBars?.length);
  const closeCountdown = useBarCloseCountdown(period, !locked && Boolean(period));

  useEffect(() => {
    if (closeCountdown == null) {
      setCountdownAnchor(null);
      return;
    }
    const sync = () => {
      setCountdownAnchor(lastBarAnchor(chartRef.current, candleRef.current, visibleBars));
    };
    sync();
    const chart = chartRef.current;
    if (!chart) return;
    chart.timeScale().subscribeVisibleLogicalRangeChange(sync);
    window.addEventListener("resize", sync);
    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(sync);
      window.removeEventListener("resize", sync);
    };
  }, [closeCountdown, visibleBars]);

  return (
    <div className="trading-chart-shell">
      <div className="chart-toolbar">
        <div className="chart-overlay-toggles" role="group" aria-label="图表叠加层">
          <label className={overlays.ema20 ? "is-on" : undefined}>
            <input checked={overlays.ema20} onChange={() => toggleOverlay("ema20")} type="checkbox" />
            <span className="chart-legend-ema">EMA20</span>
          </label>
          <label className={overlays.gaps ? "is-on" : undefined}>
            <input checked={overlays.gaps} onChange={() => toggleOverlay("gaps")} type="checkbox" />
            <span>Gap</span>
          </label>
          <label className={overlays.barIndex ? "is-on" : undefined}>
            <input checked={overlays.barIndex} onChange={() => toggleOverlay("barIndex")} type="checkbox" />
            <span>K#</span>
          </label>
          <label className={overlays.hl ? "is-on" : undefined}>
            <input checked={overlays.hl} onChange={() => toggleOverlay("hl")} type="checkbox" />
            <span>H/L</span>
          </label>
          {locked && <span className="chart-toolbar-hint">锁定区间</span>}
        </div>
        <div className="chart-toolbar-actions">
          {showForwardControls && (
            <div className="chart-replay" role="group" aria-label="分析后逐根查看">
              <button aria-label="上一根" disabled={forwardExtra <= 0} onClick={stepBack} type="button">上一根</button>
              <button aria-label="下一根" onClick={() => { void stepForward(); }} type="button">下一根</button>
            </div>
          )}
          <div className="chart-zoom-controls" role="group" aria-label="图表缩放">
          <button aria-label="放大图表" onClick={() => zoom(0.8)} type="button">＋</button>
          <button aria-label="缩小图表" onClick={() => zoom(1.25)} type="button">－</button>
          <button onClick={() => chartRef.current && resetRange(chartRef.current, visibleBars.length, locked)} type="button">重置</button>
          </div>
          {onRangeSelected && <button onClick={selectVisibleRange} type="button">使用可视区间</button>}
        </div>
      </div>
      <div className="trading-chart-stage">
        <div className="trading-chart" ref={containerRef} />
        {closeCountdown != null && countdownAnchor && (
          <div
            aria-live="polite"
            className="chart-candle-countdown"
            style={{ transform: `translate(${countdownAnchor.x}px, ${countdownAnchor.y}px)` }}
            title={`当前 ${period} K 线收盘倒计时`}
          >
            {closeCountdown}
          </div>
        )}
      </div>
    </div>
  );
}
