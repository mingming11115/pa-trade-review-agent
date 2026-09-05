from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.errors import AppError
from app.core.logging_context import get_trace_fields
from app.llm.langfuse_tracer import end_generation, start_trace
from app.llm.tools import execute_llm_tool, list_llm_tools
from app.personal.service import get_active_model

logger = logging.getLogger(__name__)

LLMDeltaKind = Literal["reasoning", "content"]
LLMDeltaCallback = Callable[["LLMStreamDelta"], Awaitable[None] | None]


def _trace_metadata(extra: dict[str, Any]) -> dict[str, Any]:
    """Merge safe application correlation ids into observability metadata."""
    metadata = dict(extra)
    metadata.update(
        {
            name: value
            for name, value in get_trace_fields().items()
            if value != "-"
        }
    )
    return metadata


@dataclass(slots=True)
class LLMStreamDelta:
    kind: LLMDeltaKind
    text: str


@dataclass(slots=True)
class LLMResponse:
    content: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model_id: str
    model: str
    raw_content: str = ""
    reasoning_content: str = ""
    provider: str = ""
    provider_request_id: str | None = None
    response_model: str | None = None
    duration_ms: int = 0
    raw_response: dict[str, Any] | None = None


def _json_content(text: str) -> dict[str, Any]:
    """将 LLM 返回的文本解析为 JSON 字典，去除可能的 markdown 代码块包裹。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去除 ```json 或 ``` 包裹
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AppError("llm_invalid_json", "大模型返回了无效 JSON", 502) from exc
    if not isinstance(value, dict):
        raise AppError("llm_invalid_json", "大模型返回结果必须是 JSON 对象", 502)
    return value


def _supports_json_object(provider: str, model: str) -> bool:
    """判断指定的 provider+model 组合是否支持 JSON object 响应格式。"""
    lowered = model.lower()
    # 推理类模型不支持 response_format=json_object
    if any(token in lowered for token in ("reasoner", "-r1", "r1-", "thinking")):
        return False
    return provider in {"openai", "deepseek", "compatible"}


def _message_reasoning(message: dict[str, Any]) -> str:
    """从非流式响应消息中提取推理/思维链内容（如有）。"""
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


async def _emit_delta(on_delta: LLMDeltaCallback | None, kind: LLMDeltaKind, text: str) -> None:
    """向 delta 回调推送一个流式片段（reasoning 或 content）。"""
    if on_delta is None or not text:
        return
    result = on_delta(LLMStreamDelta(kind=kind, text=text))
    if result is not None:
        await result


def _build_user_content(payload: dict[str, Any]) -> str:
    """根据 payload 构建 LLM 请求的 user 消息内容，拼接重试反馈（如有）。"""
    user_content = (
        str(payload.get("_user_prompt"))
        if payload.get("_user_prompt")
        else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    if payload.get("retry_feedback"):
        user_content += "\n\n## 上一次输出校验失败，请仅修正以下问题\n" + json.dumps(
            payload["retry_feedback"], ensure_ascii=False, indent=2
        )
    return user_content


def _safe_json(value: Any) -> str:
    """安全地将任意值序列化为 JSON 字符串，序列化失败时回退到 repr。"""
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        return repr(value)


async def _execute_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执行模型返回的工具调用列表，返回每个工具的结果消息。"""
    outputs: list[dict[str, Any]] = []
    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AppError("llm_tool_arguments_invalid", f"工具参数解析失败：{type(exc).__name__}", 502) from exc
        result = await execute_llm_tool(name, arguments)
        outputs.append({
            "tool_call_id": str(call.get("id") or ""),
            "role": "tool",
            "name": name,
            "content": json.dumps(result, ensure_ascii=False),
        })
    return outputs


