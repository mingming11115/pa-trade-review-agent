from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.tasks.models import TaskStatus
from app.analysis.history.snapshots import FrozenInputSnapshot
from app.analysis.routes import get_analysis_task_repository, router, start_analysis_task_run
from app.analysis.tasks.repository import RunCreateSpec
from app.auth.service import UserPublic


class FakeTask:
    id = uuid.uuid4()
    kind = "analysis"
    title = "ES 分析"
    description = ""
    status = TaskStatus.pending.value
    config_json = {
        "symbol": "ES",
        "period": "5m",
    }
    version = 1
    created_at = datetime.now(timezone.utc)
    updated_at = created_at
    archived_at = None


class FakePage:
    items = [FakeTask()]
    next_cursor = None


class FakeRepository:
    def __init__(self):
        self.created = []

    async def create_task(self, owner_id, payload):
        self.created.append((owner_id, payload))
        return FakeTask()

    async def list_tasks(self, owner_id, **kwargs):
        return FakePage()

    async def update_task(self, owner_id, task_id, payload):
        self.updated = (owner_id, task_id, payload)
        task = FakeTask()
        task.title = payload.title
        task.description = payload.description
        task.config_json = payload.config
        task.version = payload.version + 1
        return task


def test_save_task_only_calls_repository() -> None:
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_analysis_task_repository] = lambda: repository
    response = TestClient(app).post(
        "/api/v1/analysis-tasks",
        json={
            "kind": "analysis",
            "title": "ES 分析",
            "config": {
                "symbol": "ES",
                "period": "5m",
            },
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert len(repository.created) == 1


def test_task_list_returns_cursor_page() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_analysis_task_repository] = FakeRepository

    response = TestClient(app).get("/api/v1/analysis-tasks")

    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "ES 分析"
    assert response.json()["next_cursor"] is None


def test_pending_task_update_returns_incremented_version() -> None:
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_analysis_task_repository] = lambda: repository

    response = TestClient(app).patch(f"/api/v1/analysis-tasks/{FakeTask.id}", json={
        "version": 1, "title": "修改后的任务", "description": "只保存",
        "config": {"symbol": "NQ", "period": "5m"},
    })

    assert response.status_code == 200
    assert response.json()["title"] == "修改后的任务"
    assert response.json()["version"] == 2


def test_start_task_returns_flat_run_items_and_starts_each_run(monkeypatch) -> None:
    task = FakeTask()
    snapshot = FrozenInputSnapshot(
        id=uuid.uuid4(), task_id=task.id, user_id=None,
        query_json={"symbol": "ES", "period": "5m", "analysis_mode": "historical"},
        resolved_symbol="ES", bars_json=[], bars_hash="hash", prompt_versions_json={},
        model_config_json={}, confirmation_id="confirm", expires_at=task.created_at,
        created_at=task.created_at,
    )

    class Repository:
        async def get_task(self, *_args):
            return task

        async def create_runs_for_task(self, _owner, _task_id, specs: list[RunCreateSpec]):
            assert [spec.period for spec in specs] == ["5m"]
            return [SimpleNamespace(id=uuid.uuid4(), period="5m", status="queued")]

    class Manager:
        started: list[uuid.UUID] = []

        def start(self, run_id, _trace_id):
            self.started.append(run_id)

    async def fake_snapshot(*_args, **_kwargs):
        return snapshot

    monkeypatch.setattr("app.analysis.routes.create_input_snapshot", fake_snapshot)
    manager = Manager()
    result = anyio.run(
        start_analysis_task_run,
        task.id,
        UserPublic(id=None, username="local", role="admin", auth_required=False),
        Repository(),
        manager,
        object(),
        object(),
    )

    assert len(result) == 1
    assert result[0].period == "5m"
    assert result[0].status == "queued"
    assert manager.started == [result[0].run_id]
