# 分析领域模型重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将分析领域改为一次性 Task 与按周期平铺 Run，只保留行情表数据，删除并重建其他业务表，并完成用户隔离、纯 `run_id` 契约和前端异步终态等待。

**Architecture:** `analysis_tasks` 与 `analysis_runs` 是一对多平铺关系，普通 Task 一条 Run，复盘 Task 每周期一条 Run；不存在父子 Run。启动时以旧分析字段为一次性守卫，保留 `market_bars`、`market_collection_states`，Drop 其他业务表后用目标 ORM 重建。后端按用户限定所有 Run 访问，前端轮询全部 Run 至终态。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy Async、Pydantic 2、pytest/AnyIO、React 19、TypeScript、Vite、Vitest。

**Spec:** `docs/issues/analysis-domain-model-refactor/spec.md`

## Global Constraints

- 不引入 Alembic或其他新依赖，不记录迁移版本。
- 只保留 `market_bars`、`market_collection_states` 及其数据；其他业务表直接 Drop 后重建。
- 不迁移、不备份旧业务数据。
- 不存在父子 Run；删除 `parent_run_id`、`work_key`、`sequence`、`latest_run_id`。
- Task 只能执行一次，不提供再次运行、Run 重跑或失败重试。
- 生产 API、日志和前端只使用 `run_id`。
- 所有历史、详情、标注和追问访问必须按当前用户隔离。
- 初始阶段不连接真实数据库执行删除；后续以 Task 7 记录的用户追加授权为准。
- 不提交、不推送、不创建 PR，除非用户另行明确要求。

---

## 文件职责映射

- `backend/app/core/database.py`：旧结构检测、保留行情的业务表 Drop、目标结构创建。
- `backend/app/analysis/tasks/models.py`：平铺 Run ORM、外键、唯一约束和公开模型。
- `backend/app/analysis/tasks/repository.py`：一次性 Task 执行、批量创建周期 Run、状态聚合。
- `backend/app/analysis/execution/manager.py`：单条周期 Run 执行和完成后的 Task 聚合。
- `backend/app/analysis/routes.py`：一次启动返回 Run 列表、查询和取消接口。
- `backend/app/analysis/history/service.py`：用户隔离的历史、详情和标注。
- `backend/app/followup/service.py`、`backend/app/main.py`：用户隔离的追问和纯 `run_id` API。
- `backend/app/core/models.py`、`backend/app/analysis/workflow/graph.py`：结果模型与工作流标识统一。
- `frontend/src/types.ts`、`frontend/src/api.ts`：纯 `run_id` 契约与轮询 API。
- `frontend/src/App.tsx`、`frontend/src/analysisTasks.ts`、`frontend/src/SidebarNav.tsx`：一次性 Task UI、平铺周期 Run 和终态展示。
- `docs/wiki/analysis-task-run-history-data.md`、`verification.md`：最终事实和验收证据。

---

### Task 1: 原子切换目标平铺 Schema 并保留行情

**Files:**
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/analysis/tasks/models.py`
- Test: `backend/tests/test_analysis_schema_migration.py`
- Test: `backend/tests/test_analysis_history_backfill.py`
- Test: `backend/tests/test_analysis_task_repository.py`

**Interfaces:**
- Produces: `async def _needs_business_schema_reset(connection) -> bool`
- Produces: `async def _drop_business_tables(connection) -> None`
- Produces: `BUSINESS_TABLES_TO_REBUILD: tuple[str, ...]`
- Preserves: `market_bars` and `market_collection_states`
- Produces: flat `AnalysisTask`, `AnalysisRun`, `AnalysisStageAttempt`, `AnalysisAnnotation` ORM schema

- [x] **Step 1: 写目标 ORM 与 SQLite 重建失败测试**

先断言目标 ORM 不含 `parent_run_id`、`work_key`、`sequence`、`latest_run_id`，包含 `(task_id, period)` 唯一索引及外键。再在临时 SQLite 创建两张行情表、全部旧业务表并插入哨兵数据，断言 `ensure_schema()` 后行情哨兵仍存在，业务表已按新 ORM 重建且为空：

```python
def test_flat_run_schema_has_no_parent_or_latest_fields():
    assert {"parent_run_id", "work_key", "sequence"}.isdisjoint(AnalysisRun.__table__.c.keys())
    assert "latest_run_id" not in AnalysisTask.__table__.c
    assert any(index.name == "uq_analysis_run_task_period" for index in AnalysisRun.__table__.indexes)

