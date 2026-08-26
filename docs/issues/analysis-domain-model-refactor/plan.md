# 分析领域模型重构实施计划

## 前提与边界

- 已确认：直接删除 `analysis_history` 及其数据，不迁移、不备份。
- 已确认：不保留旧 API、旧字段、旧路由或 `execution` 概念的兼容层。
- 已确认：冻结输入和最终结果继续保存在 `analysis_runs`，不创建 `analysis_run_inputs` 或 `analysis_results`。
- 仍须保留现有 `analysis_runs` 的数据；任何主键/外键迁移必须先盘点所有引用并完成数据迁移。
- 本计划不引入无关功能、数据库重置或依赖升级。若现有 schema 初始化机制无法安全承载重命名/回填，则先单独设计版本化迁移方案并暂停后续任务。

## 任务 1：建立运行资源基线与迁移映射

**修改范围**：`backend/app/analysis/`、`backend/app/core/` 的模型/引用清单；相关测试。

1. 搜索 `analysis_id`、`execution_id`、`result_id`、`AnalysisExecution`、`/executions`、`/analysis-executions` 和 `analysis_history` 的全部引用。
2. 列出每个持久化表、API、前端类型、追问记录、Token 记录、日志上下文的旧标识来源与目标 `run_id` 映射。
3. 定义 `analysis_runs` 的数据迁移规则：主键 `id/run_id`、`parent_run_id`、子运行 `work_key`、以及外部引用更新顺序。
4. 写迁移前置校验测试：存在父子运行和外部引用时，映射后引用仍指向同一运行；旧 `analysis_history` 不参与迁移。

**完成条件**：形成可执行的字段映射，并证明不会遗漏现有 `analysis_runs` 的引用。

## 任务 2：迁移数据库结构到目标模型

**修改范围**：数据库迁移/初始化机制、ORM 模型、结构测试。

1. 将 `analysis_runs` 收敛为唯一运行资源：`run_id` 为唯一公开标识，父子字段统一为 `parent_run_id`。
2. 在运行表保留 `query_json`、`resolved_symbol`、`bars_json`、`bars_hash`、`prompt_versions_json`、`model_config_json`、`result_json`、`schema_version` 及运行摘要字段。
3. 新建 `analysis_stage_attempts`，唯一约束 `(run_id, stage, attempt)`；新建 `analysis_annotations`，唯一约束 `(run_id, user_id)`。
4. 删除 `analysis_history`；不注册该 ORM 模型，不创建该表，也不回填其数据。
5. 迁移保留的既有运行数据，验证父子关系、结果 JSON、输入哈希、状态、时间和外部引用。

**RED**：新增结构与迁移测试，分别覆盖普通运行、复盘父子运行、阶段重试、用户标注及删旧表。

**GREEN**：实现可重复执行的迁移；迁移完成后不再存在旧表或旧列。

**完成条件**：在 PostgreSQL 与 SQLite 测试路径中，schema 与目标模型一致且既有 `analysis_runs` 数据可读取。

## 任务 3：重构后端领域模型与持久化接口

**修改范围**：`tasks/models.py`、Repository、运行管理器、历史服务、日志上下文及后端测试。

1. 将 Python 类型、Repository 方法和内部变量统一为 `AnalysisRun` / `run_id`；删除所有 execution/result 别名方法和类型。
2. 任务只保存定义与归档状态；运行状态从最新父运行推导，`latest_run_id` 仅作查询缓存。
3. 将阶段尝试从 `stage_runs_json` 迁移为 `analysis_stage_attempts` 的 Repository 操作。
4. 将收藏、笔记、标签从 `result_json` 迁移到 `analysis_annotations`；结果 JSON 在写入后保持不可变。
5. 历史查询以父运行加当前用户标注构成，不再存在独立“历史表”模型。

**RED**：针对运行状态机、父子运行唯一性、阶段尝试幂等性、标注不修改 `result_json`、历史查询隔离用户标注编写测试。

**GREEN**：以最小实现通过测试，并移除旧实现路径。

**完成条件**：所有分析成功、失败、取消和超时仍持久化明确终态；日志上下文只关联 `task_id` 与 `run_id`。

## 任务 4：切换后端 API 与异步运行入口

**修改范围**：分析路由、主应用历史/追问路由、运行事件流、Pydantic 响应模型、API 测试。

1. 路由、请求和响应统一使用 `run_id`；删除旧标识字段。
2. 删除所有 execution 术语、路由和别名；运行启动、查询、取消和事件流只保留 run 路径。
3. 历史列表与详情仅返回运行、结果和标注所需字段；追问按 `run_id` 关联结果。
4. 更新权限、Trace、取消与失败响应测试，确保业务标识连续且不暴露模型私有推理。

**完成条件**：项目内不存在旧运行术语的 API 契约，且所有路由测试使用新资源路径。

## 任务 5：同步前端类型、调用和界面状态

**修改范围**：`frontend/src/types.ts`、`api.ts`、`App.tsx`、任务侧栏及 Vitest。

1. 将 TypeScript 类型和状态字段统一为 `AnalysisRun` / `run_id`。
2. 删除 execution 相关 API 调用、路由拼接和 UI 状态；更新历史选择、取消、详情和追问调用。
3. 用新任务 + 多次运行 + 复盘子运行覆盖侧栏、历史和详情界面。

**完成条件**：TypeScript 类型检查、现有交互测试和生产构建均不再引用旧概念。

## 任务 6：删除遗留代码、更新文档并完成验收

**修改范围**：遗留模块/别名、Wiki、Spec、验证记录及全量测试。

1. 删除所有已经无调用方的旧模型、路由、Repository 别名和文档表述。
2. 更新 Wiki，使数据结构仅描述 `analysis_tasks`、`analysis_runs`、`analysis_stage_attempts`、`analysis_annotations` 与历史视图。
3. 运行局部后端测试、`make backend-test`、`make frontend-test`、`make test`、`make build`。
4. 在 `verification.md` 记录迁移证据、测试输出、未执行项、数据删除事实及风险。
5. 发起代码评审，重点检查数据迁移、父子运行关系、状态终态、API 全量切换和无旧概念残留。

**完成条件**：所有验证通过，且检索不到旧表、旧字段、旧路由和 execution 概念的生产代码引用。

## 执行顺序与门禁

```text
任务 1 → 任务 2 → 任务 3 → 任务 4 → 任务 5 → 任务 6
```

- 每个任务完成后先运行局部测试和差异检查，再进入下一任务。
- 任务 2 的迁移测试和数据保留策略未验证前，不启动任务 3。
- 任务 4 与任务 5 必须在同一变更集完成，避免后端和前端契约不一致。
- 没有完整验证记录和 Review 结论，不提交、不发布。
