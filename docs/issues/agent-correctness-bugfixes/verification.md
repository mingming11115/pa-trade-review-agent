# 验收证据（2026-09-30）

## 修复与回归

| 缺陷 | 红阶段证据 | 修复后验证 |
| --- | --- | --- |
| 已收盘行情截断 | 对齐时漏末棒；10:07 的 10:00 棒仅含 2 分钟成交量 | 10:05 / 10:07 均返回完整两棒 |
| 工具用户身份丢失 | 认证用户工具查询返回匿名范围 | 当前用户、并发、匿名、认证失败、子任务继承和请求清理 |
| 取消不能收敛 | 启动前、首次读取中及其他 Manager 取消 | 本地/跨 Manager 取消持久化；仅中断分析子任务，等待进行中的片段写入 |
| 非法状态覆盖 | 已完成 Run 可改 running；已完成结果可替换 | CAS claim、终态保护、取消与完成竞态、结果不可变 |
| 部分成功汇总错误 | warnings / degraded 汇总成 completed | Task 保留 completed_with_warnings |
| 工具预算假完成 | 连续工具调用的中间文字变为 done | 三轮工具后预留禁用工具的最终答复；超限、空回答、length、content_filter、EOF 都不持久化成功回答 |
| 前端终态错误 | failed/cancelled/timed_out 均访问不存在的 query.analysis_mode | 三类终态、部分成功、多周期中后续失败正常展示；详情错误字段契约同步 |

补充取消链路回归：直接取消流消费者、关闭生成器、取消监测查询失败、流式回调中取消。四项测试分别先失败后通过；生成器逐层显式关闭，保证取消返回前内层执行停止。监测异常中止执行并走失败处理，不静默失去取消能力。

## 命令结果

所有后端命令使用隔离环境：

```bash
DATABASE_URL=sqlite+aiosqlite:///:memory: \
LOG_FILE_PATH=/tmp/pa-bugfix-tests.log \
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= make test
make build
git diff --check
```

- `make test`：退出码 0，后端 **231 passed**，前端 **11 文件 / 68 passed**。
- `make build`：退出码 0，TypeScript 与 Vite 成功，47 个模块。
- `git diff --check`：退出码 0。
- 取消相关局部回归：35 passed。
- 工具预算与追问局部回归：26 passed。
- 日志：`/tmp/pa-bugfix-final-test.log`、`/tmp/pa-bugfix-build.log`。

后端有 54 条 `datetime.utcnow()` 弃用警告，均来自现有 followup 会话/时间代码；新测试增加触发次数。本次未修改这些范围外时间逻辑。原 API 错误追踪测试缺少行情 Provider 替身，隔离运行先得到 503，补齐测试替身后只验证目标 500 和 trace_id 行为。

## 评审与边界

已完成两轮独立评审，发现并修复最终回答完成标记、流式首字延迟及取消监测异常问题。最终复核未发现阻断问题，并独立运行取消与仓储测试：29 passed。

- 使用模拟模型、HTTP MockTransport 和隔离 SQLite；未验证真实模型服务或 PostgreSQL 多进程压力。
- 跨 Worker 取消使用数据库意图与 250ms 轮询；数据库不可用时不能保证持久化，进程崩溃后的遗留 Run 恢复不在本次范围。
- 本次行情修复对齐窗口，休市自动扩窗与缺失源行情补齐另行处理。
- 未新增依赖、执行数据库迁移、提交、推送或部署。
