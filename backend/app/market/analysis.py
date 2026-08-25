from __future__ import annotations

from datetime import datetime, timezone

from app.core.errors import AppError
from app.core.models import Bar, BasicAnalysis, Direction, Period


PERIOD_SECONDS = {
    Period.minute_1: 60,
    Period.minute_5: 5 * 60,
    Period.minute_15: 15 * 60,
    Period.minute_30: 30 * 60,
    Period.hour_1: 60 * 60,
    Period.hour_4: 4 * 60 * 60,
    Period.day_1: 24 * 60 * 60,
}


def aggregate_bars(bars: list[Bar], period: Period) -> list[Bar]:
    """将 1 分钟 K 线聚合为更高周期的 K 线。"""
    if period is Period.minute_1 or not bars:
        return bars

    seconds = PERIOD_SECONDS[period]
    groups: dict[int, list[Bar]] = {}
    for bar in bars:
        bucket = int(bar.timestamp.timestamp()) // seconds * seconds
        groups.setdefault(bucket, []).append(bar)

    aggregated: list[Bar] = []
    for bucket, source in sorted(groups.items()):
        volumes = [bar.volume for bar in source if bar.volume is not None]
        aggregated.append(
            Bar(
                timestamp=datetime.fromtimestamp(bucket, tz=timezone.utc),
                open=source[0].open,
                high=max(bar.high for bar in source),
                low=min(bar.low for bar in source),
                close=source[-1].close,
                volume=sum(volumes) if volumes else None,
            )
        )
    return aggregated


def analyze_bars(bars: list[Bar]) -> BasicAnalysis:
    """对 K 线序列进行基础统计分析，返回涨跌方向、波动幅度等汇总指标。"""
    if not bars:
        raise AppError("no_data", "The provider returned no bars", 404)

    bullish = sum(bar.close > bar.open for bar in bars)
    bearish = sum(bar.close < bar.open for bar in bars)
    neutral = len(bars) - bullish - bearish
    first_open = bars[0].open
    latest_close = bars[-1].close
    change_percent = (
        ((latest_close - first_open) / first_open) * 100 if first_open else 0.0
    )

    if change_percent > 0.05:
        direction = Direction.bullish
    elif change_percent < -0.05:
        direction = Direction.bearish
    else:
        direction = Direction.neutral

    return BasicAnalysis(
        bar_count=len(bars),
        start=bars[0].timestamp,
        end=bars[-1].timestamp,
        first_open=first_open,
        latest_close=latest_close,
        period_high=max(bar.high for bar in bars),
        period_low=min(bar.low for bar in bars),
        change_percent=round(change_percent, 4),
        bullish_bars=bullish,
        bearish_bars=bearish,
        neutral_bars=neutral,
        direction=direction,
        method="period return threshold ±0.05%",
    )
