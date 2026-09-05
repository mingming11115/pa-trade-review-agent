"""Post-analysis follow-up chat: pinned context + multi-turn messages.

The follow-up turn is orchestrated by a small LangGraph state machine with
three nodes (``prepare_turn`` → ``stream_reply`` → ``finalize``) plus a
conditional edge for the success / empty-reply / error routes.  This keeps
the flow declarative and makes it easy to add validation, retry, or guard
nodes later without touching the streaming plumbing.

Session messages are persisted to the database so that conversations survive
process restarts.  When a session is first requested, the store loads any
existing messages from ``followup_messages``; each new user turn and assistant
reply is written through to the DB as it is committed in-memory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, TypedDict

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, Text, asc, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, SessionFactory, ensure_schema
from app.core.errors import AppError
from app.llm.client import stream_chat
from app.core.models import Bar
from app.analysis.workflow.stage1.core.bar_identity import assign_bar_refs
from app.analysis.workflow.stage1.core.data.base import KlineBar, KlineFrame
from app.analysis.workflow.stage1.core.data.snapshot import compute_indicators
from app.analysis.workflow.stage1.core.kline_features import bar_candle_direction_label, compute_kline_geometry_features

logger = logging.getLogger(__name__)
UTC = timezone.utc

try:  # pragma: no cover - optional dependency
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - exercised through fallback path
    END = None  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment,misc]


FOLLOWUP_SYSTEM_PROMPT = (
    "你是 PA Agent 的【追问助手】（post-analysis advisor），不是在执行新的完整两阶段分析。\n"
    "你的目标是：优先、直接回答用户当前问题；必要时引用价格行为/关键价位/风险控制。\n"
    "\n"
    "严格规则：\n"
    "1) 默认用自然语言回答；除非用户明确要求 JSON/决策树，否则不要输出二元决策树 JSON。\n"
    "2) 如果用户问的是【已有仓位管理】（止损/止盈/减仓/持有/加仓）：\n"
    "   - 只围绕持仓管理回答，不要重新跑完整下单决策。\n"
    "   - 先给结论（可以/不建议/条件允许），再给依据（结构/关键位/信号），再给风险控制（最大亏损、触发条件）。\n"
    "3) 如果用户问题信息不足，最多问 1-2 个澄清点（例如仓位大小、入场价、止损距离）。\n"
    "4) 不要编造数据；以用户消息附带的「当前图表K线数据」为准（与发送追问时屏幕上冻结的图表一致）。\n"
    "5) K线棒型描述（上影线/下影线/实体大小/涨跌方向）必须以「最新已收盘棒·程序计算」字段中的数值为准，\n"
    "   禁止凭记忆或猜测描述棒型特征——程序计算的 upper_wick/lower_wick/body 是唯一可信来源。\n"
    "6) 你可以调用行情数据工具获取最新 K 线或检查数据采集状态：\n"
    "   - market.get_latest_bars：拉取指定品种最近 N 根已收盘 K 线（含 EMA20/ATR14）\n"
    "   - market.get_bars：按时间区间拉取 K 线\n"
    "   - market.get_bar_count：统计区间内 K 线根数与覆盖率（不传完整 OHLC）\n"
    "   - market.get_collection_status：检查行情采集器状态（最新闭盘时间、延迟、错误）\n"
    "   当用户追问的行情数据不在附带 K 线范围内、或需要更新数据时，请主动调用工具获取。\n"
)

# 追问时附带的最大 K 线根数，避免图表窗口过大导致 prompt 膨胀
_FOLLOWUP_KLINE_LIMIT = 40
# 超过多少轮对话后触发历史压缩
_DEFAULT_FOLLOWUP_COMPACT_AFTER_TURNS = 10
# 压缩时保留最近多少轮原始对话（不压缩）
_DEFAULT_FOLLOWUP_KEEP_RECENT_TURNS = 4

_PREFIX_ROLES = {"system", "user", "assistant"}


def _positive_env_int(name: str, default: int) -> int:
    """从环境变量读取正整数，非法值或非正数时回退到默认值。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _followup_compaction_settings() -> tuple[int, int]:
    """读取历史压缩配置（compact_after, keep_recent），配置不合理时回退默认值。"""
    compact_after = _positive_env_int(
        "FOLLOWUP_COMPACT_AFTER_TURNS",
        _DEFAULT_FOLLOWUP_COMPACT_AFTER_TURNS,
    )
    keep_recent = _positive_env_int(
        "FOLLOWUP_KEEP_RECENT_TURNS",
        _DEFAULT_FOLLOWUP_KEEP_RECENT_TURNS,
    )
    if keep_recent >= compact_after:
        return (
            _DEFAULT_FOLLOWUP_COMPACT_AFTER_TURNS,
            _DEFAULT_FOLLOWUP_KEEP_RECENT_TURNS,
        )
    return compact_after, keep_recent


