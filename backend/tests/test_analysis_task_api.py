from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis.tasks.models import TaskStatus
from app.analysis.routes import get_analysis_task_repository, router


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
    latest_execution_id = None
    latest_analysis_id = None
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
