# 存量提示词知识库第一里程碑实施计划

> 执行技能：superpowers:executing-plans。由当前会话直接实施，逐任务先写失败测试，再实现和验证。状态：第一里程碑已实施并验证（2026-10-01）。

**目标：** 将工作流引用的存量 TXT 原样入库，建立第一版历史和持久化运行快照，验证实际分析从知识库加载。

**架构：** knowledge 模块提供异步导入和快照解析；graph 显式传递只读快照；PromptAssembler 保持同步拼接。第一里程碑不实现个人页面或编辑 API。

**技术栈：** FastAPI、SQLAlchemy Async、Pydantic、LangGraph、pytest；不新增依赖。

**依据：** [spec.md](spec.md)、[technical-design.md](technical-design.md)。

## 全局约束

- 默认内容来自现有工作流文件白名单，不导入 _reference，不修改 TXT。
- 新增表与接口，不删除现有业务数据和版本记录。
- 数据库缺失或失败不能静默回退文件；分析必须保存失败终态。
- 同一 Run 的两阶段、重试和复盘处理共用一个快照。
- 第一里程碑只导入 system 第一版；个人编辑、历史回滚和页面留给第二里程碑。
- 保留全部已有工作区修改；修改重叠文件前查看现有 diff，采用最小增量，不提交其他任务代码。
- 导入为显式操作，不自动修改实际数据库；测试使用隔离数据库。
- 局部测试命令在 backend 目录使用项目虚拟环境执行：下文 `python -m pytest` 实际替换为 `../.venv/bin/python -m pytest`，与 Makefile 的运行环境保持一致。

## 重点审查

1. 并发导入必须只产生一份条目和初始版本，测试放任务 1。
2. UTF-8 BOM、换行和空白不能被无意改写，等价测试放任务 1/2。
3. 传入空内容映射不能被当成未启用知识库，失败测试放任务 2。
4. 已有 Run 快照所有者不一致必须拒绝，测试放任务 3。
5. 取消、数据库故障及流式异常不能留下非终态，测试放任务 4。

## 任务 1：知识条目、第一版历史与显式导入

**文件：** 新建 backend/app/knowledge/{__init__,models,contracts,repository,service,import_prompts}.py；新建 backend/tests/test_knowledge_import.py。

**接口：** `import_default_prompts(prompt_dir: Path) -> ImportResult`（async），返回新增、跳过及不一致文件列表；模型沿用技术文档表名与唯一约束。repository 使用可注入 AsyncSession 工厂，测试不访问真实数据库。

- [x] 写 test_import_preserves_content_and_first_revision：导入白名单，断言每条原文、完整 SHA-256、revision=1、action=import；参考文件不在结果中。
- [x] 写 test_repeat_import_does_not_overwrite、test_missing_source_rolls_back、test_concurrent_import_is_idempotent，分别验证重复导入不覆盖、源缺失零写入和并发唯一性；含 BOM/换行样本。
- [x] 在 backend 执行 `python -m pytest tests/test_knowledge_import.py -q`，确认因缺失实现失败。
- [x] 实现 ORM、事务导入及 CLI；读取文件用 asyncio.to_thread。先读完并校验，再提交；唯一约束冲突回滚后有限重试。CLI 产生 trace_id，仅输出摘要，不打印内容。
- [x] 重跑上述测试，确认全部通过；检查 CLI 帮助，不执行真实导入。

## 任务 2：注入内容与有界系统缓存

**文件：** 修改 backend/app/analysis/workflow/stage1/core/prompt_assembler.py；新建 backend/tests/test_knowledge_prompt_assembler.py。

**接口：** PromptAssembler 增加 keyword-only `prompt_contents: Mapping[str, str] | None = None`。None 保留文件路径；非 None 必须完全从映射读取。

- [x] 写 test_snapshot_messages_match_files：对实际默认文件构造 Stage1 和多空/震荡/全量 Stage2，断言消息列表逐字相等。
- [x] 写 test_missing_snapshot_entry_fails_without_file_fallback、test_cache_isolates_content、test_cache_bound，断言空映射失败、交错内容不串用及缓存最多 64 项。
- [x] 执行 `python -m pytest tests/test_knowledge_prompt_assembler.py -q`，确认失败。
- [x] 实现显式内容加载；有界缓存按系统内容与程序前缀摘要索引，维持两阶段前缀相同。保留现有清缓存调用兼容性。
- [x] 重跑新增测试与现有提示词相关测试，确认通过。

## 任务 3：只读快照解析与持久化

**文件：** 扩展 knowledge/contracts.py、repository.py、service.py、models.py；新建 backend/tests/test_knowledge_snapshots.py。

**接口：** `resolve_prompt_snapshot(owner_id: UUID | None) -> PromptSnapshot`、`get_or_create_run_prompt_snapshot(run_id: UUID, owner_id: UUID | None) -> PromptSnapshot`（async）。PromptSnapshot 提供只读 contents 和每文件 metadata，scope_key 由服务生成。

