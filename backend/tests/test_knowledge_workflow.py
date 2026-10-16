from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.analysis.workflow.graph import AnalysisMemoryStore, DemoAnalysisWorkflow
from app.analysis.workflow.stage1.adapter import build_stage1_frame
from app.analysis.workflow.stage1.core.compat import PROMPT_DIR
from app.core.models import HistoricalQuery
from app.core.errors import AppError
from app.llm.client import LLMResponse
from test_knowledge_import import knowledge
from test_llm_workflow import Provider, refs, full_range, stage2_wait_response


def stage1_response():
    return {
        'cycle_position': 'normal_channel', 'direction': 'bullish', 'diagnosis_confidence': 82,
        'market_phase': 'stable', 'detected_patterns': [], 'key_signals': ['多头延续'],
        'htf_context': '长程背景偏多', 'entry_setup': '等待阶段二判断',
        'strategy_files_needed': ['上涨通道分析识别.txt'], 'support_levels': ['100'], 'resistance_levels': ['130'],
        'bar_by_bar_summary': [
            {'bar_ref': ref, 'bar_type': 'trend_bull', 'role': 'structure', 'context_effect': 'strengthens_bull',
             'follow_through': 'yes', 'trapped_side': 'none', 'reason': '延续'} for ref in refs()[-5:]
        ],
        'gate_trace': [
            {'node_id': node, 'question': node, 'answer': '中性' if node == 'momentum_enough' else '是',
             'reason': '进入阶段二' if node == 'momentum_enough' else '', 'bar_range': full_range()}
            for node in ['data_sufficiency', 'cycle_identifiable', 'not_extreme_chaos', 'direction_decidable',
                         'background_near_term_coherent', 'program_direction', 'always_in', 'momentum_enough']
        ],
        'gate_result': 'proceed', 'risk_warning': '', 'node_overrides': [],
        'incremental_delta': {'changed_fields': [], 'summary': '多头通道'},
    }


@pytest.mark.anyio
@pytest.mark.parametrize('streaming,sequential,retry_stage', [(False, False, None), (True, False, None), (False, True, None), (False, False, 'stage1'), (False, False, 'stage2')])
async def test_real_graph_uses_database_snapshot_and_records_versions(knowledge, monkeypatch, streaming, sequential, retry_stage):
    import json
    from app.knowledge.service import KnowledgeService
    from app.analysis.execution.runs import get_stage_runs, clear_stage_runs
    repo, _ = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    # A database-only marker detects accidental fallback to the original files.
    from hashlib import sha256
    from sqlalchemy import select
    from app.knowledge.models import PromptEntry, PromptVersion
    async with repo.session_factory() as session, session.begin():
        for filename in ['提示词大纲_人设与思维方式.txt', '市场诊断框架.txt', '上涨通道分析识别.txt']:
            row = await session.scalar(select(PromptEntry).where(PromptEntry.filename == filename))
            row.content += '\n数据库独有知识验证标记'
            row.revision = 2
            row.content_hash = sha256(row.content.encode()).hexdigest()
            session.add(PromptVersion(entry_id=row.id, revision=2, content=row.content,
                                     content_hash=row.content_hash, action='save'))
    run_id = uuid.uuid4()
    snapshot = await service.get_or_create_run_prompt_snapshot(run_id, None)
    responses = [stage1_response(), stage2_wait_response()]
    if retry_stage is not None:
        responses.insert(0 if retry_stage == "stage1" else 1, {"invalid": True})
    inputs = []
    async def call(system, payload, **kwargs):
        inputs.append((system, payload['_user_prompt']))
        output = responses[len(inputs)-1]
        return LLMResponse(output, 100, 20, 120, 'test-model', 'test-provider',
                           raw_content=json.dumps(output, ensure_ascii=False))
    monkeypatch.setattr('app.analysis.workflow.graph.call_llm', call)
    monkeypatch.setattr('app.analysis.workflow.graph.append_usage', lambda _: None)
    query = HistoricalQuery.model_validate({'symbol':'ES', 'period':'1m', 'start':'2022-01-01T00:00:00Z', 'end':'2022-01-02T00:00:00Z'})
    workflow = DemoAnalysisWorkflow(Provider(), AnalysisMemoryStore(), run_id, prompt_snapshot=snapshot)
    if sequential:
        workflow._compiled = None
    try:
        if streaming:
            events = [event async for event in workflow.stream(query)]
            result = events[-1]['result']
            assert result['stage2']['terminal']['outcome'] == 'wait'
        else:
            result = await workflow.invoke(query)
            assert result.stage1.gate_result == 'proceed'
            assert result.stage2.terminal.reason == '等待回撤'
        assert len(inputs) == (3 if retry_stage is not None else 2)
        assert snapshot.contents['提示词大纲_人设与思维方式.txt'] in inputs[0][0]
        assert snapshot.contents['市场诊断框架.txt'] in inputs[0][1]
        assert snapshot.contents['上涨通道分析识别.txt'] in inputs[-1][1]
        records = get_stage_runs(str(run_id))
        assert {r['stage'] for r in records} == {'stage1', 'stage2'}
        assert all(r['prompt_metadata']['knowledge']['manifest_hash'] == snapshot.manifest_hash for r in records)
        assert all(r['prompt_metadata']['knowledge']['files'] for r in records)
    finally:
        clear_stage_runs(str(run_id))


