# 统一行情存储与定时更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 历史和新增分钟 K 线统一保存到 `pa.market_archive_2023.bars`，迁移并移除 `public.market_bars`，运行一套可自动补数的本机后台采集服务。

**Architecture:** 扩展现有 HTTP provider 保存真实合约、整数价格及有效覆盖边界。归档 repository 成为所有行情读写入口，原 service 保留聚合、图表和告警接口；MinuteCollector 提供内嵌和独立 worker 共用的调度。旧表经过备份、迁移核对和读写切换后移除。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy Async、asyncpg、httpx、PostgreSQL、pytest、React、Vitest、macOS launchd；不新增依赖。

**Spec:** [spec.md](/Users/l/PycharmProjects/pa-trade-review-agent/docs/issues/unified-market-collection/spec.md)

## Global Constraints

- 统一表为 `market_archive_2023.bars`；保留已有 3,297,965 条归档、查询视图及只读权限，不压缩。
- 品种为 ES、NQ、MES、MNQ、MGC；MES/MNQ 使用真实微型合约，不映射父合约。
- 轮询默认 60 秒，lookback 30 分钟，最大单批 360 分钟，UTC 时间窗口。
- 数据与 covered_until 同事务；合法空窗可推进，失败、未发布、取消不可跳过。
- 真实 instrument_id 不可编造；旧数据保留来源，冲突时真实上游记录优先。
- 旧表删除前必须备份、核对并验证所有读写；失败保留旧表。
- 现有其他未提交修改不得覆盖；机器密钥、密码和绝对安装路径不得提交。
- 每轮 trace_id、每品种 run_id；运行成功、失败、超时、取消和崩溃恢复有持久化终态。

## Review Focus

- 换月同分钟有多个 instrument_id：查询按连续映射选择一条，不把多个合约成交量相加。
- 旧表仅有小时数据：保留兼容记录，不能因分钟不齐而丢失历史查询结果。
- WebSocket 重放或乱序：不叠加重复成交、不覆盖已确认历史收盘线，未知合约显式标记。
- 上游截断到非整分钟：只提交已确认收盘的覆盖区间，不误推进请求结束时间。
- 迁移或 worker 崩溃：数据库锁释放后可恢复，旧数据、已提交行情和未覆盖窗口不丢失。

## Task 1：统一表结构、迁移与备份核对

**Files:** Create `backend/app/market/archive.py`, `backend/app/market/migrate_archive.py`, `backend/tests/test_market_archive.py`, `backend/tests/test_market_archive_migration.py`; Modify `backend/app/core/database.py`, `backend/app/market/service.py`。

**Interfaces:**
- `ArchiveRecord`：root、continuous_symbol、instrument_id、ts_event_ns、四个 raw 价格、volume、publisher_id、timestamp_utc、source、confirmed。
- `ensure_archive_schema() -> None`：幂等创建 schema/表及显式追加字段，保留原主键和索引。
- `write_archive_records(session: AsyncSession, records: Sequence[ArchiveRecord]) -> int`：调用者控制事务。
- `read_archive_bars(symbol: str, period: str, start: datetime, end: datetime) -> list[Bar]`：确认上游记录优先，未知合约记录仅作回退。
- `migrate_legacy_bars(*, finalize: bool = False) -> dict[str, Any]`：保存数据库内完整旧表备份和核对结果；只有 finalize 且核对通过才移除旧表。

- [x] 添加失败测试：原归档重复写入不增行、整数价格精度不变、真实记录优先；旧表迁移保留来源、NULL volume、小时孤立记录；第二次迁移幂等；核对失败不删除旧表。
- [x] 运行 `cd backend && ../.venv/bin/pytest tests/test_market_archive.py tests/test_market_archive_migration.py -q`，确认新增行为未实现而失败。
- [x] 实现结构与迁移：先读取本机真实 DDL，兼容现有 bigint 与视图；旧未知合约使用显式来源和保留标识，不能冒充真实 instrument_id；小时遗留记录存放同 schema 的兼容表，分钟行情只有统一表这一来源。
- [x] 同一命令确认通过；记录迁移备份与核对策略，不执行本机旧表删除。

## Task 2：连续合约 provider 与有效覆盖边界

**Files:** Modify `backend/app/market/provider.py`; Create `backend/tests/test_archive_provider.py`。

**Interfaces:**
- `ArchiveBatch(records: list[ArchiveRecord], covered_until: datetime | None)`。
- `MassiveHistoricalProvider.get_archive_range(symbol: str, start: datetime, end: datetime) -> ArchiveBatch`，与现有 `get_range` 共用请求、重试和鉴权，不复制 HTTP 客户端逻辑。

- [x] 添加失败测试：请求 `GLBX.MDP3`/`ohlcv-1m`/`stype_in=continuous`/五品种 `.c.0`；解析 header 合约 ID 与纳秒时间；精确整数价格；合法空响应推进；422 尚未发布不推进；clamp 只推进实际已发布整分钟；鉴权和超时保留错误码；换月双合约与无效 OHLC 不静默混合。
- [x] 运行 `cd backend && ../.venv/bin/pytest tests/test_archive_provider.py tests/test_provider.py -q`，确认新测试失败。
- [x] 实现 ArchiveBatch 与原始字段校验，保留已存在 provider 方法兼容，敏感请求信息不记录。
- [x] 重跑上述测试确认通过。

