"""Stage2 在 LLM 前使用的确定性可交易性计算。"""
from __future__ import annotations

import math
from typing import Any

from app.analysis.contracts import Stage1Result
from app.analysis.workflow.stage1.core.data.base import KlineFrame

MIN_CLOSED_BARS = 35


def _current_atr(frame: KlineFrame) -> float | None:
    for value in frame.indicators.atr14:
        if math.isfinite(value) and value > 0:
            return float(value)
    return None


def build_raw_tradeability(frame: KlineFrame) -> dict[str, Any]:
    """检查不依赖 Stage1 语义的行情硬条件。"""
    closed = [bar for bar in frame.bars if bar.closed]
    atr = _current_atr(frame)
    prices = [value for bar in closed[:20] for value in (bar.high, bar.low)]
    price_span = max(prices) - min(prices) if prices else 0.0
    passed = len(closed) >= MIN_CLOSED_BARS and atr is not None and price_span > 0
    if len(closed) < MIN_CLOSED_BARS:
        reason = f"已收盘 K 线不足 {MIN_CLOSED_BARS} 根，无法稳定预热 EMA20"
    elif atr is None:
        reason = "ATR 无效或市场没有有效波动"
    elif price_span <= 0:
        reason = "近期价格没有可交易波动空间"
    else:
        reason = "原始行情满足语义分析前的硬条件"
    return {
        "passed": passed,
        "reason": reason,
        "closed_bar_count": len(closed),
        "atr": atr,
        "price_span": round(price_span, 8),
    }


def build_signal_precheck(frame: KlineFrame, stage1: Stage1Result) -> dict[str, Any]:
    """计算符合 Stage1 方向的信号棒候选及计划型限价机会。"""
    direction = stage1.direction.value if stage1.direction else "neutral"
    candidates: list[dict[str, Any]] = []
    for bar in [item for item in frame.bars if item.closed][:8]:
        bar_range = bar.high - bar.low
        if bar_range <= 0:
            continue
        bar_direction = "bullish" if bar.close > bar.open else "bearish" if bar.close < bar.open else "neutral"
        direction_matches = direction == "neutral" or bar_direction == direction
        if direction_matches and abs(bar.close - bar.open) / bar_range >= 0.2:
            candidates.append(
                {
                    "seq": bar.seq,
                    "direction": bar_direction,
                    "body_ratio": round(abs(bar.close - bar.open) / bar_range, 4),
                    "closed": True,
                }
            )
    planned_limit = bool(stage1.support_levels or stage1.resistance_levels)
    passed = bool(candidates) or planned_limit
    return {
        "passed": passed,
        "reason": "存在信号棒或计划型限价候选" if passed else "没有方向一致的信号棒或计划型限价机会",
        "direction": direction,
        "signal_candidates": candidates,
        "planned_limit_available": planned_limit,
    }


def build_risk_precheck(
    frame: KlineFrame,
    stage1: Stage1Result,
    signal: dict[str, Any],
) -> dict[str, Any]:
    """计算具体方案生成前可用的止损锚点和理论价格空间。"""
    atr = _current_atr(frame)
    closed = [bar for bar in frame.bars if bar.closed][:20]
    recent_low = min((bar.low for bar in closed), default=None)
    recent_high = max((bar.high for bar in closed), default=None)
    stop_anchors = sorted(set([*stage1.support_levels, *stage1.resistance_levels]))
    if recent_low is not None:
        stop_anchors.append(recent_low)
    if recent_high is not None:
        stop_anchors.append(recent_high)
    stop_anchors = sorted(set(stop_anchors))
    theoretical_space = (recent_high - recent_low) if recent_high is not None and recent_low is not None else 0.0
    passed = bool(signal.get("passed")) and atr is not None and bool(stop_anchors) and theoretical_space >= atr
    return {
        "passed": passed,
        "reason": "存在止损锚点和最低风险收益空间" if passed else "缺少止损锚点或最低风险收益空间",
        "atr": atr,
        "theoretical_space": round(theoretical_space, 8),
        "stop_anchors": stop_anchors,
        "target_levels": sorted(set([*stage1.support_levels, *stage1.resistance_levels, *stop_anchors])),
    }


def build_order_method_precheck(
    stage1: Stage1Result,
    signal: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, Any]:
    """根据前置证据计算允许交给 LLM 选择的下单方式集合。"""
    allowed: list[str] = []
    if risk.get("passed") and signal.get("signal_candidates"):
        allowed.extend(["突破单", "市价单"])
    if risk.get("passed") and signal.get("planned_limit_available"):
        allowed.append("限价单")
    allowed = list(dict.fromkeys(allowed))
    return {
        "passed": bool(allowed),
        "reason": "存在可用下单方式" if allowed else "没有同时满足信号与风控的下单方式",
        "cycle_position": stage1.cycle_position,
        "allowed_order_types": allowed,
    }
