from datetime import datetime, timezone

from app.core.models import HistoricalQuery
from app.personal.service import (
    PersonalSettingsUpdate,
    TokenUsageRecord,
    append_usage,
    build_debug_preview,
    get_usage,
    save_settings,
)


def test_settings_mask_key_and_preserve_existing_key(tmp_path, monkeypatch) -> None:
    settings_file = tmp_path / "settings.json"
    usage_file = tmp_path / "usage.jsonl"
    monkeypatch.setattr("app.personal.service.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.personal.service.SETTINGS_FILE", settings_file)
    monkeypatch.setattr("app.personal.service.USAGE_FILE", usage_file)

    saved = save_settings(PersonalSettingsUpdate(
        debug_enabled=True,
        active_model_id="model-1",
        models=[{"id": "model-1", "name": "OpenAI", "provider": "openai", "model": "gpt-5-mini", "api_key": "sk-secret-value"}],
    ))
    assert saved.models[0].has_api_key is True
    assert "secret" not in saved.model_dump_json()

    preserved = save_settings(PersonalSettingsUpdate(
        debug_enabled=True,
        active_model_id="model-1",
        models=[{"id": "model-1", "name": "OpenAI", "provider": "openai", "model": "gpt-5-mini"}],
    ))
    assert preserved.models[0].has_api_key is True

    preview = build_debug_preview(HistoricalQuery(
        symbol="ES", period="5m", start=datetime(2022, 1, 1, tzinfo=timezone.utc), end=datetime(2022, 1, 2, tzinfo=timezone.utc),
    ))
    assert preview.requires_confirmation is True
    assert preview.estimated_prompt_tokens > 0

    append_usage(TokenUsageRecord(
        run_id="analysis-1", model_id="model-1", model="gpt-5-mini", mode="historical", symbol="ES", period="5m",
        prompt_tokens=100, completion_tokens=20, total_tokens=120,
    ))
    usage = get_usage()
    assert usage.total_tokens == 120
    assert usage.analysis_count == 1


def test_deepseek_model_profile_is_supported(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.personal.service.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.personal.service.SETTINGS_FILE", tmp_path / "settings.json")
    settings = save_settings(PersonalSettingsUpdate(
        debug_enabled=False,
        active_model_id="deepseek-1",
        models=[{"id": "deepseek-1", "name": "DeepSeek", "provider": "deepseek", "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com", "api_key": "sk-deepseek"}],
    ))
    assert settings.models[0].provider == "deepseek"
    assert settings.models[0].model == "deepseek-v4-pro"
