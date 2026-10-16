from __future__ import annotations

import asyncio
import logging
import uuid
import traceback
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from sqlalchemy.exc import SQLAlchemyError

from app.analysis.workflow.stage1.core.prompt_assembler import (
    COMMON_SYSTEM_STAGE1_TXT_FILES, COMMON_SYSTEM_STAGE2_TXT_FILES,
    STAGE1_TASK_PROMPT_TXT_FILES, STAGE2_BASE_PROMPT_TXT_FILES,
    STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES,
)
from app.core.errors import AppError
from app.knowledge.contracts import (ImportResult, PromptSnapshot, SnapshotEntry, KnowledgeDocument,
                                     KnowledgeUpdate, KnowledgeVersion, KnowledgeVersionPage, KnowledgeSummary)
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.models import PromptEntry

logger = logging.getLogger(__name__)


def _log_database_failure(event: str, error: BaseException) -> None:
    # SQLAlchemy exception strings/parameters and database DETAIL can contain
    # entire prompt rows. Log frame locations without values or source lines.
    frames = traceback.extract_tb(error.__traceback__)
    stack = ' -> '.join(f'{frame.filename}:{frame.lineno}:{frame.name}' for frame in frames)
    logger.error('%s error_type=%s stack=%s', event, type(error).__name__, stack)


def prompt_filenames() -> tuple[str, ...]:
    """Canonical analysis file whitelist, preserving existing first-use order."""
    return tuple(dict.fromkeys((*COMMON_SYSTEM_STAGE1_TXT_FILES, *COMMON_SYSTEM_STAGE2_TXT_FILES,
                               *STAGE1_TASK_PROMPT_TXT_FILES, *STAGE2_BASE_PROMPT_TXT_FILES,
                               *STAGE2_FULL_STRATEGY_PROMPT_TXT_FILES)))


