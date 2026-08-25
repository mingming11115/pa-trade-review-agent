"""Tests for daily bar index (session-open numbering)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.analysis.workflow.stage1.core.bar_index import (
    compute_daily_indices_chronological,
    compute_daily_indices_newest_first,
    trading_day_key,
)


def test_trading_day_key_uses_chicago_calendar() -> None:
    # 2024-01-03 05:00 UTC = 2024-01-02 23:00 CST
    ts = datetime(2024, 1, 3, 5, 0, tzinfo=timezone.utc)
    assert trading_day_key(ts) == "2024-01-02"


def test_daily_indices_reset_across_days_chronological() -> None:
    stamps = [
        datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 3, 16, 0, tzinfo=timezone.utc),
    ]
    assert compute_daily_indices_chronological(stamps) == [1, 2, 1, 2]


def test_daily_indices_newest_first_aligned_with_frame_order() -> None:
    stamps = [
        datetime(2024, 1, 3, 16, 0, tzinfo=timezone.utc),  # newest
        datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc),  # oldest
    ]
    assert compute_daily_indices_newest_first(stamps) == [2, 1, 2, 1]
