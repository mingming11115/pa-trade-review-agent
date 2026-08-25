from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_admin_orchestration_lists_stage_prompt_files() -> None:
    response = TestClient(app).get("/api/v1/admin/orchestration")
    assert response.status_code == 200
    stages = {stage["id"]: stage for stage in response.json()["stages"]}
    assert [item["filename"] for item in stages["stage1"]["prompt_files"]][:4] == [
        "提示词大纲_人设与思维方式.txt",
        "二元决策.txt",
        "市场诊断框架.txt",
        "文件16-K线信号识别.txt",
    ]
    assert any(item["condition"].startswith("按 Stage 1") for item in stages["stage2"]["prompt_files"])
    assert stages["gate"]["prompt_files"] == []


def test_admin_prompt_file_read_and_version_conflict() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/admin/prompt-file", params={"filename": "市场诊断框架.txt"})
    assert response.status_code == 200
    document = response.json()
    assert "市场诊断框架" in document["content"]
    conflict = client.put(
        "/api/v1/admin/prompt-file",
        params={"filename": "市场诊断框架.txt"},
        json={"content": document["content"], "expected_version": "outdated"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "prompt_file_conflict"


def test_admin_prompt_file_rejects_path_traversal() -> None:
    response = TestClient(app).get("/api/v1/admin/prompt-file", params={"filename": "../.env"})
    assert response.status_code == 404
