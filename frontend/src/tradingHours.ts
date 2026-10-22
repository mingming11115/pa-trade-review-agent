import type { Bar, Period, ReplaySession, TradingHours } from "./types";

const newYork = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function regularWindow(symbol: string) {
  return symbol.toUpperCase().startsWith("MGC") ? { start: 500, end: 810 } : { start: 570, end: 975 };
}

function localMinute(timestamp: string) {
  const time = Date.parse(timestamp);
  if (!Number.isFinite(time)) return null;
  const parts = newYork.formatToParts(new Date(time));
  const value = (kind: string) => parts.find(part => part.type === kind)?.value;
  return { time, minute: Number(value("hour")) * 60 + Number(value("minute")), weekday: value("weekday") };
}

/** Membership uses the quote/fill source minute, never the replay cursor. */
export function inTradingHours(timestamp: string, symbol: string, hours: TradingHours = "ETH"): boolean {
  if (hours === "ETH") return true;
  const local = localMinute(timestamp), window = regularWindow(symbol);
  return !!local && local.weekday !== "Sat" && local.weekday !== "Sun" && local.minute >= window.start && local.minute < window.end;
}

const periodMinutes: Record<Period, number> = { "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440 };

/** RTH tail candles end at the session close, even when shorter than a full period. */
export function barCloseAtMs(bar: Bar, period: Period, symbol = ""): number {
  const open = Date.parse(bar.timestamp), duration = periodMinutes[period] * 60_000;
  if (bar.trading_hours !== "RTH") return open + duration;
  const local = localMinute(bar.timestamp);
  return local ? open + Math.min(periodMinutes[period], regularWindow(symbol).end - local.minute) * 60_000 : open + duration;
}

export function canMarketTrade(session: ReplaySession): boolean {
  if (session.can_market_trade !== undefined) return session.can_market_trade;
  const hours = session.state.config?.trading_hours ?? "ETH";
  if (hours === "ETH") return true;
  return session.state.last_price != null && !!session.state.last_price_at && inTradingHours(session.state.last_price_at, session.symbol, hours);
}
