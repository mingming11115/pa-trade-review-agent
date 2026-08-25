from __future__ import annotations

import json
import os
import uuid
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.config import ROOT_DIR
from app.core.errors import AppError
from app.core.models import HistoricalQuery


# 个人配置与用量数据的存储目录
DATA_DIR = ROOT_DIR / "data"
# LLM 模型配置文件路径
SETTINGS_FILE = DATA_DIR / "llm-settings.json"
# Token 用量记录文件路径（JSONL 格式）
USAGE_FILE = DATA_DIR / "token-usage.jsonl"
# 文件读写锁，保证多线程安全
_LOCK = Lock()
# 基于上下文变量的设置作用域，用于区分不同用户
_SETTINGS_SCOPE: ContextVar[str] = ContextVar("pa_settings_scope", default="local")


def set_settings_scope(username: str) -> None:
    """根据用户名设置配置作用域，对非法字符进行替换处理。"""
    _SETTINGS_SCOPE.set(re.sub(r"[^a-zA-Z0-9_.-]", "_", username)[:80] or "local")


def _settings_file() -> Path:
    """根据当前作用域返回对应的配置文件路径。"""
    scope = _SETTINGS_SCOPE.get()
    return SETTINGS_FILE if scope == "local" else DATA_DIR / f"llm-settings-{scope}.json"


def _usage_file() -> Path:
    """根据当前作用域返回对应的用量文件路径。"""
    scope = _SETTINGS_SCOPE.get()
    return USAGE_FILE if scope == "local" else DATA_DIR / f"token-usage-{scope}.jsonl"


class ModelProfileInput(BaseModel):
    """模型配置输入模型，用于接收前端提交的模型信息。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=60)
    provider: Literal["openai", "anthropic", "gemini", "deepseek", "compatible"]
    model: str = Field(min_length=1, max_length=120)
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=500)


class ModelProfilePublic(BaseModel):
    """模型配置公开模型，对外返回时隐藏完整 API Key。"""
    id: str
    name: str
    provider: str
    model: str
    base_url: str | None = None
    has_api_key: bool
    api_key_masked: str | None = None


class PersonalSettingsUpdate(BaseModel):
    """个人设置更新模型，包含调试开关、当前激活模型和模型列表。"""
    debug_enabled: bool
    active_model_id: str | None = None
    models: list[ModelProfileInput]

    @model_validator(mode="after")
    def validate_active_model(self) -> "PersonalSettingsUpdate":
        """校验模型 ID 不重复，且激活模型存在于模型列表中。"""
        ids = [model.id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("模型配置 ID 不能重复")
        if self.active_model_id and self.active_model_id not in ids:
            raise ValueError("当前模型必须存在于模型列表")
        return self


class PersonalSettingsPublic(BaseModel):
    """个人设置公开模型，用于对外返回。"""
    debug_enabled: bool = False
    active_model_id: str | None = None
    models: list[ModelProfilePublic] = Field(default_factory=list)


class TokenUsageRecord(BaseModel):
    """单次 Token 用量记录。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    analysis_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_id: str | None = None
    model: str | None = None
    mode: str
    symbol: str
    period: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    status: Literal["estimated", "completed", "failed"] = "completed"


class TokenUsageSummary(BaseModel):
    """Token 用量汇总，包含总计统计与详细记录列表。"""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    analysis_count: int
    records: list[TokenUsageRecord]


class DebugPreview(BaseModel):
    """调试预览模型，用于在正式调用 LLM 前预览输入并估算 Token 数量。"""
    confirmation_id: str
    requires_confirmation: bool
    model: ModelProfilePublic | None
    llm_input: dict[str, Any]
    estimated_prompt_tokens: int
    estimated_max_completion_tokens: int = 2000


