import { useLayoutEffect, useRef, useState } from 'react';
import type { MouseEvent } from 'react';
import type { UTCTimestamp } from 'lightweight-charts';
import type { ChartOverlayContext } from './chartOverlay';
import { formatChartTime } from './feed';
import type { Bar } from './types';
import './replayBarPicker.css';

interface Props {
  context: ChartOverlayContext;
  confirmStart?: boolean;
  bars: Bar[];
  timezoneOffsetMinutes: number;
  onSelect: (timestamp: string) => void;
  onCancel: () => void;
}

export function ReplayBarPicker({ context, confirmStart=false, bars, timezoneOffsetMinutes, onSelect, onCancel }: Props) {
  const [timestamp, setTimestamp] = useState<string | null>(null);
  const [, setViewport] = useState(0);
  const committed = useRef(false);
  const latestCancel = useRef(onCancel);
  latestCancel.current = onCancel;
  const pane = context.chart.paneSize(0);

  useLayoutEffect(() => {
    context.setInteractionLocked(true);
    const unsubscribe = context.subscribeViewportChange(() => setViewport(value => value + 1));
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      latestCancel.current();
    };
    document.addEventListener('keydown', escape);
    return () => {
      unsubscribe();
      context.setInteractionLocked(false);
      document.removeEventListener('keydown', escape);
    };
  }, [context]);

  function barAt(event: MouseEvent<HTMLDivElement>) {
    const rect = context.container.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const size = context.chart.paneSize(0);
    if (x < 0 || y < 0 || x >= size.width || y >= size.height) return null;
    const logical = context.chart.timeScale().coordinateToLogical(x);
    if (logical == null || !Number.isFinite(logical)) return null;
    const bar = bars[Math.round(logical)];
    if (!bar || !Number.isFinite(Date.parse(bar.timestamp)) || Date.parse(bar.timestamp) > Date.now()) return null;
    return bar;
  }

  const time = timestamp == null ? null : Math.floor(Date.parse(timestamp) / 1000) as UTCTimestamp;
  const x = time == null ? null : context.chart.timeScale().timeToCoordinate(time);
  const visible = x != null && x >= 0 && x < pane.width;
  const label = time == null ? '' : formatChartTime(time, timezoneOffsetMinutes, 'full');

  return <div
    className="replay-bar-picker"
    role="region"
    aria-label="K 线回放起点选择区"
    style={{ width: pane.width, height: pane.height }}
    onMouseMove={event => { if (!committed.current) setTimestamp(barAt(event)?.timestamp ?? null); }}
    onMouseLeave={() => setTimestamp(null)}
    onContextMenu={event => event.preventDefault()}
    onClick={event => {
      event.stopPropagation();
      const bar = barAt(event);
      if (!bar || committed.current || event.button !== 0) return;
      committed.current = true;
      onSelect(bar.timestamp);
    }}
  >
    {visible && <>
      <div className="replay-bar-picker-future" style={{ left: x }} />
      <div className="replay-bar-picker-line" style={{ left: x }} />
    </>}
    <div className="replay-bar-picker-hint" role="status">
      {visible ? `${label} · ${confirmStart ? '点击选择起点，再确认资金开始回放' : '点击从这根开始回放'}` : '选择起始 K 线 · 点击确认 · Esc 取消'}
    </div>
  </div>;
}
