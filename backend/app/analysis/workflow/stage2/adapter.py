from __future__ import annotations

import json
from typing import Any

from app.analysis.contracts import Decision, Stage1Result, Stage2Result, TerminalResult
from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.analysis.workflow.stage1.core.data.base import KlineFrame
from app.analysis.workflow.stage1.core.json_validator import JsonValidator, Result
from app.analysis.workflow.stage1.core.prompt_assembler import PromptAssembler
from app.analysis.workflow.stage1.core.router import route_strategy_files
from app.analysis.workflow.stage1.core.trade_metrics import compute_risk_reward


def stage1_result_to_original(stage1: Stage1Result) -> dict[str, Any]:
    return {
        "cycle_position": stage1.cycle_position,
        "direction": stage1.direction.value if stage1.direction else "neutral",
        "diagnosis_confidence": stage1.confidence,
        "detected_patterns": list(stage1.detected_patterns),
        "support_levels": list(stage1.support_levels),
        "resistance_levels": list(stage1.resistance_levels),
        "bar_by_bar_summary": list(stage1.bar_summaries),
        "gate_trace": [item.model_dump(mode="json") for item in stage1.gate_trace],
        "gate_result": stage1.gate_result,
        "risk_warning": stage1.risk_warning,
        "incremental_delta": stage1.incremental_delta.model_dump(mode="json"),
    }


def build_original_stage2_messages(
    frame: KlineFrame,
    stage1: Stage1Result,
    *,
    decision_stance: str = "conservative",
    precheck_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any], list[str]]:
    stage1_json = stage1_result_to_original(stage1)
    strategy_files = route_strategy_files(stage1_json)
    messages = PromptAssembler(PROMPT_DIR).build_stage2(
        frame,
        stage1_json,
        strategy_files,
        [],
        decision_stance=decision_stance,
    )
    if precheck_context:
        messages[-1]["content"] += (
            "\n\n## Stage2 LangGraph 前置计算上下文\n\n"
            "以下为程序权威候选与边界。你只能从允许集合中选择，并给出具体方案；"
            "不得自行修改 ATR、结构锚点或候选信号棒。\n\n```json\n"
            + json.dumps(precheck_context, ensure_ascii=False, indent=2)
            + "\n```"
        )
    return messages, stage1_json, strategy_files


def validate_original_stage2(
    raw: str,
    *,
    frame: KlineFrame,
    stage1_json: dict[str, Any],
    decision_stance: str = "conservative",
) -> Result:
    return JsonValidator().validate(
        "stage2",
        raw,
        kline_frame=frame,
        stage1_json=stage1_json,
        decision_stance=decision_stance,
        skip_next_bar=True,
    )


def _direction(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"long", "buy", "bull", "bullish", "做多", "多"}:
        return "long"
    if text in {"short", "sell", "bear", "bearish", "做空", "空"}:
        return "short"
    return None


def build_stage2_result(obj: dict[str, Any], continuity: dict[str, Any]) -> Stage2Result:
    raw_decision = obj.get("decision") if isinstance(obj.get("decision"), dict) else {}
    raw_terminal = obj.get("terminal") if isinstance(obj.get("terminal"), dict) else {}
    outcome = str(raw_terminal.get("outcome") or "wait").lower()
    if outcome not in {"trade", "reject", "wait", "error"}:
        outcome = "wait"
    order_type = str(raw_decision.get("order_type") or "不下单")
    direction = _direction(raw_decision.get("order_direction"))
    if order_type == "不下单" or outcome != "trade":
        direction = None
    decision = Decision(
        order_type=order_type,
        direction=direction,
        entry_price=raw_decision.get("entry_price"),
        stop_loss_price=raw_decision.get("stop_loss_price"),
        take_profit_price=raw_decision.get("take_profit_price"),
        take_profit_price_2=raw_decision.get("take_profit_price_2"),
        estimated_win_rate=raw_decision.get("estimated_win_rate"),
        entry_reason=str(raw_decision.get("reasoning") or "") or None,
    )
    risk_reward = compute_risk_reward(
        decision.entry_price,
        decision.take_profit_price,
        decision.stop_loss_price,
        decision.direction,
    )
    reason = str(
        raw_terminal.get("label")
        or raw_terminal.get("reason")
        or raw_decision.get("reasoning")
        or "模型未提供理由"
    )
    return Stage2Result(
        result_kind="live",
        decision=decision,
        decision_trace=list(obj.get("decision_trace") or []),
        risk_reward=risk_reward,
        continuity=continuity,
        terminal=TerminalResult(
            outcome=outcome,
            reason=reason[:1200],
            terminal_node=str(raw_terminal.get("node_id") or raw_terminal.get("terminal_node") or "stage2"),
        ),
    )
