# 原图表回放交易与保存分析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 在当前会话顺序实现，各任务按下列测试周期执行。

**Goal:** 从原图表进入回放即可交易，退出选择保存，已保存记录可以查看、继续和分析。

**Architecture:** 复用 replay 撮合引擎，用同一服务端游标控制图表和订单。运行草稿与不可变保存版本分离；保存版本通过适配层进入既有分析任务运行管理。

**Tech Stack:** FastAPI、SQLAlchemy Async、PostgreSQL、pytest；React、TypeScript、Vitest、lightweight-charts。不新增依赖。

**Spec:** [spec.md](./spec.md) 的 2026-10-03 修订，用户已回复“可以”确认。此计划取代下方旧计划的执行范围。

**状态:** 用户已回复“继续”，本轮实现与独立评审已完成；任务结果和实施调整见 verification.md。沿用当前会话由主代理顺序实施的方式，最终独立评审。直接在原项目集成，保留现有无关修改，不另起脱离原页面的预览交付。

## Global Constraints

### 2026-10-16 回放使用已存行情

用户已确认 spec 的本地归档方案，在当前工作区集成并保留其他未提交修改。使用 TDD 完成下列步骤。

- [x] 先以隔离 PostgreSQL 归档复现：覆盖元数据缺失时已存分钟被外部请求拦截；补随机起点、未知缺口与休市边界回归。
- [x] 回放历史读取移除外部补采，撮合读取独立检查完整性；连续本地分钟、已有覆盖证明或明确常规休市可推进，未知缺口暂停。
- [x] 随机起点查询当前品种实际已确认归档；更新提示文案与说明。
- [x] 局部、后端全量、前端相关验证及构建；真实页面复验指定起点、随机起点与下一根，记录证据。

### 2026-10-05 用户确认回放内交易明细样式

沿用原工作区集成；用户已确认四标签页与紧凑表格预览。此任务只涉及活动回放的图底面板，与统一交易记录入口任务独立。

- [x] 先补标签切换、格式/排序、历史过滤、保护价草稿和交易操作禁用回归，确认失败后抽取 ReplayTradeDetails 并接入原图表。
- [x] 完成局部样式与窄屏表格、资金摘要、按需保护价编辑和全部统计；保留原图表实例与状态保护。
- [x] 运行局部/全量前端测试与构建、独立评审和真实页面桌面/窄屏验收；更新 README 和 verification。

### 2026-10-05 用户确认合并交易记录

- [ ] 建立统一交易记录容器和真实记录内容组件，复用原回放保存列表并精简嵌入标题。
- [ ] 接入侧栏/更多的唯一入口，替换交易日志弹窗，保留真实回放返回与所有记录操作；验证离开回放保护和错误重试。
- [ ] 运行局部与全量前端测试、构建、独立评审和真实页面验收，更新说明及证据。

### 2026-10-04 按参考图统一回放视觉

- [x] 图表充满内容宽度、底部控制横栏与可展开交易明细，保留业务操作并更新交互测试。
- [x] 图内红蓝报价/品种标题与单列右键菜单，校验动态避让和菜单边界。
- [x] 起点选择和活动回放统一扁平布局，调整视口高度并保留现有指标副图。
- [x] 全量前端测试、构建、独立评审及桌面/窄窗真实截图对照。

### 2026-10-04 去掉常驻下单面板

- [x] 移除原图表旁常驻下单表单，仅在右键下单和改单时显示浮层；保留图上快捷下单、共享手数及持仓能力，更新既有集成回归。
- [x] 与去定位一起完成局部/全量前端测试、构建和页面验证，更新说明。

### 2026-10-04 去掉定位功能

- [x] 移除共享回放控制栏的时间输入和定位按钮，清理局部状态并同步说明；保留当前时间与其他回放操作。
- [x] 运行既有回放控件/交易集成测试、前端全量与构建，检查页面并记录验证结果。

### 2026-10-04 截图要求的图表回放交互

- [x] 在原图表增加选 K 线起点与指针预览，先验证选择、取消、范围与上下文切换测试失败，再实现。
- [x] 增加左上角 Buy / Sell、共享新单手数及右键 Reset；先验证快捷交易与视图重置测试失败，再实现。
- [x] 补完整控制器集成测试，覆盖市价成交不推进、重复提交、未知结果幂等重试与数量同步。
- [x] 前端全量测试、构建、独立评审及浏览器验收；修正顶部保护价遮挡报价按钮并补回归，更新说明和验证证据。

### 2026-10-04 用户要求图上直接平仓

按 spec 同日修订直接扩展现有持仓标签，复用下方已验证的市价只减仓能力，沿用当前工作区集成方式。

- [x] 在 `ReplayChartOverlay.test.tsx` / `ChartReplayTrading.test.tsx` 补失败回归，覆盖图上入口、多空全部平仓、不推进、重复提交及失败状态。
- [x] 修改 `ReplayChartOverlay.tsx`、`ChartReplayTrading.tsx` 和 `chartTrading.css`，接入按钮、暂停与状态保护；局部验证。
- [x] 独立评审、前端全量测试及构建；原页面验证，更新 README 和验收记录。

### 2026-10-04 用户授权跨休市补足历史

按 spec 的同日修订，直接修复现有 `ReplayService.bars`，不改变回放游标或撮合。沿用当前工作区，保留其他未提交工作。

