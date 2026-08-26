# 分析领域模型重构设计

## 1. 背景与问题

当前实现把三类不同职责压入 `analysis_runs`：一次运行的生命周期、最终分析结果、以及历史列表需要的用户标注；`stage_runs_json` 又同时承担阶段审计明细。`analysis_tasks.status` 与最近运行的状态重复，历史上多套运行标识在不同契约中也存在概念重叠。

`analysis_history` 曾是独立表，但当前业务读取已转向 `analysis_runs`。因此它不应再作为新的领域实体或新的事实来源。

本设计的目标是明确“任务、运行、结果、可复现输入、审计、用户标注”的边界，让历史成为查询视图而不是另一份业务数据。

## 2. 目标与非目标

### 目标

- 为每个分析概念指定唯一事实来源。
- 保留同一任务的多次运行，以及交易复盘的父子运行关系。
- 支持可复现：能定位一次运行所用的市场输入、提示词版本和模型配置。
- 让历史列表不依赖复制表，不把用户标注混入模型产出的分析事实。
- 让阶段重试、失败诊断和 Token 用量可以独立查询与审计。
- 将 API 主标识收敛为单一 `run_id`。

### 非目标

- 不改变 Stage1 / Stage2 的分析算法或 LLM 输出结构。
- 不改变交易、行情、追问的业务需求。
- 不在本 Issue 中改变前端信息架构或增加新的用户功能。
- 不保留 `analysis_history` 作为长期兼容数据源。

## 3. 领域语言

| 概念 | 定义 | 生命周期 |
| --- | --- | --- |
| 分析任务 `AnalysisTask` | 用户保存的、可重复执行的配置。 | 创建后可编辑、归档。 |
| 分析运行 `AnalysisRun` | 任务的一次实际执行尝试。 | 排队到终态。 |
| 分析结果 `AnalysisResult` | 本次运行的结构化业务结论。 | 终态时写入，不可变。 |
| 阶段尝试 `AnalysisStageAttempt` | Stage1/Stage2 单次调用或校验尝试的审计明细。 | 可追加/更新到运行终态。 |
| 用户标注 `AnalysisAnnotation` | 收藏、笔记、标签等用户对结果的编辑。 | 独立于模型结果演进。 |
| 分析历史 | 已结束父运行的列表/详情视图，不是表。 | 由运行、结果和标注查询得到。 |

## 4. 目标数据模型

```text
analysis_tasks
  1 ── * analysis_runs (parent_run_id IS NULL)
               1 ── * analysis_stage_attempts
               1 ── 0..1 analysis_annotations
               1 ── * analysis_runs (parent_run_id IS NOT NULL; review only)
```

### 4.1 `analysis_tasks`：任务定义

保留：`id`、`user_id`、`kind`、`title`、`description`、`config_json`、`version`、`created_at`、`updated_at`、`archived_at`。

调整：

- 删除任务级 `status`；任务不是一次执行，其当前执行状态从最新父运行推导。
- `latest_run_id` 可作为查询优化缓存，但不参与业务事实判断。
- 普通分析任务继续用规范化的 `analysis_symbol` + `analysis_period` 实现“每用户每品种周期仅一个未归档任务”的约束。

### 4.2 `analysis_runs`：执行生命周期和可筛选摘要

这是最小、稳定的运行表。使用 UUID 主键 `id`（API 公开为 `run_id`），全系统只保留这一套运行标识。

| 字段组 | 建议字段 |
| --- | --- |
| 归属与层级 | `id`、`task_id`、`user_id`、`parent_run_id`、`work_key`、`sequence` |
| 生命周期 | `status`、`current_stage`、`failure_stage`、`failure_code`、`failure_message`、`terminal_reason`、`started_at`、`completed_at`、`heartbeat_at` |
| 冻结输入 | `query_json`、`resolved_symbol`、`bars_json`、`bars_hash`、`prompt_versions_json`、`model_config_json` |
| 业务结果 | `result_json`、`schema_version` |
| 查询摘要 | `mode`、`symbol`、`period`、`direction`、`terminal_outcome`、`duration_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens` |
| 时间 | `created_at`、`updated_at` |

约束：

- 父运行：`parent_run_id`、`work_key` 为 NULL，且 `(task_id, sequence)` 唯一。
- 子运行：两字段均非空，且 `(parent_run_id, work_key)` 唯一。
- 同一任务只允许一个活跃父运行（`queued`、`running`、`cancel_requested`）。
- 终态为 `completed`、`completed_with_warnings`、`degraded`、`failed`、`cancelled`、`timed_out`。

冻结输入与运行严格一对一，故本次不建立 `analysis_run_inputs`。市场 K 线暂以冻结 JSON 保存在运行行内，以换取复现性；后续仅在输入需要独立权限、独立留存期或迁移至对象存储时，再拆分输入表或快照存储。