def _read_sources(prompt_dir: Path) -> dict[str, str]:
    contents = {}
    for filename in prompt_filenames():
        try:
            content = (prompt_dir / filename).read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            raise AppError('knowledge_import_source_invalid', '提示词导入源不可读取', 422,
                           [{'filename': filename}]) from exc
        if not content.strip():
            raise AppError('knowledge_import_source_invalid', '提示词导入源为空', 422,
                           [{'filename': filename}])
        contents[filename] = content
    return contents


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository | None = None) -> None:
        self.repository = repository if repository is not None else KnowledgeRepository()

    @staticmethod
    def _scope(owner_id: uuid.UUID | None) -> str:
        return f'user:{owner_id}' if owner_id is not None else 'local'

    @staticmethod
    def _check_filename(filename: str) -> None:
        if filename not in prompt_filenames():
            raise AppError('knowledge_prompt_not_found', '知识条目不存在', 404)

    @staticmethod
    def _document(entry: PromptEntry | None) -> KnowledgeDocument:
        if entry is None:
            raise AppError('knowledge_not_initialized', '请先导入提示词知识库', 503)
        return KnowledgeDocument(filename=entry.filename, content=entry.content,
                                 version=KnowledgeRepository.version_token(entry),
                                 size=len(entry.content.encode('utf-8')), revision=entry.revision,
                                 source='default' if entry.scope_key == 'system' else 'personal')

    async def get_document(self, owner_id: uuid.UUID | None, filename: str) -> KnowledgeDocument:
        self._check_filename(filename)
        try:
            return self._document(await self.repository.get_document_entry(self._scope(owner_id), filename))
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_management_read_failed', exc)
            raise AppError('knowledge_load_failed', '知识库读取失败', 503) from None

    async def list_documents(self, owner_id: uuid.UUID | None) -> list[KnowledgeSummary]:
        scope = self._scope(owner_id)
        try:
            rows = await self.repository.list_entries(scope)
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_management_list_failed', exc)
            raise AppError('knowledge_load_failed', '知识库读取失败', 503) from None
        defaults = {row.filename: row for row in rows if row.scope_key == 'system'}
        if set(prompt_filenames()) - defaults.keys():
            raise AppError('knowledge_not_initialized', '请先完整导入提示词知识库', 503)
        effective = dict(defaults)
        effective.update({row.filename: row for row in rows if row.scope_key == scope})
        return [KnowledgeSummary(**self._document(effective[name]).model_dump(exclude={'content'}))
                for name in prompt_filenames()]

    async def save_document(self, owner_id: uuid.UUID | None, filename: str,
                            update: KnowledgeUpdate) -> KnowledgeDocument:
        return await self._write_document(owner_id, filename, update.expected_version, content=update.content)

    async def rollback_version(self, owner_id: uuid.UUID | None, filename: str,
                               version_id: uuid.UUID, expected_version: str) -> KnowledgeDocument:
        return await self._write_document(owner_id, filename, expected_version, rollback_id=version_id)

    async def _write_document(self, owner_id: uuid.UUID | None, filename: str, expected_version: str,
                              content: str | None = None, rollback_id: uuid.UUID | None = None) -> KnowledgeDocument:
        self._check_filename(filename)
        started = perf_counter()
        action = 'rollback' if rollback_id else 'save'
        try:
            row = await self.repository.write_document(self._scope(owner_id), owner_id, filename,
                                                       expected_version, content, rollback_id)
            document = self._document(row)
            logger.info('knowledge_management_completed action=%s filename=%s revision=%d duration_ms=%d',
                        action, filename, document.revision, round((perf_counter() - started) * 1000))
            return document
        except AppError as exc:
            logger.info('knowledge_management_rejected action=%s filename=%s code=%s duration_ms=%d',
                        action, filename, exc.code, round((perf_counter() - started) * 1000))
            raise
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_management_write_failed', exc)
            raise AppError('knowledge_save_failed', '知识库保存失败，请保留草稿后重试', 503) from None

    async def list_versions(self, owner_id: uuid.UUID | None, filename: str, limit: int = 20,
                            before_revision: int | None = None) -> KnowledgeVersionPage:
        self._check_filename(filename)
        if not 1 <= limit <= 50 or (before_revision is not None and before_revision < 1):
            raise AppError('knowledge_pagination_invalid', '分页参数不合法', 422)
        try:
            rows, personal = await self.repository.document_versions(self._scope(owner_id), filename, limit, before_revision)
            items = [KnowledgeVersion(id=row.id, revision=row.revision, actor='本人' if personal else '系统',
                                      action=row.action, created_at=row.created_at, can_rollback=personal)
                     for row in rows[:limit]]
            return KnowledgeVersionPage(items=items,
                                        next_before_revision=items[-1].revision if len(rows) > limit else None)
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_management_history_failed', exc)
            raise AppError('knowledge_load_failed', '知识库历史读取失败', 503) from None

    async def import_default_prompts(self, prompt_dir: Path) -> ImportResult:
        started = perf_counter()
        contents = await asyncio.to_thread(_read_sources, prompt_dir)
        try:
            result = await self.repository.import_defaults(contents)
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_import_failed', exc)
            raise AppError('knowledge_import_failed', '知识库导入失败', 503) from None
        logger.info('knowledge_import_completed created=%d skipped=%d different=%d duration_ms=%d',
                    len(result.created), len(result.skipped), len(result.different),
                    round((perf_counter() - started) * 1000))
        return result

    async def resolve_prompt_snapshot(self, owner_id: uuid.UUID | None) -> PromptSnapshot:
        started = perf_counter()
        scope = f'user:{owner_id}' if owner_id is not None else 'local'
        logger.info('knowledge_load_started')
        try:
            rows = await self.repository.list_entries(scope)
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_load_failed', exc)
            raise AppError('knowledge_load_failed', '知识库读取失败', 503) from None
        defaults = {row.filename: row for row in rows if row.scope_key == 'system'}
        if not defaults:
            raise AppError('knowledge_not_initialized', '请先导入提示词知识库', 503)
        missing = set(prompt_filenames()) - defaults.keys()
        if missing:
            raise AppError('knowledge_prompt_missing', '默认知识库缺少提示词', 503,
                           [{'filename': name} for name in sorted(missing)])
        effective = dict(defaults)
        for row in rows:
            if row.scope_key == scope:
                if row.owner_id != owner_id:
                    raise AppError('knowledge_entry_owner_mismatch', '知识条目所有者不一致', 503)
                effective[row.filename] = row
        snapshot = PromptSnapshot(scope, {name: SnapshotEntry(row.content, row.scope_key, row.revision, row.content_hash)
                                          for name, row in effective.items() if name in prompt_filenames()})
        self._validate_snapshot(snapshot)
        logger.info('knowledge_load_completed count=%d manifest_hash=%s duration_ms=%d',
                    len(snapshot.entries), snapshot.manifest_hash, round((perf_counter() - started) * 1000))
        return snapshot

    @staticmethod
    def _validate_snapshot(snapshot: PromptSnapshot) -> None:
        if set(snapshot.entries) != set(prompt_filenames()):
            raise AppError('knowledge_prompt_missing', '运行快照提示词清单不完整', 503)
        for entry in snapshot.entries.values():
            if (not entry.content.strip() or entry.revision < 1 or
                    entry.scope_key not in {'system', snapshot.scope_key} or
                    entry.content_hash != sha256(entry.content.encode('utf-8')).hexdigest()):
                raise AppError('knowledge_snapshot_invalid', '知识库内容或版本校验失败', 503)

    async def get_or_create_run_prompt_snapshot(self, run_id: uuid.UUID, owner_id: uuid.UUID | None) -> PromptSnapshot:
        scope = f'user:{owner_id}' if owner_id is not None else 'local'
        try:
            row = await self.repository.get_run_snapshot(run_id)
            if row is None:
                snapshot = await self.resolve_prompt_snapshot(owner_id)
                row = await self.repository.save_run_snapshot(run_id, snapshot)
            if row.scope_key != scope:
                raise AppError('knowledge_snapshot_owner_mismatch', '运行快照所有者不一致', 404)
            try:
                snapshot = PromptSnapshot(scope, {name: SnapshotEntry(**entry) for name, entry in row.entries_json.items()})
                self._validate_snapshot(snapshot)
            except (TypeError, ValueError, AttributeError) as exc:
                raise AppError('knowledge_snapshot_invalid', '运行快照数据不合法', 503) from exc
            if snapshot.manifest_hash != row.manifest_hash:
                raise AppError('knowledge_snapshot_invalid', '运行快照摘要不一致', 503)
            logger.info('knowledge_run_snapshot_ready manifest_hash=%s count=%d', snapshot.manifest_hash, len(snapshot.entries))
            return snapshot
        except (SQLAlchemyError, OSError) as exc:
            _log_database_failure('knowledge_run_snapshot_failed', exc)
            raise AppError('knowledge_load_failed', '运行提示词快照读取或保存失败', 503) from None


async def resolve_prompt_snapshot(owner_id: uuid.UUID | None) -> PromptSnapshot:
    return await KnowledgeService().resolve_prompt_snapshot(owner_id)


async def get_or_create_run_prompt_snapshot(run_id: uuid.UUID, owner_id: uuid.UUID | None) -> PromptSnapshot:
    return await KnowledgeService().get_or_create_run_prompt_snapshot(run_id, owner_id)