@pytest.mark.anyio
async def test_schema_reset_preserves_only_market_data(legacy_engine, monkeypatch):
    monkeypatch.setattr("app.core.database.engine", legacy_engine)
    await ensure_schema()

    async with legacy_engine.connect() as connection:
        bars = (await connection.execute(text("SELECT COUNT(*) FROM market_bars"))).scalar_one()
        states = (await connection.execute(text("SELECT COUNT(*) FROM market_collection_states"))).scalar_one()
        users = (await connection.execute(text("SELECT COUNT(*) FROM users"))).scalar_one()
        runs = (await connection.execute(text("SELECT COUNT(*) FROM analysis_runs"))).scalar_one()

    assert (bars, states) == (1, 1)
    assert (users, runs) == (0, 0)
```

- [x] **Step 2: 运行 RED**

Run: `cd backend && ../.venv/bin/pytest tests/test_analysis_schema_migration.py::test_schema_reset_preserves_only_market_data tests/test_analysis_task_repository.py::test_flat_run_schema_has_no_parent_or_latest_fields -v`

Expected: FAIL，因为当前逻辑迁移旧 Run，且 ORM 仍包含父子与最近运行字段。

- [x] **Step 3: 写幂等与事务顺序测试**

覆盖：目标结构第二次启动不清空新写入数据；PostgreSQL fake connection 中 `DROP` 发生在 `Base.metadata.create_all` 之前；清单不包含两张行情表；失败时不执行事务外提交。

```python
assert "market_bars" not in BUSINESS_TABLES_TO_REBUILD
assert "market_collection_states" not in BUSINESS_TABLES_TO_REBUILD
assert statements.index("DROP TABLE IF EXISTS analysis_runs") < events.index("create_all")
```

- [x] **Step 4: 同时实现目标 ORM 与精确 Drop 清单**

实现固定清单，不接受动态表名或通配符：

```python
BUSINESS_TABLES_TO_REBUILD = (
    "analysis_stage_attempts", "analysis_annotations", "followup_messages",
    "analysis_runs", "analysis_tasks", "trade_import_batches", "trades",
    "alert_records", "alert_rules", "scheduled_job_runs", "prompt_versions",
    "audit_events", "user_sessions", "users", "analysis_history",
)
```

`ensure_schema()` 顺序：打开一个事务 → 检测旧字段/旧表 → 必要时按清单 Drop → `Base.metadata.create_all` → 结束事务。已是目标结构时只执行幂等 `create_all`。

同一步删除旧 ORM 字段与约束，并增加：

```python
task_id: Mapped[uuid.UUID] = mapped_column(
    ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False, index=True
)
Index("uq_analysis_run_task_period", AnalysisRun.task_id, AnalysisRun.period, unique=True)
```

阶段尝试和标注的 `run_id` 外键使用 `ON DELETE CASCADE`。不得先合入 Drop 守卫再保留旧 ORM，否则第二次启动会再次触发清空。

- [x] **Step 5: 验证 Task 1**

Run:

```bash
cd backend
../.venv/bin/pytest tests/test_analysis_schema_migration.py tests/test_analysis_history_backfill.py tests/test_analysis_task_repository.py::test_flat_run_schema_has_no_parent_or_latest_fields -v
```

Expected: PASS；行情哨兵数据保留，其他业务表为空，第二次启动不清空目标结构数据。

---

### Task 2: 实现一次性 Task 与平铺周期 Run 持久化

**Files:**
- Modify: `backend/app/analysis/tasks/repository.py`
- Modify: `backend/app/analysis/tasks/lifecycle.py`
- Test: `backend/tests/test_analysis_task_repository.py`
- Test: `backend/tests/test_analysis_task_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 的平铺 ORM 与 `(task_id, period)` 唯一约束
- Produces: `RunCreateSpec(period: str, query_json: dict[str, Any], resolved_symbol: str, bars_json: list[dict[str, Any]] | None, bars_hash: str | None, mode: str, symbol: str)`
- Produces: `async def create_runs_for_task(owner_id: uuid.UUID | None, task_id: uuid.UUID, specs: list[RunCreateSpec]) -> list[AnalysisRun]`
- Produces: `async def aggregate_task_status(task_id: uuid.UUID) -> TaskStatus`

