# 统一行情存储与后台采集验收

验收时间：2026-10-01T12:33:18.937356+00:00（UTC）。已实施到本机原项目，未执行 Git push 或 PR。

## 实际结果

- 原 2023 年以来 MES/MNQ/MGC 历史分钟线 **3,297,965 条**仍在 `pa.market_archive_2023.bars`，截止原归档边界的行数准确一致，30 条首尾样本的合约 ID、纳秒时间、整数 OHLC、成交量及 publisher_id 全部一致。
- ES、NQ、MES、MNQ、MGC 的增量写入同一表；核验时总计 **3,300,459 条**，后台继续更新，因此此计数是验收快照。
- 本机旧 `public.market_bars` 原为 0 条，迁移保留 `market_archive_2023.legacy_bars_backup` 并核对后显式 finalize 删除旧表。重启及全量测试后 `to_regclass('public.market_bars')` 仍为 NULL。
- 项目的 `query_market_range` 五品种查询均返回本地记录；MES/MNQ 不再映射父合约。小时及其他周期从分钟线聚合，迁移的历史小时线在无法完整重建时保留兼容查询。
- 现有只读 PostgreSQL 连接仍能查询 `market_archive_2023.minute_bars` 的五品种。

## 定时服务与配置

已安装 `/Users/l/Library/LaunchAgents/com.pa.market-collector.plist`，服务标识为 `gui/501/com.pa.market-collector`。`launchctl print` 实际确认 `state = running`、PID 36601。

启动命令为项目虚拟环境 `python -m app.market.worker`，工作目录为本机项目 `backend`。源代码入口 `backend/app/market/worker.py`，可移植模板 `infra/market-collector/com.pa.market-collector.plist.template`。项目 `.env` 配置 external、五品种和默认 60 秒轮询，mode 0600 且 Git 忽略；行情密钥仅写本机配置，不写入本记录或提交。前端/FastAPI 不运行时服务仍可采集，FastAPI 使用 external 模式不再另启行情循环。

服务 bootout 停止后确认注册消失，再 bootstrap 重启，运行 PID 从 35968 变为 36601。中断中的 MGC 任务实际记录 cancelled，重启后成功补数并恢复 ok，游标没有跳过待补区间。launchd 注册移除有异步延迟，最初立即断言失败，随后读取实际状态确认已停止；这是验收脚本时序问题，重启与持久化取消均已验证。

日志位于 `/Users/l/MarketData/pa-2023-mes-mnq-mgc/collector/collector.stderr.log`。每轮 trace_id 与每品种 run_id 出现在 HTTP、解析、写入与结束日志中。运行记录保存于 `public.scheduled_job_runs`，状态和覆盖游标保存于 `public.market_collection_states`。

| 品种 | 已成功覆盖至（UTC，不含） | 核验瞬时状态 |
| --- | --- | --- |
| ES | 2026-10-01 12:20:00+00:00 | running |
| MES | 2026-10-01 12:20:00+00:00 | ok |
| MGC | 2026-10-01 12:20:00+00:00 | ok |
| MNQ | 2026-10-01 12:20:00+00:00 | ok |
| NQ | 2026-10-01 12:20:00+00:00 | ok |

核验时五品种均已覆盖到 2026-10-01 12:20 UTC，这是上游当时可发布边界；其中一个任务为 running 表示下一轮正在执行。上游发布存在延迟，后台不会伪造未发布分钟。MGC 的最后有成交分钟可早于覆盖游标，合法无成交窗口不会阻塞补数。

## 最新验证

- 隔离工作区基线：`make backend-test` → 266 passed，58 条既有 warnings。
- 新增归档/provider RED → GREEN：28 passed，验证原始精度、Basic auth、连续合约、发布截断及失败。
- 迁移、采集和 WebSocket 回归在独立 PostgreSQL schema 执行；旧小时查询、旧游标停机缺口、跨日重放测试先复现失败再修复。
- 最终原项目 `make test` → **后端 290 passed、前端 70 passed**，58 条既有 warnings；`make build` → 成功。
- 安装后第一次全量运行有 4 个采集测试受真实服务的 advisory lock 干扰；测试改用独立锁命名空间后局部 8 passed，再执行上述最终全量验证。生产锁 ID 保持一致，互斥仍有效。
- 实际 HTTP 验证：归档截止后的 6 小时窗口 MES 330、MNQ 330、MGC 9 条，均返回真实 instrument_id；真实 worker --once 五品种全部 success。
- 实际 PostgreSQL 验证保存于 `/tmp/pa-market-live-verification.json`；完整测试输出为 `/tmp/pa-market-primary-test-final.log` 和 `/tmp/pa-market-primary-build-final.log`。

## 独立评审与处理

由独立 reviewer 对本 Issue 的差异进行只读评审，未加载其他用户改动。发现的四类问题全部有失败回归与修复：旧小时线未进入标准查询路径、少量分钟覆盖完整旧小时、旧 ES/NQ 游标升级跳过停机缺口、未确认分钟的成交去重记录过早清理。修复后全量验证通过。

评审未判断的部署、真实历史完整性和上游互操作性，均以上述实际本机运行证据补齐。无未处理的严重或重要评审问题。

## 执行决定与回滚

执行决定：把当前用户工作状态复制到隔离工作区、复用已安装依赖，验证后只把本 Issue 必要文件复制回原项目，以保留用户其他功能改动。若两个工作区在执行期间出现同文件并发修改，存在集成冲突风险；本次已在原工作区执行全量验证。

停止：`launchctl bootout gui/501/com.pa.market-collector`。恢复运行：`launchctl bootstrap gui/501 /Users/l/Library/LaunchAgents/com.pa.market-collector.plist`。

数据回滚：先停止服务，切回旧版本并执行旧版本 `ensure_schema` 创建原表及唯一约束，再将 `market_archive_2023.legacy_bars_backup` 的原字段插回旧表，恢复旧配置；统一归档历史与新增行保留。当前旧表备份为 0 条，无旧行情需要补回。已有 SQLite 和 PostgreSQL plain SQL 备份保留为初始历史快照，后台只更新 PostgreSQL，不自动重写这些备份。

隔离工作区和执行 ledger 保留供审查，未自动提交用户改动。
