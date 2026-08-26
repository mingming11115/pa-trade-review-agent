# 任务、分析运行与分析历史的数据结构

> 依据当前实现整理：`backend/app/analysis/tasks/models.py`、`repository.py`、`history/service.py`、`execution/manager.py` 及 `frontend/src/types.ts`。本文描述运行时实际读写的结构；若代码与其他文档不一致，以代码为准。

## 概念与关系

系统将“任务定义”和“一次实际分析”分开保存：

```text
analysis_tasks（任务定义）
  1 ── * analysis_runs（父运行 / 分析历史项）
                 1 ── * analysis_runs（仅复盘任务的子运行）
```

- **任务（AnalysisTask）**：用户配置的可重复运行单元，分为普通分析 `analysis` 与交易复盘 `review`。
- **分析运行（AnalysisRun）**：一次实际执行。`analysis_id` 是贯穿 API、结果、追问和日志上下文的业务标识。
- **分析历史**：不是当前主存储实体；它是从父 `analysis_runs` 行生成的摘要/详情视图。
- **子运行**：仅复盘任务使用。一个父运行按 `交易 × 周期` 拆成多个子运行，父运行汇总其结果。

`analysis_history` 遗留表已移除：`ensure_schema()` 会在 PostgreSQL 和 SQLite 上执行 `DROP TABLE IF EXISTS analysis_history`，不会迁移该表内数据。历史查询、详情、收藏、笔记和标签均只读写 `analysis_runs`；新功能不应再创建或依赖该表。

## 1. 任务：`analysis_tasks`

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | UUID，主键 | 任务 ID。 |
| `user_id` | UUID，可空，索引 | 归属用户；空值用于本地/未登录场景。 |
| `kind` | `analysis` / `review`，索引 | 任务类型。 |
| `title` / `description` | string | 标题（最多 200 字符）及描述（最多 2000 字符）。 |
| `status` | `pending` / `running` / `completed` / `failed` / `cancelled` | 任务聚合状态。 |
| `config_json` | JSON | 类型相关的任务配置，见下表。 |
| `analysis_symbol` / `analysis_period` | string，可空，索引 | 普通分析任务从配置中冗余出的标准化品种和周期，用于唯一性及查询。 |
| `latest_analysis_id` | string，可空 | 最近一次父运行的 `analysis_id`。 |
| `version` | int | 乐观锁版本；更新任务须携带匹配版本。 |
| `created_at` / `updated_at` | 带时区时间 | 创建与最近修改时间。 |
| `archived_at` | 带时区时间，可空 | 归档时间；普通分析任务的活跃唯一性排除此类任务。 |

### `config_json` 的判别结构

```json
// kind = "analysis"
{ "symbol": "ES", "period": "5m" }

// kind = "review"
{
  "selected_trade_ids": ["<trade-uuid>"],
  "periods": ["5m", "15m"]
}
```

`symbol` 会去空格并转为大写；有效 `period` 为 `1m`、`5m`、`15m`、`30m`、`1h`、`4h`、`1d`。普通分析任务在同一用户（或同一无用户本地空间）内，同一 `symbol + period` 且未归档时只能存在一个。

任务编辑仅允许在 `pending` 且尚无父运行时进行。任务每次创建父运行会转为 `running`，运行结束后由父运行状态更新为 `completed`、`failed` 或 `cancelled`。

## 2. 分析运行：`analysis_runs`

这是当前的分析结果、审计和历史的主表。

| 分组 | 字段 | 类型 / 含义 |
| --- | --- | --- |
| 标识与归属 | `analysis_id` | string(64)，主键，也是外部分析 ID。 |
|  | `task_id`、`user_id` | UUID，可空，均有索引。 |
| 父子结构 | `parent_analysis_id` | string，可空。空值为父运行；非空为复盘子运行。 |
|  | `work_key` | string(240)，可空。子运行必填，格式为 `<trade_id>:<period>`。 |
|  | `sequence` | int，可空。父运行在同一任务内单调递增；子运行为空。 |
| 生命周期 | `status` | 见“运行状态”。 |
|  | `current_stage` | 当前阶段，默认 `prepare`。 |
|  | `failure_stage`、`failure_code`、`failure_message`、`terminal_reason` | 失败/终止诊断信息，可空。 |
|  | `started_at`、`completed_at`、`heartbeat_at` | 带时区时间，可空。 |
| 输入快照 | `input_json` | JSON；至少保存 `snapshot_id` 和查询快照。 |
|  | `bars_json` / `bars_hash` | 冻结的 K 线数组及 SHA-256 内容哈希。 |
|  | `prompt_versions_json` / `model_config_json` | JSON；运行所用提示词版本和模型配置快照。 |
| 业务摘要 | `mode`、`symbol`、`period` | 模式（`trade_review` / `historical` / `realtime`）、品种、周期。 |
|  | `direction`、`terminal_outcome` | 方向（通常为 `bullish` / `bearish` / `neutral`）和结论（通常为 `trade` / `reject` / `wait` / `error`）。 |
| 用量 | `duration_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens` | int；当前执行链路会保存可得数据。 |
| 结果与审计 | `result_json` | JSON；完整分析响应或复盘汇总结果。 |
|  | `stage_runs_json` | JSON 数组；每个 LLM 阶段/尝试的原始内容、标准化结果、校验信息和用量。 |
| 时间 | `created_at`、`updated_at` | 带时区时间。 |

