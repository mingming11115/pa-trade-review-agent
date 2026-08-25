from datetime import datetime, timezone
import json

import anyio

from app.analysis.workflow.graph import run_demo_analysis_workflow
from app.llm.client import LLMResponse
from app.core.models import Bar, HistoricalQuery
from app.llm.prompts import stage2_system_prompt
from app.analysis.workflow.stage1.core.bar_identity import BarRange, assign_timestamp_refs


class Provider:
    async def get_range(self, query):
        return [Bar(timestamp=datetime(2022, 1, 1, 0, minute, tzinfo=timezone.utc), open=100 + minute, high=103 + minute, low=99 + minute, close=102 + minute, volume=10) for minute in range(40)]


def refs() -> list[dict]:
    values = assign_timestamp_refs(
        [datetime(2022, 1, 1, 0, minute, tzinfo=timezone.utc) for minute in range(40)],
        symbol="ES",
        timeframe="1m",
    )
    return [value.model_dump(mode="json") for value in values]


def full_range() -> dict:
    values = assign_timestamp_refs(
        [datetime(2022, 1, 1, 0, minute, tzinfo=timezone.utc) for minute in range(40)],
        symbol="ES",
        timeframe="1m",
    )
    return BarRange(start=values[0], end=values[-1]).model_dump(mode="json")


def stage2_wait_response(reason: str = "等待回撤") -> dict:
    return {
        "decision": {
            "order_type": "不下单", "order_direction": None, "entry_price": None,
            "stop_loss_price": None, "take_profit_price": None, "take_profit_price_2": None,
            "reasoning": reason, "diagnosis_confidence": 82,
            "diagnosis_confidence_reasoning": "阶段一结构可识别",
            "trade_confidence": 30, "trade_confidence_reasoning": "当前没有合格信号棒",
            "estimated_win_rate": None, "estimated_win_rate_reasoning": "无交易方案",
            "key_factors": ["等待回撤"], "watch_points": ["观察支撑"],
            "risk_assessment": "追高风险", "invalidation_condition": None,
        },
        "diagnosis_summary": {"cycle_position": "normal_channel", "direction": "bullish", "key_signals": ["多头延续"]},
        "decision_trace": [{"node_id": "planned_limit", "question": "是否存在计划型限价机会？", "answer": "否", "reason": reason, "bar_range": full_range()}],
        "terminal": {"node_id": "planned_limit", "outcome": "wait", "label": reason},
    }


