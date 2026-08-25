from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app.core.config import ROOT_DIR


PROMPT_DIR = ROOT_DIR / "backend" / "app" / "analysis" / "workflow" / "prompt_engineering"

COMMON_SYSTEM_STAGE1_TXT_FILES: tuple[str, ...] = (
    "提示词大纲_人设与思维方式.txt",
    "二元决策.txt",
)

STAGE1_TASK_PROMPT_TXT_FILES: tuple[str, ...] = (
    "市场诊断框架.txt",
    "文件16-K线信号识别.txt",
)


def stage1_prompt_txt_files() -> list[str]:
    """返回阶段一提示词所需的全部文件名列表（通用 + 任务）。"""
    return [*COMMON_SYSTEM_STAGE1_TXT_FILES, *STAGE1_TASK_PROMPT_TXT_FILES]


@lru_cache(maxsize=64)
def _read(name: str) -> str:
    """读取指定提示词文件内容并缓存，文件不存在时返回空字符串。"""
    path = PROMPT_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


@lru_cache(maxsize=1)
def stage1_system_prompt() -> str:
    """拼接阶段一通用的 system prompt（人设+思维方式+二元决策），并缓存。"""
    return "\n\n---\n\n".join(_read(name) for name in COMMON_SYSTEM_STAGE1_TXT_FILES if _read(name))


def _fmt(value: Any, digits: int = 4) -> str:
    """格式化数值为字符串，None 返回 'N/A'。"""
    return "N/A" if value is None else f"{float(value):.{digits}f}"


def _render_kline_table(rows: list[dict[str, Any]]) -> str:
    """将 K 线数据行渲染为 Markdown 表格字符串。"""
    lines = [
        "bar_timestamp | timeframe | session | day_index | 开盘价 | 最高价 | 最低价 | 收盘价 | 成交量 | EMA20 | ATR14",
        "--------------+-----------+---------+-----------+--------+--------+--------+--------+--------+-------+------",
    ]
    for row in rows:
        lines.append(
            f"{row['bar_timestamp']} | {row['timeframe']} | {row['session']} | {row['day_index']} | {_fmt(row['open'])} | {_fmt(row['high'])} | {_fmt(row['low'])} | "
            f"{_fmt(row['close'])} | {_fmt(row.get('volume'), 0)} | {_fmt(row.get('ema20'))} | {_fmt(row.get('atr14'))}"
        )
    return "\n".join(lines)


def _render_feature_table(rows: list[dict[str, Any]]) -> str:
    """将 K 线几何特征数据行渲染为 Markdown 表格字符串。"""
    lines = [
        "bar_timestamp | timeframe | session | day_index | 类型 | 实体比 | 上影比 | 下影比 | 收盘位置 | Range/ATR | EMA关系 | 重叠 | ii/iii | ioi | 微双 | 缺口 | EMA缺口数 | 近5突破 | 后续",
        "--------------+-----------+---------+-----------+------+--------+--------+--------+----------+-----------+---------+------+--------+-----+------+-----+-----------+---------+-----",
    ]
    for row in rows:
        lines.append(
            f"{row['bar_timestamp']} | {row['timeframe']} | {row['session']} | {row['day_index']} | {row['bar_type']} | {_fmt(row.get('body_ratio'), 3)} | {_fmt(row.get('upper_wick_ratio'), 3)} | "
            f"{_fmt(row.get('lower_wick_ratio'), 3)} | {_fmt(row.get('close_position'), 3)} | {_fmt(row.get('range_atr_ratio'), 3)} | "
            f"{row.get('ema_relation')} | {_fmt(row.get('overlap_prev_ratio'), 3)} | {row.get('inside_sequence')} | {row.get('ioi_pattern')} | "
            f"{row.get('micro_double')} | {row.get('gap_bar')} | {row.get('ema_gap_count')} | {row.get('breakout_prev')} | {row.get('follow_through_1_2')}"
        )
    return "\n".join(lines)


def build_stage1_user_prompt(payload: dict[str, Any]) -> str:
    """构建阶段一的 user prompt，包含 K 线数据、几何特征、程序预填充结果和输出契约。"""
    rows = payload.get("kline_data_newest_first", [])
    features = payload.get("indicators", {}).get("program_features", {})
    program_result = payload.get("program_result", {})
    n_bars = len(rows)
    task_context = "\n\n---\n\n".join(_read(name) for name in STAGE1_TASK_PROMPT_TXT_FILES if _read(name))
    return (
        "## 阶段一任务\n\n"
        "你现在只执行阶段一：市场诊断与闸门判断。不要评估具体下单、止损、止盈或仓位。\n\n"
        f"{task_context}\n\n---\n\n"
        f"## 当前分析目标\n\n品种:{payload.get('symbol')} 周期:{payload.get('period')} K线数量:{n_bars}\n"
        "（棒身份：bar_timestamp + timeframe + session；day_index 为实际交易时段开盘序号；bar_range 使用结构化 start/end）\n\n"
        f"## K线数据\n\n{_render_kline_table(rows)}\n\n"
        f"## K线几何特征（程序权威 bar_type）\n\n{_render_feature_table(rows)}\n\n"
        f"## 程序结构辅助特征\n\n```json\n{json.dumps(features, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 程序预填充节点与基础结果\n\n```json\n{json.dumps(program_result, ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 上一轮结构化上下文\n\n```json\n{json.dumps(payload.get('previous_context'), ensure_ascii=False, indent=2)}\n```\n\n"
        f"## 输出契约\n\n```json\n{json.dumps(payload.get('required_output'), ensure_ascii=False, indent=2)}\n```\n\n"
        "请严格输出阶段一 JSON；不得输出 Markdown、解释文字或阶段二交易方案。"
    )


@lru_cache(maxsize=8)
def stage2_system_prompt(cycle_position: str | None, direction: str | None) -> str:
    """根据周期位置和方向动态选择并拼接阶段二的 system prompt 文件。

    震荡区间会加载震荡策略文件；上涨方向加载上涨通道策略；下跌方向加载下跌通道策略。
    """
    files = ["提示词大纲_人设与思维方式.txt", "二元决策.txt", "逐棒分析检查单.txt", "文件16-K线信号识别.txt", "文件17-止损和止盈与仓位管理.txt", "文件23-MeasuredMove与结构目标.txt"]
    cycle = (cycle_position or "").lower()
    if "range" in cycle:
        files.extend(["震荡区间分析识别.txt", "震荡区间交易策略.txt"])
    elif direction == "bullish":
        files.extend(["上涨通道分析识别.txt", "上涨通道交易策略.txt"])
    elif direction == "bearish":
        files.extend(["下跌通道分析识别.txt", "下跌通道交易策略.txt"])
    return "\n\n---\n\n".join(_read(name) for name in files if _read(name))