- [x] 在 `backend/tests/test_replay_chart_history.py` 先写并运行失败回归：9/28 06:05 北京时间 ES 5m 的 120/500 根、历史观察截断、正常交易时段、空行情 30 日上限和回溯错误传播。
- [x] 在 `backend/app/replay/service.py` 聚合数量不足时向前分段扩大窗口，区间不重叠、按周期边界读取，保留 30 日上限；通过局部和回放回归。
- [x] 独立评审，运行全量 `make test` 和 `make build`；用本地真实行情及原页面核对该起点，记录 `verification.md`。

### 2026-10-03 用户确认当前价即时市价成交

用户明确确认「市价买入后立即按当前回放价格开仓」，按 spec 顶部修订执行，复用现有业务流程与事务。

- [x] 后端先写失败测试，再实现新市价单在提交事务内成交；不推进游标，复用账务与事件，覆盖多空、费用滑点、保护、幂等/冲突、保存恢复和旧挂单兼容。
- [x] 前端补不推进即出现持仓/成交的集成测试，更新市价与条件单文案，旧训练结束流程即时平仓后直接结束。
- [x] 局部测试后执行 `make test`、`make build`，独立评审；原页面即时买入、全平与图上持仓已验证，多空由自动化测试覆盖，更新验收记录。

### 2026-10-03 用户授权修复逐根刷新闪动

用户在诊断加载提示插入/移除造成布局跳动后回复「修复」。保持原需求和撮合行为，只修正回放加载提示的呈现。

- [x] 补充回归：已有行情时快速推进不显示加载提示；慢读仍可取消；初次加载/失败重试保留；连续请求重新计时。
- [x] 将提示移入图表内的绝对定位容器，已有行情时延迟 1 秒显示，避免撑高布局；同步重置每次读取的计时，保留 busy/ready 和未知结果保护。
- [x] 局部与全前端测试、构建、独立评审和原页面验证，记录最新验收证据。

### 2026-10-03 已批准：图上交易实现

用户对 spec 的右键下单、图上盈亏与 TP/SL 拖拽方案回复「继续」。沿用本会话直接集成原工作区的执行方式，不另建预览，不提交混有其他功能的修改。主代理协调实现和验收，可并行处理文件互不冲突的部分；完成后独立评审。

**接口与文件**：`chartOverlay.ts` 定义 `ChartOverlayContext`（chart、series、container、subscribeViewportChange、setInteractionLocked），`TradingChart.tsx` 提供可选 `renderOverlay(context)` 槽；`replay/ReplayChartOverlay.tsx` 负责右键菜单、持仓价格线、拖拽与价格吸附，导出 `ChartOrderRequest`（side、kind、price、anchor、container）供父级打开订单；`ChartReplayTrading.tsx` 协调会话、暂停、revision 与浮层；`OrderTicket.tsx` 增加预填与 portal 呈现，保留同一表单实例。专用 `chartTrading.css` 管理图上样式。

**重点回归**：加权均价不吸附 tick；0.1/0.25 价格格式；纵轴缩放对齐；拖动一侧不丢另一侧；提交已成功但读行情失败不回滚；未知结果保留幂等；观察历史/切周期/会话或 revision 变化取消预览；捕获指针后取消与卸载恢复图表手势。

- [x] 图表桥接 TDD：新建 `chartOverlay.test.ts` 验证视图通知、锁定恢复/卸载；扩展 TradingChart 用例验证覆盖层接入。实现与分析价格线独立的 overlay context，视图变化通过 primitive 更新及 ResizeObserver 通知；局部测试通过。
- [x] 图上交互 TDD：`ReplayChartOverlay.test.tsx` 验证右键价格与方向、轴外不下单，多空持仓浮盈、均价精度，TP/SL 新建/拖动/取消、tick/有效范围、保留另一侧、失败/未知和清理；实现菜单、水平线/标签、保护价预览与独立样式；局部测试通过。
- [x] 下单集成 TDD：扩展 OrderTicket 与 ChartReplayTrading 测试覆盖预填四类订单、单份表单切换位置保留数量、成交与浮盈更新、revision 防旧提交、已确认提交后加载失败保持服务器保护价。右键打开时暂停，价格及手数可修改；保护价复用现有命令。
- [x] 运行局部 Vitest、`make frontend-test`、`make build`，有跨端修改再 `make test`；独立评审修复问题。在原页面验证右键下单→推进成交→浮盈显示→拖动 TP/SL→保存恢复，更新 README 与 verification。

### 2026-10-03 用户确认去掉速度

- [x] 移除两处回放速度选择和对应速度状态，保持默认 1000ms 播放间隔；同步现有测试、说明并运行局部及全前端验证与构建。

### 2026-10-03 用户确认的下一根调整

- [x] 先测试后实现后端 `ReplayCommand.period`，默认 1m；批量分钟撮合到当前周期边界，单次事务提交，缺口/换月回滚，保存全部分钟与成交证据，兼容旧命令幂等。
- [x] 前端类型、手动/自动推进传入当前 period；按钮改「下一根」，移除「上一根」，速度改「根/秒」，补周期切换与重试测试。
- [x] 运行相关 pytest/Vitest 后全量 `make test` 与 `make build`，独立评审，并在原页面确认 5m 推进一个周期。