def test_workflow_calls_stage1_and_stage2_and_records_usage(monkeypatch) -> None:
    calls = []
    persisted = []
    responses = [
        {
            "cycle_position": "normal_channel", "direction": "bullish", "diagnosis_confidence": 82,
            "market_phase": "stable", "detected_patterns": [], "key_signals": ["多头延续"],
            "htf_context": "长程背景偏多", "entry_setup": "等待阶段二判断",
            "strategy_files_needed": ["上涨通道分析识别.txt"],
            "support_levels": ["100"], "resistance_levels": ["130"],
            "bar_by_bar_summary": [
                {"bar_ref": ref, "bar_type": "trend_bull", "role": "structure", "context_effect": "strengthens_bull", "follow_through": "yes", "trapped_side": "none", "reason": "延续"}
                for ref in refs()[-5:]
            ],
            "gate_trace": [
                {"node_id": node_id, "question": node_id, "answer": "是" if node_id != "momentum_enough" else "中性", "reason": "进入阶段二" if node_id == "momentum_enough" else "", "bar_range": full_range()}
                for node_id in ["data_sufficiency", "cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "program_direction", "always_in", "momentum_enough"]
            ],
            "gate_result": "proceed", "risk_warning": "", "node_overrides": [],
            "incremental_delta": {"changed_fields": [], "summary": "多头通道"},
        },
        stage2_wait_response(),
    ]

    async def fake_call(system, payload):
        index = len(calls)
        calls.append(payload)
        return LLMResponse(
            responses[index], 100, 20, 120, "model-1", "deepseek-v4-flash",
            raw_content=json.dumps(responses[index], ensure_ascii=False),
        )

    async def fake_start(**kwargs):
        persisted.append(("started", kwargs))
        return "stage2-run"

    async def fake_attach(run_id, response):
        persisted.append(("attached", response.total_tokens))

    async def fake_update(run_id, **kwargs):
        if run_id == "stage2-run":
            persisted.append((kwargs["status"], kwargs))

    usage = []
    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", fake_call)
    monkeypatch.setattr("app.analysis.workflow.graph.append_usage", usage.append)
    monkeypatch.setattr("app.analysis.workflow.graph.start_analysis_run", fake_start)
    monkeypatch.setattr("app.analysis.workflow.graph.attach_llm_response", fake_attach)
    monkeypatch.setattr("app.analysis.workflow.graph.update_analysis_run", fake_update)
    query = HistoricalQuery(symbol="ES", period="1m", start=datetime(2022, 1, 1, tzinfo=timezone.utc), end=datetime(2022, 1, 2, tzinfo=timezone.utc))
    result = anyio.run(run_demo_analysis_workflow, Provider(), query)

    assert len(calls) == 2
    assert result.audit.stage1_model_called is True
    assert result.audit.stage2_model_called is True
    assert result.stage1.incremental_delta.summary == "多头通道"
    assert result.stage2.terminal.reason == "等待回撤"
    assert sum(item.total_tokens for item in usage) == 240
    assert [event[0] for event in persisted] == ["started", "attached", "completed"]


def test_stage2_prompt_loads_migrated_prompt_files() -> None:
    prompt = stage2_system_prompt("trending_tr", "bullish")

    assert "交易二元决策树" in prompt
    assert "震荡区间" in prompt
    assert len(prompt) > 10_000


def test_stage2_retries_invalid_json_and_preserves_raw_response(monkeypatch) -> None:
    stage1_content = {
        "cycle_position": "normal_channel", "direction": "bullish", "diagnosis_confidence": 82,
        "market_phase": "stable", "detected_patterns": [], "key_signals": ["多头延续"],
        "htf_context": "长程背景偏多", "entry_setup": "等待阶段二判断",
        "strategy_files_needed": ["上涨通道分析识别.txt"],
        "support_levels": ["100"], "resistance_levels": ["130"],
        "bar_by_bar_summary": [
            {"bar_ref": ref, "bar_type": "trend_bull", "role": "structure", "context_effect": "strengthens_bull", "follow_through": "yes", "trapped_side": "none", "reason": "延续"}
            for ref in refs()[-5:]
        ],
        "gate_trace": [
            {"node_id": node_id, "question": node_id, "answer": "是" if node_id != "momentum_enough" else "中性", "reason": "进入阶段二" if node_id == "momentum_enough" else "", "bar_range": full_range()}
            for node_id in ["data_sufficiency", "cycle_identifiable", "not_extreme_chaos", "direction_decidable", "background_near_term_coherent", "program_direction", "always_in", "momentum_enough"]
        ],
        "gate_result": "proceed", "risk_warning": "", "node_overrides": [],
        "incremental_delta": {"changed_fields": [], "summary": "多头通道"},
    }
    valid_stage2 = stage2_wait_response()
    responses = [
        LLMResponse(stage1_content, 100, 20, 120, "model-1", "deepseek-v4-flash", raw_content=json.dumps(stage1_content)),
        LLMResponse({}, 200, 30, 230, "model-1", "deepseek-v4-flash", raw_content="分析完成，但没有 JSON"),
        LLMResponse(valid_stage2, 210, 20, 230, "model-1", "deepseek-v4-flash", raw_content=json.dumps(valid_stage2)),
    ]
    events = []

    async def fake_call(system, payload):
        return responses.pop(0)

    async def fake_start(**kwargs):
        run_id = f"stage2-{kwargs['attempt']}"
        events.append(("start", run_id))
        return run_id

    async def fake_attach(run_id, response):
        events.append(("attach", run_id, response.raw_content))

    async def fake_update(run_id, **kwargs):
        if str(run_id).startswith("stage2-"):
            events.append((kwargs["status"], run_id))

    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", fake_call)
    monkeypatch.setattr("app.analysis.workflow.graph.start_analysis_run", fake_start)
    monkeypatch.setattr("app.analysis.workflow.graph.attach_llm_response", fake_attach)
    monkeypatch.setattr("app.analysis.workflow.graph.update_analysis_run", fake_update)
    monkeypatch.setattr("app.analysis.workflow.graph.append_usage", lambda record: None)

    query = HistoricalQuery(symbol="ES", period="1m", start=datetime(2022, 1, 1, tzinfo=timezone.utc), end=datetime(2022, 1, 2, tzinfo=timezone.utc))
    result = anyio.run(run_demo_analysis_workflow, Provider(), query)

    assert result.stage2.terminal.reason == "等待回撤"
    assert [(event[0], event[1]) for event in events] == [
        ("start", "stage2-1"),
        ("attach", "stage2-1"),
        ("validation_failed", "stage2-1"),
        ("start", "stage2-2"),
        ("attach", "stage2-2"),
        ("completed", "stage2-2"),
    ]
