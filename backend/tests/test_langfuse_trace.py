from __future__ import annotations

import anyio

from app.core.logging_context import reset_trace_context, set_trace_context
from app.llm.client import LLMResponse, call_llm


class FakeLangfuseTrace:
    def generation(self, **_kwargs):
        return None


def _profile() -> dict[str, str]:
    return {
        "id": "model-1",
        "provider": "openai",
        "model": "gpt-test",
        "api_key": "test-key",
    }


def _response() -> LLMResponse:
    return LLMResponse(
        content={"ok": True},
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        model_id="model-1",
        model="gpt-test",
        provider="openai",
    )


def test_call_llm_adds_application_trace_to_langfuse_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_start_trace(name: str, **kwargs):
        captured["name"] = name
        captured.update(kwargs)
        return FakeLangfuseTrace()

    async def fake_stream(**_kwargs):
        return _response()

    monkeypatch.setattr("app.llm.client.get_active_model", _profile)
    monkeypatch.setattr("app.llm.client.start_trace", fake_start_trace)
    monkeypatch.setattr(
        "app.llm.client._call_llm_openai_compatible_stream",
        fake_stream,
    )
    tokens = set_trace_context(
        "llm-trace-1",
        task_id="task-1",
        analysis_id="analysis-1",
        execution_id="execution-1",
    )
    try:
        async def invoke():
            return await call_llm(
                "system",
                {"analysis_id": "analysis-1"},
                on_delta=lambda _delta: None,
            )

        result = anyio.run(invoke)
    finally:
        reset_trace_context(tokens)

    assert result is not None
    assert captured["name"] == "call_llm"
    assert captured["metadata"] == {
        "provider": "openai",
        "model": "gpt-test",
        "trace_id": "llm-trace-1",
        "task_id": "task-1",
        "analysis_id": "analysis-1",
        "execution_id": "execution-1",
    }


def test_call_llm_works_when_langfuse_is_disabled(monkeypatch) -> None:
    async def fake_stream(**_kwargs):
        return _response()

    monkeypatch.setattr("app.llm.client.get_active_model", _profile)
    monkeypatch.setattr("app.llm.client.start_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.llm.client._call_llm_openai_compatible_stream",
        fake_stream,
    )

    async def invoke():
        return await call_llm(
            "system",
            {"analysis_id": "analysis-1"},
            on_delta=lambda _delta: None,
        )

    result = anyio.run(invoke)

    assert result is not None
    assert result.content == {"ok": True}
