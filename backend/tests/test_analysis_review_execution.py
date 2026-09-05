from __future__ import annotations

from types import SimpleNamespace
import uuid

import anyio

from app.analysis.execution.manager import AnalysisRunManager
from app.analysis.tasks.models import RunStatus


class Repository:
    def __init__(self) -> None:
        self.result = None
        self.status = None

    async def update_run_result(self, _run_id, payload):
        self.result = payload

    async def finish_run(self, _owner_id, _run_id, *, status, **_kwargs):
        self.status = status


def test_review_period_finishes_the_same_run_with_all_trade_results(monkeypatch) -> None:
    repository = Repository()
    manager = AnalysisRunManager(repository)
    run = SimpleNamespace(id=uuid.uuid4(), user_id=None, period="5m")

    monkeypatch.setattr("app.analysis.execution.manager.HistoricalQuery.model_validate", lambda value: value)
    monkeypatch.setattr("app.analysis.execution.manager.Bar.model_validate", lambda value: value)

    async def workflow(_provider, query, *, run_id):
        assert run_id == run.id
        return SimpleNamespace(model_dump=lambda **_kwargs: {"review_result": [{"trade_id": query["trade"]}]})

    monkeypatch.setattr("app.analysis.execution.manager.run_demo_analysis_workflow", workflow)
    inputs = [
        {"query": {"trade": "trade-1"}, "bars": []},
        {"query": {"trade": "trade-2"}, "bars": []},
    ]

    anyio.run(manager._run_review_period, run, inputs)

    assert repository.status is RunStatus.completed
    assert repository.result["run_id"] == str(run.id)
    assert repository.result["query"]["period"] == "5m"
    assert [item["trade_id"] for item in repository.result["review_result"]] == ["trade-1", "trade-2"]