### 父运行与子运行约束

- 父运行：`parent_analysis_id` 与 `work_key` 都为 `null`，且在同一 `task_id` 下的非空 `sequence` 唯一。
- 子运行：`parent_analysis_id` 与 `work_key` 必须同时存在；同一父运行下 `work_key` 唯一。
- 同一任务同一时刻只能有一个状态为 `queued`、`running` 或 `cancel_requested` 的父运行。
- `result_json` 为空意味着尚未形成完整结果；失败、取消、超时仍会在运行行上留下明确终态和失败信息。

### 关键 JSON 结构

普通分析的 `input_json`：

```json
{
  "snapshot_id": "<uuid>",
  "query": {
    "symbol": "ES",
    "period": "5m",
    "start": "2026-08-26T00:00:00Z",
    "end": "2026-08-26T08:20:00Z",
    "analysis_mode": "historical",
    "trades": []
  }
}
```

`result_json` 的普通分析主干为：

```json
{
  "query": { "symbol": "ES", "period": "5m", "analysis_mode": "historical" },
  "resolved_symbol": "<实际合约>",
  "analysis": { "direction": "bullish", "bar_count": 100 },
  "bars": ["<OHLCV bar>"],
  "stage1": { "...": "结构诊断结果" },
  "stage2": { "terminal": { "outcome": "wait" }, "...": "决策结果" },
  "audit": { "...": "执行审计" },
  "llm_transcript": { "stage1": {}, "stage2": {} },
  "favorite": false,
  "notes": "",
  "tags": []
}
```

复盘父运行的 `result_json` 为聚合结构：`query.analysis_mode = "trade_review"`、`review_children`（成功子运行的完整结果）、`review_result`（扁平化复盘结论数组）和聚合后的 `status`。

`bars_json` 的每个元素是 `timestamp`、可选 `timeframe/session/day_index` 以及 `open/high/low/close/volume`。生成运行前的 `FrozenInputSnapshot` 仅在进程内保存 15 分钟，用于确认执行；运行创建后，其必要输入和 K 线副本已写入该运行行。

### 运行状态

```text
queued → running → completed | completed_with_warnings | degraded | failed | cancelled | timed_out
queued/running → cancel_requested → cancelled | failed | timed_out
```

其中 `completed`、`completed_with_warnings`、`degraded`、`failed`、`cancelled`、`timed_out` 均为终态。

## 3. 分析历史：父运行的摘要视图

历史 API `GET /api/v1/analyses` 从 `analysis_runs` 中选择 `parent_analysis_id IS NULL` 的行，按 `created_at` 倒序返回。当前 HTTP 参数可按 `symbol`、`period` 和 `mode` 筛选；服务层也具备按 `favorite` 过滤的能力，但该参数尚未暴露在此 API 上。

| 历史字段 | 来源 |
| --- | --- |
| `analysis_id`、`mode`、`symbol`、`period`、`status`、`direction`、`task_id`、`created_at`、`updated_at` | 对应父 `analysis_runs` 列。 |
| `favorite`、`notes`、`tags` | `result_json` 内的用户编辑元数据；缺失时分别按 `false`、空字符串、空数组返回。 |
| `execution_id`、`result_id` | 兼容返回字段；当前基于 `analysis_runs` 的查询固定为 `null`。 |

详情 API `GET /api/v1/analyses/{analysis_id}` 返回 `result_json`。当其中未保存有效 `llm_transcript` 时，服务会根据该运行的 `stage_runs_json` 补建并回写转录内容。

收藏、笔记和标签的更新会将这些字段合并回对应父运行的 `result_json`：笔记上限 5000 字符，标签会去空白、去重并最多保留 20 个。

## API 与前端类型映射

| 用途 | 当前 API | 主返回结构 |
| --- | --- | --- |
| 创建/查询/更新任务 | `/api/v1/analysis-tasks` | `AnalysisTaskPublic` → 前端 `AnalysisTask`。 |
| 生成输入预览 | `POST /api/v1/analysis-tasks/{task_id}/preview` | `snapshot_id`、确认令牌、过期时间、合约、K 线哈希与数量。 |
| 启动/查询运行 | `/api/v1/analysis-tasks/{task_id}/runs`、`/api/v1/analysis-runs/{analysis_id}` | `AnalysisRunPublic`、`AnalysisRunListItem`、`AnalysisRunDetailPublic`。 |
| 取消运行 | `POST /api/v1/analysis-runs/{analysis_id}/cancel` | 更新后的运行状态。 |
| 查询历史/详情 | `/api/v1/analyses`、`/api/v1/analyses/{analysis_id}` | `AnalysisHistorySummary`、完整 `result_json`。 |

前端使用同名 TypeScript 类型承接上述结构。注意 `frontend/src/types.ts` 的 `AnalysisTask` 仍声明了 `latest_execution_id`，而当前后端 `AnalysisTaskPublic` 仅返回 `latest_analysis_id`；该字段不应被视为当前后端契约。

## 相关代码入口

- [任务与运行 ORM、Pydantic 契约](../../backend/app/analysis/tasks/models.py)
- [任务和运行的持久化规则](../../backend/app/analysis/tasks/repository.py)
- [执行与复盘父子运行编排](../../backend/app/analysis/execution/manager.py)
- [历史查询、详情和用户元数据](../../backend/app/analysis/history/service.py)
- [路由契约](../../backend/app/analysis/routes.py)
