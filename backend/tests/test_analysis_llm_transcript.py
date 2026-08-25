from __future__ import annotations

from types import SimpleNamespace

import anyio

from app.analysis.execution.runs import get_analysis_llm_transcript


def test_get_analysis_llm_transcript_returns_latest_stage_text(monkeypatch) -> None:
    rows = [
        {"stage": "stage1", "attempt": 2, "status": "validated", "raw_content": '{"gate_result":"proceed"}', "reasoning_content": "先看通道。"},
        {"stage": "stage2", "attempt": 1, "status": "completed", "raw_content": '{"terminal":{"outcome":"wait"}}', "reasoning_content": "信号不足。"},
        {"stage": "stage1", "attempt": 1, "status": "validation_failed", "raw_content": '{"broken":true}', "reasoning_content": "旧尝试"},
    ]

    class FakeRepository:
        async def get_run_unscoped(self, _analysis_id):
            return SimpleNamespace(stage_runs_json=rows)

    monkeypatch.setattr("app.analysis.tasks.repository.AnalysisTaskRepository", FakeRepository)

    transcript = anyio.run(get_analysis_llm_transcript, "aid-1")
    assert transcript["stage1"]["content"] == '{"gate_result":"proceed"}'
    assert transcript["stage1"]["reasoning"] == "先看通道。"
    assert transcript["stage2"]["content"] == '{"terminal":{"outcome":"wait"}}'
    assert transcript["stage2"]["reasoning"] == "信号不足。"
