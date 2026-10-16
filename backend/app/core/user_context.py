"""Trusted user identity inherited by request-scoped LLM and background tasks."""
from contextvars import ContextVar, Token
from uuid import UUID

_current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)


def get_current_user_id() -> UUID | None:
    return _current_user_id.get()


def set_current_user_id(user_id: UUID | None) -> Token[UUID | None]:
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token[UUID | None]) -> None:
    _current_user_id.reset(token)
