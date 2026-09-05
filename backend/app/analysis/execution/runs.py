"""纯内存的 stage-runs 收集器，不再直接写库。

每次 LLM 调用的明细由 workflow 收集到 state["stage_runs"]，
最终由调用方（manager）在保存运行行时一次性写入 stage_runs_json 字段。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.llm.client import LLMResponse

logger = logging.getLogger(__name__)

_STAGE_RUNS: dict[str, list[dict[str, Any]]] = {}
_STAGE_RUN_INDEX: dict[uuid.UUID, tuple[str, str, int]] = {}


def get_stage_runs(run_id: str) -> list[dict[str, Any]]:
    return list(_STAGE_RUNS.get(run_id, []))


def clear_stage_runs(run_id: str) -> None:
    """释放指定 Run 的临时阶段记录，避免一次性运行完成后残留内存。"""
    records = _STAGE_RUNS.pop(run_id, [])
    record_ids = {str(record.get("id")) for record in records}
    for record_id in list(_STAGE_RUN_INDEX):
        if str(record_id) in record_ids:
            _STAGE_RUN_INDEX.pop(record_id, None)


def _empty_llm_transcript() -> dict[str, dict[str, str]]:
    return {
        "stage1": {"reasoning": "", "content": ""},
        "stage2": {"reasoning": "", "content": ""},
    }


def get_analysis_llm_transcript_from_stage_runs(stage_runs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """从运行行的 stage_runs_json 中提取最新有效 attempt 的 reasoning/content，组装转录。"""
    transcript = _empty_llm_transcript()
    preferred_status = {"validated", "completed", "response_received", "validation_failed"}
    chosen: dict[str, dict[str, Any]] = {}
    for item in stage_runs:
        stage = str(item.get("stage") or "")
        if stage not in transcript:
            continue
        if not (item.get("raw_content") or item.get("reasoning_content")):
            continue
        current = chosen.get(stage)
        if current is None:
            chosen[stage] = item
            continue
        # 同 stage 取最新 attempt
        if current.get("attempt", 0) < item.get("attempt", 0):
            chosen[stage] = item
        elif current.get("attempt", 0) == item.get("attempt", 0):
            # 同 attempt 取更优先的状态
            if current.get("status") not in preferred_status and item.get("status") in preferred_status:
                chosen[stage] = item
    for stage, item in chosen.items():
        transcript[stage] = {
            "reasoning": str(item.get("reasoning_content") or ""),
            "content": str(item.get("raw_content") or ""),
        }
    return transcript


async def get_analysis_llm_transcript(run_id) -> dict[str, dict[str, str]]:
    """从 `analysis_stage_attempts` 恢复 LLM 转录，不修改运行行结果。"""
    from app.analysis.tasks.repository import AnalysisTaskRepository

    repo = AnalysisTaskRepository()
    try:
        attempts = await repo.list_stage_attempts(uuid.UUID(str(run_id)))
        if attempts:
            rows = [
                {
                    "stage": attempt.stage,
                    "attempt": attempt.attempt,
                    "status": attempt.status,
                    "raw_content": attempt.raw_content,
                    "reasoning_content": attempt.reasoning_content,
                }
                for attempt in attempts
            ]
            return get_analysis_llm_transcript_from_stage_runs(rows)
    except Exception:
        pass
    return _empty_llm_transcript()


# 以下函数为 workflow 内部使用的纯内存收集器，不再写库

def new_stage_run(
    *,
    run_id: str,
    stage: str,
    attempt: int,
    status: str,
    provider: str = "",
    model: str = "",
    provider_request_id: str | None = None,
    response_model: str | None = None,
    duration_ms: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    raw_content: str = "",
    reasoning_content: str = "",
    raw_response: dict[str, Any] | None = None,
    normalized_output: dict[str, Any] | None = None,
    validation_errors: list[Any] | None = None,
    provider_error: dict[str, Any] | None = None,
    prompt_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一个 stage-run 明细记录（纯字典，非 ORM）。"""
    record = {
        "run_id": run_id,
        "stage": stage,
        "attempt": attempt,
        "status": status,
        "provider": provider,
        "model": model,
        "provider_request_id": provider_request_id,
        "response_model": response_model,
        "duration_ms": duration_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "raw_content": raw_content,
        "reasoning_content": reasoning_content,
        "raw_response": raw_response,
        "normalized_output": normalized_output,
        "validation_errors": validation_errors,
        "provider_error": provider_error,
        "prompt_metadata": prompt_metadata or {},
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _STAGE_RUNS.setdefault(run_id, [])
    _STAGE_RUNS[run_id] = upsert_stage_run(_STAGE_RUNS[run_id], record)
    record_id = uuid.uuid4()
    _STAGE_RUN_INDEX[record_id] = (run_id, stage, attempt)
    record["id"] = str(record_id)
    return record


def upsert_stage_run(stage_runs: list[dict[str, Any]], record: dict[str, Any]) -> list[dict[str, Any]]:
    """按 (stage, attempt) 替换语义更新 stage_runs 列表，返回新列表。"""
    stage = str(record.get("stage") or "")
    attempt = int(record.get("attempt") or 0)
    result = [item for item in stage_runs if not (str(item.get("stage") or "") == stage and int(item.get("attempt") or 0) == attempt)]
    result.append(record)
    result.sort(key=lambda item: (str(item.get("stage") or ""), int(item.get("attempt") or 0)))
    return result


# 以下为 workflow 使用的旧接口兼容层（已改为操作内存 stage_runs 列表）
# 这些函数将逐步被 workflow 内部直接调用 new_stage_run/upsert_stage_run 替代

async def persist_llm_response(
    response: LLMResponse,
    *,
    run_id: str,
    stage: str,
    attempt: int,
    mode: str,
    symbol: str,
    period: str,
) -> uuid.UUID | None:
    """将 LLM 响应写入内存 stage_runs。"""
    record = new_stage_run(
        run_id=run_id,
        stage=stage,
        attempt=attempt,
        status="response_received",
        provider=response.provider,
        model=response.model,
        provider_request_id=response.provider_request_id,
        response_model=response.response_model,
        duration_ms=response.duration_ms,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        total_tokens=response.total_tokens,
        raw_content=response.raw_content,
        reasoning_content=response.reasoning_content,
        raw_response=response.raw_response,
    )
    return uuid.UUID(str(record["id"]))


async def start_analysis_run(
    *,
    run_id: str,
    stage: str,
    attempt: int,
    mode: str,
    symbol: str,
    period: str,
    provider: str = "",
    model: str = "",
) -> uuid.UUID | None:
    """创建一条内存中的 stage-run 占位记录。"""
    record = new_stage_run(
        run_id=run_id,
        stage=stage,
        attempt=attempt,
        status="request_started",
        provider=provider,
        model=model,
    )
    return uuid.UUID(str(record["id"]))


async def attach_llm_response(run_id: uuid.UUID | None, response: LLMResponse) -> None:
    """更新内存 stage-run 的响应字段。"""
    if run_id is None:
        return
    loc = _STAGE_RUN_INDEX.get(run_id)
    if loc is None:
        return
    run_id, stage, attempt = loc
    stage_runs = _STAGE_RUNS.get(run_id, [])
    updated: list[dict[str, Any]] = []
    for item in stage_runs:
        if str(item.get("stage") or "") == stage and int(item.get("attempt") or 0) == attempt:
            item = dict(item)
            item.update({
                "status": "response_received",
                "provider": response.provider,
                "model": response.model,
                "provider_request_id": response.provider_request_id,
                "response_model": response.response_model,
                "duration_ms": response.duration_ms,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.total_tokens,
                "raw_content": response.raw_content,
                "reasoning_content": response.reasoning_content,
                "raw_response": response.raw_response,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        updated.append(item)
    _STAGE_RUNS[run_id] = updated


async def update_analysis_run(
    run_id: uuid.UUID | None,
    *,
    status: str,
    normalized_output: dict[str, Any] | None = None,
    validation_errors: list[Any] | None = None,
) -> None:
    """更新内存 stage-run 的校验/完成状态。"""
    if run_id is None:
        return
    loc = _STAGE_RUN_INDEX.get(run_id)
    if loc is None:
        return
    run_id, stage, attempt = loc
    updated: list[dict[str, Any]] = []
    for item in _STAGE_RUNS.get(run_id, []):
        if str(item.get("stage") or "") == stage and int(item.get("attempt") or 0) == attempt:
            item = dict(item)
            item.update({
                "status": status,
                "normalized_output": normalized_output,
                "validation_errors": validation_errors,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        updated.append(item)
    _STAGE_RUNS[run_id] = updated


async def list_analysis_runs(limit: int = 100, run_id: str | None = None) -> list[Any]:
    """列出分析运行行。"""
    from app.analysis.tasks.models import AnalysisRun as RunModel
    from app.core.database import SessionFactory, ensure_schema
    from sqlalchemy import desc, select

    await ensure_schema()
    async with SessionFactory() as session:
        statement = select(RunModel)
        if run_id:
            statement = statement.where(RunModel.id == uuid.UUID(str(run_id)))
        statement = statement.order_by(desc(RunModel.created_at)).limit(limit)
        records = (await session.scalars(statement)).all()
    return records
