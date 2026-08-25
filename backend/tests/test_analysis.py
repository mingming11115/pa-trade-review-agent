from datetime import datetime, timezone

from app.market.analysis import aggregate_bars, analyze_bars
from app.core.models import Bar, Direction, Period


def test_analyze_bars_returns_deterministic_summary() -> None:
    bars = [
        Bar(
            timestamp=datetime(2022, 6, 6, 0, 0, tzinfo=timezone.utc),
            open=100,
            high=102,
            low=99,
            close=101,
            volume=10,
        ),
        Bar(
            timestamp=datetime(2022, 6, 6, 0, 1, tzinfo=timezone.utc),
            open=101,
            high=104,
            low=100,
            close=103,
            volume=12,
        ),
    ]

    result = analyze_bars(bars)

    assert result.bar_count == 2
    assert result.latest_close == 103
    assert result.period_high == 104
    assert result.period_low == 99
    assert result.change_percent == 3
    assert result.bullish_bars == 2
    assert result.direction is Direction.bullish


def test_aggregate_bars_builds_five_minute_ohlcv() -> None:
    bars = [
        Bar(
            timestamp=datetime(2022, 6, 6, 0, minute, tzinfo=timezone.utc),
            open=100 + minute,
            high=102 + minute,
            low=99 + minute,
            close=101 + minute,
            volume=10,
        )
        for minute in range(5)
    ]

    result = aggregate_bars(bars, Period.minute_5)

    assert len(result) == 1
    assert result[0].open == 100
    assert result[0].high == 106
    assert result[0].low == 99
    assert result[0].close == 105
    assert result[0].volume == 50
