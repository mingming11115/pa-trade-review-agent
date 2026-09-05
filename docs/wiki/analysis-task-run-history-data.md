# 分析任务、运行与历史数据结构

> 更新日期：2026-09-03。本文以当前 SQLAlchemy ORM 和服务实现为权威来源，描述重构完成后的分析领域数据结构。

## 总览

当前模型由一次性任务、按周期平铺的运行、阶段尝试和用户标注组成：

```text
analysis_tasks
  1 ── * analysis_runs
             1 ── * analysis_stage_attempts
             1 ── 0..1 analysis_annotations（每个用户）
             1 ── * followup_messages（逻辑关联）
```

- `AnalysisTask` 保存一次性分析配置。任务开始执行后不可再次执行或修改。
- `AnalysisRun` 表示一个周期的完整执行，也是分析历史的事实来源。
- 普通分析任务只产生一条 Run；交易复盘任务按所选周期各产生一条平铺 Run。
- `AnalysisStageAttempt` 保存 Stage1/Stage2 每次模型调用和结构化校验的审计数据。
- `AnalysisAnnotation` 独立保存收藏、笔记和标签，不修改模型结果。
- `followup_messages` 使用 `run_id` 关联追问上下文，但当前数据库层没有声明外键。

系统中不存在父 Run、子 Run、运行批次或独立的历史结果表。API、日志、结果和追问统一使用 `run_id`。

## 1. `analysis_tasks`：一次性任务

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID，主键 | Task ID。 |
| `user_id` | UUID，可空，索引 | 所属用户；未启用认证的本地模式使用空值。 |
| `kind` | string(20)，索引 | `analysis` 或 `review`。 |
| `title` | string(200) | 任务标题。 |
| `description` | text | 任务描述。 |
| `status` | string(40)，索引 | Task 聚合状态。 |
| `config_json` | JSON | 按任务类型保存并校验的配置。 |
| `analysis_symbol` | string(100)，可空，索引 | 普通分析任务的标准化品种，用于查询和活跃任务唯一约束。 |
| `analysis_period` | string(20)，可空，索引 | 普通分析任务的周期，用于查询和活跃任务唯一约束。 |
| `version` | integer | 更新任务时使用的乐观锁版本。 |
| `created_at` / `updated_at` | timestamptz | 创建和更新时间。 |
| `archived_at` | timestamptz，可空 | 归档时间。 |

任务配置采用判别结构：

```json
// kind = "analysis"
{ "symbol": "ES", "period": "5m" }

// kind = "review"
{
  "selected_trade_ids": ["<trade-uuid>"],
  "periods": ["5m", "1h"]
}
```

普通分析任务的品种会去除首尾空白并转为大写。支持的周期为 `1m`、`5m`、`15m`、`30m`、`1h`、`4h`、`1d`。

### 唯一约束

未归档的普通分析任务按所有者、品种和周期保持唯一：

- 登录用户：`(user_id, analysis_symbol, analysis_period)` 唯一。
- 本地模式：`(analysis_symbol, analysis_period)` 唯一，条件为 `user_id IS NULL`。

这两个约束都是仅作用于 `kind = 'analysis' AND archived_at IS NULL` 的部分唯一索引。

### 生命周期

```text
pending → running → completed
                  → completed_with_warnings
                  → failed
                  → cancelled
                  → timed_out
```

只有 `pending` Task 可以编辑和启动。首次启动会在同一事务中创建全部周期 Run，并把 Task 置为 `running`；再次启动返回 `analysis_task_already_executed`。Task 的终态由其所有 Run 聚合得出。

## 2. `analysis_runs`：周期运行与历史事实

`analysis_runs` 是冻结输入、执行状态、模型结果、统计摘要和分析历史的主表。

| 分组 | 字段 | 说明 |
| --- | --- | --- |
| 标识与归属 | `id` | UUID 主键；对外名称为 `run_id`。 |
|  | `task_id` | UUID，非空，外键指向 `analysis_tasks.id`。 |
|  | `user_id` | UUID，可空，索引；用于用户隔离。 |
| 生命周期 | `status`、`current_stage` | 运行状态和当前阶段。 |
|  | `failure_stage`、`failure_code`、`failure_message` | 失败发生阶段、稳定错误码和可展示消息。 |
|  | `terminal_reason` | 取消、超时或其他终止原因。 |
|  | `started_at`、`completed_at`、`heartbeat_at` | 启动、完成和心跳时间。 |
| 冻结输入 | `query_json` | 本次运行的查询或复盘输入。 |
|  | `resolved_symbol` | 实际解析出的交易合约。 |
|  | `bars_json`、`bars_hash` | 冻结 K 线数据及内容哈希。 |
|  | `prompt_versions_json` | 本次使用的提示词版本快照。 |
|  | `model_config_json` | 本次使用的模型配置快照。 |
| 结果 | `result_json` | 经过结构化校验的完整分析结果；运行完成后作为不可变模型产出使用。 |
|  | `schema_version` | 结果结构版本。 |
| 查询摘要 | `mode`、`symbol`、`period` | 分析模式、品种和周期。 |
|  | `direction`、`terminal_outcome` | 方向与最终决策摘要。 |
| 用量 | `duration_ms` | 总耗时。 |
|  | `prompt_tokens`、`completion_tokens`、`total_tokens` | Token 用量。 |
| 时间 | `created_at`、`updated_at` | 创建和更新时间。 |

### 平铺与唯一性

数据库唯一索引 `uq_analysis_run_task_period` 保证 `(task_id, period)` 唯一。因此：