@pytest.mark.anyio
async def test_manager_snapshot_failure_persists_failed_terminal(monkeypatch):
    from app.analysis.execution.manager import AnalysisRunManager
    from app.analysis.tasks.models import RunStatus
    run = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4(), task_id=uuid.uuid4(), query_json={})
    statuses = []
    failures = []
    class Repo:
        async def get_run_unscoped(self, run_id):
            return run
        async def mark_run_running(self, run_id):
            return True
        async def finish_run(self, owner, run_id, **kwargs):
            statuses.append(kwargs['status'])
            failures.append((kwargs['failure_code'], kwargs['failure_message']))
    async def fail(run_id, owner):
        assert run_id == run.id and owner == run.user_id
        raise AppError('knowledge_load_failed', 'offline', 503)
    monkeypatch.setattr('app.analysis.execution.manager.get_or_create_run_prompt_snapshot', fail)
    await AnalysisRunManager(Repo()).run(run.id, 'knowledge-failure-trace')
    assert statuses == [RunStatus.failed]
    assert failures == [('knowledge_load_failed', 'offline')]


@pytest.mark.anyio
async def test_review_items_share_explicit_snapshot(knowledge, monkeypatch):
    from app.analysis.execution.manager import AnalysisRunManager
    from app.analysis.tasks.models import RunStatus
    from app.knowledge.service import KnowledgeService
    repo, _ = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    run = SimpleNamespace(id=uuid.uuid4(), user_id=None, period='1m')
    snapshot = await service.get_or_create_run_prompt_snapshot(run.id, None)
    class Repo:
        result = None
        async def update_run_result(self, run_id, payload):
            self.result = payload
        async def finish_run(self, owner, run_id, **kwargs):
            assert kwargs['status'] == RunStatus.completed
    async def workflow(provider, query, *, run_id, prompt_snapshot):
        assert prompt_snapshot is snapshot
        return SimpleNamespace(model_dump=lambda **kwargs: {'review_result':[{'trade_id': query['trade']}]})
    monkeypatch.setattr('app.analysis.execution.manager.run_demo_analysis_workflow', workflow)
    monkeypatch.setattr('app.analysis.execution.manager.HistoricalQuery.model_validate', lambda x: x)
    repository = Repo()
    await AnalysisRunManager(repository)._run_review_period(run,
        [{'query':{'trade':'1'},'bars':[]}, {'query':{'trade':'2'},'bars':[]}], prompt_snapshot=snapshot)
    assert [r['trade_id'] for r in repository.result['review_result']] == ['1','2']


@pytest.mark.anyio
async def test_persisted_manager_run_completes_with_knowledge_versions(knowledge, monkeypatch):
    import json
    from app.analysis.execution.manager import AnalysisRunManager
    from app.analysis.tasks.models import AnalysisTaskCreate
    from app.analysis.tasks.repository import AnalysisTaskRepository, RunCreateSpec
    from app.knowledge.models import RunPromptSnapshot
    from app.knowledge.service import KnowledgeService
    repo, factory = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    monkeypatch.setattr('app.analysis.execution.manager.get_or_create_run_prompt_snapshot', service.get_or_create_run_prompt_snapshot)
    monkeypatch.setattr('app.analysis.workflow.graph.append_usage', lambda _: None)
    outputs = [stage1_response(), stage2_wait_response()]
    async def call(system, payload, **kwargs):
        output = outputs.pop(0)
        return LLMResponse(output, 100, 20, 120, 'test-model', 'test-provider', raw_content=json.dumps(output, ensure_ascii=False))
    monkeypatch.setattr('app.analysis.workflow.graph.call_llm', call)
    repository = AnalysisTaskRepository(factory)
    task = await repository.create_task(None, AnalysisTaskCreate(kind='analysis', title='knowledge test', config={'symbol':'ES','period':'1m'}))
    query = HistoricalQuery.model_validate({'symbol':'ES', 'period':'1m', 'start':'2022-01-01T00:00:00Z', 'end':'2022-01-02T00:00:00Z', 'analysis_mode':'trade_review',
        'trades':[{'trade_id':'knowledge-trade', 'symbol':'ES', 'entered_at':'2022-01-01T00:00:00Z', 'exited_at':'2022-01-01T00:04:00Z',
                   'direction':'long', 'entry_price':100, 'exit_price':102, 'size':1, 'reported_pnl':100}]})
    bars = await Provider().get_range(query)
    spec = RunCreateSpec(period='1m', query_json={'kind':'review','items':[{'query':query.model_dump(mode='json'), 'bars':[bar.model_dump(mode='json') for bar in bars]}]},
                         resolved_symbol='ES', bars_json=None, bars_hash=None, mode='trade_review', symbol='ES')
    run = (await repository.create_runs_for_task(None, task.id, [spec]))[0]
    await AnalysisRunManager(repository).run(run.id, 'knowledge-integration-trace')
    completed = await repository.get_run(None, run.id)
    assert completed.status == 'completed'
    attempts = await repository.list_stage_attempts(run.id)
    assert {a.stage for a in attempts} == {'stage1','stage2'}
    async with factory() as session:
        snapshot_row = await session.get(RunPromptSnapshot, run.id)
    assert snapshot_row.scope_key == 'local'
    assert all(a.prompt_metadata['knowledge']['manifest_hash'] == snapshot_row.manifest_hash for a in attempts)


