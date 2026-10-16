# 存量提示词知识库第一版技术设计

日期：2026-09-30  
状态：第一里程碑已实施并验证（2026-10-01）；第二里程碑尚未实施。  
需求依据：[spec.md](spec.md)。后续扩展见根目录 [todo.md](../../../todo.md)。

## 1. 目标与交付顺序

先证明现有提示词可以从文件迁入数据库，并在实际两阶段分析中运行。保持文本内容、拼接顺序、策略路由和输出校验规则一致。随后提供每个用户独立编辑、查看版本和回滚的管理页面。

第一里程碑交付默认知识条目导入、第一版历史、运行快照、实际分析接入和验证证据。第二里程碑交付个人覆盖、管理 API 和页面。本文描述两者共用的数据与接口边界，第一里程碑不提前实现个人页面。

“知识库”在本版本中表示有版本的提示词条目库；每个条目对应一个现有工作流文件。新增条目和自定义加载条件列入 TODO。

## 2. 当前代码与接入位置

| 位置 | 当前职责 | 本次处理 |
| --- | --- | --- |
| `backend/app/llm/prompts.py` | 文件读取及较早的提示词拼接函数 | 保留兼容，不能作为唯一接入点 |
| `backend/app/analysis/workflow/prompt_engineering/*.txt` | 存量领域提示词 | 首次导入的数据来源，保留文件 |
| `backend/app/analysis/workflow/stage1/core/prompt_assembler.py` | 当前两阶段消息拼接、文件分组、进程缓存 | 接受显式知识内容快照 |
| `backend/app/analysis/workflow/stage1/adapter.py` | Stage1 消息构造 | 将快照传给拼接器 |
| `backend/app/analysis/workflow/stage2/adapter.py` | Stage2 消息构造和路由 | 将同一快照传给拼接器 |
| `backend/app/analysis/workflow/graph.py` | 实际分析图、流式与非流式入口、预览 | 在状态中传递快照 |
| `backend/app/analysis/execution/manager.py` | 执行持久化 Run，处理终态 | 解析 Run 所有者并加载运行快照 |
| `backend/app/admin/prompts.py` | 共享文件编辑、全局版本历史 | 保留兼容，不复用于个人文件写入 |
| `frontend/src/App.tsx` | 现有管理员编辑弹窗 | 第二里程碑接入独立个人入口 |

当前代码搜索未发现业务模块导入 `app.llm.prompts`；实际图调用 Stage1/Stage2 adapter。仅修改旧 prompts.py 不能证明主流程已使用知识库。

导入清单从 PromptAssembler 的公共、任务、基础策略及全量策略文件集合取并集、按首次出现顺序去重，不能导入目录下所有文件。`_reference` 是参考资料，不参与导入。阶段、角色与加载条件继续由代码定义，同一条目可能被多个阶段引用。

## 3. 分层设计

新增 `backend/app/knowledge/`：

- `models.py`：当前条目、版本及运行快照 ORM。
- `contracts.py`：条目文档、修订、快照及 API 输入输出类型。
- `repository.py`：按作用域查询、事务导入、版本追加与条件更新。
- `service.py`：导入清单校验、默认/个人解析、有效内容快照和业务错误。
- `routes.py`：第二里程碑的身份校验与请求响应适配。

数据库查询保持异步；PromptAssembler 保持同步纯拼接，不能在其 `_load()` 中访问数据库。内容在进入工作流前一次性解析完成。

## 4. 数据结构

以下为拟定新表，不修改或删除现有 `prompt_versions`。

### 4.1 `knowledge_prompt_entries`

| 字段 | 类型/约束 | 含义 |
| --- | --- | --- |
| `id` | UUID 主键 | 条目记录 |
| `scope_key` | 字符串，非空 | `system`、`local` 或 `user:<UUID>` |
| `owner_id` | 可空 UUID | 登录用户；system/local 为 null |
| `filename` | 字符串，非空 | 现有白名单文件名 |
| `content` | Text，非空 | 当前内容 |
| `revision` | 整数，初始 1 | 单调递增的修订号 |
| `content_hash` | SHA-256 完整摘要 | 内容一致性检查 |
| `created_at` / `updated_at` | UTC 时间 | 创建与更新时间 |

唯一约束为 `(scope_key, filename)`，避免 nullable owner_id 的唯一约束语义差异。scope_key 在服务器从可信身份生成，客户端不能指定。owner_id 与 scope_key 一致性由服务层校验。

### 4.2 `knowledge_prompt_versions`

字段：`id`、`entry_id`、`revision`、`content`、`content_hash`、`actor_id`、`action`、`source_version_id`、`created_at`。唯一约束 `(entry_id, revision)`。版本不可覆盖或删除。

