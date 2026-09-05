# 分析领域模型重构设计

## 1. 背景与目标

当前实现混合使用任务、运行、结果、历史及多套运行标识，并用父子 Run 表达复盘拆分。目标模型改为一次性 Task 与平铺 Run：普通分析产生一条 Run，交易复盘按时间周期各产生一条 Run，不再存在父子 Run。

本次不迁移旧业务数据。数据库初始化时仅保留行情表及其数据，其他业务表直接删除并按目标 ORM 重新创建。

目标：

- `AnalysisTask` 表示一次性执行配置，创建后最多执行一次。
- `AnalysisRun` 表示一个时间周期的完整执行，不包含父子关系。
- 普通 K 线分析 Task 对应一条 Run。
- 交易复盘 Task 每个所选时间周期对应一条 Run；该 Run 一次处理全部所选交易。
- 阶段尝试和用户标注独立存储，模型结果保持不可变。
- API、日志、前端和追问统一使用 `run_id`。
- 历史、详情、标注和追问严格按当前用户隔离。
- 前端等待异步 Run 进入持久化终态后再展示结果。

非目标：

- 不保留任何旧业务数据或旧 API 兼容层。
- 不支持 Task 再次执行、Run 重跑或失败后重试。
- 不改变 Stage1 / Stage2 分析算法和结构化校验规则。
- 不新增数据库迁移工具或依赖，不记录迁移版本。

## 2. 领域模型

| 概念 | 定义 | 生命周期 |
| --- | --- | --- |
| `AnalysisTask` | 用户创建的一次性分析配置。 | 创建后可在执行前编辑；开始执行后不可再次执行或修改。 |
| `AnalysisRun` | Task 中一个时间周期的完整执行。 | queued → running → 终态。 |
| `AnalysisStageAttempt` | 某条 Run 的 Stage1/Stage2 单次调用或校验尝试。 | 运行期间追加或更新，Run 结束后只读。 |
| `AnalysisAnnotation` | 当前用户对 Run 结果的收藏、笔记和标签。 | 独立于模型结果编辑。 |
| 分析历史 | 已结束 Run 的查询视图，不是独立表。 | 由 Run、结果和当前用户标注组成。 |

目标关系：

```text
analysis_tasks
  1 ── 1..* analysis_runs
               1 ── * analysis_stage_attempts
               1 ── 0..1 analysis_annotations（每用户）
```

不存在 `parent_run_id`、`work_key`、父 Run、子 Run 或运行批次。

## 3. 数据结构

### 3.1 `analysis_tasks`

保留：`id`、`user_id`、`kind`、`title`、`description`、`status`、`config_json`、`version`、`created_at`、`updated_at`、`archived_at`。

规则：

- `status` 是一次性 Task 的聚合状态。
- 创建后状态为 `pending`；开始执行后不允许再次执行。
- 普通分析只有一个周期；复盘配置包含一个或多个唯一周期。
- 删除 `latest_run_id`，Task 不需要“最近一次运行”缓存。
- 执行开始后 Task 配置不可修改。
- 复盘 Task 状态由全部周期 Run 聚合：全部成功为 `completed`，部分成功为 `completed_with_warnings`，全部失败为 `failed`；取消或超时按实际终态聚合。

### 3.2 `analysis_runs`

使用 UUID 主键 `id`，API 公开为 `run_id`。

| 字段组 | 字段 |
| --- | --- |
| 归属 | `id`、`task_id`、`user_id` |
| 生命周期 | `status`、`current_stage`、`failure_stage`、`failure_code`、`failure_message`、`terminal_reason`、`started_at`、`completed_at`、`heartbeat_at` |
| 冻结输入 | `query_json`、`resolved_symbol`、`bars_json`、`bars_hash`、`prompt_versions_json`、`model_config_json` |
| 结果 | `result_json`、`schema_version` |
| 查询摘要 | `mode`、`symbol`、`period`、`direction`、`terminal_outcome`、`duration_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens` |
| 时间 | `created_at`、`updated_at` |

规则：

