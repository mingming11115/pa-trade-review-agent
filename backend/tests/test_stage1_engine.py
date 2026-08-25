from datetime import datetime, timedelta, timezone

import anyio
import pytest

from app.analysis.contracts import GateTraceItem, IncrementalDelta, PrecheckResult, Stage1Result
from app.llm.client import LLMResponse
from app.core.models import Direction
from app.analysis.workflow.stage1.core.bar_identity import BarRange, BarRef
from app.analysis.workflow.stage1.engine import Stage1ModelOutput, execute_stage1_model


def bar_ref(seq: int) -> BarRef:
    return BarRef(
        bar_timestamp=datetime(2026, 8, 11, 14, 40, tzinfo=timezone.utc) - timedelta(minutes=5 * (seq - 1)),
        timeframe="5m",
        session="CME",
        day_index=21 - seq,
    )


def bar_range(oldest: int, newest: int = 1) -> BarRange:
    return BarRange(start=bar_ref(oldest), end=bar_ref(newest))


def base_result() -> Stage1Result:
    return Stage1Result(
        result_kind="live", precheck=PrecheckResult(passed=True, closed_bar_count=20), cycle_position="trend",
        direction=Direction.bullish, confidence=70, gate_result="proceed",
        gate_trace=[
            GateTraceItem(node_id="data_sufficiency", question="data", answer="是", bar_range=bar_range(20), source="program"),
            GateTraceItem(node_id="program_direction", question="direction", answer="是", bar_range=bar_range(8), source="program"),
            GateTraceItem(node_id="always_in", question="always-in", answer="是", bar_range=bar_range(20), source="program"),
        ], incremental_delta=IncrementalDelta(),
    )


def valid_output() -> dict:
    return {
        "cycle_position": "trend", "direction": "bullish", "confidence": 80, "detected_patterns": ["bull_channel"],
        "support_levels": [99], "resistance_levels": [110],
        "bar_by_bar_summary": [{"bar_ref": bar_ref(seq).model_dump(mode="json"), "bar_type": "other", "role": "structure", "context_effect": "strengthens_bull", "summary": "延续"} for seq in [5, 4, 3, 2, 1]],
        "gate_trace": [{"node_id": node, "question": node, "answer": "中性" if node == "momentum_enough" else "是", "reason": "进入阶段二" if node == "momentum_enough" else "", "bar_range": bar_range(20).model_dump(mode="json")} for node in ["cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "momentum_enough"]],
        "gate_result": "proceed", "summary": "趋势延续", "risk_warning": "", "override_requests": [],
    }


def payload() -> dict:
    return {"kline_data_newest_first": [{"bar_ref": bar_ref(seq).model_dump(mode="json"), "bar_type": "other", "close": 105} for seq in range(1, 21)]}


def test_stage1_retries_invalid_output_then_merges_program_nodes() -> None:
    responses = [{"gate_result": "wait"}, valid_output()]
    calls = []
    async def call(system, body):
        calls.append(body)
        return LLMResponse(responses[len(calls) - 1], 10, 5, 15, "m", "model")
    result, called = anyio.run(execute_stage1_model, base_result(), "system", payload(), call, lambda *_: None)
    assert called is True
    assert len(calls) == 2
    assert result.gate_result == "proceed"
    assert [item.node_id for item in result.gate_trace] == ["data_sufficiency", "cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "program_direction", "always_in", "momentum_enough"]
    assert result.model_attempts[0]["status"] == "invalid"


def test_stage1_retry_exhaustion_downgrades_unknown() -> None:
    async def call(system, body):
        return LLMResponse({"bad": True}, 10, 5, 15, "m", "model")
    result, called = anyio.run(execute_stage1_model, base_result(), "system", payload(), call, lambda *_: None)
    assert called is True
    assert result.gate_result == "unknown"
    assert result.failure_subtype == "retry_exhausted"
    assert len(result.model_attempts) == 2


def test_stage1_accepts_evidenced_program_override() -> None:
    output = valid_output()
    output["override_requests"] = [{"node_id": "always_in", "answer": "中性", "override_reason": "最近三根回到 EMA", "evidence": ["#18 至 #20 回到 EMA"]}]
    async def call(system, body):
        return LLMResponse(output, 10, 5, 15, "m", "model")
    result, _ = anyio.run(execute_stage1_model, base_result(), "system", payload(), call, lambda *_: None)
    assert result.override_audit[0]["accepted"] is True
    assert next(item for item in result.gate_trace if item.node_id == "always_in").answer == "中性"


def test_stage1_model_contract_accepts_structured_bar_refs_and_ranges() -> None:
    output = valid_output()
    refs = [
        {
            "bar_timestamp": f"2026-08-11T13:{minute:02d}:00Z",
            "timeframe": "5m",
            "session": "CME",
            "day_index": index,
        }
        for index, minute in enumerate((0, 5, 10, 15, 20), start=1)
    ]
    output["bar_by_bar_summary"] = [
        {
            "bar_ref": ref,
            "bar_type": "other",
            "role": "structure",
            "context_effect": "strengthens_bull",
            "summary": "延续",
        }
        for ref in refs
    ]
    for trace in output["gate_trace"]:
        trace["bar_range"] = {"start": refs[0], "end": refs[-1]}

    parsed = Stage1ModelOutput.model_validate(output)

    assert parsed.bar_by_bar_summary[-1].bar_ref.day_index == 5


def test_stage1_model_contract_rejects_relative_k_summary() -> None:
    output = valid_output()
    output["bar_by_bar_summary"][0].pop("bar_ref")
    output["bar_by_bar_summary"][0]["seq"] = 5
    with pytest.raises(Exception):
        Stage1ModelOutput.model_validate(output)
