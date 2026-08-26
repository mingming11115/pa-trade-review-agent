# PA 项目知识库

本目录保存跨 Issue、长期有效的项目知识，供开发者、Claude Code 和 Codex 按需读取。

## 收录范围

- 业务术语和领域模型。
- 模块职责、依赖关系和数据流。
- 状态机、关键不变量及安全边界。
- 前后端接口映射和外部依赖。
- 可复用的故障诊断经验。

## 使用规则

- 不保存单个需求的计划和进度；这些内容放在 `docs/issues/`。
- 不复制已有权威文档；使用链接指向 `README.md`、`PA-database-schema.md` 等来源。
- AI 只读取当前任务相关条目，不要一次加载全部知识库。
- 代码与 Wiki 不一致时，必须指出差异并确认权威来源。
- 只有被实际变更验证过的稳定事实才能沉淀到 Wiki。

## 现有权威资料

- 项目介绍与运行方式：`../../README.md`
- 数据库设计：`../../PA-database-schema.md`
- 前端 API 与时序审计：`../../PA-frontend-api-sequence-audit.md`
- LLM 流式输出说明：`../../backend/app/llm/流式输出梳理.md`

## Wiki 条目

- [任务、分析运行与分析历史的数据结构](analysis-task-run-history-data.md)
