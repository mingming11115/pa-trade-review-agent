from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PromptEntry(Base):
    __tablename__ = 'knowledge_prompt_entries'
    __table_args__ = (UniqueConstraint('scope_key', 'filename'),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(String(80))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    filename: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PromptVersion(Base):
    __tablename__ = 'knowledge_prompt_versions'
    __table_args__ = (UniqueConstraint('entry_id', 'revision'),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('knowledge_prompt_entries.id'))
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RunPromptSnapshot(Base):
    __tablename__ = 'knowledge_prompt_run_snapshots'

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(80))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    entries_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
