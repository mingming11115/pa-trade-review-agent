from types import SimpleNamespace

import anyio

from app.analysis.history.service import get_analysis_history


def test_opening_old_history_persists_recovered_llm_transcript(monkeypatch) -> None:
    record = SimpleNamespace(
        result_json={"analysis_id": "aid-old"},
        favorite=False,
        notes="",
        tags=[],
        updated_at=None,
    )

    session_active = False
    committed = False

    class FakeSession:

        async def __aenter__(self):
            nonlocal session_active
            if session_active:
                raise AssertionError("history recovery must not nest database sessions")
            session_active = True
            return self

        async def __aexit__(self, *args):
            nonlocal session_active
            session_active = False
            return None

        async def get(self, _model, analysis_id):
            return record if analysis_id == "aid-old" else None

        async def commit(self):
            nonlocal committed
            committed = True

    async def no_schema():
        return None

    async def recover(_analysis_id):
        assert session_active is False, "history row session must close before transcript recovery"
        return {
            "stage1": {"reasoning": "先看结构。", "content": '{"gate_result":"wait"}'},
            "stage2": {"reasoning": "", "content": ""},
        }

    monkeypatch.setattr("app.analysis.history.service.ensure_schema", no_schema)
    monkeypatch.setattr("app.analysis.history.service.SessionFactory", FakeSession)
    monkeypatch.setattr("app.analysis.history.service.get_analysis_llm_transcript", recover)

    detail = anyio.run(get_analysis_history, "aid-old")

    assert detail["llm_transcript"]["stage1"]["reasoning"] == "先看结构。"
    assert record.result_json["llm_transcript"] == detail["llm_transcript"]
    assert record.updated_at is not None
    assert committed is True
