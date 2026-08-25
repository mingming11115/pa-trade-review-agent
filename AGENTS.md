# PA 项目 AI 协作说明

## 项目概况

PA 是一个用于交易复盘、行情分析和任务编排的全栈应用。

- 后端：FastAPI、SQLAlchemy Async、LangGraph、pytest
- 前端：React、TypeScript、Vite、Vitest
- 后端入口：`backend/app/main.py`
- 前端入口：`frontend/src/App.tsx`

项目中的分析结果仅用于研究和复盘，不构成投资建议。

## 开始任务

1. 阅读 `docs/ai-coding-workflow.md`。
2. 阅读 `README.md`。
3. 执行 `git status --short`，保留工作区已有修改。
4. 从用户请求中识别 Issue；若已有对应目录，读取 `docs/issues/<issue>/`。
5. 按任务需要读取 `docs/wiki/`、相关代码和测试，不要加载无关文档。

## 工程规则路由

- 修改 Python 代码时，读取 `docs/rules/python.md`。
- 修改模块结构、API、数据库访问或分析工作流时，读取 `docs/rules/architecture.md`。
- 新增、删除或升级依赖时，读取 `docs/rules/dependencies.md`。
- 设计或修改 API、异步任务、外部调用、日志和异常处理时，读取 `docs/rules/observability.md`。
- 编写代码、测试、Review 或验收时，读取 `docs/rules/quality.md`。

规则索引见 `docs/rules/README.md`。只加载当前任务相关规则。

## 默认工作流

- 小型、低风险且验收明确的改动可以走快速路径：读取相关规则和代码，在对话中说明简短方案，修改后运行局部测试并检查差异；不强制创建 Issue 文档。
- 快速路径必须同时满足：修改范围小、不改变公共 API 或数据库、不新增依赖、不涉及安全、并发、资金或核心状态机，并且能够独立验证。
- 任何条件不满足，或实施中发现隐藏复杂度时，立即升级为标准流程。
- 新功能、行为变更和复杂重构：使用 `superpowers:brainstorming`。
- 设计获批后的任务拆解：使用 `superpowers:writing-plans`。
- 功能和缺陷实现：使用 `superpowers:test-driven-development`。
- 测试失败或异常行为：使用 `superpowers:systematic-debugging`。
- 遇到日志报错、构建失败、测试失败或异常堆栈：使用项目 Skill `diagnosing-pa-errors`；默认只诊断，用户明确要求修复后才能改代码。
- 声称完成之前：使用 `superpowers:verification-before-completion`。
- 重要变更完成后：使用 `superpowers:requesting-code-review`。

未经用户批准，不得从设计阶段进入实现阶段。

Bug 修复即使使用快速路径，也必须先添加能够复现问题的回归测试。

## Issue 文档

每个 Issue 的文档集中放在：

`docs/issues/<issue-id>-<slug>/`

- `spec.md`：需求、BDD 场景和技术方案。
- `plan.md`：原子任务、TDD 步骤和进度。
- `verification.md`：测试、构建和评审证据；仅在验收阶段创建。

需求变化时先更新 `spec.md` 并重新获得批准，再更新 `plan.md` 和代码。

## 工程约束

- 修改前阅读相关实现、调用方和测试。
- 不修改当前任务范围外的文件，不进行无关重构。
- API 变化必须同步后端契约、前端类型、调用代码和测试。
- LLM 输出必须经过结构化校验，不暴露模型私有思维过程。
- 分析任务的成功、失败、取消和超时必须留下持久化终态。
- 未经明确批准，不执行破坏性数据库操作。
- 不提交 `.env`、密钥、令牌或其他敏感信息。

## Git 安全

- 工作区现有修改属于用户，必须保留。
- 不得擅自执行 `git reset --hard`、`git checkout --`、`git clean` 或强制推送。
- 除非用户明确要求，不提交、不推送、不创建 PR。

## 验证命令

- 后端：`make backend-test`
- 前端：`make frontend-test`
- 构建：`make build`
- 全量测试：`make test`

先运行相关局部测试，再运行全量验证。没有最新命令输出，不得声称测试通过或工作完成。

## 最终报告

说明完成内容、修改文件、验证命令与结果、未执行的验证、剩余风险，以及是否执行了数据库、提交或外部操作。