action 为 `import`、`baseline`、`save` 或 `rollback`。默认导入创建 revision=1；个人首次编辑先保存当时默认内容为 baseline revision=1，再保存新内容为 revision=2。相同内容保存返回当前版本，避免无意义修订；回滚则创建新修订并记录目标版本。

### 4.3 `knowledge_prompt_run_snapshots`

字段：`run_id`（主键）、`scope_key`、`manifest_hash`、`entries_json`、`created_at`。每个条目记录文件名、有效内容、来源作用域、修订及摘要。完整内容只在受权限保护的持久化快照中保存，不输出到普通日志或对外事件。

一条 Run 只建立一个快照，复盘中的多个交易共用。持久化可保证系统模板以后更新时，仍能追溯当次实际输入；第一里程碑即保存快照，避免只有摘要却无法恢复内容。

## 5. 默认条目导入

提供显式命令：`python -m app.knowledge.import_prompts`（在 backend 目录执行）。命令默认只允许创建 system 条目，不提供覆盖参数。

1. 枚举白名单并读取全部源文件；读取使用 `asyncio.to_thread`，避免阻塞异步主链。
2. 沿用实际拼接器的 UTF-8 解码语义，不 strip、不改换行、不自动去 BOM。检测并报告空白文件、缺失文件及解码失败。
3. 在一个数据库事务中为缺失条目建立当前记录和第一版版本，已有条目跳过；事务任一失败整体回滚。
4. 唯一约束处理并发导入冲突：回滚并重新查询，不产生两份初始版本。
5. 输出新增、跳过、内容与源文件不同的文件名及数量，不输出内容。已存在条目与文件不同也不覆盖，并返回需人工检查的明确状态。

新表通过现有 `Base.metadata.create_all` 创建；必须确保新 ORM 已注册。`ensure_schema()` 只负责建表，导入不隐式挂在每次 API 请求中。导入成功后才启用数据库加载入口。

## 6. 有效内容与运行快照

建议服务接口：

```python
async def import_default_prompts(prompt_dir: Path) -> ImportResult: ...
async def resolve_prompt_snapshot(owner_id: UUID | None) -> PromptSnapshot: ...
async def get_or_create_run_prompt_snapshot(
    run_id: UUID, owner_id: UUID | None
) -> PromptSnapshot: ...
```

PromptSnapshot 是只读数据对象，包含 scope_key、manifest_hash 和按 filename 索引的条目。有效内容规则：本人覆盖存在则使用覆盖，否则使用数据库 system 条目。第一里程碑只存在 system 条目，但解析接口保留明确所有者参数。

一次解析通过单个数据库查询读取默认及本人条目，在内存中合并；不逐文件查询。合并后检查所有必要文件齐全，数据库路径绝不混用文件回退。manifest_hash 由固定排序后的文件名、来源、修订和内容摘要计算。

RunManager 在 mark_run_running 后、执行工作流前创建并保存快照。已有快照必须校验 scope_key 与 Run.user_id 对应作用域一致，随后复用。持久化快照失败必须终止运行，不得继续调用模型。

graph 的流式、非流式入口接收显式 prompt_snapshot 参数，DemoAnalysisState 保存该字段；Stage1/Stage2 adapter 接收并继续传递。非 Run 的调试预览从 current_user 解析快照，但不创建运行快照记录。测试与旧工具可继续显式使用文件拼接器，正式知识库运行必须传入快照。

流程：Run.user_id → 数据库有效条目 → 持久化 Run 快照 → graph state → adapter → PromptAssembler → LLM → 原有结构化校验。

## 7. 拼接与缓存

PromptAssembler 构造函数增加可选 `prompt_contents: Mapping[str, str]`。提供快照时 `_load()` 只读取映射，缺失条目抛出明确错误；未提供时保留原文件行为。

保留既有拼接顺序、分隔符、程序规则、动态 K 线输入及策略路由。知识库只替换文件内容来源，不迁移 K 线渲染函数或程序输出契约。

系统提示词缓存改为有界缓存（建议最多 64 项），以系统内容摘要及程序固定前缀版本作为键；不能再仅用目录路径。实例内容缓存仅属于单次快照。两个阶段使用同一个系统内容，保留原有前缀一致性。

## 8. 第二里程碑的个人管理契约

个人管理 API 前缀 `/api/v1/knowledge`，文件名通过 filename 查询参数传递：