- [x] **Step 1: 写一次性执行和平铺创建 RED 测试**

普通 Task 断言返回一条 Run；复盘 Task 配置 `periods=["5m", "1h"]` 断言返回两条 Run，且每条 `query_json` 都包含全部所选交易。第二次调用断言 `analysis_task_already_executed` 且 Run 数量不变。

- [x] **Step 2: 实现 `create_runs_for_task`**

在同一数据库事务内锁定 Task，要求 `status == pending` 且不存在 Run；验证周期非空且唯一；批量插入 Run；将 Task 状态切到 running。遇到重复周期或并发冲突统一转换为明确 `AppError`。

- [x] **Step 3: 写并实现 Task 状态聚合**

用参数化测试覆盖：

```python
@pytest.mark.parametrize(("statuses", "expected"), [
    (["completed", "completed"], "completed"),
    (["completed", "failed"], "completed_with_warnings"),
    (["failed", "failed"], "failed"),
    (["cancelled", "cancelled"], "cancelled"),
    (["timed_out", "timed_out"], "timed_out"),
])
```

- [x] **Step 4: 验证 Task 2**

Run: `cd backend && ../.venv/bin/pytest tests/test_analysis_task_repository.py tests/test_analysis_task_lifecycle.py -v`

Expected: PASS；没有父子 Run 字段，Task 只能创建一组周期 Run。

---

### Task 3: 重构执行管理器和启动 API

**Files:**
- Modify: `backend/app/analysis/execution/manager.py`
- Modify: `backend/app/analysis/routes.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_analysis_review_execution.py`
- Test: `backend/tests/test_analysis_task_api.py`
- Test: `backend/tests/test_analysis_trace.py`

**Interfaces:**
- Consumes: `create_runs_for_task(owner_id, task_id, specs) -> list[AnalysisRun]`
- Produces: `AnalysisRunStartItem(run_id: UUID, period: str, status: RunStatus)`
- Produces: `POST /analysis-tasks/{task_id}/runs -> list[AnalysisRunStartItem]` with HTTP 202

- [x] **Step 1: 写普通与复盘启动 RED API 测试**

```python
response = client.post(f"/api/v1/analysis-tasks/{review_task_id}/runs")
assert response.status_code == 202
assert {(item["period"], item["status"]) for item in response.json()} == {
    ("5m", "queued"), ("1h", "queued")
}
assert all(set(item) == {"run_id", "period", "status"} for item in response.json())
```

第二次 POST 断言 409 和 `analysis_task_already_executed`。

- [x] **Step 2: 运行 API RED**

Run: `cd backend && ../.venv/bin/pytest tests/test_analysis_task_api.py -v`

Expected: FAIL，当前接口只创建/返回一条父 Run。

- [x] **Step 3: 删除父子执行路径并启动每个周期 Run**

移除 `_run_review`、`create_review_children`、`successful_review_results`、`review_retry_work_keys` 等父子与重试路径。路由根据 Task 配置构造周期 specs，一次创建后逐条 `manager.start(run.id, trace_id)`。

- [x] **Step 4: 完成 Run 后聚合 Task 状态**

执行管理器在 Run 写入终态后调用 `aggregate_task_status(run.task_id)`；若同 Task 仍有非终态 Run，Task 保持 running。异常、取消和超时都必须先写 Run 终态，再聚合 Task。

- [x] **Step 5: 更新 Trace 测试**