- [x] 写 test_snapshot_resolves_default_and_owner_override、test_run_snapshot_is_frozen、test_snapshot_owner_mismatch_rejected、test_missing_default_fails，验证隔离、冻结、所有者校验及明确失败。
- [x] 执行 `python -m pytest tests/test_knowledge_snapshots.py -q`，确认失败。
- [x] 单次查询 system/本人条目，合并并校验白名单，生成 manifest_hash；保存 Run 快照完整内容及元数据。捕获可处理数据库错误并转为 AppError，保留异常链；记录继承的 Trace。
- [x] 重跑导入与快照测试，确认通过。

## 任务 4：实际图、执行器及预览接入

**文件：** 修改 backend/app/analysis/execution/manager.py、workflow/graph.py、workflow/stage1/adapter.py、workflow/stage2/adapter.py 及调试预览调用方；扩展 backend/tests/test_llm_workflow.py；新建 backend/tests/test_knowledge_workflow.py。

**接口：** graph 流式/非流式入口、adapter 增加显式 prompt_snapshot；DemoAnalysisState 持有快照。RunManager 根据 Run.user_id 解析快照，禁止使用后台请求身份猜测所有者。预览从已认证 current_user 解析。

- [x] 先检查这些文件当前 diff 和全部调用方，保持另一批用户上下文、取消和超时修复。
- [x] 写受控 LLM 的真实 graph 测试，断言 Stage1/Stage2 输入来自快照且经过原有校验；测试复盘共用快照、重试保持内容及 metadata 含来源/修订/摘要。
- [x] 写执行器快照失败/数据库失败/取消测试，断言不调用模型、保存正确终态；覆盖流式和非流式路径。
- [x] 执行 `python -m pytest tests/test_knowledge_workflow.py -q`，确认失败。
- [x] 实现显式传递与阶段元数据。不得在同步拼接器访问数据库。所有正式知识库分析入口在未初始化时返回明确错误。
- [x] 重跑新增工作流测试及 test_llm_workflow.py、test_workflow_cancellation.py、test_analysis_trace.py，确认通过。

## 任务 5：启用说明、旧编辑入口提示与验收

**文件：** 修改 README.md、frontend/src/App.tsx 中旧共享文件编辑提示；必要时调整 frontend/src/App.test.tsx；创建本 Issue verification.md。

- [x] 文档说明显式导入命令、知识库未初始化的行为、版本记录和回退旧代码的影响。旧管理员保存提示改为“文件模板已保存；已导入的知识库不会同步更新”，避免错误生效承诺。
- [x] 如提示有现有测试断言，先调整对应行为测试并确认旧实现失败，再更新文案；不改个人页面或新增依赖。
- [x] 执行新增知识库局部测试，然后 make backend-test；因涉及旧前端文案，执行相关 Vitest、make test 和 make build。失败时按项目诊断技能定位，区分既有问题和本次回归。
- [x] 自审数据隔离、事务边界、缓存、Trace、终态与实际调用覆盖，修复本次问题后仅重跑必要验证。
- [x] 在 verification.md 记录最新实际命令结果、内容等价证据、外部模型验证是否执行及限制。无真实模型运行结果不得声称外部端到端已通过。

## 实际数据库导入与第二里程碑

代码验收后再明确目标数据库并执行可重复、不覆盖已有内容的导入；当前计划不会在验证阶段自动向项目数据库写入。

第二里程碑另写个人管理实施任务：个人覆盖事务、并发 version token、历史分页、回滚、API 权限、前端个人入口和端到端隔离。新增条目和加载条件配置继续留在 todo.md。


## 实施记录与调整

- 当前目录实施得到用户继续指令授权；所有原有工作区改动保留，未创建分支、提交或修改实际业务数据库。
- 工作流实际引用的默认清单共 29 份 TXT，测试验证了数据库独有标记会进入模型输入，避免同文比较掩盖文件回退。
- 补齐定时预警入口：现有规则没有用户所有者，按 owner=None 的 local 作用域建立快照，沿用原全局规则语义。未来按用户管理预警属于独立需求。
- 运行快照加载也纳入现有取消观察机制，阻塞加载收到取消后可落 cancelled 终态。
- 数据库异常日志只记录异常类型与全部栈帧位置，不输出 SQL 参数、数据库 DETAIL 或代码行文本；AppError 格式化时抑制底层异常链，异常上下文仍可供程序检查。
- 为旧 API/任务测试建立隔离 SQLite 知识库，不访问配置中的 PostgreSQL。
- 前端旧保存提示测试同步为“文件模板不会更新已导入知识库”；这是明确行为改变，不再承诺下一次分析生效。
- 第二里程碑个人管理功能未实现；本计划勾选仅表示第一里程碑步骤完成。
- 最新结果与评审记录见 [verification.md](verification.md)。
