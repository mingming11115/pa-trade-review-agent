import json

from app.analysis.audit import append_stage1_audit


def test_stage1_audit_is_jsonl_and_private(tmp_path, monkeypatch) -> None:
    path = tmp_path / "stage1.jsonl"
    monkeypatch.setattr("app.analysis.audit.AUDIT_FILE", path)
    append_stage1_audit({"analysis_id": "a1", "gate_result": "proceed"})
    assert json.loads(path.read_text(encoding="utf-8"))["analysis_id"] == "a1"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