断言每条周期 Run 使用同一请求 `trace_id`，业务字段只有 `task_id`、`run_id`；日志不得出现 `analysis_id` 或 `execution_id`。

- [x] **Step 6: 验证 Task 3**

Run:

```bash
cd backend
../.venv/bin/pytest tests/test_analysis_review_execution.py tests/test_analysis_task_api.py tests/test_analysis_trace.py -v
```

Expected: PASS；复盘按周期平铺，第二次执行被拒绝，所有终态持久化。

---

### Task 4: 强制用户隔离并统一纯 `run_id` 契约

**Files:**
- Modify: `backend/app/analysis/history/service.py`
- Modify: `backend/app/followup/service.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/core/models.py`
- Modify: `backend/app/analysis/workflow/graph.py`
- Modify: `backend/app/analysis/execution/runs.py`
- Modify: `backend/app/core/logging_context.py`
- Test: `backend/tests/test_analysis_runs.py`
- Test: `backend/tests/test_analysis_task_api.py`
- Test: `backend/tests/test_followup.py`
- Test: `backend/tests/test_langfuse_trace.py`
- Test: `backend/tests/test_logging_context.py`

**Interfaces:**
- Produces: `async def get_analysis_history(run_id: uuid.UUID | str, *, user_id: uuid.UUID | None) -> dict[str, Any]`
- Produces: `async def list_analysis_history(*, limit: int = 100, symbol: str | None = None, period: str | None = None, mode: str | None = None, favorite: bool | None = None, task_id: str | None = None, user_id: uuid.UUID | None = None) -> list[AnalysisHistorySummary]`
- Produces: `DemoAnalysisResponse.run_id: str`

- [x] **Step 1: 写双用户权限 RED 测试**

创建用户 A/B 和各自 Run。A 的列表只能包含 A；A 使用 B 的 `run_id` 调用详情、标注、追问历史和追问流均返回 404 `analysis_not_found`。

```python
assert foreign_detail.status_code == 404
assert foreign_detail.json()["code"] == "analysis_not_found"
assert str(run_b.id) not in {item["run_id"] for item in list_a.json()}
```

- [x] **Step 2: 运行权限 RED**

Run: `cd backend && ../.venv/bin/pytest tests/test_analysis_runs.py tests/test_followup.py -v`

Expected: FAIL，当前详情和追问只按主键读取。

- [x] **Step 3: 在查询源头增加 owner 条件**

所有 Run 查询使用 `AnalysisRun.id == run_id` 与 owner 条件组合；无权和不存在走同一 `AppError`。路由把 `current_user.id` 传入 service，不在路由层先做可泄露存在性的查询。

- [x] **Step 4: 写纯契约 RED 测试**

递归扫描 API JSON，断言任何层级都不存在旧键：

```python
def assert_no_legacy_ids(value):
    if isinstance(value, dict):
        assert not ({"analysis_id", "execution_id", "result_id"} & value.keys())
        for child in value.values():
            assert_no_legacy_ids(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_legacy_ids(child)
```

- [x] **Step 5: 将工作流、结果、追问和日志改为 `run_id`**

`DemoAnalysisResponse` 删除 `analysis_id`，增加必需 `run_id`；工作流 state、LLM metadata 和事件统一重命名。存入 `result_json` 前执行结构化校验，确保不会序列化旧键。删除兼容别名和旧路由。

- [x] **Step 6: 验证 Task 4**

Run:

```bash
cd backend
../.venv/bin/pytest tests/test_analysis_runs.py tests/test_analysis_task_api.py tests/test_followup.py tests/test_langfuse_trace.py tests/test_logging_context.py -v
rg -n "analysis_id|execution_id|result_id|AnalysisExecution|/executions" app --glob '!core/database.py'
```

Expected: pytest PASS；`rg` 无生产运行时命中。数据库重建守卫若仍需识别旧字段，只允许集中在 `core/database.py`。

---

