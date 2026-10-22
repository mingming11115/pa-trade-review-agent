import { barCloseAtMs } from "./tradingHours";
import type { Bar, Period, TradingHours } from "./types";

/** Aligned with backend scheduled realtime Snapshot (~80 closed bars). */
export const ANALYSIS_LOOKBACK_BARS = 80;
/** Chunk size when panning left for more history. */
export const HISTORY_CHUNK_BARS = 120;
/** Prefetch when fewer than this many bars remain left of the viewport. */
export const HISTORY_EDGE_BARS = 50;
export const LIVE_POLL_MS = 30_000;
/** Chunk size when stepping forward past the analysis tip. */
export const FUTURE_CHUNK_BARS = 120;

export const PERIOD_MINUTES: Record<Period, number> = {
  "1m": 1,
  "5m": 5,
  "15m": 15,
  "30m": 30,
  "1h": 60,
  "4h": 240,
  "1d": 1440,
};

export interface SourceTimezone {
  offsetMinutes: number;
  label: string;
}

function timezoneLabel(offsetMinutes: number): string {
  if (offsetMinutes === 0) return "UTC";
  const sign = offsetMinutes > 0 ? "+" : "-";
  const absolute = Math.abs(offsetMinutes);
  const hours = Math.floor(absolute / 60);
  const minutes = absolute % 60;
  return `UTC${sign}${hours}${minutes ? `:${String(minutes).padStart(2, "0")}` : ""}`;
}

/** Read the explicit ISO-8601 offset. Offset-less or invalid values stay UTC. */
export function sourceTimezone(timestamp?: string): SourceTimezone {
  if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return { offsetMinutes: 0, label: "UTC" };
  const match = timestamp.match(/(Z|([+-])(\d{2}):(\d{2}))$/i);
  if (!match) return { offsetMinutes: 0, label: "UTC" };
  if (match[1].toUpperCase() === "Z") return { offsetMinutes: 0, label: "UTC" };
  const hours = Number(match[3]);
  const minutes = Number(match[4]);
  if (hours > 23 || minutes > 59) return { offsetMinutes: 0, label: "UTC" };
  const offsetMinutes = (match[2] === "-" ? -1 : 1) * (hours * 60 + minutes);
  return { offsetMinutes, label: timezoneLabel(offsetMinutes) };
}

/** Format epoch seconds in a fixed source offset without consulting browser timezone. */
export function formatChartTime(epochSeconds: number, offsetMinutes: number, style: "axis" | "full"): string {
  const shifted = new Date(epochSeconds * 1000 + offsetMinutes * 60_000);
  const year = shifted.getUTCFullYear();
  const month = String(shifted.getUTCMonth() + 1).padStart(2, "0");
  const day = String(shifted.getUTCDate()).padStart(2, "0");
  const hours = String(shifted.getUTCHours()).padStart(2, "0");
  const minutes = String(shifted.getUTCMinutes()).padStart(2, "0");
  return style === "full" ? `${year}-${month}-${day} ${hours}:${minutes}` : `${month}-${day} ${hours}:${minutes}`;
}

export function tradingRangeLimitMs(period: Period): number {
  return (period === "1d" ? 365 : period === "4h" ? 180 : 30) * 24 * 60 * 60_000;
}

export function lookbackDurationMs(period: Period, bars: number = ANALYSIS_LOOKBACK_BARS, tradingHours: TradingHours = "ETH"): number {
  const duration = PERIOD_MINUTES[period] * bars * 60_000;
  return tradingHours === "RTH" ? Math.min(tradingRangeLimitMs(period), Math.max(duration * 5, 4 * 24 * 60 * 60_000)) : duration;
}

const cmeLocalHour = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", weekday: "short", hour: "2-digit", hourCycle: "h23",
});

// Skip only the regular CME weekend/daily break, never infer a holiday or data gap.
function beforeRegularClosure(end: number, earliest: number): number {
  while (end > earliest) {
    const parts = cmeLocalHour.formatToParts(new Date(end - 1));
    const day = parts.find(p => p.type === "weekday")?.value;
    const hour = Number(parts.find(p => p.type === "hour")?.value);
    const closed = day === "Sat" || (day === "Fri" && hour >= 17)
      || (day === "Sun" && hour < 18) || (day !== "Fri" && day !== "Sun" && hour === 17);
    if (!closed) break;
    end = Math.max(earliest, Math.floor((end - 1) / 3_600_000) * 3_600_000);
  }
  return end;
}