def _split_followup_history(
    messages: list[dict[str, str]],
    *,
    compact_after_turns: int,
    keep_recent_turns: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]] | None:
    """将会话消息三分为 (前缀, 旧历史, 最近历史)，不满足压缩条件时返回 None。

    前缀 = system + 钉死上下文 + 决策摘要（前 3 条）；
    旧历史 = 需要被压缩为摘要的 user/assistant 对；
    最近历史 = 保留不压缩的最近 N 轮原始对话。
    """
    if len(messages) < 3:
        return None
    history = messages[3:]
    if len(history) % 2:
        return None
    for index in range(0, len(history), 2):
        if history[index].get("role") != "user" or history[index + 1].get("role") != "assistant":
            return None
    turn_count = len(history) // 2
    if turn_count <= compact_after_turns:
        return None
    recent_message_count = keep_recent_turns * 2
    return messages[:3], history[:-recent_message_count], history[-recent_message_count:]


class FollowupMessageRecord(Base):
    """追问消息持久化表：按 run_id + seq 存储完整对话。"""

    __tablename__ = "followup_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class FollowupMessagePublic(BaseModel):
    """对外返回的追问消息结构，隐藏数据库字段细节。"""

    id: str
    role: str
    content: str
    seq: int

    model_config = {"from_attributes": False}


class FollowupRequest(BaseModel):
    """单轮追问请求体：问题、可选 K 线和品种周期信息。"""

    question: str = Field(min_length=1, max_length=4000)
    bars: list[Bar] = Field(default_factory=list)
    symbol: str | None = Field(default=None, max_length=100)
    period: str | None = Field(default=None, max_length=20)


@dataclass
class FollowupSession:
    """追问会话的内存态对象，保存分析上下文和消息列表。"""

    run_id: str
    messages: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


async def load_followup_messages(run_id: str) -> list[dict[str, str]]:
    """从数据库加载指定分析的所有追问消息（按 seq 升序）。"""
    try:
        await ensure_schema()
        async with SessionFactory() as session:
            rows = (
                await session.scalars(
                    select(FollowupMessageRecord)
                    .where(FollowupMessageRecord.run_id == run_id)
                    .order_by(asc(FollowupMessageRecord.seq))
                )
            ).all()
        return [{"role": row.role, "content": row.content} for row in rows]
    except Exception:
        logger.exception("followup messages load failed run_id=%s", run_id)
        return []


async def append_followup_messages(run_id: str, messages: list[dict[str, str]]) -> None:
    """将追问消息追加写入数据库（自动计算 seq）。"""
    if not messages:
        return
    try:
        await ensure_schema()
        async with SessionFactory() as session:
            existing_count = (
                await session.scalar(
                    select(FollowupMessageRecord)
                    .where(FollowupMessageRecord.run_id == run_id)
                    .order_by(FollowupMessageRecord.seq.desc())
                    .limit(1)
                )
            )
            next_seq = (existing_count.seq + 1) if existing_count else 0
            for message in messages:
                session.add(FollowupMessageRecord(
                    run_id=run_id,
                    seq=next_seq,
                    role=str(message.get("role") or "user"),
                    content=str(message.get("content") or ""),
                ))
                next_seq += 1
            await session.commit()
    except Exception:
        logger.exception("followup messages append failed run_id=%s", run_id)