### 2026-10-03 用户授权的加载问题修复

用户在诊断后明确回复「直接修复」。保持原需求，修正验收遗漏：

- [x] 回归测试复现：实时 5m 窗口为空、最近已采集行情在窗口外，自动回查已有行情；空窗保留回放入口，迟到响应不污染切换后的品种。
- [x] 实时窗口仅在尚无图表数据时，按已有采集状态的最近收盘时间做一次有界回查；标明旧行情时间，实时轮询继续，不触发模型或外部补数。
- [x] 回归测试复现并修正回放创建无加载反馈、退出未取消、行情读取阻塞整个面板的问题；首屏缩小历史窗口，保留更多历史入口。
- [x] 创建和读取支持超时、取消与重试；行情/事件未就绪时禁交易，未知订单结果仍遵守原确认流程。
- [x] 局部测试、前端全量、构建、独立评审及原页面浏览器验证；结果写入 verification.md。

- 原图表「回放」是交易入口，不要求用户另建训练场次。
- 默认资金 100000 USD、费用 0、滑点 0；设置折叠显示。单品种净持仓，模拟成交不写真实交易日志。
- 市价、限价、止损市价、止损限价、持仓止损止盈联动、改撤单、加减仓；不新增追踪止损。
- 分钟撮合、下一分钟生效、未来行情隔离、Decimal 账务、operation_id 幂等、revision 并发保护保持不变。
- 保存不强制平仓；取消退出保留现场；不保存不覆盖旧保存版本；保存失败不得退出。
- 分析由用户点击触发，读取固定保存版本，保存本身不调用模型；终态和 trace 持久化。
- 保留旧场次和真实回放入口，增量建表，不删除旧数据，不捆绑无关修改。

## Review Focus

- 保存时推进请求未完成：停止播放、等已发请求确认后保存，未知提交结果不得绕过（任务 2、4）。
- 止损限价在跳空和同分钟反向触达：不以触发前价格虚构成交，触发状态可恢复（任务 1）。
- 继续旧记录后不保存退出：旧版本内容和已有分析输入必须不变（任务 2、5）。
- 切品种、任务、周期、浏览器关闭与迟到响应：退出保护无遗漏，旧响应不污染新回放（任务 3、4）。
- 无成交、部分平仓、仍有挂单的分析：保持真实状态和时间截断，不造出完整平仓交易（任务 5）。

## 任务 1：完整订单类型

**Files:** 修改 `backend/app/replay/{contracts,engine,service}.py`、`frontend/src/{types,api}.ts`、`frontend/src/replay/OrderTicket.tsx`；测试 `backend/tests/test_replay_{contracts,engine,repository}.py`，新建 `frontend/src/replay/OrderTicket.test.tsx`。

**Interfaces:** ReplayOrder / ReplayCommand 增加 kind=`stop_market|stop_limit`、stop_price，ReplayOrder 增加 triggered_at；默认字段保证旧订单可读取。维持 `match_minute(state, minute, instrument) -> EngineResult`。

- [x] 写失败测试：买 stop=101，open=103 时止损市价按 103+滑点成交；stop-limit=102 时触发但不按 103 成交；卖单镜像；未触发不得成交。盘中触发与触发前限价触达顺序不明时保守延后，triggered_at 保存后不重置。
- [x] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_contracts.py tests/test_replay_engine.py tests/test_replay_repository.py -q`，确认新增断言失败。
- [x] 实现条件字段/tick 校验、触发状态、撮合及改单语义；已触发订单改单保留触发状态，改为新条件单需撤旧下新。同步类型、表单、订单列表文案；复用原保护联动。
- [x] 前端测试四种类型参数、缺失价格拒绝、可见买入卖出按钮、提交失败保留输入；局部 pytest 和 `cd frontend && npm test -- --run src/replay/OrderTicket.test.tsx` 全通过后审查本任务差异。

## 任务 2：草稿与不可变保存版本

**Files:** 修改 `backend/app/replay/{models,contracts,repository,service,routes}.py`；新建 `backend/app/replay/saved.py`、`backend/tests/test_replay_saved.py`；扩展 `test_replay_postgres.py`；同步 `frontend/src/{types,api}.ts`。

**Interfaces:** 新增 ReplaySavedVersion 表（scope、session_id、源 revision、名称、保存时间、state/config/events JSON 快照）及 ReplayDraftState 表（session_id、scope、origin_version_id、discarded）；不更改旧表必填列。ReplaySaveRequest(name, operation_id, expected_revision)、SavedReplayView。`POST /sessions/{id}/save`，`POST /sessions/{id}/discard`，`GET /saved`，`GET /saved/{version_id}`，`POST /saved/{version_id}/resume`，`GET /drafts`。resume 创建独立草稿并重新映射订单 ID，避免全局主键冲突。

- [x] 写失败测试：有仓有挂单保存；同 operation_id 返回同版本；revision 冲突不保存；继续后丢弃草稿不改变旧版本；跨用户 404；订单 ID 不冲突；原无元数据场次仍可列出查看并转存。
- [x] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_saved.py -q` 确认 RED。
- [x] 实现保存事务：锁住源会话，原子复制当前修订及其成交事件；草稿丢弃采用逻辑终止，不删除原场次或保存版本。首次兼容访问旧场次时标记其旧版来源，不静默丢弃历史。
- [x] 添加 PostgreSQL 保存与推进竞争测试，断言快照只包含一个完整修订；跑 saved、repository、postgres 局部测试为绿，同步分页 API/类型并审查差异。

