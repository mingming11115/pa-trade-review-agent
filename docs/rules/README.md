# 工程规则索引

本目录保存 Claude Code 与 Codex 共享的项目工程规则。AI 修改代码前，应根据任务范围按需读取，不要一次加载全部文件。

- 修改 Python 代码：读取 `python.md`。
- 修改模块结构、API 或数据访问：读取 `architecture.md`。
- 新增、删除或升级依赖：读取 `dependencies.md`。
- 设计或修改 API、异步任务、外部调用、日志和异常处理：读取 `observability.md`。
- 编写代码、测试或进行验收：读取 `quality.md`。

规则描述必须简短、可执行，并与项目当前工具链保持一致。需要引入新工具或提高质量门槛时，应先形成 Issue 并获得批准。
