from datetime import UTC, datetime
from typing import Any
import uuid
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ReplaySession(Base):
    __tablename__ = 'replay_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scope: Mapped[str] = mapped_column(String(80), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default='active')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ReplayEvent(Base):
    __tablename__ = 'replay_events'
    __table_args__ = (UniqueConstraint('session_id', 'operation_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey('replay_sessions.id'), index=True)
    operation_id: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ReplayOrderRecord(Base):
    __tablename__ = 'replay_orders'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('replay_sessions.id'), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReplayFillRecord(Base):
    __tablename__ = 'replay_fills'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey('replay_sessions.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReplayTradeSnapshot(Base):
    __tablename__ = 'replay_trade_snapshots'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey('replay_sessions.id'), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReplaySavedVersion(Base):
    __tablename__ = 'replay_saved_versions'
    __table_args__ = (UniqueConstraint('session_id', 'operation_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scope: Mapped[str] = mapped_column(String(80), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('replay_sessions.id'), index=True)
    operation_id: Mapped[str] = mapped_column(String(120))
    source_revision: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class ReplayDraftState(Base):
    __tablename__ = 'replay_drafts'
    __table_args__ = (UniqueConstraint('scope', 'resume_operation_id'),)
    session_id: Mapped[str] = mapped_column(ForeignKey('replay_sessions.id'), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    origin_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resume_operation_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    discarded: Mapped[int] = mapped_column(Integer, default=0)


class ReplayAnalysisInput(Base):
    __tablename__ = 'replay_analysis_inputs'
    version_id: Mapped[str] = mapped_column(ForeignKey('replay_saved_versions.id'), primary_key=True)
    period: Mapped[str] = mapped_column(String(10), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReplayAnalysisLink(Base):
    __tablename__ = 'replay_analysis_links'
    version_id: Mapped[str] = mapped_column(ForeignKey('replay_saved_versions.id'), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    periods: Mapped[list[str]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
