from __future__ import annotations

import uuid
from collections.abc import Mapping
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionFactory
from app.knowledge.contracts import ImportResult, PromptSnapshot
from app.knowledge.models import PromptEntry, PromptVersion, RunPromptSnapshot


class KnowledgeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> None:
        self.session_factory = session_factory

    async def import_defaults(self, contents: Mapping[str, str]) -> ImportResult:
        """Create missing defaults atomically; bounded retry for concurrent imports."""
        for attempt in range(3):
            try:
                async with self.session_factory() as session, session.begin():
                    rows = (await session.scalars(select(PromptEntry).where(PromptEntry.scope_key == 'system'))).all()
                    existing = {row.filename: row for row in rows}
                    created, skipped, different = [], [], []
                    for filename, content in contents.items():
                        if filename in existing:
                            skipped.append(filename)
                            if existing[filename].content != content:
                                different.append(filename)
                            continue
                        entry = PromptEntry(scope_key='system', owner_id=None, filename=filename,
                                            content=content, revision=1, content_hash=sha256(content.encode('utf-8')).hexdigest())
                        session.add(entry)
                        await session.flush()
                        session.add(PromptVersion(entry_id=entry.id, revision=1, content=content,
                                                  content_hash=entry.content_hash, action='import'))
                        created.append(filename)
                return ImportResult(tuple(created), tuple(skipped), tuple(different))
            except IntegrityError:
                if attempt == 2:
                    raise
        raise RuntimeError('import retry exhausted')

    async def list_entries(self, scope_key: str) -> list[PromptEntry]:
        async with self.session_factory() as session:
            return list((await session.scalars(select(PromptEntry).where(
                PromptEntry.scope_key.in_(['system', scope_key])))).all())

    async def get_run_snapshot(self, run_id: uuid.UUID) -> RunPromptSnapshot | None:
        async with self.session_factory() as session:
            return await session.get(RunPromptSnapshot, run_id)

    async def save_run_snapshot(self, run_id: uuid.UUID, snapshot: PromptSnapshot) -> RunPromptSnapshot:
        try:
            async with self.session_factory() as session, session.begin():
                row = RunPromptSnapshot(run_id=run_id, scope_key=snapshot.scope_key,
                                        manifest_hash=snapshot.manifest_hash, entries_json=snapshot.to_payload())
                session.add(row)
            return row
        except IntegrityError:
            # Another execution claimed the same Run; use its immutable snapshot.
            existing = await self.get_run_snapshot(run_id)
            if existing is None:
                raise
            return existing

    async def get_document_entry(self, scope: str, filename: str) -> PromptEntry | None:
        async with self.session_factory() as session:
            return await self._effective_entry(session, scope, filename)

    @staticmethod
    async def _effective_entry(session: AsyncSession, scope: str, filename: str) -> PromptEntry | None:
        rows = (await session.scalars(select(PromptEntry).where(
            PromptEntry.filename == filename, PromptEntry.scope_key.in_(['system', scope])))).all()
        return next((row for row in rows if row.scope_key == scope),
                    next((row for row in rows if row.scope_key == 'system'), None))

    @staticmethod
    def version_token(entry: PromptEntry) -> str:
        return f'v1:{entry.id}:{entry.revision}'

    async def write_document(self, scope: str, owner_id: uuid.UUID | None, filename: str,
                             expected_version: str, content: str | None = None,
                             rollback_id: uuid.UUID | None = None) -> PromptEntry:
        from sqlalchemy import update
        from app.core.errors import AppError
        from app.knowledge.models import utc_now
        try:
            async with self.session_factory() as session, session.begin():
                entry = await self._effective_entry(session, scope, filename)
                if entry is None:
                    raise AppError('knowledge_not_initialized', '请先导入提示词知识库', 503)
                target = None
                if rollback_id is not None:
                    target = await session.scalar(select(PromptVersion).where(
                        PromptVersion.id == rollback_id, PromptVersion.entry_id == entry.id))
                    if entry.scope_key != scope or target is None:
                        raise AppError('knowledge_version_not_found', '版本不存在', 404)
                    content = target.content
                if expected_version != self.version_token(entry):
                    raise AppError('knowledge_version_conflict', '版本已更新，请保留草稿并重新打开条目核对', 409)
                if rollback_id is None and content == entry.content:
                    return entry
                if entry.scope_key == 'system':
                    entry = PromptEntry(scope_key=scope, owner_id=owner_id, filename=filename,
                                        content=entry.content, content_hash=entry.content_hash, revision=1)
                    session.add(entry)
                    await session.flush()
                    session.add(PromptVersion(entry_id=entry.id, revision=1, content=entry.content,
                                              content_hash=entry.content_hash, actor_id=owner_id, action='baseline'))
                revision = entry.revision + 1
                digest = sha256(content.encode('utf-8')).hexdigest()
                result = await session.execute(update(PromptEntry).where(
                    PromptEntry.id == entry.id, PromptEntry.revision == entry.revision).values(
                        content=content, content_hash=digest, revision=revision, updated_at=utc_now()))
                if result.rowcount != 1:
                    raise AppError('knowledge_version_conflict', '版本已更新，请保留草稿并重新打开条目核对', 409)
                session.add(PromptVersion(entry_id=entry.id, revision=revision, content=content,
                                          content_hash=digest, actor_id=owner_id,
                                          action='rollback' if target is not None else 'save',
                                          source_version_id=rollback_id))
                await session.flush()
                await session.refresh(entry)
            return entry
        except IntegrityError:
            raise AppError('knowledge_version_conflict', '版本已更新，请保留草稿并重新打开条目核对', 409) from None

    async def document_versions(self, scope: str, filename: str, limit: int,
                                before_revision: int | None) -> tuple[list[PromptVersion], bool]:
        from app.core.errors import AppError
        async with self.session_factory() as session:
            entry = await self._effective_entry(session, scope, filename)
            if entry is None:
                raise AppError('knowledge_not_initialized', '请先导入提示词知识库', 503)
            query = select(PromptVersion).where(PromptVersion.entry_id == entry.id)
            if before_revision is not None:
                query = query.where(PromptVersion.revision < before_revision)
            rows = list((await session.scalars(query.order_by(PromptVersion.revision.desc()).limit(limit + 1))).all())
            return rows, entry.scope_key == scope