async def _call_llm_openai_compatible_stream(
    *,
    profile: dict[str, Any],
    system: str,
    user_content: str,
    payload: dict[str, Any],
    on_delta: LLMDeltaCallback | None,
    request_started: float,
) -> LLMResponse:
    """以流式方式调用 OpenAI 兼容接口（含 DeepSeek），实时推送 delta 并返回完整响应。"""
    provider = profile["provider"]
    model = profile["model"]
    key = profile["api_key"]
    default_base = "https://api.openai.com/v1" if provider == "openai" else "https://api.deepseek.com"
    base = (profile.get("base_url") or default_base).rstrip("/")
    endpoint = (
        f"{base}/chat/completions"
        if base.endswith("/v1") or provider == "deepseek"
        else f"{base}/v1/chat/completions"
    )
    request_json: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        "temperature": 0.1,
        "stream": True,
    }
    tools = list_llm_tools()
    if tools:
        request_json["tools"] = tools
        request_json["tool_choice"] = "auto"
    if _supports_json_object(provider, model):
        request_json["response_format"] = {"type": "json_object"}

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    prompt = 0
    completion = 0
    total = 0
    body_id = ""
    response_model = model
    request_id: str | None = None

    timeout = httpx.Timeout(180.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            endpoint,
            headers={"Authorization": f"Bearer {key}"},
            json=request_json,
        ) as response:
            request_id = (
                response.headers.get("x-request-id")
                or response.headers.get("request-id")
            )
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", errors="replace")
                raise AppError(
                    "llm_provider_error",
                    f"大模型调用失败：HTTP {response.status_code}",
                    502,
                    [{"body": body[:500]}],
                )
            async for line in response.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("id"):
                    body_id = str(chunk["id"])
                if chunk.get("model"):
                    response_model = str(chunk["model"])
                usage = chunk.get("usage") or {}
                if usage:
                    prompt = int(usage.get("prompt_tokens", prompt) or prompt)
                    completion = int(usage.get("completion_tokens", completion) or completion)
                    total = int(usage.get("total_tokens", prompt + completion) or (prompt + completion))
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning_piece:
                    piece = str(reasoning_piece)
                    reasoning_parts.append(piece)
                    await _emit_delta(on_delta, "reasoning", piece)
                content_piece = delta.get("content")
                if content_piece:
                    piece = str(content_piece)
                    content_parts.append(piece)
                    await _emit_delta(on_delta, "content", piece)

    text = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    if payload.get("_preserve_raw"):
        try:
            parsed_content = _json_content(text) if text.strip() else {}
        except AppError:
            parsed_content = {}
    else:
        parsed_content = _json_content(text)
    if total <= 0:
        total = prompt + completion
    return LLMResponse(
        parsed_content,
        prompt,
        completion,
        total,
        profile["id"],
        model,
        raw_content=text,
        reasoning_content=reasoning,
        provider=provider,
        provider_request_id=request_id or body_id or None,
        response_model=response_model,
        duration_ms=round((time.monotonic() - request_started) * 1000),
        raw_response={"id": body_id, "model": response_model, "streamed": True},
    )