## 任务 3：原图表直接交易

**Files:** 新建 `frontend/src/replay/ChartReplayTrading.tsx`、`useReplayTradingController.ts`；修改 `TradingChart.tsx`、`App.tsx`、`replay/{useReplaySession,OrderTicket,PositionPanel,ReplayControls}.tsx/ts`（按实际扩展名）、`styles.css`；新建 `ChartReplayTrading.test.tsx`、`useReplayTradingController.test.tsx`，扩展 `App.test.tsx`。

**Interfaces:** controller 提供 start({symbol,start?,random_start,config,period})、activeSessionId、回放 hook 状态及 requestLeave(action:()=>void)。TradingChart 增加受控回放属性，原回放起点选择调用 controller.start；活跃会话 bars、markers、play/next/back/period 统一来自 useReplaySession，禁用第二个内部播放计时器。

- [x] 写失败测试：原页面点击回放、选起点后直接显示买卖按钮；不出现创建训练步骤；点击下单后下一分钟成交并显示持仓；切周期不丢订单、不创建第二场次。
- [x] 运行 `cd frontend && npm test -- --run src/replay/ChartReplayTrading.test.tsx src/replay/useReplayTradingController.test.tsx src/App.test.tsx` 确认 RED。
- [x] 抽取现有训练页订单/成交展示到 ChartReplayTrading，嵌在原图表容器中；保留顶部、侧栏和普通分析面板。默认资金配置折叠展示，随机起点继续由后端验证；保留其他非交易图表原有用法。
- [x] 覆盖自动播放仅一个请求、时间前后观察不能改写成交、切场次旧响应被忽略；运行上述测试及 TradingChart/useChartReplay/useReplaySession 回归，审查差异。

## 任务 4：退出弹窗与记录入口

**Files:** 新建 `frontend/src/replay/{ReplayExitDialog,SavedReplayList,SavedReplayDetail}.tsx` 及对应测试；修改 controller、App、SidebarNav、api/types/styles。

**Interfaces:** ReplayExitDialog 接收 busy/error/onSave(name)/onDiscard/onCancel；controller.requestLeave 保存待执行导航动作并暂停播放。SavedReplayList 提供 onView(versionId)、onResume(versionId)、onAnalyze(versionId)；SidebarNav 显示「回放记录」。

- [x] 写失败测试：退出、切品种、重选起点、切任务触发三选项；取消留在原图；保存失败不导航；不保存恢复旧保存版本；提交未知时不能直接保存；浏览器 beforeunload 注册/解除正确。
- [x] 运行 `cd frontend && npm test -- --run src/replay/ReplayExitDialog.test.tsx src/replay/SavedReplayList.test.tsx src/replay/useReplayTradingController.test.tsx` 确认 RED。
- [x] 实现离开保护，所有工作区导航通过同一守卫；beforeunload 仅使用原生提示，服务端每次命令已落库保证草稿恢复。草稿恢复提示与已保存列表分开显示，刷新默认暂停。
- [x] 实现详情、继续和旧训练场次兼容入口；移除要求用户单独开启训练的导航文案。运行以上测试及 App/SidebarNav 回归通过，审查差异。

## 任务 5：保存版本接入分析

**Files:** 新建 `backend/app/replay/analysis.py`、`backend/tests/test_replay_analysis.py`；修改 `backend/app/analysis/{tasks/models,history/snapshots,routes}.py` 和必要的 workflow 输入契约/提示词拼装；replay routes；前端 api/types、SavedReplayDetail、App。

**Interfaces:** `ReplayAnalysisRequest(operation_id, periods)`；`POST /saved/{version_id}/analysis` 返回 task_id 和运行标识。新增任务配置 ReplayReviewTaskConfig(saved_version_id, periods)，以独立 kind=`replay_review` 接入原管理器。`create_replay_input_snapshot(owner_id, task, repository) -> FrozenInputSnapshot` 从保存版本构造可持久化输入；query 包含 replay_version_id、截止时间、成交、持仓、挂单及已揭示行情，不把未平仓转换成假退出交易。

