from __future__ import annotations

import os
import uuid
import difflib
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, SessionFactory, ensure_schema
from app.core.errors import AppError
from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.analysis.workflow.stage1.core.prompt_assembler import (
    COMMON_SYSTEM_STAGE1_TXT_FILES,
    COMMON_SYSTEM_STAGE2_TXT_FILES,
    STAGE1_TASK_PROMPT_TXT_FILES,
    STAGE2_BASE_PROMPT_TXT_FILES,
    STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES,
    _SYSTEM_PROMPT_CACHE,
)


_LOCK = Lock()


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(30), default="save")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class PromptVersionPublic(BaseModel):
    id: uuid.UUID
    filename: str
    version: str
    content: str
    actor: str
    action: str
    created_at: datetime
    model_config = {"from_attributes": True}


class PromptVersionDiff(BaseModel):
    from_version: str
    to_version: str
    unified_diff: str


class PromptFileRef(BaseModel):
    filename: str
    placement: Literal["system", "user"]
    condition: str
    editable: bool = True


class PipelineStage(BaseModel):
    id: str
    name: str
    kind: Literal["deterministic", "llm", "gate", "review"]
    description: str
    prompt_files: list[PromptFileRef] = Field(default_factory=list)


class PipelineEdge(BaseModel):
    source: str
    target: str
    condition: str


class OrchestrationView(BaseModel):
    stages: list[PipelineStage]
    edges: list[PipelineEdge]


class PromptFileDocument(BaseModel):
    filename: str
    content: str
    version: str
    size: int


class PromptFileUpdate(BaseModel):
    content: str = Field(max_length=1_000_000)
    expected_version: str | None = None


def _refs(
    filenames: tuple[str, ...],
    placement: Literal["system", "user"],
    condition: str,
) -> list[PromptFileRef]:
    return [PromptFileRef(filename=name, placement=placement, condition=condition) for name in filenames]


def get_orchestration() -> OrchestrationView:
    stage1_files = [
        *_refs(COMMON_SYSTEM_STAGE1_TXT_FILES, "system", "每次 Stage 1 固定加载"),
        *_refs(STAGE1_TASK_PROMPT_TXT_FILES, "user", "每次 Stage 1 固定加载"),
    ]
    stage2_files = [
        *_refs(COMMON_SYSTEM_STAGE2_TXT_FILES, "system", "与 Stage 1 共用稳定前缀"),
        *_refs(STAGE2_BASE_PROMPT_TXT_FILES, "user", "每次 Stage 2 固定加载"),
        *_refs(STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES, "user", "按 Stage 1 路由结果加载；全量模式全部加载"),
    ]
    return OrchestrationView(
        stages=[
            PipelineStage(id="snapshot", name="行情快照", kind="deterministic", description="聚合 K 线并计算 EMA20、ATR14 与程序市场特征"),
            PipelineStage(id="stage1", name="Stage 1 · 市场诊断", kind="llm", description="识别周期、方向、结构、形态并输出 gate_trace", prompt_files=stage1_files),
            PipelineStage(id="gate", name="Gate · 阶段闸门", kind="gate", description="仅 gate_result=proceed 时进入 Stage 2"),
            PipelineStage(id="stage2", name="Stage 2 · 交易决策", kind="llm", description="根据 Stage 1 路由策略文本，生成交易或等待决策", prompt_files=stage2_files),
            PipelineStage(id="trade_review", name="Trade Review · 交易复盘", kind="review", description="将阶段决策与导入交易的执行结果进行对比复盘"),
            PipelineStage(id="memory", name="分析记忆", kind="deterministic", description="保存本轮 Stage 1/2 摘要，供下一轮连续分析使用"),
        ],
        edges=[
            PipelineEdge(source="snapshot", target="stage1", condition="数据预检通过"),
            PipelineEdge(source="stage1", target="gate", condition="Stage 1 完成"),
            PipelineEdge(source="gate", target="stage2", condition="gate_result = proceed"),
            PipelineEdge(source="gate", target="trade_review", condition="gate_result = wait / unknown，Stage 2 短路"),
            PipelineEdge(source="stage2", target="trade_review", condition="Stage 2 完成"),
            PipelineEdge(source="trade_review", target="memory", condition="分析结束"),
        ],
    )