- 删除 `parent_run_id`、`work_key` 和 `sequence`。
- `(task_id, period)` 唯一，保证一个 Task 每个周期最多一条 Run。
- 普通 Task 创建一条其配置周期的 Run。
- 复盘 Task 按所选周期平铺创建 Run；每条 Run 的冻结输入包含全部所选交易及该周期行情。
- 终态为 `completed`、`completed_with_warnings`、`degraded`、`failed`、`cancelled`、`timed_out`。
- `task_id` 使用外键指向 `analysis_tasks.id`。
- `result_json` 保存经过结构化校验的完整结果，写入后不可因用户操作改变。

### 3.3 `analysis_stage_attempts`

保存 Run 各分析阶段的调用、重试和校验审计，唯一键为 `(run_id, stage, attempt)`。

保存阶段、尝试次数、状态、Provider/模型、请求 ID、Token、耗时、原始内容、标准化输出、校验错误、Provider 错误和提示词元数据。`run_id` 外键指向 `analysis_runs.id`，Run 删除时级联删除阶段尝试。

模型私有推理内容不通过用户 API 或前端展示。

### 3.4 `analysis_annotations`

保存 `favorite`、`notes`、`tags`、`created_at`、`updated_at`，唯一键为 `(run_id, user_id)`。

`run_id` 外键指向 `analysis_runs.id`，Run 删除时级联删除标注。标注更新不得修改 `analysis_runs.result_json`。

## 4. 执行与 API

### 一次性执行

- `POST /analysis-tasks/{task_id}/runs` 仅允许对 `pending` Task 调用一次。
- 普通 Task 原子创建一条 Run；复盘 Task 原子创建每个周期一条 Run。
- 响应返回创建的 Run 列表，每项包含 `run_id`、`period` 和初始状态。
- Task 已开始或已有任意 Run 时再次调用，返回 `analysis_task_already_executed`（409）。
- 不提供再次运行、重跑或失败重试入口。

### 异步终态

- 启动接口返回 `202` 后，前端按返回的全部 `run_id` 轮询详情。
- 轮询持续到所有 Run 进入终态或达到客户端超时。
- completed 类终态展示结果；failed、cancelled、timed_out 展示对应状态和错误。
- queued/running 且结果为空时继续等待，不视为成功。

### 历史和权限

- 历史列表直接查询终态 `analysis_runs`，不使用独立历史表。
- 列表、详情、标注更新、追问历史和追问流都必须同时匹配 `run_id` 与当前 `user_id`。
- 无权访问与资源不存在统一返回 `analysis_not_found`（404）。
- 历史列表只读取摘要和当前用户标注；详情按需读取冻结输入、结果和允许公开的审计信息。

### 单一公开标识

- `DemoAnalysisResponse`、运行详情、历史、追问事件、日志上下文和前端状态只使用 `run_id`。
- `analysis_id`、`execution_id`、`result_id`、旧 execution/result 路由和前端兼容回退全部删除。
- 生产代码不得保留旧运行标识；旧名称只允许出现在描述被删除旧结构的测试断言中。

## 5. 数据库直接重建

### 保留表

仅保留以下表及全部现有数据：

- `market_bars`
- `market_collection_states`

### 删除并重建的表

经用户批准的一次性切换中直接 `DROP TABLE IF EXISTS`：

- `users`
- `user_sessions`
- `audit_events`
- `prompt_versions`
- `analysis_tasks`
- `analysis_runs`
- `analysis_stage_attempts`
- `analysis_annotations`
- `followup_messages`
- `trades`
- `trade_import_batches`
- `alert_rules`
- `alert_records`
- `scheduled_job_runs`
- 旧 `analysis_history`

上述表及数据不迁移、不备份、不可恢复。删除后使用目标 ORM `create_all()` 重建空表；行情表不删除、不重建、不清空。切换完成后删除应用中的 Drop 实现。

### 执行约束

- 不引入 Alembic，不记录迁移版本。
- 一次性切换仅允许对已确认的 `tradeagent` 执行；完成后应用启动不得检测旧结构或执行任何 Drop。
- 全新数据库和已经是目标结构的数据库均只通过 ORM `create_all()` 幂等补齐目标表。
- 一次性 Drop 与目标结构创建在同一数据库事务中执行；失败时由数据库回滚。
- 执行前记录将保留和删除的精确表清单；不得使用数据库级 `DROP DATABASE` 或模糊匹配。
- 用户已在第 9 节追加授权本会话直接切换 `tradeagent`；不授权其他数据库或后续自动删除。