async def replace_followup_prefix(run_id: str, messages: list[dict[str, str]]) -> None:
    """替换并重写指定分析的全部存储消息（用于会话初始化）。"""
    try:
        await ensure_schema()
        from sqlalchemy import delete

        async with SessionFactory() as session:
            await session.execute(
                delete(FollowupMessageRecord).where(FollowupMessageRecord.run_id == run_id)
            )
            for seq, message in enumerate(messages):
                session.add(FollowupMessageRecord(
                    run_id=run_id,
                    seq=seq,
                    role=str(message.get("role") or "user"),
                    content=str(message.get("content") or ""),
                ))
            await session.commit()
    except Exception:
        logger.exception("followup messages replace failed run_id=%s", run_id)


async def list_followup_history(run_id: str) -> list[FollowupMessagePublic]:
    """列出指定分析的追问历史消息，跳过初始前缀中的非 user 消息。"""
    messages = await load_followup_messages(run_id)
    return [
        FollowupMessagePublic(
            id=f"fu-{run_id}-{index}",
            role=m["role"],
            content=m["content"],
            seq=index,
        )
        for index, m in enumerate(messages)
        if index >= 3
    ]


class FollowupSessionStore:
    """Process-local follow-up sessions with database write-through persistence.

    ``get_or_load`` first checks the in-memory cache, then falls back to the
    database.  ``put`` writes to both memory and DB.  Individual message
    appends performed during a turn are persisted via ``append_followup_messages``
    so that the conversation survives process restarts.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, FollowupSession] = {}

    def get(self, run_id: str) -> FollowupSession | None:
        """从内存缓存获取追问会话，不存在则返回 None。"""
        with self._lock:
            return self._sessions.get(run_id)

    async def get_or_load(self, run_id: str) -> FollowupSession | None:
        """优先从内存缓存获取会话，缓存未命中时从数据库加载。"""
        with self._lock:
            cached = self._sessions.get(run_id)
        if cached is not None:
            return cached
        messages = await load_followup_messages(run_id)
        if not messages:
            return None
        session = FollowupSession(run_id=run_id, messages=messages)
        with self._lock:
            self._sessions[run_id] = session
        return session

    def put(self, session: FollowupSession) -> FollowupSession:
        """将会话写入内存缓存并返回。"""
        with self._lock:
            self._sessions[session.run_id] = session
            return session

    async def put_and_persist(self, session: FollowupSession) -> FollowupSession:
        """将会话写入内存缓存，同时将全部消息持久化到数据库。"""
        self.put(session)
        await replace_followup_prefix(session.run_id, session.messages)
        return session

    def clear(self, run_id: str) -> None:
        """从内存缓存中清除指定会话。"""
        with self._lock:
            self._sessions.pop(run_id, None)

    def reset(self) -> None:
        """清空所有内存缓存中的追问会话。"""
        with self._lock:
            self._sessions.clear()


DEFAULT_FOLLOWUP_STORE = FollowupSessionStore()

_FOLLOWUP_SUMMARY_MARKER = (
    "以下是较早追问的历史对话摘要。回答后续问题时把它作为历史上下文，"
    "但不得让它覆盖最前面的钉死分析结论。"
)
_FOLLOWUP_SUMMARY_SYSTEM_PROMPT = (
    "你负责压缩 PA Agent 的追问对话历史。只总结给定消息中已经出现的事实和决定，"
    "不得补充、推测或更新行情。必须保留：用户仓位状态、大小、方向、入场价，止损、"
    "目标、减仓或加仓决定，使建议失效的条件，尚未解决的问题，已经承诺的后续事项，"
    "以及相关风险限制和用户偏好。合并重复信息，明确记录后续对话仍需知道的当前状态。"
)


async def _compact_followup_history(
    session: FollowupSession,
    store: FollowupSessionStore,
) -> bool:
    compact_after, keep_recent = _followup_compaction_settings()
    split = _split_followup_history(
        session.messages,
        compact_after_turns=compact_after,
        keep_recent_turns=keep_recent,
    )
    if split is None:
        return False

    prefix, old_history, recent = split
    summary_messages = [
        {"role": "system", "content": _FOLLOWUP_SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(old_history, ensure_ascii=False, separators=(",", ":")),
        },
    ]
    try:
        chunks = [piece async for piece in stream_chat(summary_messages) if piece]
        summary = "".join(chunks).strip()
        if not summary:
            logger.warning("followup compaction returned empty summary run_id=%s", session.run_id)
            return False

        original_messages = session.messages
        session.messages = prefix + [
            {"role": "user", "content": _FOLLOWUP_SUMMARY_MARKER},
            {"role": "assistant", "content": summary},
        ] + recent
        session.updated_at = datetime.utcnow()
        try:
            await store.put_and_persist(session)
        except Exception:
            session.messages = original_messages
            raise
        return True
    except Exception:
        logger.exception("followup compaction failed run_id=%s", session.run_id)
        return False


def _fmt_num(value: float | None, digits: int = 4) -> str:
    """格式化数值为字符串，None 或 NaN 返回 'N/A'。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value:.{digits}f}"


