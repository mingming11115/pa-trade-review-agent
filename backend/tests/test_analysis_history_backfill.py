import anyio
import uuid
from types import SimpleNamespace

from app.analysis.history.service import get_analysis_history
from app.core.database import Base


def test_legacy_analysis_history_table_is_not_registered() -> None:
    assert "analysis_history" not in Base.metadata.tables


def test_opening_history_recovers_transcript_without_mutating_result(monkeypatch) -> None:
    run_id = uuid.uuid4()
    original_result = {"run_id": str(run_id)}
    record = SimpleNamespace(
        id=run_id,
        result_json=dict(original_result),
    )

    class FakeSession:

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def scalar(self, _statement):
            return record

    async def no_schema():
        return None

    async def recover(_run_id):
        assert _run_id == run_id
        return {
            "stage1": {"reasoning": "先看结构。", "content": '{"gate_result":"wait"}'},
            "stage2": {"reasoning": "", "content": ""},
        }

    monkeypatch.setattr("app.analysis.history.service.ensure_schema", no_schema)
    monkeypatch.setattr("app.analysis.history.service.SessionFactory", FakeSession)
    monkeypatch.setattr("app.analysis.history.service.get_analysis_llm_transcript", recover)

    detail = anyio.run(get_analysis_history, run_id)

    assert detail["llm_transcript"]["stage1"]["reasoning"] == "先看结构。"
    assert record.result_json == original_result, "结果 JSON 在读取后必须保持不可变"
