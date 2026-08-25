import anyio

from app.analysis.execution.manager import run_streamed_analysis
from app.llm.client import LLMStreamDelta
from app.core.models import Bar, HistoricalQuery


def test_saved_analysis_forwards_llm_deltas(monkeypatch) -> None:
    query = HistoricalQuery.model_validate({
        "symbol": "ES",
        "period": "5m",
        "start": "2026-08-11T01:00:00Z",
        "end": "2026-08-11T01:05:00Z",
        "analysis_mode": "realtime",
    })
    bars = [Bar.model_validate({
        "timestamp": "2026-08-11T01:00:00Z",
        "open": 6400,
        "high": 6402,
        "low": 6399,
        "close": 6401,
        "volume": 10,
    })]
    expected_result = object()

    async def fake_stream(_provider, _query):
        yield {"type": "llm_delta", "stage": "stage1", "kind": "reasoning", "text": "先看结构。", "message": "思考中…"}
        yield {"type": "result", "stage": "complete", "message": "分析完成", "result": expected_result}

    monkeypatch.setattr("app.analysis.execution.manager.stream_demo_analysis_workflow", fake_stream)
    forwarded = []

    async def collect():
        result = await run_streamed_analysis(bars, query, forwarded.append)
        assert result is expected_result

    anyio.run(collect)

    assert forwarded == [{
        "type": "llm_delta",
        "stage": "stage1",
        "kind": "reasoning",
        "text": "先看结构。",
        "message": "思考中…",
    }]
