from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.models import HistoricalQuery, Period


UTC = timezone.utc


def normalize_analysis_symbol(value: str) -> str:
    return value.strip().upper()


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class RunStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    degraded = "degraded"
    failed = "failed"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"
    timed_out = "timed_out"


class StageRunStatus(str, Enum):
    request_started = "request_started"
    response_received = "response_received"
    validating = "validating"
    validated = "validated"
    completed = "completed"
    validation_failed = "validation_failed"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class AnalysisTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=100)
    period: Period

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = normalize_analysis_symbol(value)
        if not normalized:
            raise ValueError("symbol must not be blank")
        return normalized


class ReviewTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_trade_ids: list[str] = Field(min_length=1)
    periods: list[Period] = Field(min_length=1)


class AnalysisTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["analysis", "review"]
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    config: AnalysisTaskConfig | ReviewTaskConfig | dict[str, Any]

    @model_validator(mode="after")
    def validate_config_for_kind(self) -> "AnalysisTaskCreate":
        config_type = AnalysisTaskConfig if self.kind == "analysis" else ReviewTaskConfig
        self.config = TypeAdapter(config_type).validate_python(self.config)
        return self


class AnalysisTaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    config: dict[str, Any]

    def validated_config(self, kind: str) -> AnalysisTaskConfig | ReviewTaskConfig:
        config_type = AnalysisTaskConfig if kind == "analysis" else ReviewTaskConfig
        return TypeAdapter(config_type).validate_python(self.config)


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default=TaskStatus.pending.value, index=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    analysis_symbol: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    analysis_period: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "uq_analysis_tasks_owner_symbol_period_active",
    AnalysisTask.user_id,
    AnalysisTask.analysis_symbol,
    AnalysisTask.analysis_period,
    unique=True,
    postgresql_where=text("kind = 'analysis' AND archived_at IS NULL AND user_id IS NOT NULL"),
    sqlite_where=text("kind = 'analysis' AND archived_at IS NULL AND user_id IS NOT NULL"),
)
Index(
    "uq_analysis_tasks_local_symbol_period_active",
    AnalysisTask.analysis_symbol,
    AnalysisTask.analysis_period,
    unique=True,
    postgresql_where=text("kind = 'analysis' AND archived_at IS NULL AND user_id IS NULL"),
    sqlite_where=text("kind = 'analysis' AND archived_at IS NULL AND user_id IS NULL"),
)


class AnalysisRun(Base):
    """Task 中一个时间周期的完整运行。"""

    __tablename__ = "analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default=RunStatus.queued.value, index=True)
    current_stage: Mapped[str] = mapped_column(String(40), default="prepare")
    failure_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolved_symbol: Mapped[str] = mapped_column(String(100), default="")
    bars_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    bars_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    prompt_versions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(40), default="historical", index=True)
    symbol: Mapped[str] = mapped_column(String(100), default="")
    period: Mapped[str] = mapped_column(String(20), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    direction: Mapped[str] = mapped_column(String(20), default="neutral")
    terminal_outcome: Mapped[str] = mapped_column(String(40), default="wait")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


Index("uq_analysis_run_task_period", AnalysisRun.task_id, AnalysisRun.period, unique=True)
Index("ix_analysis_runs_symbol_period", AnalysisRun.symbol, AnalysisRun.period)
Index("ix_analysis_runs_mode_status_created", AnalysisRun.mode, AnalysisRun.status, AnalysisRun.created_at)


class AnalysisStageAttempt(Base):
    """阶段尝试审计：Stage1/Stage2 单次调用或校验尝试的明细，独立于运行行存储。"""

    __tablename__ = "analysis_stage_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    response_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    raw_content: Mapped[str] = mapped_column(Text, default="")
    reasoning_content: Mapped[str] = mapped_column(Text, default="")
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    normalized_output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_errors: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    provider_error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    prompt_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "uq_analysis_stage_attempt_run_stage_attempt",
    AnalysisStageAttempt.run_id,
    AnalysisStageAttempt.stage,
    AnalysisStageAttempt.attempt,
    unique=True,
)


class AnalysisAnnotation(Base):
    """用户标注：收藏、笔记、标签，独立于模型产出的 `result_json`。"""

    __tablename__ = "analysis_annotations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


Index(
    "uq_analysis_annotation_run_user",
    AnalysisAnnotation.run_id,
    AnalysisAnnotation.user_id,
    unique=True,
)


class AnalysisTaskPublic(BaseModel):
    id: uuid.UUID
    kind: Literal["analysis", "review"]
    title: str
    description: str
    status: TaskStatus
    config: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AnalysisRunPublic(BaseModel):
    run_id: uuid.UUID = Field(validation_alias="id")
    task_id: uuid.UUID
    period: str
    status: RunStatus
    current_stage: str
    failure_stage: str | None
    failure_code: str | None
    failure_message: str | None
    terminal_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AnalysisRunListItem(BaseModel):
    run_id: uuid.UUID = Field(validation_alias="id")
    task_id: uuid.UUID | str
    status: str
    created_at: datetime
    completed_at: datetime | None
    direction: str | None
    terminal_outcome: str | None
    symbol: str | None
    period: str | None
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AnalysisRunDetailPublic(BaseModel):
    run_id: uuid.UUID = Field(validation_alias="id")
    task_id: uuid.UUID
    status: str
    mode: str
    symbol: str
    period: str
    direction: str
    terminal_outcome: str
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any]
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SnapshotPreviewPublic(BaseModel):
    snapshot_id: uuid.UUID
    confirmation_id: str
    expires_at: datetime
    resolved_symbol: str
    bars_hash: str
    bar_count: int


class EnsureLiveAnalysisTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=100)
    period: Period
    title: str | None = Field(default=None, max_length=200)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = normalize_analysis_symbol(value)
        if not normalized:
            raise ValueError("symbol must not be blank")
        return normalized


Index("ix_analysis_tasks_owner_created", AnalysisTask.user_id, AnalysisTask.created_at)