def build_decision_recall(stage1: dict[str, Any] | None, stage2: dict[str, Any] | None) -> str:
    """将阶段一和阶段二的关键结论拼成中文摘要，作为追问会话的首条 assistant 消息。"""
    s1 = stage1 or {}
    s2 = stage2 or {}
    decision = s2.get("decision") or {}
    terminal = s2.get("terminal") or {}

    direction = s1.get("direction") or "unknown"
    cycle = s1.get("cycle_position") or "—"
    gate = s1.get("gate_result") or "unknown"
    confidence = s1.get("confidence")
    if isinstance(confidence, (int, float)):
        conf_pct = round(confidence * 100) if confidence <= 1 else round(confidence)
        conf_text = f"{conf_pct}%"
    else:
        conf_text = "—"
    patterns = s1.get("detected_patterns") or []
    pattern_text = "、".join(str(p) for p in patterns) if patterns else "未识别"
    supports = s1.get("support_levels") or []
    resistances = s1.get("resistance_levels") or []

    outcome = terminal.get("outcome") or "—"
    reason = terminal.get("reason") or "—"
    order_type = decision.get("order_type") or "不下单"
    order_dir = decision.get("direction") or "—"
    entry = decision.get("entry_price")
    stop = decision.get("stop_loss_price")
    tp1 = decision.get("take_profit_price")
    tp2 = decision.get("take_profit_price_2")
    entry_reason = decision.get("entry_reason") or ""

    lines = [
        "【决策回忆摘要】",
        f"阶段一：方向={direction}，周期位置={cycle}，闸门={gate}，置信度={conf_text}，形态={pattern_text}",
        f"支撑：{('、'.join(str(x) for x in supports) if supports else '—')}",
        f"阻力：{('、'.join(str(x) for x in resistances) if resistances else '—')}",
        (
            f"阶段二：结果={outcome}，订单={order_type}，方向={order_dir}，"
            f"入场={entry if entry is not None else '—'}，"
            f"止损={stop if stop is not None else '—'}，"
            f"止盈={tp1 if tp1 is not None else '—'}，"
            f"第二目标={tp2 if tp2 is not None else '—'}"
        ),
        f"终止理由：{reason}",
    ]
    if entry_reason:
        lines.append(f"入场依据：{entry_reason}")
    return "\n".join(lines)


def build_analysis_context_payload(result: dict[str, Any]) -> dict[str, Any]:
    """从分析结果中提取并构建钉死的上下文 JSON（stage1/stage2 + 元数据）。"""
    return {
        "run_id": result.get("run_id"),
        "resolved_symbol": result.get("resolved_symbol"),
        "query": {
            "symbol": (result.get("query") or {}).get("symbol"),
            "period": (result.get("query") or {}).get("period"),
            "start": (result.get("query") or {}).get("start"),
            "end": (result.get("query") or {}).get("end"),
            "analysis_mode": (result.get("query") or {}).get("analysis_mode"),
        },
        "stage1": result.get("stage1"),
        "stage2": result.get("stage2"),
    }