def _allowed_filenames() -> set[str]:
    view = get_orchestration()
    return {item.filename for stage in view.stages for item in stage.prompt_files}


def _prompt_path(filename: str) -> Path:
    if filename != Path(filename).name or filename not in _allowed_filenames() or not filename.endswith(".txt"):
        raise AppError("prompt_file_not_allowed", "该提示词文件不允许访问", 404)
    path = PROMPT_DIR / filename
    if not path.is_file():
        raise AppError("prompt_file_missing", "提示词文件不存在", 404)
    return path


def _version(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()[:16]


def get_prompt_file(filename: str) -> PromptFileDocument:
    path = _prompt_path(filename)
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AppError("prompt_file_read_failed", "提示词文件读取失败", 500) from exc
    return PromptFileDocument(filename=filename, content=content, version=_version(content), size=len(content.encode("utf-8")))


def save_prompt_file(filename: str, update: PromptFileUpdate) -> PromptFileDocument:
    path = _prompt_path(filename)
    with _LOCK:
        current = get_prompt_file(filename)
        if update.expected_version and update.expected_version != current.version:
            raise AppError("prompt_file_conflict", "文件已被其他操作修改，请刷新后重试", 409)
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                handle.write(update.content)
                temporary = Path(handle.name)
            os.chmod(temporary, path.stat().st_mode)
            temporary.replace(path)
            _SYSTEM_PROMPT_CACHE.clear()
        except OSError as exc:
            raise AppError("prompt_file_write_failed", "提示词文件保存失败", 500) from exc
    return get_prompt_file(filename)


async def record_prompt_version(document: PromptFileDocument, actor: str, action: str = "save") -> None:
    await ensure_schema()
    async with SessionFactory() as session:
        exists = await session.scalar(select(PromptVersion.id).where(PromptVersion.filename == document.filename, PromptVersion.version == document.version))
        if not exists:
            session.add(PromptVersion(filename=document.filename, version=document.version, content=document.content, actor=actor, action=action)); await session.commit()


async def list_prompt_versions(filename: str) -> list[PromptVersionPublic]:
    _prompt_path(filename); await ensure_schema()
    async with SessionFactory() as session:
        rows = (await session.scalars(select(PromptVersion).where(PromptVersion.filename == filename).order_by(PromptVersion.created_at.desc()).limit(100))).all()
    return [PromptVersionPublic.model_validate(row) for row in rows]


async def rollback_prompt_version(filename: str, version_id: uuid.UUID, actor: str) -> PromptFileDocument:
    await ensure_schema()
    async with SessionFactory() as session:
        version = await session.get(PromptVersion, version_id)
    if version is None or version.filename != filename:
        raise AppError("prompt_version_not_found", "提示词版本不存在", 404)
    current = get_prompt_file(filename)
    document = save_prompt_file(filename, PromptFileUpdate(content=version.content, expected_version=current.version))
    await record_prompt_version(document, actor, "rollback")
    return document


async def diff_prompt_versions(filename: str, left_id: uuid.UUID, right_id: uuid.UUID) -> PromptVersionDiff:
    _prompt_path(filename); await ensure_schema()
    async with SessionFactory() as session:
        left, right = await session.get(PromptVersion, left_id), await session.get(PromptVersion, right_id)
    if left is None or right is None or left.filename != filename or right.filename != filename:
        raise AppError("prompt_version_not_found", "提示词版本不存在", 404)
    diff = "".join(difflib.unified_diff(left.content.splitlines(keepends=True), right.content.splitlines(keepends=True), fromfile=left.version, tofile=right.version))
    return PromptVersionDiff(from_version=left.version, to_version=right.version, unified_diff=diff)
