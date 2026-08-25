import json
from datetime import datetime, timezone

import anyio

from app.analysis.workflow.graph import DemoAnalysisWorkflow
from app.llm.client import LLMResponse, LLMStreamDelta
from app.core.models import Bar, HistoricalQuery


class Provider:
    async def get_range(self, query):
        return [
            Bar(
                timestamp=datetime(2022, 1, 1, 0, minute, tzinfo=timezone.utc),
                open=100 + minute,
                high=103 + minute,
                low=99 + minute,
                close=102 + minute,
                volume=10,
            )
            for minute in range(40)
        ]


def test_stream_emits_reasoning_then_content_deltas(monkeypatch) -> None:
    async def fake_call(system, payload, *, on_delta=None):
        body = {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "diagnosis_confidence": 70,
            "market_phase": "stable",
            "detected_patterns": [],
            "key_signals": ["延续"],
            "htf_context": "偏多",
            "entry_setup": "等待",
            "strategy_files_needed": [],
            "support_levels": ["100"],
            "resistance_levels": ["130"],
            "bar_by_bar_summary": [],
            "gate_trace": [],
            "gate_result": "wait",
            "risk_warning": "",
            "node_overrides": [],
            "incremental_delta": {"changed_fields": [], "summary": "等待"},
        }
        raw = json.dumps(body, ensure_ascii=False)
        if on_delta is not None:
            await on_delta(LLMStreamDelta(kind="reasoning", text="先看结构。"))
            await on_delta(LLMStreamDelta(kind="content", text=raw))
        return LLMResponse(
            body, 10, 5, 15, "model-1", "deepseek-reasoner",
            raw_content=raw, reasoning_content="先看结构。",
        )

    async def _persist(*_a, **_k):
        return "run-1"

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr("app.analysis.workflow.graph.call_llm", fake_call)
    monkeypatch.setattr("app.analysis.workflow.graph.append_usage", lambda *_a, **_k: None)
    monkeypatch.setattr("app.analysis.workflow.graph.persist_llm_response", _persist)
    monkeypatch.setattr("app.analysis.workflow.graph.start_analysis_run", _persist)
    monkeypatch.setattr("app.analysis.workflow.graph.attach_llm_response", _noop)
    monkeypatch.setattr("app.analysis.workflow.graph.update_analysis_run", _noop)

    workflow = DemoAnalysisWorkflow(provider=Provider())
    query = HistoricalQuery(
        symbol="ES",
        period="1m",
        start=datetime(2022, 1, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2022, 1, 1, 0, 39, tzinfo=timezone.utc),
        analysis_mode="historical",
    )

    events = []

    async def collect():
        async for event in workflow.stream(query):
            events.append(event)

    anyio.run(collect)

    deltas = [e for e in events if e.get("type") == "llm_delta"]
    assert any(e.get("kind") == "reasoning" and "结构" in e.get("text", "") for e in deltas)
    assert any(e.get("kind") == "content" and e.get("text", "").lstrip().startswith("{") for e in deltas)
    assert any(e.get("type") == "result" for e in events)
