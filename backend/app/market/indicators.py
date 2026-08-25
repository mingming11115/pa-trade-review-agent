from __future__ import annotations

import math
from typing import Any

from app.core.models import Bar
from app.analysis.workflow.stage1.core.bar_identity import assign_timestamp_refs


def ema_full(values: list[float], period: int = 20) -> list[float | None]:
    """计算指数移动平均线（EMA）。

    使用前 ``period`` 个值的简单移动平均作为初始值，
    之后递推计算 EMA。长度不足时全部返回 None。

    Args:
        values: 收盘价等数值序列。
        period: EMA 周期，默认为 20。

    Returns:
        与 ``values`` 等长的列表，前 ``period - 1`` 个元素为 None。
    """
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    alpha = 2.0 / (period + 1)
    # 以前 period 个值的简单平均作为 EMA 种子值
    value = sum(values[:period]) / period
    result[period - 1] = value
    for index in range(period, len(values)):
        value = values[index] * alpha + value * (1 - alpha)
        result[index] = value
    return result


def atr_full(bars: list[Bar], period: int = 14) -> list[float | None]:
    """计算平均真实波幅（ATR）。

    先逐根 K 线计算真实波幅（True Range），再用 Wilder 平滑法递推。

    Args:
        bars: K 线序列。
        period: ATR 周期，默认为 14。

    Returns:
        与 ``bars`` 等长的列表，前 ``period - 1`` 个元素为 None。
    """
    result: list[float | None] = [None] * len(bars)
    if len(bars) < period:
        return result
    true_ranges = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else math.nan
        # 真实波幅 = max(当日振幅, 当日高与前收之差, 当日低与前收之差)
        true_ranges.append(
            bar.high - bar.low if math.isnan(previous_close) else max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    # 首个 ATR 取前 period 个 TR 的简单平均
    value = sum(true_ranges[:period]) / period
    result[period - 1] = value
    # Wilder 平滑：ATR = (前 ATR * (period - 1) + 当日 TR) / period
    for index in range(period, len(bars)):
        value = (value * (period - 1) + true_ranges[index]) / period
        result[index] = value
    return result


def classify_bar(bar: Bar, previous: Bar | None) -> str:
    """对单根 K 线进行形态分类。

    识别外包线、内包线、十字星、趋势 K 等类型。

    Args:
        bar: 当前 K 线。
        previous: 上一根 K 线（可为 None）。

    Returns:
        形态标签：flat / outside_bull / outside_bear / inside / doji /
        trend_bull / trend_bear / other。
    """
    price_range = bar.high - bar.low
    if price_range == 0:
        return "flat"
    # 外包线：高低点都超过前一根
    if previous and bar.high > previous.high and bar.low < previous.low:
        return "outside_bull" if bar.close >= bar.open else "outside_bear"
    # 内包线：高低点都被前一根包含
    if previous and bar.high <= previous.high and bar.low >= previous.low:
        return "inside"
    body_ratio = abs(bar.close - bar.open) / price_range
    if body_ratio <= 0.1:
        return "doji"
    if body_ratio >= 0.6:
        return "trend_bull" if bar.close > bar.open else "trend_bear"
    return "other"


def _overlap_ratio(current: Bar, previous: Bar) -> float:
    """计算两根 K 线区间的重叠比例。

    重叠区域越小说明价格波动越剧烈，越大说明走势越粘合。
    """
    overlap = max(0.0, min(current.high, previous.high) - max(current.low, previous.low))
    denominator = max(current.high, previous.high) - min(current.low, previous.low)
    return overlap / denominator if denominator > 0 else 0.0


def _is_inside(bar: Bar | None, older: Bar | None) -> bool:
    """判断 ``bar`` 是否为 ``older`` 的内包线。"""
    return bool(bar and older and bar.high <= older.high and bar.low >= older.low)


def _is_outside(bar: Bar | None, older: Bar | None) -> bool:
    """判断 ``bar`` 是否为 ``older`` 的外包线。"""
    return bool(bar and older and bar.high >= older.high and bar.low <= older.low)


def _find_swings(newest: list[Bar]) -> list[dict[str, Any]]:
    """识别摆动高点和摆动低点。

    遍历 K 线，若某根的高点同时高于左右相邻 K 线则记为摆动高点，
    低点同时低于左右相邻 K 线则记为摆动低点。

    Args:
        newest: 按从新到旧排列的 K 线列表。

    Returns:
        摆动点列表，每项含 ``seq``（序号）、``kind``（high/low）、``price``。
    """
    swings = []
    for index in range(1, len(newest) - 1):
        bar = newest[index]
        if bar.high > newest[index - 1].high and bar.high > newest[index + 1].high:
            swings.append({"seq": index + 1, "kind": "high", "price": round(bar.high, 6)})
        if bar.low < newest[index - 1].low and bar.low < newest[index + 1].low:
            swings.append({"seq": index + 1, "kind": "low", "price": round(bar.low, 6)})
    return swings


def _swing_structure(swings: list[dict[str, Any]]) -> str:
    """根据最近两个摆动高点和低点判断波段结构。

    Returns:
        ``HH+HL``（高点抬高 + 低点抬高 = 上升趋势）、
        ``LL+LH``（低点降低 + 高点降低 = 下降趋势）、
        ``mixed``（混合）、``insufficient``（数据不足）。
    """
    highs = [item for item in swings if item["kind"] == "high"]
    lows = [item for item in swings if item["kind"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    if highs[0]["price"] > highs[1]["price"] and lows[0]["price"] > lows[1]["price"]:
        return "HH+HL"
    if highs[0]["price"] < highs[1]["price"] and lows[0]["price"] < lows[1]["price"]:
        return "LL+LH"
    return "mixed"


def _hl_count(newest: list[Bar], atr: float | None) -> dict[str, Any]:
    """统计连续创新高 / 新低的 K 线数量。

    从最新 K 线向旧方向遍历，若某根高点突破前一根高点则牛市计数 +1，
    低点跌破前一根低点则熊市计数 +1。出现反向大实体 K 线时重置计数。

    Args:
        newest: 按从新到旧排列的 K 线列表。
        atr: 当前 ATR 值，用于确定重置阈值。

    Returns:
        含牛/熊计数、触发序号及候选标签的字典。
    """
    bull = bear = 0
    last_bull = last_bear = None
    reset_range = (atr or 0) * 1.2
    for older_index in range(len(newest) - 1, 0, -1):
        newer, older = newest[older_index - 1], newest[older_index]
        if newer.high > older.high:
            bull += 1
            last_bull = older_index
        elif reset_range and newer.close < older.low and newer.high - newer.low >= reset_range:
            # 出现强力反向阴线，重置牛市计数
            bull = 0
        if newer.low < older.low:
            bear += 1
            last_bear = older_index
        elif reset_range and newer.close > older.high and newer.high - newer.low >= reset_range:
            # 出现强力反向阳线，重置熊市计数
            bear = 0
    tag = lambda count, prefix: "none" if count <= 0 else f"{prefix}{min(count, 3)}"
    return {"bull_count": bull, "bear_count": bear, "last_bull_trigger_seq": last_bull, "last_bear_trigger_seq": last_bear, "bull_candidate": tag(bull, "h"), "bear_candidate": tag(bear, "l")}


def _breakout_events(newest: list[Bar], refs: list[Any], atr: float | None) -> list[dict[str, Any]]:
    """检测突破事件（向上 / 向下突破前期高低点）并跟踪回测。

    按时间顺序遍历 K 线，维护运行中的区间高低点。当收盘价突破
    区间高/低时记录突破事件；后续若收盘价跌回突破位则标记为失败，
    若回测到突破位附近则记录测试引用。

    Args:
        newest: 按从新到旧排列的 K 线列表。
        refs: 与 ``newest`` 对应的时间戳引用对象列表。
        atr: 当前 ATR 值，用于计算回测容差。

    Returns:
        按时间排序的突破事件列表（最多 6 条）。
    """
    chronological = list(reversed(list(enumerate(newest, start=1))))
    running_high = running_low = None
    events = []
    for seq, bar in chronological:
        if running_high is not None and bar.close > running_high:
            # 向上突破前期高点
            events.append({"event": "breakout", "direction": "up", "level_price": running_high, "trigger_ref": refs[seq - 1].model_dump(mode="json"), "bar_range": {"start": refs[seq - 1].model_dump(mode="json"), "end": refs[seq - 1].model_dump(mode="json")}})
        elif running_low is not None and bar.close < running_low:
            # 向下突破前期低点
            events.append({"event": "breakout", "direction": "down", "level_price": running_low, "trigger_ref": refs[seq - 1].model_dump(mode="json"), "bar_range": {"start": refs[seq - 1].model_dump(mode="json"), "end": refs[seq - 1].model_dump(mode="json")}})
        # 检查最近两根 K 线是否回测或跌破已有突破位
        tolerance = (atr or 0) * 0.15
        for event in events[-2:]:
            if event["event"] != "breakout":
                continue
            if event["direction"] == "up" and bar.close < event["level_price"]:
                event["event"] = "failed"
            elif event["direction"] == "up" and bar.low <= event["level_price"] + tolerance:
                event["test_ref"] = refs[seq - 1].model_dump(mode="json")
            elif event["direction"] == "down" and bar.close > event["level_price"]:
                event["event"] = "failed"
            elif event["direction"] == "down" and bar.high >= event["level_price"] - tolerance:
                event["test_ref"] = refs[seq - 1].model_dump(mode="json")
        running_high = bar.high if running_high is None else max(running_high, bar.high)
        running_low = bar.low if running_low is None else min(running_low, bar.low)
    return sorted(events, key=lambda item: item["trigger_ref"]["bar_timestamp"])[:6]


def build_market_indicators(
    source_bars: list[Bar], visible_bars: list[Bar], *, symbol: str, timeframe: str
) -> dict[str, Any]:
    """构建完整的市场指标体系。

    这是市场分析的核心入口函数，整合 EMA、ATR、K 线形态、波段结构、
    突破事件等维度的指标，输出逐根 K 线特征和程序级特征。

    Args:
        source_bars: 用于计算指标的全量 K 线（需包含足够历史数据）。
        visible_bars: 当前可见的 K 线窗口。
        symbol: 交易标的符号。
        timeframe: 时间周期。

    Returns:
        包含 ``ema20``、``atr14``、``per_bar``（逐根特征）和
        ``program_features``（程序级特征）的字典。
    """
    closes = [bar.close for bar in source_bars]
    ema20 = ema_full(closes, 20)
    atr14 = atr_full(source_bars, 14)
    # 按时间戳索引，将 EMA、ATR、K 线形态汇总到查找表
    by_timestamp = {
        bar.timestamp: (ema20[index], atr14[index], classify_bar(bar, source_bars[index - 1] if index else None))
        for index, bar in enumerate(source_bars)
    }
    # 取可见 K 线最近 100 根，按从新到旧排列
    newest = list(reversed(visible_bars[-100:]))
    refs = assign_timestamp_refs(
        [bar.timestamp for bar in newest], symbol=symbol, timeframe=timeframe
    )
    per_bar = []
    for seq, (bar, ref) in enumerate(zip(newest, refs), start=1):
        ema, atr, bar_type = by_timestamp[bar.timestamp]
        index = seq - 1
        # 获取连续的前几根 K 线用于形态判断
        older = newest[index + 1] if index + 1 < len(newest) else None
        older2 = newest[index + 2] if index + 2 < len(newest) else None
        older3 = newest[index + 3] if index + 3 < len(newest) else None
        price_range = bar.high - bar.low
        # 内包序列：连续 2 根或 3 根内包线
        inside_sequence = "iii" if _is_inside(bar, older) and _is_inside(older, older2) and _is_inside(older2, older3) else "ii" if _is_inside(bar, older) and _is_inside(older, older2) else "none"
        # IOI 模式：内-外-内
        ioi_pattern = _is_inside(older2, older3) and _is_outside(older, older2) and _is_inside(bar, older)
        # 微双底/微双顶：低点或高点在 ATR 容差范围内重合
        tolerance = (atr or 0) * 0.02
        micro_double = "MDB" if older and abs(bar.low - older.low) <= tolerance else "MDT" if older and abs(bar.high - older.high) <= tolerance else "none"
        # 缺口 K 线：整根 K 线在 EMA 之上或之下
        gap_bar = "bull_gap" if ema is not None and bar.low > ema else "bear_gap" if ema is not None and bar.high < ema else "none"
        # 相对前 5 根的高低点突破
        previous_five = newest[index + 1:index + 6]
        breakout_prev = "none"
        if previous_five:
            high_break = bar.high > max(item.high for item in previous_five)
            low_break = bar.low < min(item.low for item in previous_five)
            breakout_prev = "both" if high_break and low_break else "up" if high_break else "down" if low_break else "none"
        # 后续跟进：检查后续 1-2 根 K 线是否延续当前方向
        newer_bars = newest[max(0, index - 2):index]
        follow = "pending" if not newer_bars or bar.close == bar.open else "yes" if any(item.close > bar.close for item in newer_bars) and bar.close > bar.open or any(item.close < bar.close for item in newer_bars) and bar.close < bar.open else "failed" if any(item.close < bar.open for item in newer_bars) and bar.close > bar.open or any(item.close > bar.open for item in newer_bars) and bar.close < bar.open else "no"
        per_bar.append({
            "bar_timestamp": ref.bar_timestamp.isoformat(),
            "timeframe": ref.timeframe,
            "session": ref.session,
            "day_index": ref.day_index,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "ema20": round(ema, 6) if ema is not None else None,
            "atr14": round(atr, 6) if atr is not None else None,
            "bar_type": bar_type,
            "body_ratio": round(abs(bar.close - bar.open) / price_range, 4) if price_range else 0.0,
            "upper_wick_ratio": round((bar.high - max(bar.open, bar.close)) / price_range, 4) if price_range else 0.0,
            "lower_wick_ratio": round((min(bar.open, bar.close) - bar.low) / price_range, 4) if price_range else 0.0,
            "close_position": round((bar.close - bar.low) / price_range, 4) if price_range else 0.5,
            "range_atr_ratio": round(price_range / atr, 4) if atr else None,
            "ema_relation": "above" if ema is not None and bar.close > ema else "below" if ema is not None and bar.close < ema else "at_or_unknown",
            "overlap_prev_ratio": round(_overlap_ratio(bar, older), 4) if older else None,
            "inside_sequence": inside_sequence,
            "ioi_pattern": ioi_pattern,
            "micro_double": micro_double,
            "gap_bar": gap_bar,
            "ema_gap_count": 0,
            "breakout_prev": breakout_prev,
            "follow_through_1_2": follow,
        })
    # 统计连续 EMA 缺口的持续根数
    for index, item in enumerate(per_bar):
        side = item["gap_bar"]
        if side == "none":
            continue
        item["ema_gap_count"] = next((offset for offset, candidate in enumerate(per_bar[index:], start=0) if candidate["gap_bar"] != side), len(per_bar) - index)
    # ---- 程序级特征：基于 20 根 K 线窗口汇总 ----
    feature_window = visible_bars[-20:]
    current_atr = per_bar[0]["atr14"] if per_bar else None
    range_high = max((bar.high for bar in feature_window), default=None)
    range_low = min((bar.low for bar in feature_window), default=None)
    current_close = visible_bars[-1].close if visible_bars else None
    width = range_high - range_low if range_high is not None and range_low is not None else None
    overlaps = [_overlap_ratio(feature_window[index], feature_window[index - 1]) for index in range(1, len(feature_window))]
    recent_types = [item["bar_type"] for item in per_bar[:10]]
    current_ema = per_bar[0]["ema20"] if per_bar else None
    older_ema = per_bar[min(9, len(per_bar) - 1)]["ema20"] if per_bar else None
    swings = _find_swings(newest[:40])
    swing_structure = _swing_structure(swings)
    hl_count = _hl_count(newest[:40], current_atr)
    # 支撑位 = 当前价下方最近的摆动低点；阻力位 = 当前价上方最近的摆动高点
    supports = sorted({item["price"] for item in swings if item["kind"] == "low" and current_close is not None and item["price"] < current_close}, reverse=True)[:3]
    resistances = sorted({item["price"] for item in swings if item["kind"] == "high" and current_close is not None and item["price"] > current_close})[:3]
    # 测量移动：以区间高度等距投射目标价
    height = width if width and width > 0 else None
    feature_refs = refs[:len(feature_window)]
    feature_range = ({"start": feature_refs[-1].model_dump(mode="json"), "end": feature_refs[0].model_dump(mode="json")} if feature_refs else None)
    measured_moves = ([{"kind": "range_up", "height": round(height, 6), "target_price": round(range_high + height, 6), "bar_range": feature_range}, {"kind": "range_down", "height": round(height, 6), "target_price": round(range_low - height, 6), "bar_range": feature_range}] if height else [])
    # 回撤深度：当前价相对最近摆动点的回撤幅度（ATR 倍数）
    recent_swing = swings[0] if swings else None
    pullback_depth_atr = None
    pullback_bars = None
    if recent_swing and current_close is not None and current_atr:
        depth = recent_swing["price"] - current_close if recent_swing["kind"] == "high" else current_close - recent_swing["price"]
        pullback_depth_atr = round(max(0.0, depth) / current_atr, 4)
        pullback_bars = max(0, recent_swing["seq"] - 1)
    overlap_mean = round(sum(overlaps[-9:]) / len(overlaps[-9:]), 4) if overlaps else None
    doji_inside_ratio = round(sum(item in {"doji", "inside"} for item in recent_types) / len(recent_types), 4) if recent_types else None
    range_width_atr = round(width / current_atr, 4) if width is not None and current_atr else None
    # 铁丝网评分：重叠率高 + 十字/内包多 + 区间窄 => 市场胶着
    barbwire_score = min(1.0, (0.4 if overlap_mean is not None and overlap_mean >= 0.65 else 0) + (0.2 if doji_inside_ratio is not None and doji_inside_ratio >= 0.4 else 0) + (0.2 if range_width_atr is not None and range_width_atr <= 3 else 0))
    trend_bull = sum(item["bar_type"] == "trend_bull" for item in per_bar[:8])
    trend_bear = sum(item["bar_type"] == "trend_bear" for item in per_bar[:8])
    # 方向评分：综合 EMA 斜率、价格与 EMA 关系、波段结构、趋势 K 数量
    direction_score = (1 if current_ema is not None and older_ema is not None and current_ema > older_ema else -1 if current_ema is not None and older_ema is not None and current_ema < older_ema else 0) + (1 if current_ema is not None and current_close > current_ema else -1 if current_ema is not None and current_close < current_ema else 0) + (1 if swing_structure == "HH+HL" else -1 if swing_structure == "LL+LH" else 0) + (1 if trend_bull > trend_bear * 1.5 else -1 if trend_bear > trend_bull * 1.5 else 0)
    return {
        "ema20": [item["ema20"] for item in per_bar],
        "atr14": [item["atr14"] for item in per_bar],
        "per_bar": per_bar,
        "program_features": {
            "lookback_bars": len(feature_window),
            "range_high": range_high,
            "range_low": range_low,
            "range_width_atr": range_width_atr,
            # 价格在区间中的相对位置（0=最低, 1=最高）
            "price_position": round((current_close - range_low) / width, 4) if current_close is not None and width else None,
            # 区间分区：下 1/3 / 中 1/3 / 上 1/3
            "zone": "lower_third" if current_close is not None and width and (current_close - range_low) / width < 1 / 3 else "upper_third" if current_close is not None and width and (current_close - range_low) / width > 2 / 3 else "middle_third",
            "dist_to_high_atr": round((range_high - current_close) / current_atr, 4) if current_atr and current_close is not None else None,
            "dist_to_low_atr": round((current_close - range_low) / current_atr, 4) if current_atr and current_close is not None else None,
            "overlap_mean_10": overlap_mean,
            "doji_inside_ratio_10": doji_inside_ratio,
            "barbwire_score": barbwire_score,
            "barbwire_candidate": barbwire_score >= 0.6,
            "ema20_current": current_ema,
            "ema20_slope_10": round(current_ema - older_ema, 6) if current_ema is not None and older_ema is not None else None,
            "atr14_current": current_atr,
            "swing_structure": swing_structure,
            "swings": [
                {**{k: v for k, v in swing.items() if k != "seq"}, "bar_ref": refs[swing["seq"] - 1].model_dump(mode="json")}
                for swing in swings[:8]
            ],
            "pullback_depth_atr": pullback_depth_atr,
            "pullback_bars": pullback_bars,
            "hl_count": {
                **{k: v for k, v in hl_count.items() if not k.endswith("_seq")},
                "last_bull_trigger_ref": refs[hl_count["last_bull_trigger_seq"] - 1].model_dump(mode="json") if hl_count["last_bull_trigger_seq"] else None,
                "last_bear_trigger_ref": refs[hl_count["last_bear_trigger_seq"] - 1].model_dump(mode="json") if hl_count["last_bear_trigger_seq"] else None,
            },
            "breakout_events": _breakout_events(newest[:40], refs[:40], current_atr),
            "supports": supports,
            "resistances": resistances,
            # 做多失效位 = 最近支撑；做空失效位 = 最近阻力
            "invalidation_long": supports[0] if supports else None,
            "invalidation_short": resistances[0] if resistances else None,
            "measured_moves": measured_moves,
            # 程序门控：数据充分性、方向判断、Always-In 状态
            "program_gate": {
                "data_sufficiency": {"answer": "是" if len(visible_bars) >= 20 and current_ema is not None and current_atr is not None else "否", "bar_count": len(visible_bars)},
                "program_direction": {"answer": "是" if abs(direction_score) >= 3 else "中性", "direction": "bullish" if direction_score >= 3 else "bearish" if direction_score <= -3 else "neutral", "score": direction_score},
                "always_in": {"answer": "是" if abs(direction_score) >= 3 and overlap_mean is not None and overlap_mean < 0.65 else "中性", "always_in": "long" if direction_score >= 3 else "short" if direction_score <= -3 else "neutral"},
            },
        },
    }