def _bars_to_newest_first_frame(
    bars: list[Bar],
    *,
    symbol: str,
    period: str,
    limit: int = _FOLLOWUP_KLINE_LIMIT,
) -> KlineFrame | None:
    """将 Bar 列表转换为最新在前、含指标的 KlineFrame，截取指定数量。"""
    if not bars:
        return None
    ordered = sorted(bars, key=lambda bar: bar.timestamp, reverse=True)
    sliced = ordered[:limit]
    kline_bars = [
        KlineBar(
            seq=index + 1,
            ts_open=bar.timestamp.timestamp() * 1000,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=float(bar.volume or 0.0),
            closed=True,
        )
        for index, bar in enumerate(sliced)
    ]
    indicators = compute_indicators(kline_bars)
    return KlineFrame(
        symbol=symbol,
        timeframe=period,
        bars=tuple(kline_bars),
        indicators=indicators,
        snapshot_ts_local_ms=int(datetime.utcnow().timestamp() * 1000),
    )


def render_kline_block(
    bars: list[Bar],
    *,
    symbol: str,
    period: str,
) -> str:
    """渲染图表 K 线数据块（含身份信息和最新棒程序计算几何特征）为文本。"""
    frame = _bars_to_newest_first_frame(bars, symbol=symbol, period=period)
    if frame is None:
        return (
            "## 当前图表K线数据\n"
            "（本次追问未附带图表 K 线；请仅依据钉死的分析结果回答，并在需要棒型细节时向用户索取。）\n"
        )

    table_lines = [
        "bar_timestamp | timeframe | session | day_index | 开盘价 | 最高价 | 最低价 | 收盘价 | 阳阴 | 成交量 | EMA20 | ATR14",
        "--------------+-----------+---------+-----------+--------+--------+--------+--------+------+--------+-------+------",
    ]
    refs = assign_bar_refs(frame.bars, symbol=frame.symbol, timeframe=frame.timeframe)
    for index, bar in enumerate(frame.bars):
        ema = frame.indicators.ema20[index] if index < len(frame.indicators.ema20) else math.nan
        atr = frame.indicators.atr14[index] if index < len(frame.indicators.atr14) else math.nan
        ref = refs[index]
        table_lines.append(
            f"{ref.bar_timestamp.isoformat()} | {ref.timeframe} | {ref.session} | {ref.day_index} | "
            f"{bar.open:<9.4f} | {bar.high:<9.4f} | {bar.low:<9.4f} | {bar.close:<9.4f} | "
            f"{bar_candle_direction_label(bar):<4} | {bar.volume:<9.0f} | "
            f"{_fmt_num(None if math.isnan(ema) else ema):<10} | {_fmt_num(None if math.isnan(atr) else atr)}"
        )

    features = compute_kline_geometry_features(frame, limit=1)
    if features:
        feat = features[0]
        ref = refs[0]
        k1_block = (
            "## 最新已收盘棒·程序计算\n"
            f"bar_timestamp={ref.bar_timestamp.isoformat()}\n"
            f"timeframe={ref.timeframe}\n"
            f"session={ref.session}\n"
            f"day_index={ref.day_index}\n"
            f"bar_type={feat.bar_type}\n"
            f"body={_fmt_num(feat.body_ratio)}\n"
            f"upper_wick={_fmt_num(feat.upper_wick_ratio)}\n"
            f"lower_wick={_fmt_num(feat.lower_wick_ratio)}\n"
            f"close_position={_fmt_num(feat.close_position)}\n"
            f"range_atr={_fmt_num(feat.range_atr_ratio)}\n"
            f"ema_relation={feat.ema_relation}\n"
            f"direction={bar_candle_direction_label(frame.bars[0])}\n"
            f"OHLC={frame.bars[0].open:.4f}/{frame.bars[0].high:.4f}/"
            f"{frame.bars[0].low:.4f}/{frame.bars[0].close:.4f}\n"
        )
    else:
        k1_block = "## 最新已收盘棒·程序计算\n（不足一根已收盘 K 线，无法计算）\n"

    return (
        "## 当前图表K线数据\n"
        f"（与发送追问时屏幕上冻结的图表一致；身份=时间戳+周期+交易时段，展示日内开盘序号；共展示最近 {len(frame.bars)} 根）\n"
        f"品种:{symbol} 周期:{period}\n\n"
        + "\n".join(table_lines)
        + "\n\n"
        + k1_block
    )


def build_user_turn_content(
    question: str,
    bars: list[Bar],
    *,
    symbol: str,
    period: str,
) -> str:
    """构建用户追问消息内容：K 线数据块 + 用户问题。"""
    kline = render_kline_block(bars, symbol=symbol, period=period)
    return f"{kline}\n## 用户问题\n{question.strip()}\n"


