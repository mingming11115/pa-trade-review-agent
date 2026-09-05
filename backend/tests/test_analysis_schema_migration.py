from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.analysis.tasks.models import AnalysisAnnotation, AnalysisRun, AnalysisStageAttempt, AnalysisTask
from app.auth.service import User  # noqa: F401
from app.core.database import ensure_schema
from app.market.service import CollectionState, MarketBar  # noqa: F401


@pytest.mark.anyio
async def test_ensure_schema_never_drops_existing_tables(tmp_path, monkeypatch) -> None:
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'target.db'}")
    async with test_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE analysis_history (id INTEGER PRIMARY KEY, marker TEXT)"))
        await connection.execute(text("INSERT INTO analysis_history VALUES (1, 'must-remain')"))

    monkeypatch.setattr("app.core.database.engine", test_engine)
    await ensure_schema()

    async with test_engine.connect() as connection:
        marker = (await connection.execute(text("SELECT marker FROM analysis_history"))).scalar_one()
    assert marker == "must-remain"
    await test_engine.dispose()


def test_flat_run_schema_has_no_parent_or_latest_fields() -> None:
    assert {"parent_run_id", "work_key", "sequence"}.isdisjoint(AnalysisRun.__table__.c.keys())
    assert "latest_run_id" not in AnalysisTask.__table__.c
    assert any(index.name == "uq_analysis_run_task_period" for index in AnalysisRun.__table__.indexes)
    assert next(iter(AnalysisRun.__table__.c.task_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(AnalysisStageAttempt.__table__.c.run_id.foreign_keys)).ondelete == "CASCADE"
    assert next(iter(AnalysisAnnotation.__table__.c.run_id.foreign_keys)).ondelete == "CASCADE"

