from __future__ import annotations

import uuid
from datetime import datetime, timezone

import anyio
from fastapi.testclient import TestClient

import app.main as main_module
from app.analysis.history.service import AnalysisHistoryUpdate
from app.analysis.tasks.models import AnalysisRunPublic
from app.auth.service import UserPublic
from app.main import analysis_detail, analysis_followup_history, analysis_followup_stream, app
from app.followup.service import FollowupRequest


def test_admin_analysis_runs_returns_persisted_run_metadata(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    run_id = uuid.uuid4()
    task_id = uuid.uuid4()
    record = AnalysisRunPublic(
        run_id=run_id,
        task_id=task_id,
        period="5m",
        status="completed",
        current_stage="complete",
        failure_stage=None,
        failure_code=None,
        failure_message=None,
        terminal_reason=None,
        started_at=now,
        completed_at=now,
        created_at=now,
    )

    async def fake_list(limit: int, run_id: str | None):
        assert limit == 10
        assert run_id == str(run_id)
        return [record]

    monkeypatch.setattr("app.main.list_analysis_runs", fake_list)
    response = TestClient(app).get(
        "/api/v1/admin/analysis-runs",
        params={"limit": 10, "run_id": str(run_id)},
    )
    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["run_id"] == str(run_id)
    assert payload["status"] == "completed"
    assert payload["current_stage"] == "complete"


def test_analysis_detail_limits_lookup_to_current_user(monkeypatch) -> None:
    user = UserPublic(id=uuid.uuid4(), username="user-a", role="user", auth_required=True)

    async def fake_get(run_id: str, *, user_id: uuid.UUID | None):
        assert run_id == "foreign-run"
        assert user_id == user.id
        return {"run_id": run_id}

    monkeypatch.setattr("app.main.get_analysis_history", fake_get)

    result = anyio.run(analysis_detail, "foreign-run", user)

    assert result == {"run_id": "foreign-run"}


def test_followup_endpoints_authorize_run_for_current_user(monkeypatch) -> None:
    user = UserPublic(id=uuid.uuid4(), username="user-a", role="user", auth_required=True)
    authorized: list[tuple[str, uuid.UUID | None]] = []

    async def fake_get(run_id: str, *, user_id: uuid.UUID | None):
        authorized.append((run_id, user_id))
        return {"run_id": run_id}

    async def fake_list(run_id: str):
        assert run_id == "foreign-run"
        return []

    monkeypatch.setattr("app.main.get_analysis_history", fake_get)
    monkeypatch.setattr("app.main.list_followup_history", fake_list)

    async def run() -> None:
        response = await analysis_followup_stream(
            "foreign-run",
            FollowupRequest(question="test"),
            user,
        )
        assert response.status_code == 200
        assert await analysis_followup_history("foreign-run", user) == []

    anyio.run(run)

    assert authorized == [("foreign-run", user.id), ("foreign-run", user.id)]


def test_annotation_update_limits_run_to_current_user(monkeypatch) -> None:
    user = UserPublic(id=uuid.uuid4(), username="user-a", role="user", auth_required=True)
    update = AnalysisHistoryUpdate(favorite=True, notes="mine", tags=["setup"])

    async def fake_update(run_id: str, payload: AnalysisHistoryUpdate, *, user_id: uuid.UUID | None):
        assert run_id == "foreign-run"
        assert payload == update
        assert user_id == user.id
        return {"run_id": run_id, "favorite": True}

    monkeypatch.setattr("app.main.update_analysis_history", fake_update)

    result = anyio.run(main_module.analysis_annotation_update, "foreign-run", update, user)

    assert result == {"run_id": "foreign-run", "favorite": True}
