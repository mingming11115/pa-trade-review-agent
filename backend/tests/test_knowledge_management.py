from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic import ValidationError

from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.core.errors import AppError
from app.knowledge.service import KnowledgeService
from test_knowledge_import import knowledge

NAME = '市场诊断框架.txt'


@pytest.fixture
async def service(knowledge):
    repo, _ = knowledge
    result = KnowledgeService(repo)
    await result.import_default_prompts(PROMPT_DIR)
    return result


@pytest.mark.anyio
async def test_save_is_private_and_run_snapshots_remain_frozen(service):
    from app.knowledge.contracts import KnowledgeUpdate
    owner = uuid.uuid4()
    original = await service.get_document(owner, NAME)
    old_run = uuid.uuid4()
    frozen = await service.get_or_create_run_prompt_snapshot(old_run, owner)
    updated = await service.save_document(owner, NAME, KnowledgeUpdate(content='私人知识', expected_version=original.version))
    assert updated.source == 'personal' and updated.revision == 2
    assert (await service.get_document(uuid.uuid4(), NAME)).content == original.content
    assert (PROMPT_DIR / NAME).read_text(encoding='utf-8') == original.content
    assert (await service.get_or_create_run_prompt_snapshot(old_run, owner)).contents == frozen.contents
    assert (await service.get_or_create_run_prompt_snapshot(uuid.uuid4(), owner)).contents[NAME] == '私人知识'
    same = await service.save_document(owner, NAME, KnowledgeUpdate(content=updated.content, expected_version=updated.version))
    assert same.version == updated.version
    history = await service.list_versions(owner, NAME, limit=1)
    assert history.items[0].revision == 2 and history.next_before_revision == 2
    baseline = (await service.list_versions(owner, NAME, before_revision=2)).items[0]
    restored = await service.rollback_version(owner, NAME, baseline.id, updated.version)
    assert restored.content == original.content and restored.revision == 3
    assert (await service.resolve_prompt_snapshot(owner)).contents[NAME] == original.content
    with pytest.raises(AppError) as error:
        await service.save_document(owner, NAME, KnowledgeUpdate(content='过期', expected_version=original.version))
    assert error.value.status_code == 409
    with pytest.raises(AppError) as error:
        await service.rollback_version(uuid.uuid4(), NAME, baseline.id, original.version)
    assert error.value.status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize('personal', [False, True])
async def test_concurrent_saves_have_one_winner(service, personal):
    from app.knowledge.contracts import KnowledgeUpdate
    owner = uuid.uuid4()
    doc = await service.get_document(owner, NAME)
    if personal:
        doc = await service.save_document(owner, NAME, KnowledgeUpdate(content='个人第一版', expected_version=doc.version))
    results = await asyncio.gather(*(service.save_document(owner, NAME, KnowledgeUpdate(content=text, expected_version=doc.version))
                                    for text in ['并发甲', '并发乙']), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    failures = [r for r in results if isinstance(r, Exception)]
    assert isinstance(failures[0], AppError) and failures[0].status_code == 409


@pytest.mark.anyio
async def test_validation_and_foreign_version_are_rejected(service):
    from app.knowledge.contracts import KnowledgeUpdate
    owner = uuid.uuid4()
    doc = await service.get_document(owner, NAME)
    history = await service.list_versions(owner, NAME)
    assert len(history.items) == 1 and not history.items[0].can_rollback
    for content in ['  \n', 'x' * 1_000_001]:
        with pytest.raises(ValidationError):
            KnowledgeUpdate(content=content, expected_version=doc.version)
    with pytest.raises(AppError) as error:
        await service.get_document(owner, '../secret')
    assert error.value.status_code == 404
    other = await service.get_document(owner, '二元决策.txt')
    other = await service.save_document(owner, other.filename, KnowledgeUpdate(content='其他文件', expected_version=other.version))
    version = (await service.list_versions(owner, other.filename)).items[0]
    with pytest.raises(AppError) as error:
        await service.rollback_version(owner, NAME, version.id, doc.version)
    assert error.value.status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize('personal', [False, True])
async def test_version_write_failure_rolls_back_current_and_history(service, knowledge, personal, caplog):
    from sqlalchemy import event, select, func
    from sqlalchemy.exc import OperationalError
    from app.knowledge.models import PromptEntry, PromptVersion
    from app.knowledge.contracts import KnowledgeUpdate
    repo, factory = knowledge
    owner = uuid.uuid4()
    doc = await service.get_document(owner, NAME)
    if personal:
        doc = await service.save_document(owner, NAME, KnowledgeUpdate(content='已保存个人内容', expected_version=doc.version))
    before = await service.list_versions(owner, NAME)
    def fail(mapper, connection, target):
        raise OperationalError('INSERT', {'content':'PRIVATE-FAILED-PROMPT'}, Exception('PRIVATE-FAILED-PROMPT'))
    event.listen(PromptVersion, 'before_insert', fail)
    try:
        with pytest.raises(AppError) as error:
            await service.save_document(owner, NAME, KnowledgeUpdate(content='PRIVATE-FAILED-PROMPT', expected_version=doc.version))
        assert error.value.status_code == 503
    finally:
        event.remove(PromptVersion, 'before_insert', fail)
    assert (await service.get_document(owner, NAME)).model_dump() == doc.model_dump()
    assert (await service.list_versions(owner, NAME)).model_dump() == before.model_dump()
    assert 'PRIVATE-FAILED-PROMPT' not in caplog.text
    if not personal:
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(PromptEntry).where(PromptEntry.owner_id == owner)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('personal', [False, True])
async def test_postgres_concurrent_personal_saves(personal):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.core.config import get_settings
    from app.knowledge.models import PromptEntry, PromptVersion, RunPromptSnapshot
    from app.knowledge.repository import KnowledgeRepository
    name = 'test_knowledge_' + uuid.uuid4().hex
    engine = create_async_engine(get_settings().database_url)
    translated = engine.execution_options(schema_translate_map={None:name})
    try:
        async with translated.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{name}"'))
            for table in [PromptEntry.__table__, PromptVersion.__table__, RunPromptSnapshot.__table__]:
                await connection.run_sync(table.create)
        factory = async_sessionmaker(translated, expire_on_commit=False)
        service = KnowledgeService(KnowledgeRepository(factory))
        await service.import_default_prompts(PROMPT_DIR)
        await test_concurrent_saves_have_one_winner(service, personal)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        await engine.dispose()
