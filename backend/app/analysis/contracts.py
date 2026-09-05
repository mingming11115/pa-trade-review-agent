from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.models import Bar, Direction, HistoricalQuery
from app.analysis.workflow.stage1.core.bar_identity import BarRange


# 分析模式：交易复盘 / 历史分析 / 实时分析
class AnalysisMode(str, Enum):
    trade_review = "trade_review"
    historical = "historical"
    realtime = "realtime"


# 触发类型：手动 / K线收盘 / 结构变化 / 定时
class TriggerType(str, Enum):
    manual = "manual"
    bar_closed = "bar_closed"
    structure_changed = "structure_changed"
    periodic = "periodic"


# 分析触发事件，记录触发类型与发生时间
class AnalysisTrigger(BaseModel):
    type: TriggerType = TriggerType.manual
    occurred_at: datetime


# 市场快照：品种、合约、周期、K线、指标、最小变动价位
class MarketSnapshot(BaseModel):
    symbol: str
    contract: str
    period: str
    bars: list[Bar]
    indicators: dict[str, Any] = Field(default_factory=dict)
    tick_size: float | None = None


# 交易快照：交易ID、品种、进出场时间、方向、进出场价格、仓位、已报告盈亏
class TradeSnapshot(BaseModel):
    trade_id: UUID | str
    symbol: str
    entered_at: datetime
    exited_at: datetime
    direction: Literal["long", "short"]
    entry_price: float
    exit_price: float
    size: float
    reported_pnl: float | None = None


# 上一轮分析上下文：stage1/stage2 结果摘要，以及距上轮的K线数量
class PreviousContext(BaseModel):
    stage1: dict[str, Any] | None = None
    stage2: dict[str, Any] | None = None
    bars_since_previous: int = 0


# 完整分析快照：分析ID、模式、触发、查询、市场、交易列表、上一轮上下文、生成时间
class AnalysisSnapshot(BaseModel):
    run_id: UUID
    mode: AnalysisMode
    trigger: AnalysisTrigger
    query: HistoricalQuery
    market: MarketSnapshot
    trades: list[TradeSnapshot] = Field(default_factory=list)
    previous_context: PreviousContext | None = None
    generated_at: datetime


# 预检结果：是否通过、失败类型与原因、已收盘K线数量
class PrecheckResult(BaseModel):
    passed: bool
    failure_type: str | None = None
    reason: str | None = None
    closed_bar_count: int


# 闸门追踪项：节点ID、问题、答案（是/否/中性/等待/不适用）、原因、K线范围、来源（程序/AI）
class GateTraceItem(BaseModel):
    node_id: str
    question: str
    answer: Literal["是", "否", "中性", "等待", "不适用"]
    reason: str = ""
    bar_range: BarRange | None
    source: Literal["program", "ai"]


# 增量变化：本轮与上轮相比是否变化、摘要、已变化字段列表
class IncrementalDelta(BaseModel):
    changed: bool = False
    summary: str = "首次分析，无上一轮结果"
    changed_fields: list[str] = Field(default_factory=list)


# Stage1 结果：实际产出或失败；预检、周期位置、方向、置信度、模式、支撑/阻力位、K线摘要、闸门追踪等
class Stage1Result(BaseModel):
    result_kind: Literal["live", "failed"]
    precheck: PrecheckResult
    cycle_position: str | None = None
    direction: Direction | None = None
    confidence: int = Field(ge=0, le=100)
    detected_patterns: list[str] = Field(default_factory=list)
    support_levels: list[float] = Field(default_factory=list)
    resistance_levels: list[float] = Field(default_factory=list)
    bar_summaries: list[dict[str, Any]] = Field(default_factory=list)
    gate_trace: list[GateTraceItem] = Field(default_factory=list)
    gate_result: Literal["proceed", "wait", "unknown"]
    failure_subtype: str | None = None
    incremental_delta: IncrementalDelta = Field(default_factory=IncrementalDelta)
    risk_warning: str = ""
    override_audit: list[dict[str, Any]] = Field(default_factory=list)
    normalization_audit: list[dict[str, Any]] = Field(default_factory=list)
    model_attempts: list[dict[str, Any]] = Field(default_factory=list)


# 交易决策：下单类型、方向、入场价、止损、止盈（两档）、估算胜率、入场理由
class Decision(BaseModel):
    order_type: str = "不下单"
    direction: Literal["long", "short"] | None = None
    entry_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    take_profit_price_2: float | None = None
    estimated_win_rate: int | None = None
    entry_reason: str | None = None


# 终端结果：最终结局（交易/拒绝/等待/错误）、原因、终端节点
class TerminalResult(BaseModel):
    outcome: Literal["trade", "reject", "wait", "error"]
    reason: str
    terminal_node: str


# Stage2 结果：短路/实际/失败；决策、决策追踪、盈亏比、连续性、终端结果
class Stage2Result(BaseModel):
    result_kind: Literal["short_circuit", "live", "failed"]
    decision: Decision = Field(default_factory=Decision)
    decision_trace: list[dict[str, Any]] = Field(default_factory=list)
    risk_reward: dict[str, Any] | None = None
    continuity: dict[str, Any] = Field(default_factory=dict)
    terminal: TerminalResult


# 交易复盘结果：交易ID、执行指标、对比、问题、优点、改进建议、总结
class TradeReviewResult(BaseModel):
    trade_id: UUID | str
    execution_metrics: dict[str, Any]
    comparison: dict[str, Any]
    issues: list[dict[str, Any]] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    summary: str


# 分析审计：开始/完成时间、stage1/stage2 是否调用模型、验证次数、警告、图执行轨迹
class AnalysisAudit(BaseModel):
    started_at: datetime
    completed_at: datetime
    stage1_model_called: bool = False
    stage2_model_called: bool = False
    validation_attempts: int = 1
    warnings: list[str] = Field(default_factory=list)
    graph_trail: list[str] = Field(default_factory=list)
