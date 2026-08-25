"""Semantic node_id registry for gate_trace / decision_trace / terminal.

Chapter-style IDs (1.2, 9.0P, 14…) are legacy aliases only. Canonical IDs are
snake_case capability names defined here.
"""

from __future__ import annotations

from typing import Any

# ── Stage1 ──────────────────────────────────────────────────────────────────
DATA_SUFFICIENCY = "data_sufficiency"
CYCLE_IDENTIFIABLE = "cycle_identifiable"
NOT_EXTREME_CHAOS = "not_extreme_chaos"
DIRECTION_DECIDABLE = "direction_decidable"
BACKGROUND_NEAR_TERM_COHERENT = "background_near_term_coherent"
PROGRAM_DIRECTION = "program_direction"
ALWAYS_IN = "always_in"
MOMENTUM_ENOUGH = "momentum_enough"

# ── Stage2 structure branches (occasional in decision_trace) ────────────────
TRADER_EQUATION_PRINCIPLE = "trader_equation_principle"  # forbidden in stage1
SPIKE_PATH = "spike_path"
SWING_STRUCTURE = "swing_structure"
CHANNEL_DIRECTION = "channel_direction"
RANGE_TYPE = "range_type"
AT_RANGE_BOUNDARY = "at_range_boundary"
WEDGE_TYPE = "wedge_type"

# ── Stage2 signal / execution ───────────────────────────────────────────────
SIGNAL_BAR_QUALITY = "signal_bar_quality"
PLANNED_LIMIT = "planned_limit"
SIGNAL_BAR_CLOSED = "signal_bar_closed"
SIGNAL_DIRECTION_OK = "signal_direction_ok"
SIGNAL_NOT_OVERLONG = "signal_not_overlong"
SIGNAL_FIRST_ENTRY = "signal_first_entry"
FOLLOW_THROUGH = "follow_through"
SIGNAL_SECOND_ENTRY = "signal_second_entry"
ENTRY_BAR_STRONG = "entry_bar_strong"

# ── Stage2 risk ─────────────────────────────────────────────────────────────
STOP_DEFINED = "stop_defined"
STOP_NOT_EXCESSIVE = "stop_not_excessive"
TRADER_EQUATION = "trader_equation"

# ── Stage2 order routing ────────────────────────────────────────────────────
ORDER_MARKET = "order_market"
ORDER_BREAKOUT = "order_breakout"
ORDER_LIMIT = "order_limit"
ORDER_BREAKOUT_ENTRY = "order_breakout_entry"

# ── Stage2 prohibition ──────────────────────────────────────────────────────
PROHIBITION_SCAN = "prohibition_scan"

# ── Ordered walks ───────────────────────────────────────────────────────────
STAGE1_ORDER: tuple[str, ...] = (
    DATA_SUFFICIENCY,
    CYCLE_IDENTIFIABLE,
    NOT_EXTREME_CHAOS,
    DIRECTION_DECIDABLE,
    BACKGROUND_NEAR_TERM_COHERENT,
    PROGRAM_DIRECTION,
    ALWAYS_IN,
    MOMENTUM_ENOUGH,
)

STAGE1_AI_ORDER: tuple[str, ...] = (
    CYCLE_IDENTIFIABLE,
    NOT_EXTREME_CHAOS,
    DIRECTION_DECIDABLE,
    BACKGROUND_NEAR_TERM_COHERENT,
    MOMENTUM_ENOUGH,
)

STAGE1_PROGRAM_IDS: frozenset[str] = frozenset(
    {DATA_SUFFICIENCY, PROGRAM_DIRECTION, ALWAYS_IN}
)

STAGE2_SIGNAL_ORDER: tuple[str, ...] = (
    SIGNAL_BAR_QUALITY,
    PLANNED_LIMIT,
    SIGNAL_BAR_CLOSED,
    SIGNAL_DIRECTION_OK,
    SIGNAL_NOT_OVERLONG,
    SIGNAL_FIRST_ENTRY,
    FOLLOW_THROUGH,
    SIGNAL_SECOND_ENTRY,
    ENTRY_BAR_STRONG,
)

STAGE2_RISK_ORDER: tuple[str, ...] = (
    STOP_DEFINED,
    STOP_NOT_EXCESSIVE,
    TRADER_EQUATION,
)