`result_json` 保存完整、经过结构化校验的 `DemoAnalysisResponse` 或复盘汇总结果，`schema_version` 标注其结构版本。结果与运行严格一对一，故本次不建立 `analysis_results`；运行表同时保存列表筛选需要的方向、结论和状态摘要。

### 4.3 `analysis_stage_attempts`：阶段审计

主键 `id`，唯一键 `(run_id, stage, attempt)`；保存 `status`、provider/model、request ID、用量、耗时、原始内容、推理内容、标准化输出、校验错误、provider 错误、提示词元数据、开始/更新时间。

这替代 `analysis_runs.stage_runs_json`。原始响应/推理内容的权限与留存期需按现有 LLM 数据治理规则控制；前端不展示模型私有思维过程。

### 4.4 `analysis_annotations`：用户编辑数据

以 `(run_id, user_id)` 为主键或唯一键，保存：`favorite`、`notes`、`tags`、`created_at`、`updated_at`。

这替代写入 `result_json` 的 `favorite`、`notes`、`tags`，保证模型结论不可被用户操作修改。

## 5. 查询与 API 设计

### 历史

“历史”查询父 `analysis_runs`，左连接当前用户的 `analysis_annotations`：

- 列表只读取运行摘要和标注，不加载 `result_json` 或 K 线。
- 详情按 `run_id` 读取运行行中的输入和结果、阶段审计及标注。
- 追问以 `run_id` 关联对应结果。

### 任务与运行

- `POST /analysis-tasks/{task_id}/runs` 创建父运行和输入快照，返回 `run_id`。
- 复盘任务创建父运行后，建立子运行；父运行只保存聚合状态和汇总结果。
- 任务列表以最新父运行汇总显示运行状态；归档是任务自身状态。

### 契约迁移

所有后端路由、前端类型与调用方在同一变更中切换到 `run_id`；不保留旧标识字段或别名路由。

## 6. 数据迁移与删除策略

### 必须先完成的迁移

1. 创建新表、索引与约束。
2. 从现有 `analysis_runs` 拆分阶段审计和用户标注；冻结输入与结果继续留在运行表，校验每个父/子运行的行数、哈希和状态一致。
3. 在同一版本内同步切换后端路由、前端类型和调用方到 `run_id`，移除旧别名路由。
4. 直接删除 `analysis_history`，不导入、不备份该表中的旧数据。

### 删除门禁

- 在数据库结构迁移中执行 `DROP TABLE IF EXISTS analysis_history`；不执行数据回填或兼容读取。
- 删除前只需确认该表是目标表；删除后该表及其中数据不可恢复。

## 7. BDD 验收场景

1. **普通任务重复运行**：给定一个未归档普通任务，当其完成两次运行时，那么任务下有两条父运行，历史列表按时间返回两条，且任务配置不被结果覆盖。
2. **复盘父子运行**：给定包含两笔交易和两个周期的复盘任务，当运行完成时，那么有一条父运行和四条唯一 `work_key` 的子运行，父运行结果仅汇总子运行结果。
3. **历史详情可复现**：给定一条完成运行，当读取详情时，那么能取得运行行内的不可变输入、结果和阶段审计；结果没有用户笔记或标签字段。
4. **标注独立**：给定完成运行，当用户收藏并添加笔记/标签时，那么只更新 `analysis_annotations`，运行的 `result_json` 保持不变。
5. **旧表删除**：给定数据库存在 `analysis_history`，当结构迁移执行时，那么该表及其数据被删除，运行历史只来自新模型。
6. **无兼容契约**：给定客户端请求旧资源路径或旧标识字段，当新版本发布后，那么请求不再受支持；所有项目内调用方使用 `run_id` 和新路径。

## 8. 风险与待确认决策

| 决策 | 建议 | 需要确认 |
| --- | --- | --- |
| 旧表数据 | 直接删除，不迁移、不备份。 | 已确认。 |
| 主标识 | `run_id` 为 UUID，不保留旧字段或路由兼容。 | 已确认。 |
| 阶段原文留存 | 独立表、按现有权限控制。 | 原始响应/推理内容保留多久、谁可读取？ |
| 结果存储 | 结果表保存 JSON，运行表保存摘要。 | 是否有结果全文检索或数据仓库需求？ |
| 任务状态 | 任务只保留归档，运行状态从最新运行推导。 | UI 是否需要显式“暂停任务”状态？ |

## 9. 实施边界

本 Spec 仅定义目标架构。获得批准后，再创建 `plan.md`，按“schema → 数据拆分 → API → 前端 → 删除旧表与旧契约”的顺序实施，并以结构迁移、后端测试、前端测试和构建结果作为验收证据。