@pytest.mark.anyio
async def test_cancellation_during_snapshot_loading_reaches_terminal(knowledge, monkeypatch):
    import asyncio
    from app.analysis.execution.manager import AnalysisRunManager
    from app.analysis.tasks.models import AnalysisTaskCreate
    from app.analysis.tasks.repository import AnalysisTaskRepository, RunCreateSpec
    _, factory = knowledge
    repository = AnalysisTaskRepository(factory)
    task = await repository.create_task(None, AnalysisTaskCreate(kind='analysis', title='cancel load', config={'symbol':'ES','period':'1m'}))
    spec = RunCreateSpec(period='1m', query_json={}, resolved_symbol='ES', bars_json=None, bars_hash=None, mode='historical', symbol='ES')
    run = (await repository.create_runs_for_task(None, task.id, [spec]))[0]
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def blocked(run_id, owner):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr('app.analysis.execution.manager.get_or_create_run_prompt_snapshot', blocked)
    manager = AnalysisRunManager(repository)
    manager.start(run.id, 'knowledge-load-cancel')
    worker = manager.tasks[run.id]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await repository.request_run_cancel(None, run.id)
        await asyncio.wait_for(stopped.wait(), 1)
        await asyncio.gather(worker, return_exceptions=True)
        assert (await repository.get_run(None, run.id)).status == 'cancelled'
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.anyio
async def test_personal_save_and_rollback_reach_both_stages(knowledge, monkeypatch):
    import json
    from app.knowledge.contracts import KnowledgeUpdate
    from app.knowledge.service import KnowledgeService
    from app.analysis.execution.runs import clear_stage_runs
    repo, _ = knowledge
    service = KnowledgeService(repo)
    await service.import_default_prompts(PROMPT_DIR)
    owner, other = uuid.uuid4(), uuid.uuid4()
    filename = '提示词大纲_人设与思维方式.txt'
    initial = await service.get_document(owner, filename)
    old_id = uuid.uuid4()
    old = await service.get_or_create_run_prompt_snapshot(old_id, owner)
    updated = await service.save_document(owner, filename, KnowledgeUpdate(content=initial.content + '\n个人保存生效标记', expected_version=initial.version))
    new = await service.get_or_create_run_prompt_snapshot(uuid.uuid4(), owner)
    assert (await service.get_or_create_run_prompt_snapshot(old_id, owner)).contents == old.contents
    assert '个人保存生效标记' not in (await service.resolve_prompt_snapshot(other)).contents[filename]
    inputs = []
    responses = [stage1_response(), stage2_wait_response()]
    async def call(system, payload, **kwargs):
        inputs.append(system)
        output = responses[(len(inputs)-1) % 2]
        return LLMResponse(output, 100, 20, 120, 'test-model', 'test-provider', raw_content=json.dumps(output, ensure_ascii=False))
    monkeypatch.setattr('app.analysis.workflow.graph.call_llm', call)
    monkeypatch.setattr('app.analysis.workflow.graph.append_usage', lambda _: None)
    query = HistoricalQuery.model_validate({'symbol':'ES', 'period':'1m', 'start':'2022-01-01T00:00:00Z', 'end':'2022-01-02T00:00:00Z'})
    baseline = (await service.list_versions(owner, filename)).items[-1]
    await service.rollback_version(owner, filename, baseline.id, updated.version)
    restored = await service.get_or_create_run_prompt_snapshot(uuid.uuid4(), owner)
    for snapshot in [new, restored, old]:
        run_id = uuid.uuid4()
        try:
            result = await DemoAnalysisWorkflow(Provider(), AnalysisMemoryStore(), run_id, prompt_snapshot=snapshot).invoke(query)
            assert result.stage1.gate_result == 'proceed'
        finally:
            clear_stage_runs(str(run_id))
    assert all('个人保存生效标记' in system for system in inputs[:2])
    assert all('个人保存生效标记' not in system for system in inputs[2:])