STAGE2_ORDER_ROUTE_IDS: tuple[str, ...] = (
    ORDER_MARKET,
    ORDER_BREAKOUT,
    ORDER_LIMIT,
    ORDER_BREAKOUT_ENTRY,
)

# Full stage2 walk rank (structure → signal → risk → order → prohibition)
STAGE2_ORDER: tuple[str, ...] = (
    SPIKE_PATH,
    SWING_STRUCTURE,
    CHANNEL_DIRECTION,
    RANGE_TYPE,
    AT_RANGE_BOUNDARY,
    WEDGE_TYPE,
    *STAGE2_SIGNAL_ORDER,
    *STAGE2_RISK_ORDER,
    *STAGE2_ORDER_ROUTE_IDS,
    PROHIBITION_SCAN,
)

_ORDER_RANK: dict[str, int] = {
    nid: index for index, nid in enumerate((*STAGE1_ORDER, *STAGE2_ORDER))
}

LEGACY_TO_SEMANTIC: dict[str, str] = {
    "0.3": TRADER_EQUATION_PRINCIPLE,
    "1.1": DATA_SUFFICIENCY,
    "1.2": CYCLE_IDENTIFIABLE,
    "1.3": NOT_EXTREME_CHAOS,
    "2.1": DIRECTION_DECIDABLE,
    "2.2": BACKGROUND_NEAR_TERM_COHERENT,
    "2.3": PROGRAM_DIRECTION,
    "2.4": ALWAYS_IN,
    "2.4.0": ALWAYS_IN,
    "2.5": MOMENTUM_ENOUGH,
    "3.5": SPIKE_PATH,
    "4.1": SWING_STRUCTURE,
    "4.2": CHANNEL_DIRECTION,
    "6.2": RANGE_TYPE,
    "6.3": AT_RANGE_BOUNDARY,
    "8.2": WEDGE_TYPE,
    "9.0": SIGNAL_BAR_QUALITY,
    "9.0P": PLANNED_LIMIT,
    "9.1": SIGNAL_BAR_CLOSED,
    "9.2": SIGNAL_DIRECTION_OK,
    "9.3": SIGNAL_NOT_OVERLONG,
    "9.4": SIGNAL_FIRST_ENTRY,
    "9.5": FOLLOW_THROUGH,
    "9.6": SIGNAL_SECOND_ENTRY,
    "9.7": ENTRY_BAR_STRONG,
    "10.1": STOP_DEFINED,
    "10.2": STOP_NOT_EXCESSIVE,
    "10.3": TRADER_EQUATION,
    "11.1": ORDER_MARKET,
    "11.2": ORDER_BREAKOUT,
    "11.3": ORDER_LIMIT,
    "11.4": ORDER_BREAKOUT_ENTRY,
    "14": PROHIBITION_SCAN,
    "14.1": PROHIBITION_SCAN,
}

SEMANTIC_IDS: frozenset[str] = frozenset(LEGACY_TO_SEMANTIC.values())

STAGE2_SIGNAL_IDS: frozenset[str] = frozenset(STAGE2_SIGNAL_ORDER)
STAGE2_RISK_IDS: frozenset[str] = frozenset(STAGE2_RISK_ORDER)
STAGE2_ORDER_IDS: frozenset[str] = frozenset(STAGE2_ORDER_ROUTE_IDS)

STAGE1_FORBIDDEN_GATE_NODES: frozenset[str] = frozenset({TRADER_EQUATION_PRINCIPLE})

OVERRIDE_TRACE_NODES: frozenset[str] = frozenset({CYCLE_IDENTIFIABLE, PROGRAM_DIRECTION})

ORDER_SECTION_9_REQUIRED: frozenset[str] = frozenset(STAGE2_SIGNAL_ORDER)