async def call_llm(
    system: str,
    payload: dict[str, Any],
    *,
    on_delta: LLMDeltaCallback | None = None,
) -> LLMResponse | None:
    """调用大模型完成一次分析请求，支持流式 delta 回调和工具调用。

    根据 profile 中的 provider 字段自动路由到 OpenAI/DeepSeek、Anthropic 或 Gemini 接口。
    当 provider 为 OpenAI 兼容且提供了 on_delta 时，优先使用流式路径。
    返回解析后的 LLMResponse，若未配置模型则返回 None。
    """
    profile = get_active_model()
    if profile is None:
        return None
    if not profile.get("api_key"):
        raise AppError("llm_api_key_missing", "当前模型尚未配置 API Key", 422)

    provider = profile["provider"]
    model = profile["model"]
    key = profile["api_key"]
    tools = list_llm_tools()
    run_id = str(payload.get("run_id") or payload.get("_run_id") or "unknown")
    user_content = _build_user_content(payload)
    timeout = httpx.Timeout(180.0, connect=15.0)
    request_started = time.monotonic()
    logger.info(
        "LLM request run_id=%s provider=%s model=%s payload=%s",
        run_id,
        provider,
        model,
        _safe_json({
            "system": system,
            "payload": payload,
            "user_content": user_content,
        }),
    )

    lf_trace = start_trace(
        "call_llm",
        user_id=run_id,
        metadata=_trace_metadata({"provider": provider, "model": model}),
    )
    lf_gen = None
    if lf_trace is not None:
        try:
            lf_gen = lf_trace.generation(
                name="llm-request",
                model=model,
                input={"system": system, "user_content": user_content},
                metadata=_trace_metadata({"run_id": run_id, "provider": provider}),
            )
        except Exception:
            lf_gen = None

    if provider in {"openai", "deepseek", "compatible"} and on_delta is not None:
        try:
            response = await _call_llm_openai_compatible_stream(
                profile=profile,
                system=system,
                user_content=user_content,
                payload=payload,
                on_delta=on_delta,
                request_started=request_started,
            )
            logger.info(
                "LLM response run_id=%s provider=%s model=%s response=%s",
                run_id,
                provider,
                model,
                _safe_json(response.raw_response or {
                    "raw_content": response.raw_content,
                    "reasoning_content": response.reasoning_content,
                    "content": response.content,
                }),
            )
            end_generation(
                lf_gen,
                output=response.raw_content,
                usage={
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                },
                metadata={
                    "duration_ms": response.duration_ms,
                    "response_model": response.response_model,
                    "provider_request_id": response.provider_request_id,
                },
            )
            return response
        except AppError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AppError("llm_provider_error", f"大模型调用失败：{type(exc).__name__}", 502) from exc

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            if provider in {"openai", "deepseek", "compatible"}:
                default_base = "https://api.openai.com/v1" if provider == "openai" else "https://api.deepseek.com"
                base = (profile.get("base_url") or default_base).rstrip("/")
                endpoint = (
                    f"{base}/chat/completions"
                    if base.endswith("/v1") or provider == "deepseek"
                    else f"{base}/v1/chat/completions"
                )
                request_json: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.1,
                }
                if tools:
                    request_json["tools"] = tools
                    request_json["tool_choice"] = "auto"
                if _supports_json_object(provider, model):
                    request_json["response_format"] = {"type": "json_object"}
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    json=request_json,
                )
                response.raise_for_status()
                body = response.json()
                message = body["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    followup_messages = [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls},
                    ]
                    followup_messages.extend(await _execute_tool_calls(tool_calls))
                    followup_response = await client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": model,
                            "messages": followup_messages,
                            "temperature": 0.1,
                            "response_format": request_json.get("response_format"),
                        },
                    )
                    followup_response.raise_for_status()
                    body = followup_response.json()
                    message = body["choices"][0]["message"]
                text = str(message.get("content") or "")
                reasoning = _message_reasoning(message)
                usage = body.get("usage", {})
                prompt = int(usage.get("prompt_tokens", 0))
                completion = int(usage.get("completion_tokens", 0))
                total = int(usage.get("total_tokens", prompt + completion))
            elif provider == "anthropic":
                base = (profile.get("base_url") or "https://api.anthropic.com").rstrip("/")
                response = await client.post(
                    f"{base}/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    json={
                        "model": model,
                        "system": system,
                        "messages": [{"role": "user", "content": user_content}],
                        "max_tokens": 4000,
                    },
                )
                response.raise_for_status()
                body = response.json()
                text = body["content"][0]["text"]
                reasoning = ""
                usage = body.get("usage", {})
                prompt = int(usage.get("input_tokens", 0))
                completion = int(usage.get("output_tokens", 0))
                total = prompt + completion
            else:
                base = (profile.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
                response = await client.post(
                    f"{base}/v1beta/models/{model}:generateContent",
                    params={"key": key},
                    json={
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"parts": [{"text": user_content}]}],
                        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                    },
                )
                response.raise_for_status()
                body = response.json()
                text = body["candidates"][0]["content"]["parts"][0]["text"]
                reasoning = ""
                usage = body.get("usageMetadata", {})
                prompt = int(usage.get("promptTokenCount", 0))
                completion = int(usage.get("candidatesTokenCount", 0))
                total = int(usage.get("totalTokenCount", prompt + completion))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AppError("llm_provider_error", f"大模型调用失败：{type(exc).__name__}", 502) from exc

    if reasoning:
        await _emit_delta(on_delta, "reasoning", reasoning)
    if text:
        await _emit_delta(on_delta, "content", text)

    if payload.get("_preserve_raw"):
        try:
            parsed_content = _json_content(text)
        except AppError:
            parsed_content = {}
    else:
        parsed_content = _json_content(text)
    response_payload = LLMResponse(
        parsed_content,
        prompt,
        completion,
        total,
        profile["id"],
        model,
        raw_content=text,
        reasoning_content=reasoning,
        provider=provider,
        provider_request_id=(
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
            or str(body.get("id") or "")
            or None
        ),
        response_model=str(body.get("model") or model),
        duration_ms=round((time.monotonic() - request_started) * 1000),
        raw_response=body,
    )
    logger.info(
        "LLM response run_id=%s provider=%s model=%s response=%s",
        run_id,
        provider,
        model,
        _safe_json({
            "raw_content": response_payload.raw_content,
            "reasoning_content": response_payload.reasoning_content,
            "content": response_payload.content,
            "raw_response": response_payload.raw_response,
            "provider_request_id": response_payload.provider_request_id,
            "response_model": response_payload.response_model,
            "duration_ms": response_payload.duration_ms,
            "usage": {
                "prompt_tokens": response_payload.prompt_tokens,
                "completion_tokens": response_payload.completion_tokens,
                "total_tokens": response_payload.total_tokens,
            },
        }),
    )
    end_generation(
        lf_gen,
        output=response_payload.raw_content,
        usage={
            "prompt_tokens": response_payload.prompt_tokens,
            "completion_tokens": response_payload.completion_tokens,
            "total_tokens": response_payload.total_tokens,
        },
        metadata={
            "duration_ms": response_payload.duration_ms,
            "response_model": response_payload.response_model,
            "provider_request_id": response_payload.provider_request_id,
        },
    )
    return response_payload