/** Load earlier bars without treating an empty calendar window as the archive boundary. */
export async function loadHistoryBars(
  period: Period, start: string, end: string,
  load: (start: string, end: string) => Promise<Bar[]>,
  isCurrent: () => boolean = () => true,
): Promise<Bar[]> {
  const endMs = Date.parse(end);
  const limit = tradingRangeLimitMs(period);
  const earliest = endMs - limit;
  const maxRequest = PERIOD_MINUTES[period] * 2000 * 60_000;
  // Short ETH chunks can fit entirely inside a weekend. Include earlier sessions
  // in the first request so a failed upstream cannot hide existing Friday bars.
  let duration = Math.min(limit, Math.max(endMs - Date.parse(start), 4 * 24 * 60 * 60_000));
  let right = endMs;
  while (isCurrent()) {
    right = beforeRegularClosure(right, earliest);
    if (right <= earliest) return [];
    const left = Math.max(endMs - duration, right - maxRequest);
    const bars = await load(new Date(left).toISOString(), new Date(right).toISOString());
    if (!isCurrent()) return [];
    if (bars.length || left <= earliest) return bars;
    right = left;
    duration = Math.min(limit, duration * 2);
  }
  return [];
}

/** UTC ms when the currently forming period bucket closes. */
export function currentBucketCloseAtMs(period: Period, nowMs: number = Date.now()): number {
  const periodMs = PERIOD_MINUTES[period] * 60_000;
  return Math.floor(nowMs / periodMs) * periodMs + periodMs;
}

/** Remaining ms until the forming bucket closes (0 at exact boundary before next tick). */
export function remainingToBucketCloseMs(period: Period, nowMs: number = Date.now()): number {
  return Math.max(0, currentBucketCloseAtMs(period, nowMs) - nowMs);
}

/** Compact countdown for chart chrome: `ss` / `mm:ss` / `h:mm:ss`. */
export function formatBarCountdown(remainingMs: number): string {
  const totalSeconds = Math.max(0, Math.ceil(remainingMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** Rolling window for live poll / realtime Snapshot (end ≈ now). */
export function liveLookbackWindow(period: Period, now: Date = new Date(), tradingHours: TradingHours = "ETH"): { start: string; end: string } {
  const end = now.toISOString();
  const start = new Date(now.getTime() - lookbackDurationMs(period, ANALYSIS_LOOKBACK_BARS, tradingHours)).toISOString();
  return { start, end };
}

/** Bars whose whole period ends at or before `now`; excludes the forming bucket. */
export function closedBars(bars: Bar[], period: Period, now: Date = new Date(), symbol = ""): Bar[] {
  return bars.filter((bar) => barCloseAtMs(bar, period, symbol) <= now.getTime());
}

export function mergeBars(existing: Bar[], incoming: Bar[]): Bar[] {
  const byTs = new Map<string, Bar>();
  for (const bar of existing) byTs.set(bar.timestamp, bar);
  for (const bar of incoming) byTs.set(bar.timestamp, bar);
  return [...byTs.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}

export function lastClosedTs(bars: Bar[]): string | null {
  return bars.length ? bars[bars.length - 1].timestamp : null;
}

export function chartIdentity(opts: {
  symbol: string;
  period: string;
  feedKind: "history" | "live";
  trading_hours?: TradingHours;
  start?: string;
  end?: string;
}): string {
  if (opts.feedKind === "live") return `${opts.symbol}|${opts.period}|live${opts.trading_hours === "RTH" ? "|RTH" : ""}`;
  return `${opts.symbol}|${opts.period}|history|${opts.start ?? ""}|${opts.end ?? ""}${opts.trading_hours === "RTH" ? "|RTH" : ""}`;
}

/** True when `next` is `prev` with older bars prepended (same trailing sequence). */
export function isBarPrepend(prev: Bar[], next: Bar[]): boolean {
  if (prev.length === 0 || next.length <= prev.length) return false;
  const offset = next.length - prev.length;
  for (let i = 0; i < prev.length; i += 1) {
    if (prev[i].timestamp !== next[i + offset]?.timestamp) return false;
  }
  return true;
}
