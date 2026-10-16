import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  CandlestickSeries,
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
import { useChartReplay, type ReplayLoader } from "./useChartReplay";
import { computePaio } from "./paio";
import { buildBarLookup, PaioPrimitive, type PaioVisibility } from "./PaioPrimitive";
import { createChartOverlayContext, type ChartOverlayContext } from "./chartOverlay";
import { ReplayBarPicker } from "./ReplayBarPicker";
import { IndicatorPanel, IndicatorSettingsDialog } from "./IndicatorPanel";
import { IndicatorLegend } from "./IndicatorLegend";
import { loadIndicatorSettings, saveIndicatorSettings, type IndicatorKey } from "./indicatorSettings";
import { createChartIndicators } from "./chartIndicators";
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
  paio: PaioPrimitive,
  nextBars: Bar[],
  analysisBars?: Bar[],
) {
  series.setData(nextBars.map(toCandle));
  paio.setModel(computePaio(nextBars, { analysisBars }), buildBarLookup(nextBars));
}

const DEFAULT_OVERLAYS = {
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
  onReplayLoad,
  onStartTradingReplay,
  replaySetup,
  replayStartDisabled=false,
  replayStarting=false,
  replayStartingSeconds=0,
  onCancelTradingReplay,
  renderOverlay,
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
  onReplayLoad?: ReplayLoader;
  onStartTradingReplay?: (start?:string)=>void;
  replaySetup?: ReactNode;
  replayStartDisabled?: boolean;
  replayStarting?:boolean;
  replayStartingSeconds?:number;
  onCancelTradingReplay?:()=>void;
  onPeriodChange?: (period: Period) => void;
  renderOverlay?: (context: ChartOverlayContext) => ReactNode;
}) {
  const [inputTimezone, setInputTimezone] = useState(timezoneOffsetMinutes);
  const replay = useChartReplay(chartKey, period, inputTimezone, onReplayLoad);
  const [replayOpen, setReplayOpen] = useState(false);
  const [replayDate, setReplayDate] = useState("");
  const [replayNow, setReplayNow] = useState(Date.now);
  const replayMax = new Date(replayNow + inputTimezone * 60000).toISOString().slice(0, 16);
  const replayTime = Date.parse(`${replayDate}Z`) - inputTimezone * 60000;
  const replayDateInvalid = !Number.isFinite(replayTime) || replayTime > replayNow;
  useEffect(() => {
    if (!replayOpen) return;
    setReplayNow(Date.now());
    const timer = window.setInterval(() => setReplayNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [replayOpen]);
  const replayActive = replay.bars !== null;
  const displayKey = `${chartKey}:${replayActive ? "replay" : "normal"}`;
  const barPickerIdentity = `${displayKey}:${period ?? ''}`;
  const [barPickerKey, setBarPickerKey] = useState<string | null>(null);
  const selectingBar = replayOpen && !replay.busy && !replayStarting && barPickerKey === barPickerIdentity;
  useEffect(() => { setBarPickerKey(null); }, [barPickerIdentity, replayOpen, replay.busy, replayStarting]);
  useEffect(() => { setReplayOpen(false); }, [chartKey]);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const overlayBridgeRef = useRef<ReturnType<typeof createChartOverlayContext> | null>(null);
  const [overlayContext, setOverlayContext] = useState<ChartOverlayContext | null>(null);
  const hasOverlay = Boolean(renderOverlay) || selectingBar;
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const indicatorsRef = useRef<ReturnType<typeof createChartIndicators> | null>(null);
  const [indicatorChart, setIndicatorChart] = useState<IChartApi | null>(null);
  const [indicatorSettingsKey, setIndicatorSettingsKey] = useState<IndicatorKey | null>(null);
  const [indicators, setIndicators] = useState(loadIndicatorSettings);
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
  analysisBarsRef.current = replayActive ? undefined : analysisBars;
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
    if (replay.bars) return replay.bars;
    if (!analysisBars?.length) return bars;
    return [...analysisBars, ...afterBars.slice(0, forwardExtra)];
  }, [analysisBars, afterBars, bars, forwardExtra, replay.bars]);

  const markerSeriesData = useMemo(() => {
    const tip = visibleBars[visibleBars.length - 1]?.timestamp;
    const filtered = tip
      ? markers.filter((marker) => Date.parse(marker.timestamp) <= Date.parse(tip))
      : markers;
    return filtered.map(toMarkerShape);
  }, [markers, visibleBars]);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      height: 460,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#8290a2",
        fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif',
        attributionLogo: false,
      },
      localization: {
        locale: "zh-CN",
        timeFormatter: (time: Time) => timezoneFormat(time, timezoneOffsetMinutes, "full"),
      },
      grid: {
        vertLines: { color: "#edf1f6" },
        horzLines: { color: "#edf1f6" },
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
    const paio = new PaioPrimitive();
    series.attachPrimitive(paio);

    chartRef.current = chart;
    candleRef.current = series;
    indicatorsRef.current = createChartIndicators(chart);
    setIndicatorChart(chart);
    paioRef.current = paio;
    markersApiRef.current = createSeriesMarkers(series, []);

    return () => {
      // This effect cleans up before the optional overlay effect below.
      overlayBridgeRef.current?.dispose();
      overlayBridgeRef.current = null;
      markersApiRef.current = null;
      candleRef.current = null;
      indicatorsRef.current = null;
      paioRef.current = null;
      chartRef.current = null;
      prevKeyRef.current = "";
      prevVisibleRef.current = [];
      priceLinesRef.current = [];
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleRef.current;
    const container = containerRef.current;
    if (!hasOverlay || !chart || !series || !container) return;
    const bridge = createChartOverlayContext({ chart, series, container });
    overlayBridgeRef.current = bridge;
    setOverlayContext(bridge.context);
    return () => {
      bridge.dispose();
      if (overlayBridgeRef.current === bridge) overlayBridgeRef.current = null;
      setOverlayContext(null);
    };
  }, [hasOverlay]);

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
    const paio = paioRef.current;
    if (!chart || !series || !paio) return;

    if (visibleBars.length === 0) {
      series.setData([]);
      paio.setModel(computePaio([]), buildBarLookup([]));
      prevVisibleRef.current = [];
      prevKeyRef.current = displayKey;
      return;
    }

    const pinned = analysisBarsRef.current;
    const applyFull = (fit: boolean) => {
      applySeriesData(series, paio, visibleBars, pinned);
      if (fit) resetRange(chart, visibleBars.length, locked);
    };

    const identityChanged = prevKeyRef.current !== displayKey;
    const prev = prevVisibleRef.current;

    if (identityChanged || prev.length === 0) {
      applyFull(true);
    } else if (isBarPrepend(prev, visibleBars)) {
      applyFull(false);
    } else if (visibleBars.length < prev.length || !samePrefix(prev, visibleBars)) {
      applyFull(replayActive);
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
        paio.setModel(computePaio(visibleBars, { analysisBars: pinned }), buildBarLookup(visibleBars));
      }
    } else if (visibleBars.length === prev.length + 1) {
      const last = visibleBars[visibleBars.length - 1];
      series.update(toCandle(last));
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

    prevKeyRef.current = displayKey;
    prevVisibleRef.current = visibleBars;
  }, [visibleBars, displayKey, locked, analysisBars]);

  useEffect(() => {
    const visibility: PaioVisibility = {
      gaps: overlays.gaps,
      barIndex: overlays.barIndex,
      hl: overlays.hl,
    };
    paioRef.current?.setVisibility(visibility);
  }, [overlays.gaps, overlays.barIndex, overlays.hl]);

  useEffect(() => { saveIndicatorSettings(indicators); }, [indicators]);

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
    if (!replayActive && forwardExtra === 0) {
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
      if (!replayActive && price != null) addLine(price, color, title, 2, 0);
    });

    markersApiRef.current?.setMarkers(markerSeriesData);
  }, [markerSeriesData, stage1, stage2, forwardExtra, replayActive]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleRef.current;
    if (!chart || !series || !onNeedHistory || !period || replayActive) return;

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
  }, [onNeedHistory, period, chartKey, replayActive]);

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
    if ((replayActive || analysisBars?.length) && visibleBars.length) {
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

  const showForwardControls = Boolean(!replayActive && locked && analysisBars?.length);
  const closeCountdown = useBarCloseCountdown(period, !replayActive && !locked && Boolean(period));

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
        {locked && !replayActive && <span className="chart-toolbar-hint">锁定区间</span>}
        <div className="chart-toolbar-actions">
          <IndicatorPanel settings={indicators} onChange={setIndicators} displayControls={<>
              <label className={indicators.ema.enabled && indicators.ema.visible ? "is-on" : undefined}>
                <input checked={indicators.ema.enabled && indicators.ema.visible} onChange={() => setIndicators(current => ({ ...current, ema: { ...current.ema, enabled: true, visible: !current.ema.enabled || !current.ema.visible } }))} type="checkbox" />
                <span style={{ color: indicators.ema.color }}>EMA{indicators.ema.period}</span>
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
          </>} />
          {(onReplayLoad||onStartTradingReplay) && <button type="button" disabled={replay.busy} aria-expanded={replayOpen} onClick={() => {
            setReplayOpen(replayActive || !replayOpen);
            if (!replayDate && visibleBars.length) setReplayDate(new Date(Date.parse(visibleBars[visibleBars.length - 1].timestamp) + inputTimezone * 60000).toISOString().slice(0, 16));
          }}>回放</button>}
          {showForwardControls && (
            <div className="chart-replay" role="group" aria-label="分析后逐根查看">
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
      {replayOpen && <div className="chart-replay-panel" role="group" aria-label="K 线回放">
        <button type="button" disabled={replay.busy || replayStarting || visibleBars.length === 0} aria-pressed={selectingBar} onClick={() => {
          replay.setPlaying(false);
          setBarPickerKey(selectingBar ? null : barPickerIdentity);
        }}>{selectingBar ? '取消选择' : '选择 K 线'}</button>
        <label>开始时间 <input aria-label="回放开始时间" type="datetime-local" max={replayMax} value={replayDate} onChange={event => setReplayDate(event.target.value)} /></label>
        <label>输入时区 <select aria-label="回放输入时区" disabled={replay.busy} value={inputTimezone} onChange={event => {
          const nextOffset = Number(event.target.value);
          const parsed = Date.parse(`${replayDate}Z`);
          if (Number.isFinite(parsed)) setReplayDate(new Date(parsed + (nextOffset - inputTimezone) * 60000).toISOString().slice(0, 16));
          setInputTimezone(nextOffset);
        }}>
          <option value={timezoneOffsetMinutes}>图表时间 UTC{timezoneOffsetMinutes >= 0 ? "+" : ""}{timezoneOffsetMinutes / 60}</option>
          {timezoneOffsetMinutes !== 480 && <option value={480}>北京时间 UTC+8</option>}
        </select></label>
        {replaySetup}
        <button className={replaySetup ? "primary-action replay-start-action" : undefined} type="button" disabled={replay.busy || replayStarting || replayStartDisabled || replayDateInvalid} onClick={() => { setBarPickerKey(null); if(onStartTradingReplay)onStartTradingReplay(new Date(replayTime).toISOString());else void replay.start(replayDate); }}>{replaySetup ? "开始回放" : "从此处回放"}</button>
        <button type="button" disabled={replay.busy||replayStarting||replayStartDisabled} title={onStartTradingReplay ? "从当前品种已存的本地行情中随机选择回放起点" : "从 2023 年 1 月 1 日至今随机选择当前品种的回放起点"} onClick={() => {
          setBarPickerKey(null);
          if(onStartTradingReplay){onStartTradingReplay();return;}
          void replay.startRandom().then(time => {
            if (time !== undefined) setReplayDate(new Date(time + inputTimezone * 60000).toISOString().slice(0, 16));
          });
        }}>随机回放</button>
        {replayActive && <>
          <button type="button" disabled={replay.busy} onClick={() => { replay.setPlaying(false); void replay.next(); }}>下一根</button>
          <button type="button" disabled={replay.busy && !replay.playing} onClick={() => replay.setPlaying(!replay.playing)}>{replay.playing ? "暂停" : "播放"}</button>
          <span>回放至 {timezoneFormat(Math.floor(Date.parse(visibleBars[visibleBars.length - 1].timestamp) / 1000) as UTCTimestamp, timezoneOffsetMinutes, "full")}</span>
        </>}
        <button type="button" onClick={() => { if(replayStarting)onCancelTradingReplay?.();else replay.exit(); setReplayOpen(false); }}>{replayStarting?'取消创建':replaySetup?'取消':'退出回放'}</button>
        {replayStarting && <span role="status">正在创建回放… {replayStartingSeconds} 秒 · 正在准备历史行情，可取消等待</span>}
        {replay.busy && <span role="status">正在加载行情… {replay.elapsedSeconds} 秒{replay.elapsedSeconds >= 10 ? " · 历史数据补充中，可退出取消" : ""}</span>}
        {replay.error && <span role="alert">{replay.error}</span>}
      </div>}
      <div className="trading-chart-stage" style={{ height: `calc(var(--chart-base-height, 460px) + ${140 * (Number(indicators.macd.enabled) + Number(indicators.rsi.enabled))}px)` }}>
        <div className="trading-chart" ref={containerRef} />
        {indicatorChart && indicatorsRef.current && containerRef.current && <IndicatorLegend
          chart={indicatorChart} renderer={indicatorsRef.current} container={containerRef.current}
          bars={visibleBars} settings={indicators} period={period} identity={displayKey}
          onChange={setIndicators} onSettings={setIndicatorSettingsKey}
        />}
        {overlayContext && renderOverlay?.(overlayContext)}
        {overlayContext && selectingBar && <ReplayBarPicker
          context={overlayContext}
          confirmStart={Boolean(replaySetup)}
          bars={visibleBars}
          timezoneOffsetMinutes={timezoneOffsetMinutes}
          onCancel={() => setBarPickerKey(null)}
          onSelect={timestamp => {
            if (replay.busy || replayStarting) return;
            setBarPickerKey(null);
            const time = Date.parse(timestamp);
            const local = new Date(time + inputTimezone * 60000).toISOString().slice(0, 16);
            setReplayDate(local);
            if (replaySetup) return;
            if (onStartTradingReplay) onStartTradingReplay(new Date(time).toISOString());
            else void replay.start(local);
          }}
        />}
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
      {indicatorSettingsKey && <IndicatorSettingsDialog key={indicatorSettingsKey} indicator={indicatorSettingsKey} settings={indicators} onChange={setIndicators} onClose={() => setIndicatorSettingsKey(null)} />}
    </div>
  );
}