def seed_followup_session(run_id: str, result: dict[str, Any]) -> FollowupSession:
    """根据分析结果初始化追问会话，设置 system prompt、钉死上下文和决策摘要。"""
    stage1 = result.get("stage1") if isinstance(result.get("stage1"), dict) else None
    stage2 = result.get("stage2") if isinstance(result.get("stage2"), dict) else None
    context = build_analysis_context_payload(result)
    messages = [
        {"role": "system", "content": FOLLOWUP_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "以下是本场分析的钉死结论（JSON）。后续追问请以此为上下文，"
                "不要重新执行完整两阶段分析。\n\n"
                + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            ),
        },
        {"role": "assistant", "content": build_decision_recall(stage1, stage2)},
    ]
    return FollowupSession(run_id=run_id, messages=messages)


async def ensure_followup_session(
    run_id: str,
    result: dict[str, Any],
    store: FollowupSessionStore | None = None,
) -> FollowupSession:
    """确保追问会话存在：优先从缓存/数据库获取，不存在则初始化并持久化。"""
    active_store = store or DEFAULT_FOLLOWUP_STORE
    existing = await active_store.get_or_load(run_id)
    if existing is not None:
        return existing
    session = seed_followup_session(run_id, result)
    return await active_store.put_and_persist(session)


class FollowupTurnState(TypedDict, total=False):
    run_id: str
    session: FollowupSession
    store: FollowupSessionStore
    user_content: str
    chunks: list[str]
    answer: str
    error: AppError | None
    route: str
    queue: asyncio.Queue[dict[str, Any] | None]


class FollowupGraph:
    """LangGraph-orchestrated single-turn follow-up workflow.

    Nodes:
      prepare_turn — append the user message to the session.
      stream_reply — stream LLM deltas into ``state['queue']``; on error,
                     roll back the user turn and set ``error``.
      finalize     — persist the assistant reply (or raise on empty answer).

    Conditional edges route on the ``route`` field:
      prepare_turn → ``stream``  = stream_reply
      prepare_turn → ``end``     = END (unused for now, reserved for guards)
      stream_reply → ``finalize`` = finalize
      stream_reply → ``end``       = END (error path)
    """

    def __init__(self) -> None:
        self._compiled = self._build_compiled_graph()

    def _build_compiled_graph(self):  # type: ignore[no-untyped-def]
        """构建并编译 LangGraph 状态图，返回编译后的图实例（无 langgraph 时返回 None）。"""
        if StateGraph is None:
            return None
        graph = StateGraph(FollowupTurnState)
        graph.add_node("prepare_turn", self._prepare_turn)
        graph.add_node("stream_reply", self._stream_reply)
        graph.add_node("finalize", self._finalize)
        graph.set_entry_point("prepare_turn")
        graph.add_conditional_edges("prepare_turn", self._route_prepare, {"stream": "stream_reply", "end": END})
        graph.add_conditional_edges("stream_reply", self._route_reply, {"finalize": "finalize", "end": END})
        graph.add_edge("finalize", END)
        return graph.compile()

    # -- node implementations ------------------------------------------------

    @staticmethod
    def _prepare_turn(state: FollowupTurnState) -> dict[str, Any]:
        """图节点：将用户消息追加到会话中，路由到 stream。"""
        session = state["session"]
        session.messages.append({"role": "user", "content": state["user_content"]})
        session.updated_at = datetime.utcnow()
        return {"session": session, "route": "stream"}

    @staticmethod
    async def _stream_reply(state: FollowupTurnState) -> dict[str, Any]:
        """图节点：流式调用 LLM 获取回复，将 delta 推入队列；出错时回滚用户消息。"""
        session = state["session"]
        queue = state.get("queue")
        run_id = state["run_id"]
        chunks: list[str] = []
        try:
            async for piece in stream_chat(session.messages):
                if not piece:
                    continue
                chunks.append(piece)
                if queue is not None:
                    await queue.put({"type": "delta", "content": piece, "run_id": run_id})
        except AppError as exc:
            if session.messages and session.messages[-1].get("role") == "user":
                session.messages.pop()
            return {"chunks": chunks, "error": exc, "route": "end"}
        return {"chunks": chunks, "error": None, "route": "finalize"}

    @staticmethod
    def _finalize(state: FollowupTurnState) -> dict[str, Any]:
        """图节点：将助手回复写入会话并持久化；空回复时抛出异常。"""
        session = state["session"]
        answer = "".join(state.get("chunks", [])).strip()
        if not answer:
            if session.messages and session.messages[-1].get("role") == "user":
                session.messages.pop()
            raise AppError("followup_empty_reply", "追问助手未返回内容", 502)
        session.messages.append({"role": "assistant", "content": answer})
        session.updated_at = datetime.utcnow()
        state["store"].put(session)
        return {"answer": answer, "route": "end"}

    # -- routing --------------------------------------------------------------

    @staticmethod
    def _route_prepare(state: FollowupTurnState) -> str:
        """条件边：prepare_turn 后的路由判断。"""
        return state.get("route", "stream")

    @staticmethod
    def _route_reply(state: FollowupTurnState) -> str:
        """条件边：stream_reply 后的路由判断（正常→finalize，出错→end）。"""
        return state.get("route", "finalize")

    # -- fallback (no langgraph) ---------------------------------------------

    async def _run_fallback(self, state: FollowupTurnState) -> FollowupTurnState:
        """无 LangGraph 时的降级执行路径：按顺序调用三个节点。"""
        result = self._prepare_turn(state)
        state.update(result)
        result = await self._stream_reply(state)
        state.update(result)
        if state.get("error") is not None:
            raise state["error"]
        result = self._finalize(state)
        state.update(result)
        return state

    async def run(self, state: FollowupTurnState) -> FollowupTurnState:
        """执行追问图：有 LangGraph 时走编译图，否则走降级路径。"""
        if self._compiled is None:
            return await self._run_fallback(state)
        result = await self._compiled.ainvoke(state, config={"recursion_limit": 16})
        error = result.get("error") if isinstance(result, dict) else None
        if error is not None:
            raise error
        return result  # type: ignore[return-value]