HUMAN_LABELS: dict[str, str] = {
    DATA_SUFFICIENCY: "数据充足度",
    CYCLE_IDENTIFIABLE: "周期可识别",
    NOT_EXTREME_CHAOS: "非极端混乱",
    DIRECTION_DECIDABLE: "方向可判断",
    BACKGROUND_NEAR_TERM_COHERENT: "长短背景协调",
    PROGRAM_DIRECTION: "程序方向",
    ALWAYS_IN: "Always-In",
    MOMENTUM_ENOUGH: "惯性强度",
    SIGNAL_BAR_QUALITY: "信号棒质量",
    PLANNED_LIMIT: "计划型限价",
    SIGNAL_BAR_CLOSED: "信号棒已收盘",
    SIGNAL_DIRECTION_OK: "信号方向一致",
    SIGNAL_NOT_OVERLONG: "信号棒长度",
    SIGNAL_FIRST_ENTRY: "首次入场",
    FOLLOW_THROUGH: "跟随",
    SIGNAL_SECOND_ENTRY: "二次入场",
    ENTRY_BAR_STRONG: "入场棒强度",
    STOP_DEFINED: "止损明确",
    STOP_NOT_EXCESSIVE: "止损不过大",
    TRADER_EQUATION: "交易者方程",
    ORDER_MARKET: "市价路由",
    ORDER_BREAKOUT: "突破路由",
    ORDER_LIMIT: "限价路由",
    ORDER_BREAKOUT_ENTRY: "突破交易路由",
    PROHIBITION_SCAN: "禁止行为扫描",
    SPIKE_PATH: "尖峰路径",
    SWING_STRUCTURE: "波段结构",
    CHANNEL_DIRECTION: "通道方向",
    RANGE_TYPE: "区间类型",
    AT_RANGE_BOUNDARY: "区间边界",
    WEDGE_TYPE: "楔形类型",
    TRADER_EQUATION_PRINCIPLE: "交易者方程原则",
}


def canonicalize_node_id(raw: Any) -> str:
    """Map legacy chapter IDs (and aliases) to the semantic canonical form."""
    text = str(raw or "").strip()
    if not text:
        return text
    text = text.lstrip("§")
    if text in LEGACY_TO_SEMANTIC:
        return LEGACY_TO_SEMANTIC[text]
    # Already semantic, or unknown custom id — pass through.
    return text


def is_stage2_signal_node(node_id: str) -> bool:
    return canonicalize_node_id(node_id) in STAGE2_SIGNAL_IDS


def is_order_route_node(node_id: str) -> bool:
    return canonicalize_node_id(node_id) in STAGE2_ORDER_IDS


def is_prohibition_node(node_id: str) -> bool:
    return canonicalize_node_id(node_id) == PROHIBITION_SCAN


def is_risk_node(node_id: str) -> bool:
    return canonicalize_node_id(node_id) in STAGE2_RISK_IDS


def node_order_key(node_id: str) -> tuple[int, str]:
    """Stable sort key for gate/decision traces (replaces numeric chapter sort)."""
    canonical = canonicalize_node_id(node_id)
    rank = _ORDER_RANK.get(canonical)
    if rank is None:
        return (10_000, canonical)
    return (rank, canonical)


def human_label(node_id: str) -> str:
    canonical = canonicalize_node_id(node_id)
    return HUMAN_LABELS.get(canonical, canonical)


def _remap_trace_list(items: Any) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        if "node_id" in item:
            item["node_id"] = canonicalize_node_id(item.get("node_id"))


def remap_trace_payload(obj: Any) -> Any:
    """Rewrite chapter-style node_ids in analysis payloads (mutates dicts in place)."""
    if not isinstance(obj, dict):
        return obj

    _remap_trace_list(obj.get("gate_trace"))
    _remap_trace_list(obj.get("decision_trace"))
    _remap_trace_list(obj.get("node_overrides"))
    _remap_trace_list(obj.get("override_requests"))

    terminal = obj.get("terminal")
    if isinstance(terminal, dict) and "node_id" in terminal:
        terminal["node_id"] = canonicalize_node_id(terminal.get("node_id"))
    if "terminal_node" in obj:
        obj["terminal_node"] = canonicalize_node_id(obj.get("terminal_node"))

    stage1 = obj.get("stage1")
    if isinstance(stage1, dict):
        remap_trace_payload(stage1)

    stage2 = obj.get("stage2")
    if isinstance(stage2, dict):
        remap_trace_payload(stage2)

    decision = obj.get("decision")
    if isinstance(decision, dict):
        # some payloads nest traces under decision
        _remap_trace_list(decision.get("decision_trace"))

    return obj
