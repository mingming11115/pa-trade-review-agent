from __future__ import annotations

import logging
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_trace_id_var: ContextVar[str] = ContextVar("pa_trace_id", default="-")
_task_id_var: ContextVar[str] = ContextVar("pa_task_id", default="-")
_run_id_var: ContextVar[str] = ContextVar("pa_run_id", default="-")

_FIELD_VARS = {
    "trace_id": _trace_id_var,
    "task_id": _task_id_var,
    "run_id": _run_id_var,
}


@dataclass(frozen=True, slots=True)
class TraceContextTokens:
    tokens: dict[str, Token[str]]


def normalize_trace_id(value: str | None) -> str:
    """Return a safe caller-provided trace id or generate a UUID."""
    if value and _TRACE_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


def get_trace_id() -> str:
    return _trace_id_var.get()


def get_trace_fields() -> dict[str, str]:
    return {name: variable.get() for name, variable in _FIELD_VARS.items()}


def set_trace_context(
    trace_id: str,
    *,
    task_id: str | None = None,
    run_id: str | None = None,
) -> TraceContextTokens:
    values = {
        "trace_id": trace_id,
        "task_id": task_id or "-",
        "run_id": run_id or "-",
    }
    return TraceContextTokens(
        {
            name: _FIELD_VARS[name].set(str(value))
            for name, value in values.items()
        }
    )


def reset_trace_context(tokens: TraceContextTokens) -> None:
    for name, token in reversed(tuple(tokens.tokens.items())):
        _FIELD_VARS[name].reset(token)


@contextmanager
def bind_trace_fields(
    *,
    task_id: str | None = None,
    run_id: str | None = None,
) -> Iterator[None]:
    values = {
        "task_id": task_id,
        "run_id": run_id,
    }
    tokens = {
        name: _FIELD_VARS[name].set(str(value))
        for name, value in values.items()
        if value is not None
    }
    try:
        yield
    finally:
        for name, token in reversed(tuple(tokens.items())):
            _FIELD_VARS[name].reset(token)


class TraceContextFilter(logging.Filter):
    """Attach the active trace context to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, value in get_trace_fields().items():
            setattr(record, name, value)
        return True


def set_request_id(request_id: str) -> None:
    """Compatibility alias for callers that still use request_id."""
    _trace_id_var.set(request_id)


def get_request_id() -> str:
    """Compatibility alias returning the active application trace id."""
    return get_trace_id()