## 6. BDD 验收场景

1. **普通 Task 一次性执行**：给定 pending 普通 Task，当首次执行时创建唯一周期 Run；再次执行返回 409，且不新增 Run。
2. **复盘按周期平铺**：给定两笔交易和 `5m、1h` 两个周期，当执行复盘 Task 时创建两条平铺 Run；每条 Run 处理两笔交易，不存在父子关系。
3. **周期唯一**：给定 Task 已有 `5m` Run，当再次创建相同周期 Run 时，数据库唯一约束阻止重复。
4. **Task 状态聚合**：给定复盘 Task 的多个周期 Run，当全部成功、部分成功或全部失败时，Task 分别进入 completed、completed_with_warnings 或 failed。
5. **无重跑和重试**：给定任意已开始 Task 或终态 Run，当用户尝试再次执行或重试时，请求不受支持且数据不改变。
6. **历史详情可复现**：给定完成 Run，当读取详情时返回不可变输入、结果和允许公开的阶段审计；结果不包含用户笔记或标签。
7. **标注独立**：给定完成 Run，当用户更新收藏、笔记或标签时，只更新 `analysis_annotations`，`result_json` 保持不变。
8. **用户隔离**：给定用户 A 和 B 各有 Run，当 A 使用 B 的 `run_id` 读取详情、更新标注或追问时，返回与不存在一致的 404。
9. **异步终态**：给定详情依次返回 queued、running、completed，当用户启动分析时，前端持续等待并展示 completed 结果；失败、取消和超时显示对应终态。
10. **单一契约**：给定新 API 响应和前端调用，当检查契约时，只存在 `run_id`，不存在 `analysis_id`、`execution_id` 或 `result_id`。
11. **数据库直接重建**：给定数据库包含行情和其他业务表，当初始化执行时，行情两表及其数据保持不变，其他业务表被 Drop 后按目标结构重建为空表。
12. **关联完整性**：给定 Run 存在阶段尝试和标注，当 Run 被删除时，关联行级联删除，不留下孤儿数据。

## 7. 风险与边界

- 用户、会话、审计、提示词版本、任务、运行、追问、交易、导入、告警和调度记录都会永久删除。
- 删除用户后需要通过现有初始化方式重新创建管理员或用户，否则认证功能不可用。
- 只保留行情 K 线与采集状态；`scheduled_job_runs` 不属于保留数据。
- PostgreSQL 和 SQLite 都必须验证行情数据在重建前后行数及关键值一致。
- 真实 PostgreSQL 切换必须以前后摘要验证行情表内容保持不变。
- 除本次已授权的 `tradeagent` 切换外，不执行数据库删除、提交、推送或发布，除非用户另行明确要求。

## 8. 实施边界

本 Spec 获批后更新 `plan.md`，按“数据库重建 → 平铺 Run 模型 → 权限与契约 → 前端异步终态 → 全量验证与 Review”的顺序实施。所有代码修改遵循 TDD；Blocker 未关闭前不得提交或发布。

## 9. 真实数据库直接切换（2026-09-03 批准）

- 用户明确指定目标为 `localhost:5432/tradeagent`，并授权直接删除旧业务结构，不保留兼容能力。
- 执行前必须确认 `current_database() = 'tradeagent'`，记录两张行情表的行数与内容摘要，并记录全部待删除表。
- 一次性执行固定清单 Drop 与目标 ORM 建表；不得连接或修改 `postgres`、`sun`、`pa` 等其他数据库。
- 执行后必须验证行情表行数与内容摘要不变、旧表不存在、新业务表为空、关键外键和唯一约束存在。
- 真实数据库切换成功后，删除应用启动时的旧 Schema 检测与自动 Drop 兼容逻辑；`ensure_schema()` 只负责幂等创建目标表。
- 第三方弃用警告必须定位来源；优先通过显式配置或兼容版本修复，不以全局忽略警告代替解决。
- 本次授权不包含提交、推送、PR 或发布。