## Task 3：项目查询、回填与实时写入切换

**Files:** Modify `backend/app/market/service.py`, `backend/app/market/live.py`, `backend/app/core/config.py`, `backend/app/main.py`, relevant frontend symbol validation/types/callers found with `rg`; Modify `backend/tests/test_market_data.py`, `backend/tests/test_live_market.py`（采用仓库实际测试文件名）以及相关 API/前端测试。

**Interfaces:** 保留 `read_stored_bars`、`query_market_range`、`LocalFirstMarketProvider.get_range` 的外部签名和现有响应字段；内部分钟来源统一为 archive；小时从分钟聚合，仅必要时回退已迁移小时记录。

- [x] 搜索所有 MarketBar、upsert_bars、符号别名与 ES/NQ 白名单调用方，列出精确修改清单。
- [x] 添加失败测试：MES/MNQ 不再返回父合约；MGC 可查；归档本地命中无需联网；回填原始数据写统一表；小时聚合和旧小时回退；图表与分析 API 结构保持兼容；告警读取统一行情。
- [x] 为 WebSocket 添加 instrument_id/raw 字段保留、重复与乱序、确认分钟不被覆盖测试；没有可靠成交去重标识时不累计已确认分钟，历史轮询负责最终校正。
- [x] 运行相关 pytest/Vitest，确认新行为失败。
- [x] 移除 MarketBar ORM 注册及全部旧表读写；查询和回填调用 archive repository，扩展五品种配置与前端约束；小时不再持久化重复副本。
- [x] 重跑相关测试确认通过；静态搜索确认没有运行路径依赖 `public.market_bars`，迁移模块和测试引用除外。

## Task 4：统一调度、游标与持久化终态

**Files:** Modify `backend/app/market/service.py`, `backend/app/core/config.py`, `backend/app/main.py`; Create `backend/tests/test_unified_market_collector.py`。

**Interfaces:**
- `CollectionState.covered_until: datetime | None`：显式可重复迁移新增字段。
- 保留 `MinuteCollector.collect_symbol(symbol, now) -> int`, `run_once(now) -> None`, `run_forever() -> None`, `stop() -> None`。
- 设置 `collector_mode`（embedded/external）、`collector_interval_seconds`（默认 60），默认五品种。

- [x] 添加失败测试：从归档 metadata 覆盖结束初始化三个品种；无元数据才使用存储边界；合法无成交游标推进；上游未发布和失败不推进；数据写入失败游标回滚；长停机每批最多 360 分钟；lookback 重读不重复。
- [x] 添加失败测试：数据库 advisory lock 防双 worker；持久化槽去重；单品种失败其他继续；告警每轮一次；超时/取消终态；遗留 running 恢复为 interrupted；trace_id/run_id 在上游和落库一致且无 key。
- [x] 运行 `cd backend && ../.venv/bin/pytest tests/test_unified_market_collector.py tests/test_market_data.py tests/test_config.py -q`，确认新增测试失败。
- [x] 实现锁、槽、终态与独立覆盖游标；原始记录、游标和成功终态同事务提交；失败使用独立事务记录，取消清理受保护且有限时。
- [x] 运行上述测试和 lifespan 相关测试，确认通过。

## Task 5：本机后台服务、真实补数与旧表移除

**Files:** Create `backend/app/market/worker.py`, `infra/market-collector/com.pa.market-collector.plist.template`; Modify `.env.example`, `Makefile`, `README.md`; Create `backend/tests/test_market_worker.py`。

**Interfaces:** `python -m app.market.worker [--once]`：复用 MinuteCollector，处理 SIGTERM/SIGINT、数据库不可用重试和 engine.dispose；Makefile 提供 worker 启动及显式迁移入口。

- [x] 添加失败测试：--once 不循环；停止信号可结束并记录取消；external 模式 FastAPI 不另启分钟循环；配置/启动错误有非零退出码。
- [x] 运行 worker 和 main 局部测试确认失败，实现 worker 与 launchd 模板，重跑确认通过。
- [x] 执行 `make backend-test`、`make frontend-test`、`make build`、`make test`；只修复本 Issue 引起的失败，既有无关失败报告证据。
- [x] 在本机暂停已有采集写入，备份旧表，执行不删除模式迁移；核对品种/周期计数、字段、边界样本及原 3,297,965 条归档保留。执行统一 worker --once 和图表/分析/告警读写检查。
- [x] 核对通过后 finalize 删除旧表，确认重启不重建；失败停止 finalize，保留旧表和备份。
- [x] 安装用户 launchd 服务，设置本机 external 模式，从现有 env 读取 key；执行启动、停止、重启验证，检查 PostgreSQL 新数据与五品种状态。保留回滚命令及旧表备份位置。
- [x] 自审数据一致性、权限、日志与全部调用方；创建 `verification.md`，引用最新测试与真实运行输出，并写明数据最新边界、迁移核对和服务状态。

## 执行方式

推荐在当前会话由主代理逐项执行，避免并行编辑已有大量未提交改动。执行前按 worktree 技能处理隔离，并保留当前工作区所需上下文；不自动提交用户其他改动。

实施计划已确认并执行；产品代码、数据库迁移、旧表移除及后台服务安装已验收，见 verification.md。
