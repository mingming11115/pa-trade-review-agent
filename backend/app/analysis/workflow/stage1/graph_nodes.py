from __future__ import annotations

import math
from typing import Any

from app.analysis.workflow.stage1.core.data.base import KlineFrame
from app.analysis.workflow.stage1.core.decision_nodes import (
    CHAOS_DIRECTION_SCORE_MAX,
    CHAOS_EMA_FLAT_ATR_RATIO,
    CHAOS_OVERLAP_THRESHOLD,
    build_program_trace_node,
    judge_always_in,
    judge_data_sufficiency,
    judge_direction,
)
from app.analysis.workflow.stage1.core.kline_features import compute_kline_geometry_features


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _recent_features(frame: KlineFrame, limit: int = 20):
    return compute_kline_geometry_features(frame, limit=min(limit, len(frame.bars)))


def _trend_counts(frame: KlineFrame, limit: int = 20) -> tuple[int, int]:
    features = _recent_features(frame, limit)
    return (
        sum(item.bar_type == "trend_bull" for item in features),
        sum(item.bar_type == "trend_bear" for item in features),
    )


def build_cycle_features(frame: KlineFrame) -> dict[str, Any]:
    bars = list(frame.bars[:20])
    features = _recent_features(frame)
    overlaps = [item.overlap_prev_ratio for item in features if item.overlap_prev_ratio is not None]
    higher_highs = higher_lows = lower_highs = lower_lows = 0
    for newer, older in zip(bars, bars[1:]):
        higher_highs += newer.high > older.high
        higher_lows += newer.low > older.low
        lower_highs += newer.high < older.high
        lower_lows += newer.low < older.low
    bull, bear = _trend_counts(frame)
    from app.analysis.workflow.stage1.core.bar_identity import BarRange, assign_bar_refs
    refs = assign_bar_refs(bars, symbol=frame.symbol, timeframe=frame.timeframe)
    recent_range = BarRange(start=refs[-1], end=refs[0]).model_dump(mode="json") if refs else None
    return {
        "bar_count": len(frame.bars),
        "recent_bar_range": recent_range,
        "mean_overlap": _mean(overlaps),
        "bull_trend_bars": bull,
        "bear_trend_bars": bear,
        "swing_structure": {
            "higher_highs": higher_highs,
            "higher_lows": higher_lows,
            "lower_highs": lower_highs,
            "lower_lows": lower_lows,
        },
    }


def build_chaos_metrics(frame: KlineFrame) -> dict[str, Any]:
    features = _recent_features(frame)
    overlaps = [item.overlap_prev_ratio for item in features if item.overlap_prev_ratio is not None]
    mean_overlap = _mean(overlaps)
    high_overlap = mean_overlap is not None and mean_overlap >= CHAOS_OVERLAP_THRESHOLD
    ema = frame.indicators.ema20
    atr = frame.indicators.atr14
    lookback = min(10, len(ema) - 1)
    ema_slope_atr_ratio: float | None = None
    ema_flat = False
    slope_score = 0
    if lookback > 0 and math.isfinite(ema[0]) and math.isfinite(ema[lookback]):
        slope = ema[0] - ema[lookback]
        current_atr = atr[0] if atr and math.isfinite(atr[0]) and atr[0] > 0 else None
        if current_atr:
            ema_slope_atr_ratio = round(slope / current_atr, 4)
            ema_flat = abs(ema_slope_atr_ratio) < CHAOS_EMA_FLAT_ATR_RATIO
            slope_score = 1 if ema_slope_atr_ratio > CHAOS_EMA_FLAT_ATR_RATIO else -1 if ema_slope_atr_ratio < -CHAOS_EMA_FLAT_ATR_RATIO else 0
    bull, bear = _trend_counts(frame)
    trend_score = 1 if bull >= 1.5 * max(bear, 1) else -1 if bear >= 1.5 * max(bull, 1) else 0
    direction_score = trend_score + slope_score
    no_direction = abs(direction_score) <= CHAOS_DIRECTION_SCORE_MAX
    return {
        "ema_flat": ema_flat,
        "ema_slope_atr_ratio": ema_slope_atr_ratio,
        "mean_overlap": mean_overlap,
        "high_overlap": high_overlap,
        "direction_score": direction_score,
        "no_direction": no_direction,
        "chaos_score": int(ema_flat) + int(high_overlap) + int(no_direction),
    }


def build_momentum_metrics(frame: KlineFrame) -> dict[str, Any]:
    features = _recent_features(frame, 8)
    bull = sum(item.bar_type == "trend_bull" for item in features)
    bear = sum(item.bar_type == "trend_bear" for item in features)
    overlaps = [item.overlap_prev_ratio for item in features if item.overlap_prev_ratio is not None]
    return {
        "bar_count": len(features),
        "bull_trend_bars": bull,
        "bear_trend_bars": bear,
        "trend_bar_ratio": round((bull + bear) / len(features), 4) if features else 0.0,
        "mean_overlap": _mean(overlaps),
    }


def build_program_node_context(frame: KlineFrame) -> dict[str, Any]:
    direction, direction_fill = judge_direction(frame)
    return {
        "data_valid": build_program_trace_node(judge_data_sufficiency(frame), frame=frame),
        "direction": build_program_trace_node(direction_fill, frame=frame),
        "direction_value": direction,
        "always_in": build_program_trace_node(judge_always_in(frame), frame=frame),
        "cycle_features": build_cycle_features(frame),
        "chaos_metrics": build_chaos_metrics(frame),
        "momentum_metrics": build_momentum_metrics(frame),
    }
