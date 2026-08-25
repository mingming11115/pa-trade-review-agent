from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.analysis.tasks.models import AnalysisRunPublic
from app.main import app


def test_admin_analysis_runs_returns_persisted_run_metadata(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    record = AnalysisRunPublic(
        analysis_id="analysis-1",
        task_id=None,
        parent_analysis_id=None,
        work_key=None,
        sequence=1,
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

    async def fake_list(limit: int, analysis_id: str | None):
        assert limit == 10
        assert analysis_id == "analysis-1"
        return [record]

    monkeypatch.setattr("app.main.list_analysis_runs", fake_list)
    response = TestClient(app).get(
        "/api/v1/admin/analysis-runs",
        params={"limit": 10, "analysis_id": "analysis-1"},
    )
    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["analysis_id"] == "analysis-1"
    assert payload["status"] == "completed"
    assert payload["current_stage"] == "complete"