_DEFAULT_GRAPH = FollowupGraph()


async def stream_followup_turn(
    *,
    run_id: str,
    result: dict[str, Any],
    request: FollowupRequest,
    store: FollowupSessionStore | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """执行一轮追问对话：追加用户消息，流式返回助手回复，并持久化。

    通过 :class:`FollowupGraph` (LangGraph) 编排。``stream_reply`` 节点将
    ``delta`` 事件实时推入共享队列，调用方可在 LLM 输出到达时即时接收，
    图级别的 ``status`` / ``done`` / ``error`` 事件标记生命周期。
    """
    active_store = store or DEFAULT_FOLLOWUP_STORE
    question = request.question.strip()
    if not question:
        raise AppError("followup_empty_question", "追问内容不能为空", 422)

    query = result.get("query") or {}
    symbol = request.symbol or result.get("resolved_symbol") or query.get("symbol") or "UNKNOWN"
    period = request.period or query.get("period") or "1m"

    session = await ensure_followup_session(run_id, result, store=active_store)
    user_content = build_user_turn_content(question, request.bars, symbol=symbol, period=period)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def _run_graph() -> None:
        try:
            await queue.put({"type": "status", "message": "追问助手思考中…", "run_id": run_id})
            final = await _DEFAULT_GRAPH.run(FollowupTurnState(
                run_id=run_id,
                session=session,
                store=active_store,
                user_content=user_content,
                chunks=[],
                answer="",
                error=None,
                route="stream",
                queue=queue,
            ))
            await append_followup_messages(run_id, [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": final.get("answer", "")},
            ])
            turn_count = max(0, (len(session.messages) - 3) // 2)
            await _compact_followup_history(session, active_store)
            await queue.put({
                "type": "done",
                "message": "追问完成",
                "run_id": run_id,
                "content": final.get("answer", ""),
                "turn_count": turn_count,
            })
        except AppError as exc:
            await queue.put({"type": "error", "code": exc.code, "message": exc.message, "details": exc.details})
        except Exception:
            await queue.put({"type": "error", "code": "internal_error", "message": "追问失败"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(_run_graph())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
    finally:
        await task
