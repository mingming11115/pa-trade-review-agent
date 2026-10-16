from __future__ import annotations

import uuid
from hashlib import sha256

import pytest
from sqlalchemy import select, update

from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.core.errors import AppError
from test_knowledge_import import knowledge


@pytest.mark.anyio
async def test_snapshot_resolves_default_and_owner_override(knowledge):
    from app.knowledge.models import PromptEntry
    from app.knowledge.service import KnowledgeService
    repo, factory = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    owner = uuid.uuid4()
    filename = '市场诊断框架.txt'
    async with factory() as session, session.begin():
        session.add(PromptEntry(scope_key=f'user:{owner}', owner_id=owner, filename=filename,
                                content='本人知识内容', revision=2, content_hash=sha256('本人知识内容'.encode()).hexdigest()))
    own = await service.resolve_prompt_snapshot(owner)
    other = await service.resolve_prompt_snapshot(uuid.uuid4())
    assert own.contents[filename] == '本人知识内容'
    assert other.contents[filename] == (PROMPT_DIR / filename).read_text(encoding='utf-8')
    assert own.entries[filename].revision == 2
    assert own.manifest_hash != other.manifest_hash
    with pytest.raises(TypeError):
        own.contents[filename] = '改动'


@pytest.mark.anyio
async def test_run_snapshot_is_frozen(knowledge):
    from app.knowledge.models import PromptEntry, RunPromptSnapshot
    from app.knowledge.service import KnowledgeService
    repo, factory = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    run, owner = uuid.uuid4(), uuid.uuid4()
    first = await service.get_or_create_run_prompt_snapshot(run, owner)
    filename = '市场诊断框架.txt'
    async with factory() as session, session.begin():
        await session.execute(update(PromptEntry).where(PromptEntry.filename == filename).values(
            content='新的默认版本', revision=2, content_hash=sha256('新的默认版本'.encode()).hexdigest()))
    repeated = await service.get_or_create_run_prompt_snapshot(run, owner)
    fresh = await service.get_or_create_run_prompt_snapshot(uuid.uuid4(), owner)
    assert repeated.contents == first.contents
    assert fresh.contents[filename] == '新的默认版本'
    async with factory() as session:
        row = await session.get(RunPromptSnapshot, run)
    assert row.entries_json[filename]['content'] == first.contents[filename]


@pytest.mark.anyio
async def test_snapshot_owner_mismatch_rejected(knowledge):
    from app.knowledge.service import KnowledgeService
    repo, _ = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    run = uuid.uuid4()
    await service.get_or_create_run_prompt_snapshot(run, uuid.uuid4())
    with pytest.raises(AppError) as error:
        await service.get_or_create_run_prompt_snapshot(run, uuid.uuid4())
    assert error.value.code == 'knowledge_snapshot_owner_mismatch'


@pytest.mark.anyio
async def test_missing_default_fails(knowledge):
    from app.knowledge.models import PromptEntry
    from app.knowledge.service import KnowledgeService
    repo, factory = knowledge
    service = KnowledgeService(repo)
    with pytest.raises(AppError) as error:
        await service.resolve_prompt_snapshot(None)
    assert error.value.code == 'knowledge_not_initialized'
    await service.import_default_prompts(PROMPT_DIR)
    async with factory() as session, session.begin():
        row = await session.scalar(select(PromptEntry).where(PromptEntry.filename == '二元决策.txt'))
        row.filename = '不可用.txt'
    with pytest.raises(AppError) as error:
        await service.resolve_prompt_snapshot(None)
    assert error.value.code == 'knowledge_prompt_missing'


@pytest.mark.anyio
@pytest.mark.parametrize("raw_transport", [False, True])
async def test_snapshot_load_failure_is_explicit(knowledge, monkeypatch, raw_transport):
    from sqlalchemy.exc import OperationalError
    from app.knowledge.service import KnowledgeService
    repo, _ = knowledge
    async def fail(scope_key):
        if raw_transport:
            raise OSError('offline')
        raise OperationalError('query', {}, Exception('offline'))
    monkeypatch.setattr(repo, 'list_entries', fail)
    with pytest.raises(AppError) as error:
        await KnowledgeService(repo).resolve_prompt_snapshot(None)
    assert error.value.code == 'knowledge_load_failed'
