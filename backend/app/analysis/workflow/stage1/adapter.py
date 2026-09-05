from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from app.analysis.contracts import GateTraceItem, Stage1Result
from app.analysis.execution.runs import persist_llm_response, update_analysis_run
from app.llm.client import LLMResponse
from app.core.models import Bar, Direction
from app.analysis.workflow.stage1.core.data.base import KlineBar, KlineFrame
from app.analysis.workflow.stage1.core.bar_identity import BarRange
from app.analysis.workflow.stage1.core.data.snapshot import build_analysis_frame
from app.analysis.workflow.stage1.core.json_validator import JsonValidator, Ok, ValidationError
from app.analysis.workflow.stage1.core.prompt_assembler import PromptAssembler
from app.analysis.workflow.stage1.core.retry_feedback import build_retry_feedback
from app.analysis.workflow.stage1.core.compat import PROMPT_DIR


PROGRAM_NODE_IDS = {"data_sufficiency", "program_direction", "always_in"}


def build_stage1_frame(
    bars: list[Bar],
    symbol: str,
    timeframe: str,
    *,
    visible_count: int | None = None,
) -> KlineFrame:
    newest_first = [
        KlineBar(
            seq=index,
            ts_open=bar.timestamp.timestamp() * 1000,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=float(bar.volume or 0),
            closed=True,
        )
        for index, bar in enumerate(reversed(bars), start=1)
    ]
    count = visible_count or len(newest_first)
    frame = build_analysis_frame(newest_first, count, symbol, timeframe)
    if frame is None:
        raise ValueError(f"无法从 {len(bars)} 根 K 线构建 Stage 1 窗口")
    return frame


def build_original_stage1_messages(
    frame: KlineFrame,
    *,
    program_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    messages = PromptAssembler(PROMPT_DIR).build_stage1(frame, analysis_mode="original")
    if not program_context:
        return messages
    context_block = (
        "\n\n---\n\n## Stage1 LangGraph 程序上下文\n\n"
        "以下是 LLM 调用前已完成的确定性计算。请使用这些结构化事实判断 "
        "cycle_identifiable/not_extreme_chaos/direction_decidable/background_near_term_coherent/momentum_enough，不要自行修改程序权威节点。\n\n"
        + json.dumps(program_context, ensure_ascii=False, indent=2)
    )
    result = [dict(message) for message in messages]
    result[-1]["content"] += context_block
    return result


def _level_price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def _trace_item(item: dict[str, Any]) -> GateTraceItem | None:
    node_id = str(item.get("node_id") or "")
    answer = item.get("answer")
    if not node_id or answer not in {"是", "否", "中性", "等待", "不适用"}:
        return None
    raw_range = item.get("bar_range")
    bar_range = BarRange.model_validate(raw_range) if raw_range is not None else None
    return GateTraceItem(
        node_id=node_id,
        question=str(item.get("question") or item.get("label") or f"决策节点 {node_id}"),
        answer=answer,
        reason=str(item.get("reason") or ""),
        bar_range=bar_range,
        source="program" if node_id in PROGRAM_NODE_IDS else "ai",
    )


def merge_original_stage1(base: Stage1Result, obj: dict[str, Any]) -> Stage1Result:
    traces = [_trace_item(item) for item in obj.get("gate_trace", []) if isinstance(item, dict)]
    levels_support = [_level_price(item) for item in obj.get("support_levels", [])]
    levels_resistance = [_level_price(item) for item in obj.get("resistance_levels", [])]
    direction = obj.get("direction")
    base.cycle_position = str(obj.get("cycle_position") or "unknown")
    base.direction = Direction(direction) if direction in {item.value for item in Direction} else Direction.neutral
    base.confidence = int(obj.get("diagnosis_confidence") or 0)
    base.detected_patterns = [str(item) for item in obj.get("detected_patterns", [])]
    base.support_levels = [item for item in levels_support if item is not None]
    base.resistance_levels = [item for item in levels_resistance if item is not None]
    base.bar_summaries = list(obj.get("bar_by_bar_summary") or [])
    base.gate_trace = [item for item in traces if item is not None]
    base.gate_result = obj.get("gate_result") if obj.get("gate_result") in {"proceed", "wait", "unknown"} else "unknown"
    base.risk_warning = str(obj.get("risk_warning") or "")
    delta = obj.get("incremental_delta") or {}
    base.incremental_delta.changed_fields = list(delta.get("changed_fields") or [])
    base.incremental_delta.changed = bool(base.incremental_delta.changed_fields)
    base.incremental_delta.summary = str(delta.get("summary") or obj.get("htf_context") or "阶段一诊断完成")
    base.override_audit = list(obj.get("node_overrides") or [])
    return base


async def execute_original_stage1(
    base: Stage1Result,
    frame: KlineFrame,
    call: Callable[[str, dict[str, Any]], Awaitable[LLMResponse | None]],
    on_usage: Callable[[LLMResponse, str], None],
    *,
    max_attempts: int = 2,
    analysis_context: dict[str, str] | None = None,
) -> tuple[Stage1Result, bool]:
    if not base.precheck.passed:
        return base, False
    messages = build_original_stage1_messages(frame)
    system_prompt = messages[0]["content"]
    user_prompt = messages[1]["content"]
    validator = JsonValidator()
    attempts: list[dict[str, Any]] = []
    retry_text = ""
    for attempt in range(1, max_attempts + 1):
        response = await call(
            system_prompt,
            {"_user_prompt": user_prompt + retry_text, "_preserve_raw": True},
        )
        if response is None:
            return base, False
        on_usage(response, f"stage1_attempt_{attempt}")
        context = analysis_context or {}
        run_id = await persist_llm_response(
            response,
            run_id=context.get("run_id", "unknown"),
            stage="stage1",
            attempt=attempt,
            mode=context.get("mode", "historical"),
            symbol=context.get("symbol", frame.symbol),
            period=context.get("period", frame.timeframe),
        )
        raw = response.raw_content or ""
        result = validator.validate("stage1", raw, kline_frame=frame)
        if isinstance(result, Ok):
            await update_analysis_run(run_id, status="validated", normalized_output=result.obj)
            attempts.append({"attempt": attempt, "status": "valid", "raw": result.obj})
            base.model_attempts = attempts
            return merge_original_stage1(base, result.obj), True
        assert isinstance(result, ValidationError)
        validation_errors = result.invalid_fields or result.missing_fields or [result.message]
        await update_analysis_run(run_id, status="validation_failed", validation_errors=validation_errors)
        attempts.append({
            "attempt": attempt,
            "status": "invalid",
            "category": result.category,
            "errors": validation_errors,
        })
        retry_text = "\n\n" + build_retry_feedback(
            result,
            stage="stage1",
            attempt=attempt,
            max_attempts=max_attempts,
            frame=frame,
            previous_raw=raw,
        )
    base.result_kind = "failed"
    base.gate_result = "unknown"
    base.failure_subtype = "retry_exhausted"
    base.model_attempts = attempts
    base.risk_warning = "Stage 1 原版校验连续失败，已降级为 unknown"
    return base, True