def _read_settings_raw() -> dict[str, Any]:
    """读取原始配置，文件不存在时返回默认空配置。"""
    path = _settings_file()
    if not path.exists():
        return {"debug_enabled": False, "active_model_id": None, "models": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AppError("llm_settings_invalid", "本地模型配置无法读取", 500) from exc


def _mask_key(key: str | None) -> str | None:
    """对 API Key 做脱敏处理，仅保留首尾部分字符。"""
    if not key:
        return None
    return f"{key[:3]}••••{key[-4:]}" if len(key) > 8 else "••••••••"


def get_public_settings() -> PersonalSettingsPublic:
    """获取公开设置，将原始配置转换为脱敏后的公开模型。"""
    raw = _read_settings_raw()
    return PersonalSettingsPublic(
        debug_enabled=bool(raw.get("debug_enabled")),
        active_model_id=raw.get("active_model_id"),
        models=[ModelProfilePublic(
            id=model["id"], name=model["name"], provider=model["provider"], model=model["model"],
            base_url=model.get("base_url"), has_api_key=bool(model.get("api_key")), api_key_masked=_mask_key(model.get("api_key")),
        ) for model in raw.get("models", [])],
    )


def get_active_model() -> dict[str, Any] | None:
    """获取当前激活的模型原始配置，未设置时返回 None。"""
    raw = _read_settings_raw()
    active_id = raw.get("active_model_id")
    return next((model for model in raw.get("models", []) if model.get("id") == active_id), None)


def save_settings(update: PersonalSettingsUpdate) -> PersonalSettingsPublic:
    """保存设置，保留已有 API Key，通过临时文件原子写入并设置权限。"""
    current = _read_settings_raw()
    existing_keys = {model["id"]: model.get("api_key") for model in current.get("models", [])}
    payload = update.model_dump()
    for model in payload["models"]:
        model["api_key"] = model.get("api_key") or existing_keys.get(model["id"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = _settings_file()
    temporary = settings_file.with_suffix(".tmp")
    with _LOCK:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(settings_file)
    return get_public_settings()


def append_usage(record: TokenUsageRecord) -> None:
    """追加写入一条 Token 用量记录到 JSONL 文件。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        usage_file = _usage_file()
        with usage_file.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")
        os.chmod(usage_file, 0o600)


def get_usage(limit: int = 200) -> TokenUsageSummary:
    """读取并汇总 Token 用量记录，按时间倒序返回最近 limit 条。"""
    records: list[TokenUsageRecord] = []
    usage_file = _usage_file()
    if usage_file.exists():
        for line in usage_file.read_text(encoding="utf-8").splitlines():
            try:
                records.append(TokenUsageRecord.model_validate_json(line))
            except ValueError:
                continue
    records = sorted(records, key=lambda item: item.occurred_at, reverse=True)[:limit]
    return TokenUsageSummary(
        prompt_tokens=sum(item.prompt_tokens for item in records),
        completion_tokens=sum(item.completion_tokens for item in records),
        total_tokens=sum(item.total_tokens for item in records),
        analysis_count=len({item.analysis_id for item in records}),
        records=records,
    )


def build_debug_preview(query: HistoricalQuery) -> DebugPreview:
    """构建调试预览，组装 LLM 输入并粗略估算 prompt Token 数量。"""
    settings = get_public_settings()
    model = next((item for item in settings.models if item.id == settings.active_model_id), None)
    llm_input = {
        "mode": query.analysis_mode,
        "market_query": query.model_dump(mode="json", exclude={"trades"}),
        "trades": [trade.model_dump(mode="json") for trade in query.trades],
        "pipeline": ["stage1", "gate", "stage2", "trade_review"],
    }
    # 按 4 字符约等于 1 Token 的粗略比例估算
    serialized = json.dumps(llm_input, ensure_ascii=False)
    estimate = max(1, (len(serialized) + 3) // 4)
    return DebugPreview(
        confirmation_id=str(uuid.uuid4()),
        requires_confirmation=settings.debug_enabled,
        model=model,
        llm_input=llm_input,
        estimated_prompt_tokens=estimate,
    )