| 方法与路径 | 行为 |
| --- | --- |
| GET `/orchestration` | 阶段和加载条件，普通用户可读 |
| GET `/prompts` | 本人有效条目摘要、来源、修订及阶段引用，不返回正文 |
| GET `/prompt?filename=...` | 读取本人有效内容和 opaque version token |
| PUT `/prompt?filename=...` | content + expected_version，保存本人覆盖 |
| GET `/versions?filename=...` | 本人版本历史；limit 1..50，before_revision 游标 |
| POST `/versions/{version_id}/rollback?filename=...` | expected_version，回滚本人历史并追加修订 |

返回 version 为 `v1:<entry UUID>:<revision>`；历史含 can_rollback，默认历史不可回滚，首次个人修改保存 baseline 和 save。身份切换清空编辑状态并使旧响应失效，防止旧账号私有内容在新账号页面展示。

version token 必须标识来源及修订，不能只用内容 hash；否则内容 A→B→A 时无法发现过期编辑。更新采用条件 UPDATE 或首次创建唯一约束保证多实例并发正确，不依赖进程 Lock。

未登录返回 401，非法文件及他人版本返回 404，冲突返回 409，空白内容返回 422。前端保留失败时的编辑草稿。历史列表需要分页，不能一次返回无限版本。

## 9. 失败、Trace 与兼容

知识库缺失文件使用 `knowledge_prompt_missing`，未导入使用 `knowledge_not_initialized`，数据库失败使用 `knowledge_load_failed`。RunManager 沿用已有异常处理保存 failed 终态；取消及超时沿用现有执行机制，本次不修改其状态语义。

导入命令生成一个 trace_id；API 复用请求 trace_id；运行快照解析继承 RunManager 的 trace_id、task_id、run_id。记录加载开始、完成数量/耗时/manifest_hash、失败异常，不记录完整内容或 Cookie。阶段 prompt_metadata 附带实际使用的文件名、来源、修订及摘要。

现有管理员共享文件编辑接口保留，但其保存不更新数据库默认条目。页面入口已切换为个人数据库管理，不再调用共享文件写接口。默认知识库在线修改不属于当前个人编辑范围。

回滚部署使用旧代码读取保留的 TXT 文件；个人数据与新表保留。该回滚会使个人修改暂不参与分析，应在部署记录中说明。不运行删除表或重置业务数据的命令。

## 10. 验证与验收证据

| 验证层 | 必须证明的行为 |
| --- | --- |
| 导入测试 | 全白名单原文和摘要一致、第一版存在、重复导入不覆盖、缺失源文件不部分导入 |
| 内容等价测试 | 文件来源与数据库快照构造的 Stage1/Stage2 消息逐字相等；覆盖多空、震荡及全量策略模式 |
| 工作流测试 | 真实 graph 使用数据库内容，受控 LLM 输出通过既有校验，Run 完成且保留版本元数据 |
| 快照测试 | 保存后当前 Run 内容不变，下一次 Run 使用新版；复盘多个交易使用同一快照 |
| 隔离测试 | 两个用户交错调用、缓存命中和版本查询均不串用 |
| 失败测试 | 默认缺失、数据库失败、快照写入失败均不调用 LLM且保存失败终态 |
| 第二里程碑 UI/API | 普通用户编辑、冲突草稿保留、本人历史回滚、他人版本拒绝 |

先运行相关 pytest；后端里程碑再运行 make backend-test。涉及页面后运行局部 Vitest、make test 和 make build。命令结果保存到验收阶段的 verification.md。

外部模型验证单独记录：有可用数据库、模型凭据和行情服务时，用一条实际 Run 确认数据库内容加载、两个阶段响应和持久化结果；否则明确只完成受控工作流验证，不能宣称外部端到端通过。

## 11. 实施前检查

- 审核本文的数据持久化、显式导入及分两里程碑交付方式。
- 批准后编写 plan.md，定义原子任务和先失败后通过的测试步骤。
- 不改动当前已存在的 backend/requirements.txt 和 infra/ 工作区变更。
- 不新增依赖，不自动执行真实数据库导入，不修改原提示词文本。


## 12. 第一里程碑实现说明

默认条目清单为 29 份。实际分析接入包含持久化任务、旧 demo 流式/非流式接口、调试预览及定时预警。当前预警规则没有 user_id，定时运行按 owner=None 的 local 作用域处理，未引入新的规则所有权模型。

数据库错误日志记录异常类型及栈帧位置，不格式化 SQLAlchemy 异常正文、SQL 参数或底层数据库 DETAIL，以避免完整提示词进入日志。运行加载也使用既有取消观察器；知识库 AppError 的安全错误码和消息会写入持久化失败终态。

验收结果见 [verification.md](verification.md)。本地 PostgreSQL 已导入 29 条默认知识及第一版历史；由于当前未配置模型和行情密钥，外部模型端到端验证尚未执行。
