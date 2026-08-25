from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.analysis.contracts import GateTraceItem, Stage1Result
from app.core.errors import AppError
from app.llm.client import LLMResponse
from app.core.models import Direction
from app.analysis.workflow.stage1.core.bar_identity import BarRange, BarRef


AI_NODE_IDS = ("cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "momentum_enough")
ANSWER_VALUES = {"是", "否", "中性", "等待", "不适用"}
class Stage1TraceOutput(BaseModel):
    node_id: str
    question: str
    answer: Literal["是", "否", "中性", "等待", "不适用"]
    reason: str = ""
    bar_range: BarRange | None
    skipped: bool = False

    @model_validator(mode="after")
    def validate_skipped(self) -> "Stage1TraceOutput":
        if self.skipped and (self.answer != "不适用" or self.bar_range is not None):
            raise ValueError("skipped 节点必须 answer=不适用 且 bar_range=null")
        return self


class BarSummaryOutput(BaseModel):
    bar_ref: BarRef
    bar_type: str
    role: Literal["structure", "signal", "entry", "confirmation", "noise", "trap", "climax", "test"]
    context_effect: Literal["strengthens_bull", "weakens_bull", "strengthens_bear", "weakens_bear", "neutral", "transition"]
    summary: str


class OverrideRequest(BaseModel):
    node_id: Literal["program_direction", "always_in"]
    answer: Literal["是", "否", "中性", "等待", "不适用"]
    override_reason: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class Stage1ModelOutput(BaseModel):
    cycle_position: str
    direction: Direction
    confidence: int = Field(ge=0, le=100)
    detected_patterns: list[str]
    support_levels: list[float]
    resistance_levels: list[float]
    bar_by_bar_summary: list[BarSummaryOutput]
    gate_trace: list[Stage1TraceOutput]
    gate_result: Literal["proceed", "wait", "unknown"]
    summary: str
    risk_warning: str = ""
    override_requests: list[OverrideRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gate(self) -> "Stage1ModelOutput":
        ids = [item.node_id for item in self.gate_trace]
        if ids != list(AI_NODE_IDS):
            raise ValueError(f"gate_trace 必须严格包含并依次排列 {AI_NODE_IDS}")
        if self.gate_result == "wait":
            cycle_blocked = self.gate_trace[0].answer != "是"
            chaos_blocked = self.gate_trace[1].answer == "否"
            if not (cycle_blocked or chaos_blocked):
                raise ValueError("wait 仅允许由 cycle_identifiable 周期不可识别或 not_extreme_chaos 极端混乱触发")
            if self.gate_trace[-1].answer == "中性":
                raise ValueError("wait 时最后节点不得为中性")
        if self.gate_result == "proceed" and (self.gate_trace[0].answer != "是" or self.gate_trace[1].answer == "否"):
            raise ValueError("cycle_identifiable 未通过或 not_extreme_chaos 极端混乱时不得 proceed")
        if self.gate_result == "proceed" and self.gate_trace[-1].reason and not any(word in self.gate_trace[-1].reason for word in ("通过", "阶段二", "继续")):
            raise ValueError("proceed 时最后节点 reason 必须表达进入阶段二")
        return self


def normalize_stage1_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(payload)
    audit = []
    direction_aliases = {"多": "bullish", "多头": "bullish", "空": "bearish", "空头": "bearish", "震荡": "neutral", "中性": "neutral"}
    raw_direction = normalized.get("direction")
    if raw_direction in direction_aliases:
        normalized["direction"] = direction_aliases[raw_direction]
        audit.append({"field": "direction", "from": raw_direction, "to": normalized["direction"], "reason": "枚举别名规范化"})
    raw_gate = normalized.get("gate_result")
    gate_aliases = {"continue": "proceed", "pass": "proceed", "等待": "wait", "未知": "unknown"}
    if raw_gate in gate_aliases:
        normalized["gate_result"] = gate_aliases[raw_gate]
        audit.append({"field": "gate_result", "from": raw_gate, "to": normalized["gate_result"], "reason": "枚举别名规范化"})
    return normalized, audit


def _identity(value: BarRef | dict[str, Any]) -> tuple[Any, str, str]:
    return BarRef.model_validate(value).identity


def _validate_bar_summaries(output: Stage1ModelOutput, max_seq: int, program_bars: list[dict[str, Any]]) -> list[str]:
    errors = []
    if max_seq >= 5 and len(output.bar_by_bar_summary) != 5:
        errors.append("bar_by_bar_summary 必须恰好包含最近五根已收盘 K 线")
    expected_bars = list(reversed(program_bars[: min(5, max_seq)]))
    expected_refs = [_identity(item["bar_ref"]) for item in expected_bars if isinstance(item, dict) and item.get("bar_ref")]
    actual_refs = [item.bar_ref.identity for item in output.bar_by_bar_summary]
    if expected_refs and actual_refs != expected_refs:
        errors.append("bar_by_bar_summary 必须按时间顺序引用最近五根权威 bar_ref")
    program_types = {
        _identity(item["bar_ref"]): item.get("bar_type")
        for item in program_bars
        if isinstance(item, dict) and item.get("bar_ref")
    }
    for item in output.bar_by_bar_summary:
        expected_type = program_types.get(item.bar_ref.identity)
        if expected_type and item.bar_type != expected_type:
            errors.append(f"{item.bar_ref.model_dump(mode='json')}.bar_type 必须服从程序值 {expected_type}")
    available_refs = {
        _identity(item["bar_ref"])
        for item in program_bars
        if isinstance(item, dict) and item.get("bar_ref")
    }
    for item in output.gate_trace:
        if item.bar_range is None:
            continue
        if available_refs and (
            item.bar_range.start.identity not in available_refs
            or item.bar_range.end.identity not in available_refs
        ):
            errors.append(f"{item.node_id}.bar_range 引用了当前窗口不存在的 K 线身份")
    latest_close = program_bars[0].get("close") if program_bars else None
    if isinstance(latest_close, (int, float)):
        if any(level >= latest_close for level in output.support_levels):
            errors.append("support_levels 只能保留当前收盘价下方的有效支撑")
        if any(level <= latest_close for level in output.resistance_levels):
            errors.append("resistance_levels 只能保留当前收盘价上方的有效阻力")
    return errors


def _arbitrate_overrides(base: Stage1Result, requests: list[OverrideRequest]) -> list[dict[str, Any]]:
    audit = []
    for request in requests:
        accepted = bool(request.override_reason.strip() and request.evidence)
        record = request.model_dump()
        record.update({"accepted": accepted, "reason": "具备理由和结构证据" if accepted else "缺少理由或结构证据"})
        if accepted:
            target = next((node for node in base.gate_trace if node.node_id == request.node_id), None)
            if target:
                record["program_value"] = target.answer
                target.answer = request.answer
                target.reason = request.override_reason
                record["final_value"] = target.answer
        audit.append(record)
    return audit


def merge_stage1_result(base: Stage1Result, output: Stage1ModelOutput, normalization_audit: list[dict[str, Any]]) -> Stage1Result:
    program_nodes = {item.node_id: item for item in base.gate_trace if item.source == "program"}
    ai_nodes = {item.node_id: item for item in output.gate_trace}
    ordered_ids = ("data_sufficiency", "cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "program_direction", "always_in", "momentum_enough")
    merged = []
    for node_id in ordered_ids:
        if node_id in program_nodes:
            merged.append(program_nodes[node_id])
        else:
            item = ai_nodes[node_id]
            merged.append(GateTraceItem(**item.model_dump(), source="ai"))
    base.gate_trace = merged
    base.cycle_position = output.cycle_position
    base.direction = output.direction
    base.confidence = output.confidence
    base.detected_patterns = output.detected_patterns
    base.support_levels = output.support_levels
    base.resistance_levels = output.resistance_levels
    base.bar_summaries = [item.model_dump() for item in output.bar_by_bar_summary]
    base.gate_result = output.gate_result
    base.incremental_delta.summary = output.summary
    base.risk_warning = output.risk_warning
    base.normalization_audit.extend(normalization_audit)
    base.override_audit.extend(_arbitrate_overrides(base, output.override_requests))
    return base


async def execute_stage1_model(
    base: Stage1Result,
    system: str,
    payload: dict[str, Any],
    call: Callable[[str, dict[str, Any]], Awaitable[LLMResponse | None]],
    on_usage: Callable[[LLMResponse, str], None],
    *,
    max_attempts: int = 2,
) -> tuple[Stage1Result, bool]:
    if not base.precheck.passed:
        return base, False
    attempts = []
    current_payload = dict(payload)
    for attempt in range(1, max_attempts + 1):
        try:
            response = await call(system, current_payload)
            if response is None:
                return base, False
            on_usage(response, f"stage1_attempt_{attempt}")
            normalized, audit = normalize_stage1_payload(response.content)
            output = Stage1ModelOutput.model_validate(normalized)
            semantic_errors = _validate_bar_summaries(output, base.precheck.closed_bar_count, payload.get("kline_data_newest_first", []))
            if semantic_errors:
                raise ValueError("；".join(semantic_errors))
            attempts.append({"attempt": attempt, "status": "valid", "raw": response.content})
            base.model_attempts = attempts
            return merge_stage1_result(base, output, audit), True
        except (AppError, ValidationError, ValueError) as exc:
            errors = exc.errors() if isinstance(exc, ValidationError) else [str(exc)]
            attempts.append({"attempt": attempt, "status": "invalid", "errors": errors})
            current_payload = {**payload, "retry_feedback": {"attempt": attempt, "validation_errors": errors, "instruction": "仅修正列出的字段，保持程序节点和行情事实不变"}}
    base.result_kind = "failed"
    base.gate_result = "unknown"
    base.failure_subtype = "retry_exhausted"
    base.model_attempts = attempts
    base.risk_warning = "Stage 1 模型输出连续校验失败，已降级为 unknown"
    return base, True
