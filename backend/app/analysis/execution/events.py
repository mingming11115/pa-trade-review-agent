from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AnalysisEvent:
    execution_id: uuid.UUID
    sequence: int
    type: str
    stage: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    terminal: bool = False


_buffers: dict[uuid.UUID, list[AnalysisEvent]] = {}


async def append_event(
    execution_id: uuid.UUID,
    type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    terminal: bool = False,
) -> AnalysisEvent:
    events = _buffers.setdefault(execution_id, [])
    event = AnalysisEvent(
        execution_id=execution_id,
        sequence=len(events) + 1,
        type=type,
        stage=stage,
        message=message,
        payload=payload or {},
        terminal=terminal,
    )
    events.append(event)
    return event


async def list_events(execution_id: uuid.UUID, after_sequence: int = 0) -> list[AnalysisEvent]:
    events = _buffers.get(execution_id, [])
    return [event for event in events if event.sequence > after_sequence]


def drop_events(execution_id: uuid.UUID) -> None:
    _buffers.pop(execution_id, None)