- [x] 写失败测试：有成交、无成交、部分平仓、未平仓均可创建分析输入；只包含保存 cursor 前行情；继续草稿不改输入；同 operation_id 不重复创建运行；跨用户拒绝。
- [x] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_analysis.py -q` 确认 RED。
- [x] 复用现有任务、快照和 AnalysisRunManager，增加 replay_review 适配分支；行情快照首次分析前固化并关联保存版本，重试读取同一输入。模型提示明确模拟回放、未平仓和费用，不读取未来行情或真实 Trade 表。
- [x] 关联 trace_id/session_id/version_id/task_id/run_id，沿用失败/取消/超时终态；前端「分析」调用后显示现有运行与结果视图并能返回回放记录。用测试替身验证模型输入，不实际消耗用户模型额度。
- [x] 覆盖结果关联、重复点击、分析失败及终态，运行 replay_analysis、analysis_task_api、workflow 相关测试与前端详情/App 回归，通过后审查差异。

## 任务 6：整体验收

**Files:** 更新 README、spec 状态、verification.md；不得用上一版测试数字证明本次修改。

- [x] 对照本计划所有验收项检查 API 契约、数据兼容、退出守卫覆盖和分析隔离；独立评审修复严重问题。
- [x] 执行 `make test`、`make build`，记录本次实际输出；若环境限制阻断本地数据库访问按权限流程申请，不以 SQLite 代替并发验证。
- [x] 原页面浏览器验收：开始回放→买入/卖出与条件挂单→推进→退出取消→保存退出→刷新→记录查看→继续后不保存→原版本不变。验证原入口和窄屏布局。
- [x] 验证分析按钮到运行结果流程；使用隔离测试数据/模型替身，未进行真实模型调用需明确记录。更新本次验收证据和使用说明。
- [x] 仅审查本 Issue 差异，保留无关工作；不自动发布或提交混有其他功能的文件。

## 自审映射

订单范围对应任务 1；草稿、快照与兼容对应任务 2；原页面和统一游标对应任务 3；退出/保存/记录对应任务 4；分析和终态对应任务 5；实际交付证据对应任务 6。Review Focus 的五种情况均已写入任务测试。

---

## 上一版实施计划（历史参考）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付独立的完整模拟交易训练和真实交易回放，共享分钟行情回放能力。

**Architecture:** 后端 replay 领域承担撮合、持久化、权限和幂等。前端两个工作区共用受控图表和播放控制器；训练只读取已经发生的行情，真实回放由交易快照生成事件。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy Async、PostgreSQL、pytest；React 19、TypeScript、lightweight-charts、Vitest。

**Spec:** [spec.md](./spec.md)，已获用户确认。

状态：用户已选择继续，按推荐的当前会话顺序实施；功能已集成到原项目。下方保留原始计划，实际任务结果及差异以 verification.md 为准。

## Global Constraints

- 两个独立入口，共用行情与回放组件；模拟成交不写入真实交易日志。
- 下一根行情开始撮合，分钟行情驱动；同分钟无法判断止损止盈先后时止损优先。
- 金额使用 Decimal，API 金额用字符串；初始资金默认 100,000，币种 USD；费用和滑点默认 0。
- 首版一场次一个品种 ES/NQ/MES/MNQ/MGC；整手、净持仓、无实盘连接、无保证金强平模型。
- 每次推进最多一根可用分钟；空窗查找最多 7 个自然日、5 次查询。
- 列表每页默认 20、最多 100；bars 最多 2,000 根；events 最多 200 条。
- 所有场次按用户 scope 隔离，未认证部署沿用 local；客户端不能指定 owner。
- 同一命令的 operation_id 保持不变；expected_revision 控制并发；成交与游标同事务提交。
- 增量建表、保留既有数据；默认不新增依赖；验收时才创建 verification.md。
- 当前目录包含大量既有未提交工作。执行前读取 using-git-worktrees，隔离时必须包含这些必要基线，不能直接从旧 HEAD 丢掉现有回放/归档代码。只提交本任务变更，禁止覆盖其他工作。

## Review Focus

- NaN/Infinity、超长金额、负费用、非整数数量不能进入数据库或产生无法显示的金额（任务 1）。
- 相同游标内多个修改/撤单与推进竞争时，不能用旧配置成交或重复扣费（任务 4）。
- 分钟边界、UTC+8 输入、夏令时显示及多周期切换不能揭露尚未发生的数据（任务 3、6）。
- 切换场次后旧请求迟到或卸载时请求完成，不能污染新场次和自动恢复播放（任务 6）。
- 真实回放跨页或同秒开平仓、重叠多空及缺失 reported_pnl 时，事件顺序、持仓和估算标识必须稳定（任务 5、8）。

## 文件与接口地图

- 新建 `backend/app/replay/{__init__,contracts,instruments,engine,accounting,models,repository,market,history,service,routes}.py`：契约、品种参数、纯撮合、账务、ORM、事务、行情适配、真实事件、业务编排、HTTP。
- 修改 `backend/app/main.py` 注册路由；按需在 `backend/app/core/database.py` 加入模型注册以使用现有 create_all；不改旧表结构。
- `backend/app/market/archive.py` 仅新增回放所需带 instrument_id/confirmed/source 的读取能力，复用既有查询优先级；不改变普通行情 API。
- 新建 `frontend/src/replay/{ReplayWorkspace,ReplaySessionList,ReplayControls,TrainingWorkspace,OrderTicket,PositionPanel,ReplayResults,HistoricalWorkspace,TradeEventList}.tsx` 和 `useReplaySession.ts`。
- 修改 `frontend/src/{api.ts,types.ts,App.tsx,SidebarNav.tsx,TradingChart.tsx,styles.css}`：集中契约与请求、两个入口、受控展示接入和样式。保留现有 useChartReplay 普通模式。
- 新测试逐任务列明；文档只放本 Issue 目录和必要 README 使用说明。

## 任务 1：领域契约、品种参数与账务

**Files:** 新建 contracts.py、instruments.py、accounting.py、包 __init__.py；测试 `backend/tests/test_replay_contracts.py`、`test_replay_accounting.py`。

**Interfaces:** contracts.py 定义 TrainingConfig、InstrumentSpec、MinuteSnapshot、ReplayOrder、ReplayFill、PositionState、AccountState、EngineState、EngineResult、SessionCreate、ReplayCommand、SessionView、EventPage、ReplayBarsPage。命令用 type 区分的 Pydantic union，operation_id/expected_revision 必填。MinuteSnapshot 含开盘时间、Decimal OHLC、instrument_id、source、confirmed。accounting.py 输出 `apply_fill(state: AccountState, fill: ReplayFill, instrument: InstrumentSpec) -> AccountState`；instruments.py 输出 `get_instrument(symbol: str) -> InstrumentSpec`。

- [ ] RED：契约测试拒绝非有限值、负费用、零/小数手数、非 tick 价格、客户端 owner；默认资金与费用按规格。账务测试用测试品种 multiplier=10、tick=0.25，2 手从 100 到 102，毛利 40、每边每手费用 1、净利 36；部分平仓费用不重复分摊。
- [ ] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_contracts.py tests/test_replay_accounting.py -q`，确认失败来自新增功能缺失。
- [ ] 从 CME 官方合约规格核对五个品种 tick/乘数，将来源链接写入 instruments.py 注释；实现上述类型、Decimal 计算、加权均价、权益/回撤/胜率，合约参数和规则版本可序列化为快照。
- [ ] 重跑相同测试，要求全部 PASS；补充小数价格多次加减仓精度断言。
- [ ] 检查差异后仅提交本任务文件：`feat: add replay contracts and accounting`。

