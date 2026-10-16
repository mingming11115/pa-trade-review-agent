# 个人知识库管理第二里程碑实施计划

> 执行技能：superpowers:executing-plans。当前会话直接实施，先写失败测试再实现；结束时独立只读评审。状态：用户“完成”指令授权继续，已实施。

**目标：** 页面读取和保存数据库中的本人提示词，历史与回滚也使用同一数据库；本人下一次分析使用保存后的内容。

**架构：** 复用现有 knowledge 表、快照解析和阶段编辑界面。新增个人管理 API，替换旧界面的文件读写调用；保留后台共享文件 API 兼容性。所有者来自 current_user，客户端不指定所有者。

**技术栈：** FastAPI、SQLAlchemy Async、Pydantic、React、TypeScript、pytest、Vitest；不新增依赖。

**依据：** [spec.md](spec.md)、[technical-design.md](technical-design.md)。本次完成已批准设计中尚未实施的个人管理范围。

## 全局约束

- 当前数据库中 29 个 system 默认条目保留，不重新导入或覆盖。
- 所有登录用户都可编辑自己的知识，管理员也只修改本人；关闭认证时沿用 local。
- 只编辑现有白名单，不增加条目、删除、重命名或更改加载条件；后续功能仍留在 todo.md。
- 保存、历史追加、回滚使用一个数据库事务。版本不能仅用内容摘要，必须包含来源条目 ID 和修订号，避免 A→B→A 导致过期保存漏检。
- 同一 Run 的原快照不受保存影响，下一 Run 加载新内容；其他用户和 TXT 不变。
- 工作区已经包含行情采集、前端改版等改动，保留现状并采用最小增量；不提交或发布其他改动。
- 日志不输出内容、SQL 参数和私有正文，继承已有 Trace。

## 重点审查

1. 两个页面首次同时保存不能产生重复个人条目或丢失更新，任务 1 覆盖。
2. 内容 A→B→A 后旧版本仍然冲突，任务 1 覆盖。
3. 他人历史 ID、其他文件版本 ID 不可回滚，任务 1/2 覆盖。
4. 保存失败、冲突、切换文件和关闭页面不能静默丢失草稿，任务 3 覆盖。
5. 保存后的新分析、原 Run 及其他用户必须分别使用正确内容，任务 4 覆盖。

## 任务 1：个人内容事务与历史

**文件：** 扩展 backend/app/knowledge/contracts.py、repository.py、service.py；新增 backend/tests/test_knowledge_management.py。

**接口：** 在 contracts.py 定义 KnowledgeDocument（filename、content、version、size、source、revision）、KnowledgeVersion（id、revision、actor、action、created_at、can_rollback）、KnowledgeVersionPage（items、next_before_revision）、KnowledgeUpdate（content、expected_version）、KnowledgeRollback（expected_version）。source 为 default 或 personal；version 为 `v1:<entry UUID>:<revision>`。

Service 提供异步 `get_document(owner_id, filename)`、`save_document(owner_id, filename, update)`、`list_versions(owner_id, filename, limit=20, before_revision=None)`、`rollback_version(owner_id, filename, version_id, expected_version)`。对应具体输入输出类型使用上述契约；owner_id 为 UUID|None，version_id 为 UUID。

- [x] 写测试：默认读取不建立个人记录；首次修改建立 baseline=1 和 save=2；同内容保存不追加修订；个人修改不影响系统模板、其他人及 TXT。
- [x] 写测试：过期版本、首次并发创建、后续并发保存均返回409；A→B→A 后最初 token 仍过期。
- [x] 写测试：历史按 revision 降序分页，limit 范围1..50；无个人修改时可见默认第一版，但 can_rollback=false；他人/其他文件版本返回404；回滚追加新修订且保留旧版本。
- [x] 写测试：空白、超出1,000,000字符、非法文件名不能改变当前内容及历史；数据库失败不能部分保存或泄漏内容到日志。
- [x] 在 backend 执行 `../.venv/bin/python -m pytest tests/test_knowledge_management.py -q`，确认缺失功能导致失败。
- [x] 实现事务：服务验证白名单与输入，Repository 按本人作用域查询当前/默认条目；已有条目采用带 revision 条件的 UPDATE。首次创建由唯一约束保护；IntegrityError 转为409。历史与当前更新同事务，任一步失败整体回滚。回滚始终产生新 revision，source_version_id 指向本人目标版本。
- [x] 重跑上述测试及既有 knowledge 测试，确认通过。

## 任务 2：普通用户可访问的知识库 API

**文件：** 新建 backend/app/knowledge/routes.py，修改 backend/app/main.py；新增 backend/tests/test_knowledge_management_api.py。

**接口：** `/api/v1/knowledge/orchestration` 返回现有 OrchestrationView（复用原阶段引用来源）；`/api/v1/knowledge/prompts` 返回本人可见条目摘要；条目内容 GET/PUT、历史 GET、回滚 POST 路径沿用技术设计。回滚请求必须携带当前 expected_version。

