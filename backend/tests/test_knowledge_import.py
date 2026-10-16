from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.core.errors import AppError


@pytest.fixture
async def knowledge(tmp_path):
    from app.knowledge.repository import KnowledgeRepository
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield KnowledgeRepository(factory), factory
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_import_preserves_content_and_first_revision(knowledge, tmp_path):
    from app.knowledge.models import PromptEntry, PromptVersion
    from app.knowledge.service import KnowledgeService, prompt_filenames
    repo, factory = knowledge
    names = prompt_filenames()
    text = '\ufeff原文\n  保留空白 \n'
    for name in names:
        (tmp_path / name).write_text(text, encoding='utf-8')
    (tmp_path / 'ignored.txt').write_text('不应导入', encoding='utf-8')
    result = await KnowledgeService(repo).import_default_prompts(tmp_path)
    assert set(result.created) == set(names)
    assert result.skipped == ()
    async with factory() as session:
        entries = (await session.scalars(select(PromptEntry))).all()
        versions = (await session.scalars(select(PromptVersion))).all()
    assert len(entries) == len(names) == len(versions)
    for entry in entries:
        assert entry.content == text
        assert entry.content_hash == hashlib.sha256(text.encode()).hexdigest()
        assert entry.revision == 1 and entry.scope_key == 'system'
    assert all(v.action == 'import' and v.revision == 1 and v.content == text for v in versions)


@pytest.mark.anyio
async def test_repeat_import_does_not_overwrite(knowledge):
    from app.knowledge.service import KnowledgeService
    from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
    repo, _ = knowledge
    service = KnowledgeService(repo)
    first = await service.import_default_prompts(PROMPT_DIR)
    second = await service.import_default_prompts(PROMPT_DIR)
    assert first.created and not second.created
    assert second.skipped == first.created
    assert not second.different


@pytest.mark.anyio
async def test_missing_source_rolls_back(knowledge, tmp_path):
    from app.knowledge.models import PromptEntry
    from app.knowledge.service import KnowledgeService, prompt_filenames
    repo, factory = knowledge
    (tmp_path / prompt_filenames()[0]).write_text('valid', encoding='utf-8')
    with pytest.raises(AppError) as error:
        await KnowledgeService(repo).import_default_prompts(tmp_path)
    assert error.value.code == 'knowledge_import_source_invalid'
    async with factory() as session:
        assert not (await session.scalars(select(PromptEntry))).all()


@pytest.mark.anyio
async def test_concurrent_import_is_idempotent(knowledge):
    from app.knowledge.models import PromptVersion
    from app.knowledge.service import KnowledgeService, prompt_filenames
    from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
    repo, factory = knowledge
    await asyncio.gather(*(KnowledgeService(repo).import_default_prompts(PROMPT_DIR) for _ in range(2)))
    async with factory() as session:
        versions = (await session.scalars(select(PromptVersion))).all()
    assert len(versions) == len(prompt_filenames())


@pytest.mark.anyio
async def test_changed_source_is_reported_without_overwriting(knowledge, tmp_path):
    from app.knowledge.service import KnowledgeService, prompt_filenames
    repo, _ = knowledge
    for name in prompt_filenames():
        (tmp_path / name).write_text('初始内容', encoding='utf-8')
    service = KnowledgeService(repo)
    await service.import_default_prompts(tmp_path)
    changed = prompt_filenames()[0]
    (tmp_path / changed).write_text('修改后的文件', encoding='utf-8')
    result = await service.import_default_prompts(tmp_path)
    assert result.different == (changed,)
    assert (await service.resolve_prompt_snapshot(None)).contents[changed] == '初始内容'


def test_cli_imports_v1_into_isolated_database_and_is_repeatable(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path
    from sqlalchemy import create_engine, text
    env = dict(os.environ, DATABASE_URL=f'sqlite+aiosqlite:///{tmp_path / "cli.db"}')
    cwd = Path(__file__).resolve().parents[1]
    first = subprocess.run([sys.executable, '-m', 'app.knowledge.import_prompts'], cwd=cwd, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    summary = json.loads(first.stdout)
    assert summary['created'] and not summary['skipped']
    second = subprocess.run([sys.executable, '-m', 'app.knowledge.import_prompts'], cwd=cwd, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)['skipped'] == summary['created']
    engine = create_engine(f'sqlite:///{tmp_path / "cli.db"}')
    try:
        with engine.connect() as connection:
            count = connection.scalar(text('SELECT COUNT(*) FROM knowledge_prompt_versions WHERE revision=1 AND action=\'import\''))
        assert count == len(summary['created'])
    finally:
        engine.dispose()


@pytest.mark.anyio
async def test_database_errors_do_not_log_prompt_content(knowledge, monkeypatch, caplog):
    from sqlalchemy.exc import StatementError
    from app.knowledge.service import KnowledgeService
    from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
    repo, _ = knowledge
    async def fail(contents):
        raise StatementError('insert failed', 'INSERT', {'content':'PRIVATE-KNOWLEDGE-CONTENT'}, Exception('row PRIVATE-KNOWLEDGE-CONTENT'))
    monkeypatch.setattr(repo, 'import_defaults', fail)
    with pytest.raises(AppError):
        await KnowledgeService(repo).import_default_prompts(PROMPT_DIR)
    assert 'PRIVATE-KNOWLEDGE-CONTENT' not in caplog.text