## 任务 2：确定性分钟撮合

**Files:** 新建 engine.py；测试 `backend/tests/test_replay_engine.py`。

**Interfaces:** 消费任务 1 类型，输出 `match_minute(state: EngineState, minute: MinuteSnapshot, instrument: InstrumentSpec) -> EngineResult`。EngineResult 包含新状态、订单变更、成交和带 ambiguity/reason 的事件；输入不被原地修改。

- [ ] RED：测试当前已揭示分钟不成交；下一分钟 open=100、滑点 1 tick 的买单成交 100.25；买限价 100 遇 open=99 成交 99，未触达保留。
- [ ] RED：多仓保护 99/103，分钟 OHLC=101/104/98/102 时止损 99；跳空 open=98 则按 98 减滑点；新限价开仓同分钟仅可能先触达的止盈不执行；多空镜像用参数化断言。
- [ ] RED：验证既有保护、主动平仓、开仓顺序；保护退出撤销其余开仓单；加仓继承保护、部分平仓缩小保护数量、reduce-only 不反向。
- [ ] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_engine.py -q`，确认 RED 后实现 match_minute，按 spec 第 4 节逐条实现，不自行引入 OHLC 有利路径。
- [ ] 重跑要求 PASS，重复同一输入输出一致且输入未变；提交 `feat: implement deterministic replay matching`。

## 任务 3：可见行情和来源适配

**Files:** 新建 market.py；局部修改 archive.py；测试 `backend/tests/test_replay_market.py`。

**Interfaces:** `ReplayMarket.visible_bars(symbol: str, cursor: datetime, period: Period, before: datetime | None, limit: int) -> ReplayBarsPage`；`ReplayMarket.next_minute(symbol: str, cursor: datetime) -> MinuteSnapshot`（均 async）。visible_bars 仅聚合已结束分钟，大周期尾柱附 is_partial。next_minute 返回完整分钟给撮合，不向客户端泄露预取内容。

- [ ] RED：cursor=10:02Z 时仅有 10:00、10:01 分钟；10:02 分钟极端 high 不影响 5m 柱；UTC+8 等价输入结果相同；边界和跨日固定样本可重复。
- [ ] RED：休市空窗可推进、已知缺口/未覆盖/超时暂停；5 次/7 天限制；confirmed=false 不作为完成分钟；instrument_id 变化能被上层识别。
- [ ] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_market.py -q`，确认失败；实现现有归档/行情服务适配，保留元数据和错误原因，外部调用不持有场次锁。
- [ ] 重跑并执行 `tests/test_market_archive.py tests/test_archive_provider.py`，要求 PASS；提交 `feat: add cursor-bounded replay market feed`。

## 任务 4：场次持久化、幂等与训练 API

**Files:** 新建 models.py、repository.py、service.py、routes.py；修改 main.py、必要的 database.py；测试 `backend/tests/test_replay_repository.py`、`test_replay_api.py`、`test_replay_trace.py`。

**Interfaces:** `ReplayService.create(scope: str, payload: SessionCreate) -> SessionView`、`get(scope: str, session_id: UUID) -> SessionView`、`command(scope: str, session_id: UUID, payload: ReplayCommand) -> SessionView`（async）。repository.py 的 `ReplayRepository.transact(scope: str, session_id: UUID, command: ReplayCommand, minute: MinuteSnapshot | None) -> SessionView` 持有行锁、复查 revision，事务内调用纯引擎。列表/bars/events 使用 spec 第 5 节 URL 和限制。