- 普通 Task 创建一个与配置周期一致的 Run。
- 复盘 Task 为每个所选周期创建一个 Run。
- 每条复盘 Run 的 `query_json` 包含全部所选交易，而不是按交易拆成子 Run。
- 同一个 Task 不可能重复创建相同周期的 Run。

`task_id` 外键配置 `ON DELETE CASCADE`；删除 Task 会级联删除其 Run。

### Run 生命周期

```text
queued → running → completed
                 → completed_with_warnings
                 → degraded
                 → failed
                 → cancelled
                 → timed_out

queued/running → cancel_requested → cancelled | failed | timed_out
```

`completed`、`completed_with_warnings`、`degraded`、`failed`、`cancelled` 和 `timed_out` 都是终态。成功、失败、取消和超时都会写入持久化终态；失败信息保存在同一 Run 行中。

## 3. `analysis_stage_attempts`：阶段尝试审计

每条记录表示某个 Run 的某个阶段的一次模型调用或校验尝试。

| 分组 | 字段 |
| --- | --- |
| 标识 | `id`、`run_id`、`stage`、`attempt`、`status` |
| 模型调用 | `provider`、`model`、`provider_request_id`、`response_model` |
| 性能与用量 | `duration_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens` |
| 原始与规范化数据 | `raw_content`、`reasoning_content`、`raw_response`、`normalized_output` |
| 错误与元数据 | `validation_errors`、`provider_error`、`prompt_metadata` |
| 时间 | `started_at`、`updated_at` |

关键约束：

- `(run_id, stage, attempt)` 唯一。
- `run_id` 外键指向 `analysis_runs.id`，并使用 `ON DELETE CASCADE`。
- 阶段数据与 `result_json` 分开保存，便于审计单次调用、验证失败和 Token 用量。

## 4. `analysis_annotations`：用户标注

| 字段 | 说明 |
| --- | --- |
| `id` | UUID 主键。 |
| `run_id` | 所属 Run，外键级联删除。 |
| `user_id` | 标注所属用户；本地模式可为空。 |
| `favorite` | 是否收藏。 |
| `notes` | 用户笔记，API 限制最多 5000 字符。 |
| `tags` | JSON 标签数组；服务层会去空白、去重并限制数量。 |
| `created_at` / `updated_at` | 创建和更新时间。 |

唯一索引 `(run_id, user_id)` 保证同一用户对同一 Run 最多一条标注。收藏、笔记和标签只写入该表，不合并回 `analysis_runs.result_json`。

## 5. `followup_messages`：追问消息

| 字段 | 说明 |
| --- | --- |
| `id` | 自增整数主键。 |
| `run_id` | 字符串形式的 Run ID，索引。 |
| `seq` | 会话内消息顺序，索引。 |
| `role` | 消息角色。 |
| `content` | 消息正文。 |
| `created_at` | 创建时间。 |

追问服务在访问分析详情和追问接口时先按当前用户校验 Run，因此无权访问和 Run 不存在对外都表现为相同的 404。需要注意：`followup_messages.run_id` 当前是逻辑关联，不是数据库外键，删除 Run 不会由数据库自动级联清理追问消息。

## 6. 历史、详情与用户隔离

“分析历史”不是独立表，而是以下数据的组合视图：

```text
AnalysisRun + 当前用户的 AnalysisAnnotation
```

- 历史列表直接查询当前用户的 `analysis_runs`，可按 `task_id`、`symbol`、`period`、`mode` 和收藏状态过滤。
- 历史详情通过 `run_id` 返回冻结输入、结构化结果和允许公开的阶段信息。
- 收藏、笔记和标签来自当前用户的 `analysis_annotations`。
- 所有详情、标注和追问入口均在查询源头加入 owner 条件。
- 访问其他用户的 `run_id` 与访问不存在的 ID 返回同样的 404，避免泄露资源存在性。
- 未启用认证时，`user_id IS NULL` 代表本地数据空间。

## 7. 数据删除与初始化行为

当前 `ensure_schema()` 只在事务中执行 SQLAlchemy `Base.metadata.create_all()`：

- 可幂等创建缺失表。
- 不检测旧字段。
- 不执行 `DROP TABLE`。
- 不迁移旧业务数据。
- 不清空现有数据。

本次重构曾对指定的 `tradeagent` 数据库执行一次性业务表重建；该破坏性逻辑已经从生产代码删除。行情表 `market_bars` 和 `market_collection_states` 不属于分析领域级联关系，仍独立保存行情和采集状态。

## 8. 已移除的旧结构

当前生产模型和 API 不再包含：

- 父子 Run、`parent_run_id`、`work_key`、`sequence`。
- Task 的 `latest_run_id` 或“最近一次运行”缓存。
- `analysis_id`、`execution_id`、`result_id` 等兼容标识。
- 独立的 `analysis_history`、`analysis_executions`、`analysis_results`、`analysis_stage_runs` 等旧表。
- Run 重跑、失败重试和 Task 再次执行。

不要在新代码中重新引入这些概念。分析对象的唯一公开运行标识是 `run_id`。

## 9. 代码入口

- [任务、Run、阶段尝试与标注 ORM](../../backend/app/analysis/tasks/models.py)
- [任务与 Run 持久化规则](../../backend/app/analysis/tasks/repository.py)
- [Task/Run 状态迁移](../../backend/app/analysis/tasks/lifecycle.py)
- [执行管理与 Task 状态聚合](../../backend/app/analysis/execution/manager.py)
- [历史查询、详情和标注](../../backend/app/analysis/history/service.py)
- [追问消息持久化](../../backend/app/followup/service.py)
- [数据库初始化](../../backend/app/core/database.py)