### Task 5: 前端平铺周期 Run 与异步终态轮询

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/analysisTasks.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/SidebarNav.tsx`
- Test: `frontend/src/api.test.ts`
- Test: `frontend/src/analysisTasks.test.ts`
- Test: `frontend/src/App.test.tsx`
- Test: `frontend/src/SidebarNav.test.tsx`

**Interfaces:**
- Consumes: start response `Array<{run_id: string; period: string; status: AnalysisRunStatus}>`
- Produces: `waitForRunsTerminal(runIds: string[], options: {intervalMs: number; timeoutMs: number; signal: AbortSignal}) -> Promise<AnalysisRunDetail[]>`

- [x] **Step 1: 写 queued → running → completed 轮询 RED 测试**

模拟启动返回两个周期 Run；详情依次返回 queued、running、completed。使用 fake timers 推进轮询，断言完成前不展示结果，全部终态后按周期显示结果。

```typescript
expect(await screen.findByText("5m · 分析中")).toBeInTheDocument();
await vi.advanceTimersByTimeAsync(1000);
expect(await screen.findByText("5m · 完成")).toBeInTheDocument();
```

另测 failed、cancelled、timed_out 和客户端超时。

- [x] **Step 2: 运行前端 RED**

Run: `cd frontend && npx vitest run src/App.test.tsx src/api.test.ts`

Expected: FAIL，当前启动后只查询一次详情。

- [x] **Step 3: 实现可取消轮询函数**

`waitForRunsTerminal` 每 500ms 查询非终态 Run，最长 60s；组件卸载或用户取消时通过 `AbortSignal` 停止。仅 completed 类终态读取结果，其他终态保留错误信息。

- [x] **Step 4: 删除前端兼容字段和再次执行入口**

类型与组件只读取 `run_id/runId`。删除 `as any` 的 `analysis_id`、`analysisId`、`latestExecutionId` 回退、`getAnalysisHistoryCompat`、旧 execution 路径测试数据，以及已执行 Task 的“再次运行/重试”按钮。

- [x] **Step 5: 平铺显示周期 Run**

侧栏在 Task 下直接列出周期 Run，例如 `5m · 完成`、`1h · 失败`；不显示父子层级、sequence 或 work key。Task 已开始后禁用编辑和执行操作。

- [x] **Step 6: 验证 Task 5**

Run:

```bash
cd frontend
npx tsc -b
npx vitest run src/api.test.ts src/analysisTasks.test.ts src/App.test.tsx src/SidebarNav.test.tsx
rg -n "analysis_id|execution_id|result_id|analysisId|executionId|resultId|latestExecutionId|/executions" src
```

Expected: 编译和测试 PASS；`rg` 无命中。

---

### Task 6: 文档、全量验证与 Code Review

**Files:**
- Modify: `docs/wiki/analysis-task-run-history-data.md`
- Modify: `docs/issues/analysis-domain-model-refactor/plan.md`
- Modify: `docs/issues/analysis-domain-model-refactor/verification.md`

**Interfaces:**
- Consumes: Tasks 1–5 的最终代码和测试输出。
- Produces: 可复核的验收证据和 Review 结论。

- [x] **Step 1: 更新 Wiki**

只描述一次性 Task、平铺周期 Run、阶段尝试、标注、用户隔离和数据库直接重建；不得出现父子 Run、latest Run、再次运行或兼容契约。

- [x] **Step 2: 执行旧概念与差异扫描**

Run:

```bash
rg -n "parent_run_id|work_key|latest_run_id|analysis_id|execution_id|result_id|AnalysisExecution|/executions" backend/app frontend/src docs/wiki
git diff --check
```

Expected: 除 `backend/app/core/database.py` 精确旧结构守卫外无生产命中；`git diff --check` 退出码 0。

- [x] **Step 3: 执行局部与全量门禁**

Run:

```bash
make backend-test
make frontend-test
make test
make build
```

Expected: 全部退出码 0；记录测试文件数、测试数、警告和构建产物摘要。

- [x] **Step 4: 更新 verification.md**

记录：保留/删除表清单、SQLite 行情哨兵证据、PostgreSQL SQL 顺序测试、一次性 Task、平铺周期 Run、双用户隔离、纯 `run_id`、前端终态轮询，以及当时尚未执行真实数据库删除的风险；最终真实库证据由 Task 7–9 追加。

- [x] **Step 5: 发起独立 Code Review**

Reviewer 必须读取 Spec、Plan、完整 diff 和最新门禁输出，重点检查：删除守卫是否可能重复触发、行情数据是否绝对不受影响、是否存在跨用户读取、是否仍有父子 Run/旧标识、异步终态是否完整、外键删除策略是否安全。

- [x] **Step 6: 处理 Review 并重新验证**

Blocker/Major 逐项使用 `superpowers:receiving-code-review` 验证并修复；修复后重跑相关局部测试以及 `make test && make build`。只有 Review 无 Blocker/Major 且全量门禁最新通过，才将 Task 6 标记完成。

---

## 执行顺序与停止条件

```text
Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6
```

- Task 1 未证明行情数据保持不变前，不得继续 ORM 重建。
- Task 2 未删除父子字段并证明一次性约束前，不得切换 API。
- Task 4 的用户隔离未通过前，不得进入前端验收。
- 任一数据库删除测试触及非临时数据库，立即停止。
- Task 1–6 阶段未经新授权不连接真实数据库；用户随后已对 `tradeagent` 明确授权，执行证据见 Task 7。
- 没有最新全量验证和独立 Review 结论，不得声称完成、提交或发布。

---

### Task 7: 直接切换 `tradeagent` 并验证数据边界

**Files:**
- Modify: `backend/app/core/database.py`
- Modify: `backend/tests/test_analysis_schema_migration.py`
- Modify: `docs/issues/analysis-domain-model-refactor/verification.md`

- [x] **Step 1: 为真实旧表清单补充 RED 测试**

断言固定删除清单覆盖 `tradeagent` 中发现的全部旧分析表，且永远不包含 `market_bars`、`market_collection_states`。

- [x] **Step 2: 运行 RED 并补齐固定清单**

Run: `cd backend && ../.venv/bin/pytest tests/test_analysis_schema_migration.py -v`

- [x] **Step 3: 记录执行前证据**

只连接 `localhost:5432/tradeagent`，记录目标库名、public 表、两张行情表行数与确定性摘要。

- [x] **Step 4: 执行一次性真实重建**

使用 `DATABASE_URL=postgresql+asyncpg://sun@localhost:5432/tradeagent` 导入完整 ORM 后调用 `ensure_schema()`；任何目标检查不匹配立即停止。

