"""Shared chart and replay trading windows; all bar timestamps are UTC opens.

RTH is the product's weekday chart window, intersected with actual market data.
It deliberately does not manufacture bars or claim an exchange holiday calendar.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.models import Bar, TradingHours

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
PERIOD_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
RTH_MAX_RANGE_DAYS = {"1m": 30, "5m": 30, "15m": 30, "30m": 30, "1h": 30, "4h": 180, "1d": 365}


def _utc(timestamp: datetime) -> datetime:
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


def rth_window(timestamp: datetime, symbol: str) -> tuple[datetime, datetime]:
    """Return the UTC RTH bounds for the timestamp's New York calendar date."""
    local = _utc(timestamp).astimezone(NEW_YORK)
    normalized = symbol.strip().upper().split(":")[-1]
    opened, closed = (time(8, 20), time(13, 30)) if normalized.startswith("MGC") else (time(9, 30), time(16, 15))
    return (
        datetime.combine(local.date(), opened, tzinfo=NEW_YORK).astimezone(UTC),
        datetime.combine(local.date(), closed, tzinfo=NEW_YORK).astimezone(UTC),
    )


def in_trading_hours(timestamp: datetime, symbol: str, trading_hours: TradingHours) -> bool:
    """ETH includes all available bars; RTH includes weekday open, excludes close."""
    if trading_hours == "ETH":
        return True
    value = _utc(timestamp)
    opened, closed = rth_window(value, symbol)
    return value.astimezone(NEW_YORK).weekday() < 5 and opened <= value < closed


def bucket_start(timestamp: datetime, period: str, symbol: str, trading_hours: TradingHours) -> datetime:
    """Return the selected session's aggregation anchor (UTC for legacy ETH)."""
    minutes = PERIOD_MINUTES[period]
    value = _utc(timestamp)
    if trading_hours == "ETH":
        return datetime.fromtimestamp(int(value.timestamp()) // (minutes * 60) * minutes * 60, UTC)
    opened, _ = rth_window(value, symbol)
    if period == "1d":
        return opened
    offset = int((value - opened).total_seconds() // (minutes * 60))
    return opened + timedelta(minutes=offset * minutes)


def bucket_end(timestamp: datetime, period: str, symbol: str, trading_hours: TradingHours) -> datetime:
    """Return an exclusive close, truncating the last RTH candle at session close."""
    opened = bucket_start(timestamp, period, symbol, trading_hours)
    end = opened + timedelta(minutes=PERIOD_MINUTES[period])
    return min(end, rth_window(timestamp, symbol)[1]) if trading_hours == "RTH" else end


def aggregate_session_bars(bars: list[Bar], period: str, symbol: str, trading_hours: TradingHours) -> list[Bar]:
    """Filter minutes before aggregation; repeated same-period aggregation is stable."""
    groups: dict[datetime, list[Bar]] = {}
    for bar in sorted(bars, key=lambda item: item.timestamp):
        if in_trading_hours(bar.timestamp, symbol, trading_hours):
            groups.setdefault(bucket_start(bar.timestamp, period, symbol, trading_hours), []).append(bar)
    result: list[Bar] = []
    for opened, source in sorted(groups.items()):
        volumes = [bar.volume for bar in source if bar.volume is not None]
        result.append(Bar(
            timestamp=opened, timeframe=period, trading_hours=trading_hours,
            open=source[0].open, high=max(bar.high for bar in source),
            low=min(bar.low for bar in source), close=source[-1].close,
            volume=sum(volumes) if volumes else None,
        ))
    return result