- [x] 先写 API 测试并运行，验证普通用户读取/保存成功、本人身份取自认证依赖、跨用户访问被拒绝、未登录401、非法条目404、冲突409、非法输入422；历史分页契约完整。
- [x] 路由仅做权限及响应适配，使用 current_user 而非 require_admin；注入 KnowledgeService 便于隔离数据库测试，不在路由内写 SQL。
- [x] 日志记录文件名、操作、修订、结果和耗时；使用已有请求 Trace，不打印内容。
- [x] 运行新增 API 测试及原知识库/管理员 API 测试，确认通过。原共享文件 API 保留，个人 UI 不再调用它们。

## 任务 3：页面编辑入口切换到个人数据库内容

**文件：** 修改 frontend/src/types.ts、api.ts、App.tsx、App.test.tsx；只在现有布局需要时调整 styles.css。

**接口：** 新增 getKnowledgeOrchestration、getKnowledgePrompt、saveKnowledgePrompt、getKnowledgeVersions、rollbackKnowledgeVersion，并映射后端契约。旧文件 API 函数可保留供兼容调用，但编辑界面全部使用新函数。

- [x] 先更新/新增 Vitest 场景：普通用户能打开“个人知识库”；读取显示默认/个人来源和版本；编辑保存调用个人 API 且携带原 token；历史加载、分页与回滚携带当前 token。
- [x] 测试409或网络失败保留 textarea 草稿；界面显示冲突提示，不用旧响应覆盖新选择；存在未保存修改时切换文件/关闭要明确确认。
- [x] 在 frontend 执行 `npm test -- --run src/App.test.tsx`，确认新增场景失败。
- [x] 复用当前阶段列表和编辑器，将原“管理后台”入口改为“个人知识库”，移除管理员隐藏条件。标题和来源说明明确本人独立；保存成功显示“已保存，仅影响自己的后续分析”。
- [x] 保存/回滚/加载版本具备 loading、防重复提交和错误捕获；保存成功刷新当前内容与历史。默认初始历史的回滚按钮禁用。
- [x] 运行对应 Vitest，确认正常、冲突与失败行为通过，不重新设计其他工作台布局。

## 任务 4：保存到分析生效的完整验证

**文件：** 扩展 backend/tests/test_knowledge_workflow.py、test_knowledge_snapshots.py；更新 README.md、spec.md、technical-design.md、verification.md。

- [x] 先写测试，通过正式 save_document 修改内容，创建新 Run 快照并执行真实图（受控 LLM）；断言模型输入包含新版、原 Run 保持旧版、其他用户仍读默认、两阶段系统缓存正确。
- [x] 测试回滚后下一次分析使用恢复内容，原修订历史不变。该测试验证实际服务与分析输入，不通过直接 SQL 更新绕过个人保存逻辑。
- [x] 按失败证据补齐实现或接入缺口，运行全部知识库局部测试。
- [x] 运行 make test 和 make build，处理本次回归；记录既有问题，不覆盖其他任务工作区代码。
- [x] 独立只读评审用户隔离、事务、并发 token、前端草稿、分页、Trace 和模型输入；必要修复先加失败测试再改实现。
- [x] 更新验收文档，明确页面访问路径、保存效果和真实外部模型验证是否执行。复用隔离数据测试，不在实际数据库修改用户原有提示词来验收。

## 交付状态

验收成功后，用户可通过“更多 → 个人知识库”查看和编辑已入库内容。后台保存与分析使用同一个个人数据库来源，版本历史和回滚按本人隔离；旧 TXT 只保留导入来源及兼容资料。


## 实施裁决与评审

- 用户要求直接完成既有范围，因此在当前会话执行全部任务，不重复停下来询问。工作区已有授权保留，未提交、推送或合并。
- 技术设计中原先拟定的文件名路径改为 filename 查询参数，与现有编辑接口风格一致；前后端与文档同步，没有更改权限或业务范围。
- 操作期间禁用编辑、切换和关闭，并同步 ref 防止重复请求；账号变化时递增身份 generation、清空状态，所有迟到响应都不再应用。
- 独立评审发现身份切换的旧响应泄露风险，失败回归测试复现后修复；补充网络失败、草稿切换、历史分页、事务历史插入失败与 PostgreSQL 并发测试。

## 阶段说明布局调整（2026-10-01）

- [x] 按用户要求，将说明移至顶部独立入口，完整区域展示阶段选择、文件用途和加载条件。
- [x] 视图切换保留编辑草稿，切换阶段恢复阅读顶部，窄屏支持横向阶段选择和正文滚动。
- [x] 局部测试、全量前端测试、构建及桌面/窄屏浏览器验收通过，证据记录于 verification.md。