- [x] **Step 5: 记录执行后证据**

验证行情摘要不变、旧表全部消失、目标业务表为空、`analysis_runs(task_id, period)` 唯一约束及 Run 外键级联存在。

### Task 8: 删除运行时旧 Schema 兼容并处置警告

**Files:**
- Modify: `backend/app/core/database.py`
- Modify: `backend/tests/test_analysis_schema_migration.py`
- Modify as required: `backend/pytest.ini`、依赖清单或对应初始化代码

- [x] **Step 1: 写无自动 Drop 的 RED 测试**

断言 `ensure_schema()` 不再检测旧字段或执行 Drop，只幂等调用目标 ORM `create_all`。

- [x] **Step 2: 删除兼容代码并运行局部测试**

删除旧字段守卫、固定 Drop 清单和启动时自动重建路径；保留标准事务内 `create_all`。

- [x] **Step 3: 定位并修复第三方警告**

分别确认 FastAPI TestClient 与 LangGraph 警告的触发源；仅采用不隐藏真实项目警告的兼容修复。

### Task 9: 最终门禁与验收记录

**Files:**
- Modify: `docs/issues/analysis-domain-model-refactor/plan.md`
- Modify: `docs/issues/analysis-domain-model-refactor/verification.md`

- [x] **Step 1: 运行全量门禁**

Run: `make backend-test`、`make frontend-test`、`make test`、`make build`、`git diff --check`。

- [x] **Step 2: 更新验收证据并 Review**

记录真实 PostgreSQL 前后摘要、警告结果、未执行的外部操作，并确认无未关闭 Blocker/Major。