def _active_chat_profile() -> dict[str, Any]:
    """获取当前激活的聊天模型 profile，若未配置则抛出 AppError。"""
    profile = get_active_model()
    if profile is None:
        raise AppError("llm_model_missing", "尚未配置可用大模型", 422)
    if not profile.get("api_key"):
        raise AppError("llm_api_key_missing", "当前模型尚未配置 API Key", 422)
    return profile


def _split_system_messages(messages: list[dict[str, str]]) -> tuple[str | None, list[dict[str, str]]]:
    """将混合消息列表拆分为 system 提示词和 user/assistant 对话消息。"""
    system_parts: list[str] = []
    chat: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role == "system":
            if content.strip():
                system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            continue
        chat.append({"role": role, "content": content})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, chat


_MAX_TOOL_ROUNDS = 3


async def _stream_openai_compatible_with_tools(
    *,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    payload_messages: list[dict[str, Any]],
    timeout: httpx.Timeout,
) -> AsyncIterator[str]:
    """Stream a chat completion, transparently executing tool calls.

    When the model emits ``tool_calls`` alongside (or instead of) text, this
    function collects the call deltas, executes each tool via the shared
    registry, appends the results as ``role=tool`` messages, and re-streams.
    Up to ``_MAX_TOOL_ROUNDS`` rounds are allowed before bailing out.
    """
    from app.llm.tools import execute_llm_tool, list_llm_tools

    tools = list_llm_tools()
    current_messages = list(payload_messages)

    for _round in range(_MAX_TOOL_ROUNDS):
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                endpoint,
                headers=headers,
                json={
                    "model": model,
                    "messages": current_messages,
                    "temperature": 0.2,
                    "stream": True,
                    **({"tools": tools, "tool_choice": "auto"} if tools else {}),
                },
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise AppError(
                        "llm_provider_error",
                        f"大模型调用失败：HTTP {response.status_code}",
                        502,
                        [{"body": body[:500]}],
                    )
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    finish_reason = choices[0].get("finish_reason") or finish_reason
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(str(piece))
                        yield str(piece)
                    for call in delta.get("tool_calls") or []:
                        idx = call.get("index", 0)
                        acc = tool_calls_by_index.setdefault(idx, {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if call.get("id"):
                            acc["id"] = call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            acc["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            acc["function"]["arguments"] += fn["arguments"]

        if not tool_calls_by_index:
            return

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts) or None}
        assistant_msg["tool_calls"] = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
        current_messages.append(assistant_msg)

        for idx in sorted(tool_calls_by_index):
            call = tool_calls_by_index[idx]
            fn_name = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError, ValueError):
                args = {}
            result = await execute_llm_tool(fn_name, args)
            current_messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": fn_name,
                "content": json.dumps(result, ensure_ascii=False),
            })

    logger.warning("stream_chat exceeded %d tool rounds, stopping", _MAX_TOOL_ROUNDS)


