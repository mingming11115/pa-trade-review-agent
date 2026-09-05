from __future__ import annotations

import logging
from uuid import UUID

from app.core.logging_context import (
    TraceContextFilter,
    bind_trace_fields,
    get_request_id,
    get_trace_fields,
    get_trace_id,
    normalize_trace_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)


def test_normalize_trace_id_accepts_safe_value_and_replaces_unsafe_value() -> None:
    assert normalize_trace_id("web_123.a-b") == "web_123.a-b"

    generated = normalize_trace_id("bad value\nforged")

    assert str(UUID(generated)) == generated
    assert "bad" not in generated


def test_trace_context_is_reset_and_filter_adds_business_fields() -> None:
    assert get_trace_id() == "-"
    tokens = set_trace_context(
        "trace-1",
        task_id="task-1",
        run_id="run-1",
    )
    try:
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1, "event", (), None
        )

        assert TraceContextFilter().filter(record) is True
        assert (
            record.trace_id,
            record.task_id,
            record.run_id,
        ) == ("trace-1", "task-1", "run-1")
        assert get_trace_fields() == {
            "trace_id": "trace-1",
            "task_id": "task-1",
            "run_id": "run-1",
        }
    finally:
        reset_trace_context(tokens)

    assert get_trace_id() == "-"


def test_bind_trace_fields_restores_outer_context() -> None:
    tokens = set_trace_context("trace-outer", task_id="task-outer")
    try:
        with bind_trace_fields(run_id="run-child"):
            assert get_trace_fields()["task_id"] == "task-outer"
            assert get_trace_fields()["run_id"] == "run-child"

        assert get_trace_fields()["run_id"] == "-"
    finally:
        reset_trace_context(tokens)


def test_request_id_compatibility_uses_trace_context() -> None:
    previous = get_trace_id()
    set_request_id("legacy-request")
    try:
        assert get_request_id() == "legacy-request"
        assert get_trace_id() == "legacy-request"
    finally:
        set_request_id(previous)
