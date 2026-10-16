from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.models import Bar
from app.core.errors import AppError
from app.market.service import AlertRule, ScheduledJobRun, evaluate_alert_rules
from app.knowledge.service import KnowledgeService
from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from test_knowledge_import import knowledge


@pytest.mark.anyio
@pytest.mark.parametrize('unavailable', [False, True])
async def test_scheduled_alert_analysis_uses_knowledge_and_persists_terminal(knowledge, monkeypatch, unavailable):
    repo, factory = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    async with factory() as session, session.begin():
        session.add(AlertRule(name='knowledge alert', symbol='ES', period='1m', threshold=10, enabled=True))
    async def schema():
        pass
    async def market_range(*args, **kwargs):
        return SimpleNamespace(bars=[Bar(timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                        open=100, high=102, low=99, close=101, volume=10)])
    async def load(run_id, owner_id):
        assert owner_id is None  # Current scheduled alert rules are ownerless.
        if unavailable:
            raise AppError('knowledge_load_failed', 'offline', 503)
        return await service.get_or_create_run_prompt_snapshot(run_id, owner_id)
    async def workflow(provider, query, *, run_id, prompt_snapshot):
        assert prompt_snapshot.contents['市场诊断框架.txt']
        return SimpleNamespace(run_id=str(run_id), stage2=SimpleNamespace(terminal=SimpleNamespace(outcome='wait')))
    async def persist(result):
        pass
    monkeypatch.setattr('app.analysis.history.service.persist_analysis_result', persist)
    monkeypatch.setattr('app.market.service.SessionFactory', factory)
    monkeypatch.setattr('app.market.service.ensure_schema', schema)
    monkeypatch.setattr('app.market.service.query_market_range', market_range)
    monkeypatch.setattr('app.knowledge.service.get_or_create_run_prompt_snapshot', load)
    monkeypatch.setattr('app.analysis.workflow.graph.run_demo_analysis_workflow', workflow)
    await evaluate_alert_rules(datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc))
    async with factory() as session:
        run = await session.scalar(select(ScheduledJobRun))
    assert run.status == ('failed' if unavailable else 'completed')
    if not unavailable:
        snapshot = await repo.get_run_snapshot(run.id)
        assert snapshot is not None
        assert run.detail['run_id'] == str(run.id)
