"""Tool rounds must end with a final answer or an explicit failure."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.errors import AppError
from app.followup.service import FollowupRequest, FollowupSessionStore, stream_followup_turn
from app.llm.client import _stream_openai_compatible_with_tools


def install_provider(
    monkeypatch: pytest.MonkeyPatch,
    answer: Callable[[int], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    requests: list[dict[str, Any]] = []
    executions: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        choice = answer(len(requests))
        content = 'data: ' + json.dumps({"choices": [choice]}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.llm.client.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executions.append(name)
        return {"bar_count": len(executions)}

    monkeypatch.setattr("app.llm.tools.execute_llm_tool", execute)
    monkeypatch.setattr("app.llm.tools.list_llm_tools", lambda: [{
        "type": "function", "function": {"name": "probe", "parameters": {"type": "object"}},
    }])
    return requests, executions


def tool_choice(index: int) -> dict[str, Any]:
    return {
        "delta": {
            "content": "正在查询，尚未得出结论。",
            "tool_calls": [{"index": 0, "id": f"call-{index}", "function": {
                "name": "probe", "arguments": "{}",
            }}],
        },
        "finish_reason": "tool_calls",
    }


def chat(messages: list[dict[str, Any]] | None = None):
    return _stream_openai_compatible_with_tools(
        endpoint="https://model.invalid/chat/completions",
        headers={}, model="test", payload_messages=messages or [{"role": "user", "content": "分析"}],
        timeout=httpx.Timeout(1),
    )


@pytest.mark.anyio
async def test_tool_budget_reserves_a_final_answer_using_last_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    def response(index: int) -> dict[str, Any]:
        return tool_choice(index) if index <= 3 else {
            "delta": {"content": "最终结论"}, "finish_reason": "stop",
        }

    requests, executions = install_provider(monkeypatch, response)
    answer = "".join([piece async for piece in chat()])

    assert answer.endswith("最终结论")
    assert len(executions) == 3
    assert len(requests) == 4
    assert requests[-1].get("tool_choice") == "none"
    assert requests[-1]["messages"][-1]["role"] == "tool"
    assert json.loads(requests[-1]["messages"][-1]["content"]) == {"bar_count": 3}


@pytest.mark.anyio
async def test_tool_budget_exhaustion_raises_instead_of_returning_intermediate_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _, executions = install_provider(monkeypatch, tool_choice)
    yielded: list[str] = []
    with pytest.raises(AppError) as caught:
        async for piece in chat():
            yielded.append(piece)
    assert caught.value.code == "llm_tool_budget_exhausted"
    assert "最终结论" not in "".join(yielded)
    assert len(executions) == 3


@pytest.mark.anyio
async def test_normal_tool_round_reaches_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, executions = install_provider(monkeypatch, lambda index: tool_choice(index) if index == 1 else {
        "delta": {"content": "答案"}, "finish_reason": "stop",
    })
    assert "".join([piece async for piece in chat()]).endswith("答案")
    assert len(requests) == 2
    assert len(executions) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("final_content, finish_reason, expected_code", [
    (None, "tool_calls", "llm_tool_budget_exhausted"),
    ("", "stop", "llm_empty_reply"),
    ("截断答案", "length", "llm_incomplete_response"),
    ("截断答案", None, "llm_incomplete_response"),
    ("受限答案", "content_filter", "llm_incomplete_response"),
])
async def test_followup_budget_failure_has_no_done_or_persisted_answer(
    monkeypatch: pytest.MonkeyPatch, final_content: str | None,
    finish_reason: str | None, expected_code: str,
) -> None:
    install_provider(monkeypatch, lambda index: tool_choice(index) if index <= 3 or final_content is None else {
        "delta": {"content": final_content}, "finish_reason": finish_reason,
    })
    monkeypatch.setattr("app.followup.service.stream_chat", chat)
    stored: list[dict[str, str]] = []

    async def load(_run_id: str) -> list[dict[str, str]]:
        return []

    async def replace(_run_id: str, _messages: list[dict[str, str]]) -> None:
        return None

    async def append(_run_id: str, messages: list[dict[str, str]]) -> None:
        stored.extend(messages)

    monkeypatch.setattr("app.followup.service.load_followup_messages", load)
    monkeypatch.setattr("app.followup.service.replace_followup_prefix", replace)
    monkeypatch.setattr("app.followup.service.append_followup_messages", append)
    store = FollowupSessionStore()
    events = [event async for event in stream_followup_turn(
        run_id="budget-probe", result={"query": {"symbol": "ES", "period": "5m"}},
        request=FollowupRequest(question="是否需要补查？"), store=store,
    )]
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == expected_code
    assert not any(event["type"] == "done" for event in events)
    assert stored == []
    assert len(store.get("budget-probe").messages) == 3


@pytest.mark.anyio
async def test_answer_chunks_are_delivered_before_provider_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    release = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            await release.wait()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Stream())

    client_type = httpx.AsyncClient
    monkeypatch.setattr("app.llm.client.httpx.AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    generator = chat()
    try:
        assert await asyncio.wait_for(anext(generator), timeout=1) == "first"
    finally:
        release.set()
        await generator.aclose()