async def stream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """以流式方式完成一次自由格式聊天，逐段 yield 文本片段。

    与 ``call_llm``（分析 JSON 模式）不同，此函数：
    - 接受完整的 Chat 格式 messages 列表
    - 不强制 response_format=json_object
    - 逐段 yield 文本 delta（非流式 provider 会一次性 yield 完整内容）
    - 支持 OpenAI 兼容 provider 的工具调用：模型发起 tool_calls 时自动执行并继续对话
    """
    if not messages:
        raise AppError("llm_empty_messages", "聊天消息不能为空", 422)

    profile = _active_chat_profile()
    provider = profile["provider"]
    model = profile["model"]
    key = profile["api_key"]
    system, chat_messages = _split_system_messages(messages)  # 拆分 system 与对话消息
    if not chat_messages:
        raise AppError("llm_empty_messages", "聊天消息不能为空", 422)

    lf_trace = start_trace(
        "stream_chat",
        metadata=_trace_metadata({"provider": provider, "model": model}),
    )
    lf_gen = None
    if lf_trace is not None:
        try:
            lf_gen = lf_trace.generation(
                name="chat-request",
                model=model,
                input=chat_messages,
                metadata=_trace_metadata({"provider": provider}),
            )
        except Exception:
            lf_gen = None

    _output_parts: list[str] = []
    timeout = httpx.Timeout(120.0, connect=15.0)

    try:
        if provider in {"openai", "deepseek", "compatible"}:
            default_base = "https://api.openai.com/v1" if provider == "openai" else "https://api.deepseek.com"
            base = (profile.get("base_url") or default_base).rstrip("/")
            endpoint = (
                f"{base}/chat/completions"
                if base.endswith("/v1") or provider == "deepseek"
                else f"{base}/v1/chat/completions"
            )
            payload_messages = ([{"role": "system", "content": system}] if system else []) + chat_messages
            async for piece in _stream_openai_compatible_with_tools(
                endpoint=endpoint,
                headers={"Authorization": f"Bearer {key}"},
                model=model,
                payload_messages=payload_messages,
                timeout=timeout,
            ):
                _output_parts.append(piece)
                yield piece
            end_generation(lf_gen, output="".join(_output_parts), usage=None, metadata={"provider": provider})
            return

        if provider == "anthropic":
            base = (profile.get("base_url") or "https://api.anthropic.com").rstrip("/")
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base}/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    json={
                        "model": model,
                        "system": system or "",
                        "messages": chat_messages,
                        "max_tokens": 4000,
                        "temperature": 0.2,
                    },
                )
                response.raise_for_status()
                body = response.json()
                text = body["content"][0]["text"]
                usage = body.get("usage", {})
                prompt = int(usage.get("input_tokens", 0))
                completion = int(usage.get("output_tokens", 0))
                if text:
                    _output_parts.append(text)
                    yield text
                end_generation(
                    lf_gen,
                    output=text,
                    usage={"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
                    metadata={"provider": provider},
                )
            return

        # Gemini / other: single-shot free-form reply.
        base = (profile.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
        contents: list[dict[str, Any]] = []
        for message in chat_messages:
            role = "user" if message["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": message["content"]}]})
        request_json: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }
        if system:
            request_json["systemInstruction"] = {"parts": [{"text": system}]}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base}/v1beta/models/{model}:generateContent",
                params={"key": key},
                json=request_json,
            )
            response.raise_for_status()
            body = response.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            usage = body.get("usageMetadata", {})
            prompt = int(usage.get("promptTokenCount", 0))
            completion = int(usage.get("candidatesTokenCount", 0))
            if text:
                _output_parts.append(text)
                yield text
            end_generation(
                lf_gen,
                output=text,
                usage={"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
                metadata={"provider": provider},
            )
    except AppError:
        end_generation(lf_gen, output="".join(_output_parts), usage=None, metadata={"error": "AppError"})
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError) as exc:
        end_generation(lf_gen, output="".join(_output_parts), usage=None, metadata={"error": type(exc).__name__})
        raise AppError("llm_provider_error", f"大模型调用失败：{type(exc).__name__}", 502) from exc