- [ ] RED：创建/保存/恢复、scope 404、owner 注入拒绝、订单改撤和保护修改、finished/abandoned 禁止下单、finish 有持仓拒绝；保存配置快照不随参数表变化。
- [ ] RED：相同 operation_id 内容相同返回原结果（优先于 revision 检查），内容不同 409；过期 revision 409；事务中途失败时成交/现金/游标全不变。
- [ ] RED：PostgreSQL 两连接同时推进或改价竞争，只有一个成功；同命令超时重试只扣一次费用。数据库测试必须用隔离测试库，未配置则记录未验证，不能用 mock 冒充锁验证。
- [ ] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_repository.py tests/test_replay_api.py tests/test_replay_trace.py -q`，确认失败；实现五张表、唯一幂等键、分页、命令状态机、注册路由和请求 Trace。
- [ ] 记录失败/超时事件但不改变交易状态；换月拒绝撮合并保留可恢复/放弃场次；行情来源快照与成交同事务保存。补充 API 到行情边界同 trace_id 和异常堆栈的日志测试。
- [ ] 重跑要求 PASS；验证重复 ensure_schema 不破坏旧数据；提交 `feat: persist replay sessions and expose training API`。

## 任务 5：真实交易快照与事件回放

**Files:** 新建 history.py；扩展 service.py/contracts.py；测试 `backend/tests/test_replay_history.py` 及 test_replay_api.py。

**Interfaces:** `build_trade_events(trades: list[TradeSnapshot]) -> list[HistoricalTradeEvent]`、`historical_state(events: list[HistoricalTradeEvent], cursor: datetime) -> HistoricalState`；快照和事件类型在 contracts.py。创建 historical 场次通过既有交易查询权限读取，持久化副本；minute cursor 与交易原始时间同时保留。

- [ ] RED：同品种多笔、混合品种拒绝、开平仓前后各 60 分钟默认区间；修改原始记录不改变快照；同秒同笔先 entry 后 exit，其余按 trade ID 稳定排序。
- [ ] RED：未来 exit/最终盈亏不可见；重叠多空逐笔汇总；report_pnl=56、fees=1.44 时最终仍展示 56；无报告值时显示估算并按实际数量/乘数和费用算净值。
- [ ] 运行 `cd backend && ../.venv/bin/pytest tests/test_replay_history.py tests/test_replay_api.py -q`，确认失败后实现事件生成、seek、事件分页、来源合约标识和参考估算，不修改原交易。
- [ ] 重跑要求 PASS，增加跨页事件无重复/遗漏断言；提交 `feat: add historical trade replay snapshots`。

## 任务 6：共享前端受控回放

**Files:** 修改 types.ts、api.ts、TradingChart.tsx；新建 useReplaySession.ts、ReplayControls.tsx、ReplaySessionList.tsx；测试 `frontend/src/replay/useReplaySession.test.tsx`、`ReplayControls.test.tsx` 及现有 TradingChart.test.tsx。

**Interfaces:** types.ts 镜像任务 1 HTTP 类型，金额保留 string。api.ts 输出 createReplaySession/listReplaySessions/getReplaySession/getReplayBars/getReplayEvents/sendReplayCommand，每个接受 AbortSignal。`useReplaySession(sessionId: string | null)` 返回 session/bars/events/busy/error/playing/speed/observedCursor，暴露 command/seek/play/pause/refresh；命令带稳定 operation_id。

- [ ] RED：自动播放任意时刻只有一个 advance；慢响应不并发、不跳分钟；超时重试同 ID；409 后暂停刷新且不重发下单。
- [ ] RED：A 请求晚于 B 返回不能污染 B；卸载取消；刷新默认暂停；回看禁用下单但不撤销成交；切周期沿用同游标且不请求完整未来柱。
- [ ] 运行 `cd frontend && npm test -- --run src/replay/useReplaySession.test.tsx src/replay/ReplayControls.test.tsx src/TradingChart.test.tsx`，确认失败后实现 hook 和受控模式；受控图表禁用内部回放/实时倒计时，保留普通模式原行为。
- [ ] 重跑并执行 useChartReplay.test.tsx，要求 PASS；提交 `feat: add shared replay controls and client state`。

## 任务 7：完整模拟训练界面

**Files:** 新建 TrainingWorkspace.tsx、OrderTicket.tsx、PositionPanel.tsx、ReplayResults.tsx；扩展 ReplaySessionList.tsx/styles.css；测试 `frontend/src/replay/TrainingWorkspace.test.tsx`。

**Interfaces:** `TrainingWorkspace({sessionId}: {sessionId: string})` 消费任务 6 hook；OrderTicket 发送 typed ReplayCommand，不自行计算撮合；资金曲线/统计读取后端状态和事件。

- [ ] RED：创建场次默认 100000/0/0；随机/指定时间、品种、初始资金可选；无效数量和价格阻止提交；市价/限价、多空、改撤单、保护价、加减仓命令参数正确。
- [ ] RED：成交后展示费用/浮盈/净利/回撤/胜率；回看禁用操作；结束有仓提示提交全平并推进，只有成交后才 finish；无后续行情可恢复或放弃。
- [ ] 运行 `cd frontend && npm test -- --run src/replay/TrainingWorkspace.test.tsx`，确认失败后实现布局、表单、订单持仓、资金曲线和结果页；Decimal 金额仅格式化，前端不决定财务数值。
- [ ] 重跑要求 PASS，覆盖请求失败保留表单、歧义成交说明、分钟级撮合限制及键盘可访问操作；提交 `feat: build complete replay trading workspace`。

## 任务 8：真实回放界面及双入口集成

**Files:** 新建 HistoricalWorkspace.tsx、TradeEventList.tsx、ReplayWorkspace.tsx；修改 App.tsx、SidebarNav.tsx、styles.css；测试 `frontend/src/replay/HistoricalWorkspace.test.tsx`、`ReplayWorkspace.test.tsx`、现有 App.test.tsx/SidebarNav.test.tsx。

**Interfaces:** `ReplayWorkspace({mode, initialTradeIds}: {mode: 'training' | 'historical'; initialTradeIds?: string[]})` 管理列表/创建/工作区路由；`HistoricalWorkspace({sessionId}: {sessionId: string})` 共用 hook，以未来事件目标时间定位，不提前渲染未来成交内容。

- [ ] RED：侧栏两个独立入口、交易日志“回放”预选 ID；选择同品种交易并创建；上一/下一成交、时间定位、标记随 cursor 出现和消失。
- [ ] RED：跨页事件稳定、重叠持仓、报告/估算标签、“连续合约行情”和原合约名称可见；未来平仓价/PnL 不出现在动态明细；修改真实记录不影响当前快照。
- [ ] 运行 `cd frontend && npm test -- --run src/replay/HistoricalWorkspace.test.tsx src/replay/ReplayWorkspace.test.tsx src/App.test.tsx src/SidebarNav.test.tsx`，确认失败后实现入口和工作区，保留现有分析/交易日志交互。
- [ ] 重跑要求 PASS；两个功能均可独立创建、退出和恢复；提交 `feat: integrate historical replay and independent navigation`。

## 任务 9：集成验证、审阅与使用说明

**Files:** 更新 README.md；验收时创建 `docs/issues/trade-replay/verification.md`；按验证结果修正相关实现/测试。

- [ ] 按 spec 16 项 BDD 映射实际测试，补齐缺失场景；核对 contracts/types/api 一致性、权限、事务、分钟边界和未来数据隔离。
- [ ] 使用 verification-before-completion 和 requesting-code-review 技能；仅在用户选定执行方式后按对应流程开展评审，严重问题必须修复后重验。
- [ ] 运行 `make test`（内部含 backend-test/frontend-test），然后 `make build`；记录命令、退出码、实际失败/跳过。既有失败使用项目 diagnosing-pa-errors，区分本次回归与基线，未授权的独立问题只诊断。
- [ ] 浏览器验收：新建训练→挂单→推进成交→改保护→部分平仓→刷新恢复→结束；交易日志→真实回放→跳成交→回看→恢复；检查窄窗口、表单键盘操作、加载失败和错误提示。
- [ ] 写入使用步骤、分钟撮合和连续合约限制、测试证据、数据库验证情况与回退方式。未执行的验证明确标记，不声称完成。
- [ ] 按确认的集成方式提交本任务文件；无用户发布要求则不部署、不合并。

## 自审与执行交接

- Spec 1–3：任务 6–8；Spec 4：任务 1–3；Spec 5–6：任务 3–5；Spec 7–8：任务 1–9。
- Review Focus 五类边界分别已加入任务 1、4、3/6、6、5/8。
- 前五任务先稳定后端契约，任务 6 提供两个界面的共享接口，任务 7/8 分别完成两个功能；最后统一验收，不将内部里程碑误作交付完成。
- 推荐在当前会话由主代理顺序执行：接口依赖紧密，现有工作区改动多，集中处理更易保持一致；也可选择逐任务子代理实现和审阅。执行前需用户审阅本计划并选择方式。

## 2026-10-13 规格补充实施任务

### 任务 10：训练回放盈利目标与亏损限制提示

**Files:** 修改 `backend/app/replay/contracts.py`、`accounting.py`、`engine.py`、`service.py`，`frontend/src/types.ts`、`frontend/src/replay/ChartReplayTrading.tsx` 与相关样式；扩展 `backend/tests/test_replay_contracts.py`、`test_replay_engine.py`、`frontend/src/replay/ChartReplayTrading.test.tsx`。

- [ ] RED：配置接受可选正数盈利目标/亏损限制，拒绝零和负数；推进后按 equity - initial_cash（含浮盈与费用）记录盈利/亏损阈值首次触发，并在序列化恢复后保持已触发状态。
- [ ] RED：开始设置能提交两个阈值；触发相应阈值时显示提示；确认服务端状态返回 active、交易入口仍可用，且无阈值时不显示提示。
- [ ] 实现账户阈值状态的后端持久化；命令执行后检查阈值，包括直接市价成交；兼容旧场次缺失字段。前端仅在已有训练会话中显示提示并可从开始表单配置；不改变播放、仓位、订单或下单权限。
- [ ] 运行后端 replay 契约/撮合与前端 ChartReplayTrading 相关用例；再运行 `make backend-test`、`make frontend-test`、`make build`，记录真实输出和已有失败；不修改无关文件。

## 2026-10-15 回放启动交互

- [x] 调整图表启动区，将资金与折叠选填设置放在开始操作前；图上选柱改为确认起点。
- [x] 覆盖必填资金校验、无目标启动、选柱确认；运行相关回放/图表测试、前端回归与构建，并查看实际页面。


## 2026-10-17 回放起点统一菜单

- [x] TDD 验证三种起点共用菜单、日期按需显示、取消/模式切换、资金及忙碌防护。
- [x] 抽取起点菜单并接入原选柱、日期、随机行为，保留资金设置和回放控制。
- [x] 局部与全量前端、构建、独立评审、实际页面验收及记录。
