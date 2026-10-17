# 回放 Prop Firm 账户考核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有回放中选择 Lucid 考核账户，由后端判定进度与结果，保存恢复后维持同一规则和状态。

**Architecture:** 扩展现有 replay 契约、撮合和事务，增加版本化预设及独立 assessment 状态。金额继续用 Decimal，规则快照随现有 JSON 保存；前端只选择、展示和控制操作权限，不另算考核结果。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic、SQLAlchemy Async、PostgreSQL、pytest；React、TypeScript、Vitest。

**Spec:** [spec.md](./spec.md)，2026-10-17 已获用户批准。

**Status:** 计划已编写并自检，等待用户审阅及选择执行方式；以下任务均未实施。

## Global Constraints

- 首版提供自由练习及 Lucid Flex / Pro / Daily；不包含出金、Direct 免考、Pre-Live、实盘或购买流程。
- Flex 和 Pro 25K 的日损固定为 OFF，只有 Daily 显示回撤类型；Maxx 以禁用项显示“规则待核实”。
- 首版按公开的标称 50% 判定，明确标注未模拟官方 cushion 容差；Pro 无考核一致性要求。
- 通过或失败后停止撮合与新交易，允许查看和保存；重新挑战必须新建场次。
- 交易、考核变化及事件证据保持同事务、同 revision 和 operation_id。
- 不增加依赖，不修改行情归档或真实交易日志；旧记录缺少考核字段时按自由练习处理。
- `America/New_York`、18:00 新时段、16:45 强平；金额精确计算，HTTP 使用字符串。
- 规则版本日期与行情日期分开；分钟 OHLC 保守模型不能声称复刻 tick 执行或官方审核。

## Review Focus

1. 考核已终结但外层 session.status 仍为 active，恢复或旧客户端不能重新下单；任务 4、5、7 覆盖。
2. 未知提交的原操作重试必须返回原结果，不能被新产生的终态/停牌门禁挡住；任务 4、7 覆盖。
3. 大周期第二分钟触线后，游标和证据不能继续走到第五分钟；任务 4 覆盖。
4. cursor 可以越过无成交分钟，last_price 未必是16:45结算价；缺可信报价须暂停而非捏造强平；任务 3、4 覆盖。
5. 费用、同分钟保护单和多挂单共同影响失败及仓位；不能先按整根最低/最高价误判尚未建立的持仓；任务 3、4 覆盖。

## 文件边界与执行依赖

新建后端 `account_presets.py`（只读规则目录）、`assessment.py`（日账及状态）、`assessment_matching.py`（考核撮合路径），均位于 `backend/app/replay/`。公共 Pydantic 类型留在 `contracts.py`，复用 `accounting.apply_fill/mark_to_market`，不复制资金账本。

前端新建 `ReplayEvaluationSetup.tsx`、`ReplayEvaluationSummary.tsx` 和 `replayEvaluation.ts`；接入既有回放和记录组件，复用现有样式文件，不改变 App 导航或 TradingChart 起点协议。

任务依赖：1 → 2 → 3 → 4 → 5；6 依赖 1，7 依赖 4、5、6；8 汇总验收。任务 6 可在后端接口冻结后独立进行，避免多人同时修改共享契约。

执行时先按 using-git-worktrees 技能检查并复用合适的隔离工作区，否则从当前项目 HEAD 建立工作区。将本 Issue 已审阅文档带入工作区；不得纳入现有 `logs/app.log` 修改。每项验证通过后单独提交该项文件；下方提交消息是建议，不表示本轮已提交。

## 任务 1：固定账户目录与创建契约

**Files:** 新建 `backend/app/replay/account_presets.py`、`backend/tests/test_replay_account_presets.py`；修改 `contracts.py`、`routes.py`、`service.py` 及 `backend/tests/test_replay_contracts.py`、`test_replay_api.py`。

**Interfaces:**
- `AccountRuleSnapshot`：`preset_id, rules_version, firm, product, initial_cash, profit_target, max_loss, max_minis, max_micros, consistency_limit, drawdown_mode, daily_loss_limit, timezone, session_open, session_close, source_urls, limitations`。规则版本为字符串 `2026-10-17.v1`，不得复用外层撮合版本 `config.rules_version: int`。
- `AccountPresetCatalog`：`items: list[AccountRuleSnapshot]`、`unavailable: list[UnavailableAccountOption]`；后者含 `product, reason`。
- `list_account_presets() -> AccountPresetCatalog`、`resolve_account_preset(preset_id: str) -> AccountRuleSnapshot`、`resolve_training_config(request: SessionCreate) -> tuple[TrainingConfig, AccountRuleSnapshot | None]`。
- `TrainingConfig.account_preset_id: str | None = None`；目录 HTTP 为 `GET /api/v1/replay/account-presets`，沿用 `current_user`。示例 ID：`lucid-flex-50k-eod-dll-off-v1`。

- [ ] **1.1 红测试：** `test_catalog_contains_exact_verified_matrix` 断言可执行组合数为 27（Flex 4、Pro 7、Daily 16），50K 数值为 `3000/2000/4/40`，Daily DLL 为 `600/1200/1800/2700`；四档目标及额度逐一参数化。`test_unverified_presets_are_rejected` 覆盖 Maxx、Flex ON、Pro25 ON、未知 ID。`test_preset_config_cannot_override_rules` 覆盖伪造底线、状态、手数、冲突资金及旧盈亏目标，旧默认资金仍为 `100000`。
- [ ] **1.2 验证红：** 在 backend 运行 `../.venv/bin/pytest tests/test_replay_account_presets.py tests/test_replay_contracts.py tests/test_replay_api.py -q`；应因目录/字段/接口尚不存在而失败，不能把环境故障当红测试。
- [ ] **1.3 实现：** 按 spec 固定数值、来源和限制。用 `model_fields_set` 区分未传初始资金与显式覆盖；未传时由预设填入，显式传入只接受同值；考核模式旧 profit_target/loss_limit 只允许 null。historical 模式拒绝账户预设。未知组合用 `AppError('replay_invalid_preset', ..., 422)`；服务端固定资金，费用/滑点继续使用原校验。创建时先解析配置，初始账户余额必须使用解析后的值。
- [ ] **1.4 验证绿：** 重跑 1.2，全部通过；HTTP 测试断言金额为字符串、目录有版本/来源/禁用说明，自由练习不依赖目录请求。
- [ ] **1.5 提交：** 仅提交本任务文件，消息 `feat(replay): add verified Lucid account presets`，更新本任务复选框。

## 任务 2：交易日日账、追踪底线和考核状态

**Files:** 新建 `backend/app/replay/assessment.py`、`backend/tests/test_replay_assessment.py`；修改 `contracts.py`、`service.py` 创建初始化。

**Interfaces:**
- `AssessmentState`：`rules: AccountRuleSnapshot`，`status: Literal['active','daily_paused','passed','failed']`，`trading_day`（交易时段结束日），`session_open_cash, peak_eod_cash, peak_intraday_equity, loss_floor`，`days: list[AssessmentDay]`，`paused_until, terminal_at, reason`，`progress: AssessmentProgress`。
- `AssessmentDay` 含 `trading_day, net_pnl, had_fills, settled`；`AssessmentProgress` 含 `net_profit, required_profit, consistency_ratio, remaining_loss, remaining_daily_loss, trading_days`，无适用比率/日损时为 null。
- `AssessmentEvent` 含 `kind, at, trading_day, equity, threshold, reason, ambiguous, bar_timestamp, time_precision, candidate_paths`；`time_precision` 为 `minute` 或 `instant`。`candidate_paths: list[AssessmentPathSummary]` 默认为空；摘要含 `path: Literal['OHLC','OLHC']`、`status`、`equity`、`loss_floor`、`fills: list[ReplayFill]`，只用于解释两种模拟结果，不回填账本。`EngineState.assessment` 默认为 null，`EngineResult.assessment_events` 默认为空数组。
- `initialize_assessment(rules: AccountRuleSnapshot, cursor: datetime) -> AssessmentState`。
- `refresh_assessment(state: EngineState, at: datetime, *, had_fill: bool = False) -> list[AssessmentEvent]`：对已估值的私有状态更新日收益、指标和状态；不自行记成交。
- `close_assessment_day(state: EngineState, at: datetime) -> list[AssessmentEvent]`、`open_assessment_day(state: EngineState, at: datetime) -> list[AssessmentEvent]`：只结算一次，调用者先确保需要强平的持仓已处理。

- [ ] **2.1 红测试：** `test_eod_floor_trails_only_settled_cash_and_locks` 断言50K底线 `48000 → 49000 → 50100`、亏损日不回退；`test_intraday_peak_includes_unrealized_profit` 断言浮盈使 Daily intraday 上移而 EOD 不变。`test_consistency_waits_instead_of_failing` 用日收益 `2000,1000` 得 `required_profit=4000`、active，补至各日 `2000,1000,1000` 才 passed。`test_pro_passes_only_flat_net_of_fees` 用净利2999保持active、3000空仓passed、3000浮盈但持仓不通过。
- [ ] **2.2 验证红：** backend 运行 `../.venv/bin/pytest tests/test_replay_assessment.py -q`，观察预期缺失功能失败。
- [ ] **2.3 实现：** 日收益是当日现金差额，包含入场和出场费用；有实际成交才计交易日，不创建周末空日。净利≤0时比率 null，50%要求的 `required_profit=max(target, best_day/0.5)`，Pro 为 target。EOD/Intraday 底线使用 spec 公式；日损底线=`session_open_cash-DLL`。硬底线 `equity<=floor` 优先于 DLL；通过仅在空仓且非停牌/失败时判定。通过撤销挂单，终态不可逆，日损暂停在下一有效开盘解除。全部派生金额/比率用 Decimal；界面可四舍五入显示，判定不得使用显示精度。
- [ ] **2.4 验证绿：** 重跑 2.2；补充参数化 `test_trading_day_dst_weekend_and_zero_profit`（2026-03-08/2026-11-01 DST、周五至周日、净利0与亏损），以及低于/等于/高于阈值、日损暂停、规则快照 JSON 往返、旧 state 缺字段默认 null。
- [ ] **2.5 提交：** `feat(replay): track evaluation progress and session risk`。

## 任务 3：分钟路径、风控强平与可信日终价格

**Files:** 新建 `backend/app/replay/assessment_matching.py`、`backend/tests/test_replay_assessment_matching.py`；修改 `engine.py`、`contracts.py`；回归 `test_replay_engine.py`。

**Interfaces:**
- 保持 `match_minute(state: EngineState, minute: MinuteSnapshot, instrument: InstrumentSpec) -> EngineResult`；无 assessment 走既有撮合语义，有 assessment 使用同一引擎内的风控路径。
- `match_assessment_minute(state: EngineState, minute: MinuteSnapshot, instrument: InstrumentSpec) -> EngineResult`、`settle_immediate_assessment(state: EngineState, instrument: InstrumentSpec) -> EngineResult`；均返回副本及本次 fills/events，不重复计已有成交。
- `settle_assessment_boundary(state: EngineState, at: datetime, instrument: InstrumentSpec) -> EngineResult`；负责16:45/18:00边界及日账结算，未满足强平报价条件抛 `replay_settlement_price_missing`（409）。
- `EngineState.last_price_at: AwareDatetime | None = None`；创建及每根已确认分钟设置为该价格的实际可见时间，不能随空分钟推进伪造更新。

**确定的分钟模型：**
1. 仅对考核账户在私有副本分别推演 `O→H→L→C` 与 `O→L→H→C`。这是两种约定路径，不声称穷尽真实 tick；沿价格线段按首先触及的价位依次处理事件，入场前已经经过的极值不能影响新持仓。
2. 开盘跳空先检查已有持仓的风控/保护，按开盘价及不利滑点成交。线段上按距离最近的触发价执行；同价优先级为硬失败、DLL、止损、只减仓订单、止盈、开仓订单；同类订单按已有稳定顺序。每次成交后先扣费、估值、检查风险，再继续。
3. 止损限价须先触发后遇到合法限价；不得拿触发前低点成交。触线价按品种 tick 对齐到实际越线的一侧，随后应用原滑点/费用；跳空亏损不截断。使用现有 `apply_fill/mark_to_market`，不建第二套账务。
4. 比较两条路径，保守选择顺序为 failed、daily_paused、active、passed；同状态优先较低权益，再较小剩余亏损空间，完全相同固定选 O→H→L→C。成交、底线或结果不同时保存两条路径摘要和 `ambiguous=true`；只提交所选路径的成交，不重复扣费。终态后不再消费该路径的后续价位。
5. 分钟内事件的 at/cursor 使用该分钟结束时刻并标记 minute 精度，bar_timestamp 记录原分钟；不伪造秒级时间。16:44 分钟正常撮合后在16:45按其 close 强平、撤单、扣费、检查风险并结算EOD。没有对应已确认报价且持仓未平时暂停；空仓可用现金结算。

- [ ] **3.1 红测试：** `test_intrabar_floor_touch_survives_close_recovery`：50K、NQ多1均价150，O150/H180/L50/C160，在EOD底线48000失败并平仓。`test_protective_stop_precedes_lower_risk_floor`：同例加SL130，应先止损、保持active，不按之后L50误判。`test_entry_does_not_see_preentry_extreme` 覆盖两路径不同入场顺序；`test_intraday_paths_choose_conservative_result` 用O150/H250/L149/C210验证盘中追踪歧义。
- [ ] **3.2 验证红：** backend 运行 `../.venv/bin/pytest tests/test_replay_assessment_matching.py tests/test_replay_engine.py -q`。
- [ ] **3.3 实现：** 按上述顺序完成路径推演和边界结算；新产生 failed/daily_paused 事件须在同一结果内自动平仓撤单，再估值检查费用是否使暂停升级失败。每次待成交开仓前用规则校验实际累计仓位，超出则取消该单并留原因，不回滚其他合法成交。正常强平不因持仓过时段本身判失败。
- [ ] **3.4 验证绿：** 重跑3.2；参数化多空、四种订单、保护单同触发、只减仓、加减仓、多挂单累积、跳空、tick和费用、DLL平仓费用导致硬失败。`test_cutoff_requires_exact_confirmed_quote` 验证16:44close可用、陈旧/缺失/未确认价拒绝且原状态不变；`test_replay_costs_are_applied_once_across_candidate_paths` 验证两候选只记一次费用。原自由练习撮合测试全部通过。
- [ ] **3.5 提交：** `feat(replay): enforce evaluation risk during minute matching`。

## 任务 4：创建、即时订单与 advance 的原子集成

**Files:** 修改 `backend/app/replay/service.py`、`repository.py`（仅必要的事件适配）、`backend/tests/test_replay_advance.py`、`test_replay_market_orders.py`、`test_replay_repository.py`、`test_replay_local_archive.py`、`test_replay_postgres.py`。

**Interfaces:** `assert_assessment_command(state: EngineState, command: ReplayCommand, instrument: InstrumentSpec) -> None` 放在 assessment.py；使用任务1–3输出。保留 `ReplayService.command/create/_edit_order` 与 repository.transact 对外签名。

- [ ] **4.1 红测试：** `test_terminal_second_minute_stops_five_minute_advance` 断言cursor为第2分钟结束、evidence.minutes仅两根、无后续成交；`test_daily_pause_can_advance_but_cannot_trade` 断言订单拒绝而时间可推进，下一时段恢复；`test_immediate_fill_and_liquidation_commit_together` 断言原成交+风险强平、资金和状态同revision。`test_preset_market_order_requires_fresh_session_quote` 拒绝新时段用旧报价下单。
- [ ] **4.2 验证红：** backend 运行 `../.venv/bin/pytest tests/test_replay_advance.py tests/test_replay_market_orders.py tests/test_replay_repository.py tests/test_replay_local_archive.py -q`。
- [ ] **4.3 实现：** create 在最终起点确定后初始化 assessment 和 last_price_at。命令先查原幂等结果，再检查权限；行锁内再次核对考核终态、时段和限仓。修改前校验，防止无效改单先撤旧单。即时成交先沿用 `_edit_order` 记账，再调用 settle_immediate_assessment 合并新增风险成交。考核模式市价要求 last_price_at==cursor 且在当前允许交易时段，陈旧报价提示先推进。
- [ ] **4.4 实现 advance：** 每分钟前处理跨时段边界，调用 match_minute，累积实际消费的分钟和事件；遇 passed/failed 立即停止并保留实际cursor，不执行原无条件 `state.cursor=target`。非终态才完成剩余已证明空分钟的时间推进，跨16:45/18:00须结算。预加载发现未知缺口、未确认分钟或换月继续维持整批回滚；业务失败/日停是成功提交的结果，不能抛错回滚强平。
- [ ] **4.5 验证绿：** 重跑4.2；新增 `test_retry_terminal_operation_returns_original_result`、`test_multiple_pending_entries_cannot_overfill_limit`（50K NQ≤4、MNQ≤40、MGC≤40）、`test_missing_cutoff_quote_rolls_back_day_and_cash`。在 `test_replay_postgres.py` 复用隔离schema验证双连接触线只有一次、保存竞态只见完整revision、注入成交写失败时资金/日收益/考核状态全回滚；运行 `../.venv/bin/pytest tests/test_replay_postgres.py -q`，记录真实执行或跳过原因。
- [ ] **4.6 提交：** `feat(replay): persist evaluation decisions atomically`。

## 任务 5：保存恢复、规则版本与审计日志

**Files:** 修改 `backend/app/replay/saved.py`、`service.py`、`backend/tests/test_replay_saved.py`、`test_replay_api.py`，必要时扩展 `test_replay_analysis.py` 兼容快照。

**Interfaces:** `restored_session_status(original: dict[str, Any]) -> str` 在 saved.py：assessment passed/failed 返回 completed；其余沿用可继续草稿语义。事件证据新增 `assessment_events`，恢复事件保留原规则事件及 origin_version_id，不重新执行。

- [ ] **5.1 红测试：** `test_saved_assessment_round_trip_preserves_all_four_states` 参数化 active/daily_paused/passed/failed，比较规则、底线、日收益、暂停时间和事件；`test_resume_terminal_assessment_cannot_unlock_commands` 拒绝advance/place/amend；`test_saved_rules_do_not_follow_changed_catalog` 修改目录后仍使用旧快照；`test_resave_retains_original_risk_evidence` 验证二次保存仍有原因和来源版本。
- [ ] **5.2 验证红：** backend 运行 `../.venv/bin/pytest tests/test_replay_saved.py tests/test_replay_api.py tests/test_replay_analysis.py -q`。
- [ ] **5.3 实现：** 保存/恢复只深拷贝已有规则，不重新resolve预设；终态恢复为只读，服务端独立终态检查仍保留。discard不改变考核结果；用户退出、保存失败和数据异常不写failed。保留事件链，兼容旧事件缺assessment字段。
- [ ] **5.4 实现日志与验证绿：** 仅在事务提交成功后输出考核变化日志，字段为 event、trace_id、session_id、operation_id、revision、preset_id、rules_version、status、reason、duration_ms；记录到本次实际变化，幂等返回不重复报新终态。HTTP caplog测试传固定 X-Trace-ID，分别覆盖通过、硬失败、行情异常；跨用户404、保存/恢复幂等及原分析流程继续通过。重跑5.2。
- [ ] **5.5 提交：** `feat(replay): retain evaluation results across saved sessions`。

## 任务 6：前端契约、目录加载与启动表单

**Files:** 新建 `frontend/src/replay/ReplayEvaluationSetup.tsx` 及同名测试；修改 `frontend/src/types.ts`、`api.ts`、`api.test.ts`、`replay/ChartReplayTrading.tsx`、`ChartReplayTrading.test.tsx`、`replayLayout.css`。

**Interfaces:** TypeScript 类型与任务1–2同名同字段，Money仍是string，旧state的assessment/last_price_at可缺省。`getReplayAccountPresets(signal?: AbortSignal): Promise<AccountPresetCatalog>` 放api.ts。`ReplayEvaluationSetup` 采用受控 props `{ value: string | null; onChange: (presetId: string | null) => void; onReadyChange: (ready: boolean) => void; disabled?: boolean }`，null为自由练习，选择值从服务端目录解析。

- [ ] **6.1 红测试：** `starts_evaluation_with_preset_and_costs_only` 断言发送account_preset_id、fee_per_side、slippage_ticks，不发送底线/终态/自由目标；`invalid_free_cash_does_not_block_evaluation`；`catalog_failure_does_not_block_free_practice`；组件测试覆盖27组合、失效选项自动纠正、Maxx禁用、Flex/Pro25 DLL OFF、Daily回撤选择、目录错误重试和AbortSignal。
- [ ] **6.2 验证红：** frontend 运行 `npm test -- --run src/replay/ReplayEvaluationSetup.test.tsx src/replay/ChartReplayTrading.test.tsx src/api.test.ts`。
- [ ] **6.3 实现：** 默认自由练习，只有切换考核才加载目录；考核初值Flex50K/EOD/OFF。用户先选起点再确认账户，沿用现有TradingChart流程；考核模式隐藏自由目标及可编辑初始资金，展示只读规则。目录未就绪禁开始考核，切回自由练习立即恢复原校验。请求取消或晚到结果不能覆盖已切换模式。
- [ ] **6.4 验证绿：** 重跑6.2，断言账户选项、来源版本和分钟模拟说明可读，原起点/资金/费用/快捷下单测试保持通过。
- [ ] **6.5 提交：** `feat(replay): select Lucid accounts before replay`。

## 任务 7：操作权限、实时进度和保存记录展示

**Files:** 新建 `frontend/src/replay/replayEvaluation.ts`、`ReplayEvaluationSummary.tsx` 及对应测试；修改 `useReplaySession.ts`、`ReplayControls.tsx`、`ChartReplayTrading.tsx`、`ReplayTradeDetails.tsx`、`SavedReplayList.tsx`、`SavedReplayDetail.tsx`、相关样式及各自 `.test.tsx`，扩展 `useReplayTradingController.test.tsx`。

**Interfaces:** `evaluationPermissions(session: ReplaySession): {canAdvance: boolean; canTrade: boolean}` 仅选择状态，free/historical维持既有行为。hook返回独立canAdvance/canTrade，并叠加busy/ready/observer/retry门禁。`ReplayEvaluationSummary({assessment, detailed?}: {assessment: AssessmentState; detailed?: boolean})` 只显示服务端progress和交易日标签。

- [ ] **7.1 红测试：** `daily_pause_keeps_next_bar_enabled`（canAdvance=true/canTrade=false）；`inner_terminal_state_stops_active_outer_session`（外层active、内层passed/failed均禁新命令且停止计时器）；`unknown_operation_retry_bypasses_new_input_gate`（无参数command保留原operation_id重试）；`terminal_session_can_still_save_and_exit`。
- [ ] **7.2 验证红：** frontend 运行 `npm test -- --run src/replay/useReplaySession.test.tsx src/replay/ReplayControls.test.tsx src/replay/ChartReplayTrading.test.tsx src/replay/useReplayTradingController.test.tsx`。
- [ ] **7.3 实现门禁：** 播放/下一根使用canAdvance，下单/改单/保护价使用canTrade；command仅对新输入做动作级门禁，无参数的待确认重试仍可执行。终态到来立即setPlaying(false)，避免旧闭包再发请求；允许看历史、切周期、保存/退出。HistoricalWorkspace共用控件不退化。
- [ ] **7.4 红测并实现展示：** 新Summary测试断言四状态中文文案、净利≤0比率“—”、Pro无一致性、净收益不误用realized_pnl毛利、服务端交易日不被浏览器时区改写。图下紧凑摘要、交易明细统计页显示日收益与规则限制；保留四个原tab。记录列表/详情展示账户、规模、版本及结果；终态“继续”改为“查看”，原分析仍可用，自由练习旧记录正常。考核不显示“达到目标仍可继续交易”的原自由提示。
- [ ] **7.5 验证绿：** 重跑7.2，再运行 `npm test -- --run src/replay/ReplayEvaluationSummary.test.tsx src/replay/replayEvaluation.test.ts src/replay/ReplayTradeDetails.test.tsx src/replay/SavedReplayList.test.tsx src/replay/SavedReplayDetail.test.tsx`，随后 `npm test -- --run src/replay`；覆盖切账户、退出弹窗、未知提交、加载失败和窄屏明细，不把测试时钟等待改大掩盖问题。
- [ ] **7.6 提交：** `feat(replay): show evaluation progress and enforce terminal controls`。

## 任务 8：整体验证、评审及用户说明

**Files:** 更新 `README.md` 回放说明；验收时新建 `docs/issues/replay-propfirm-evaluation/verification.md`；更新本计划进度。仅修复本Issue范围的评审问题。

- [ ] **8.1 规则证据检查：** 对照spec数值、27组合、未开放选项及明确模拟口径；验证规则日期不会跟随行情起点改变，旧自由练习不被预设限制。检查无依赖/数据库破坏性变化，所有公共契约及调用同步。
- [ ] **8.2 全量验证：** 局部测试均通过后在根目录顺序运行 `make test`、`make build`；记录最新命令、结果和跳过项。PostgreSQL真实并发测试如未执行须明确限制，不能用SQLite通过替代。发现非预期错误先按 diagnosing-pa-errors 诊断；本次实现引起的问题在已授权范围内修复。
- [ ] **8.3 浏览器验收：** 在原页面检查自由练习→考核选择、Flex50K摘要、Daily各选项、日损停牌仍可推进、终态禁单及保存恢复。桌面和窄窗口均检查图表高度、折叠明细和表格滚动。不得伪造真实数据结果；难以从归档行情稳定触发的阈值场景以已通过的确定测试证明，并在证据中区分。
- [ ] **8.4 独立评审：** 使用 requesting-code-review 技能，对规则、分钟路径歧义、EOD/DST、原子性、终态恢复与UI门禁评审；修复阻断问题后只重跑受影响局部及必要全量检查，不无故重复全套。
- [ ] **8.5 交付文档与提交：** verification.md记录测试/构建/浏览器/评审的实际证据及未覆盖规则；README说明入口、暂停与失败区别、版本保存、分钟模拟和不可选配置。执行 verification-before-completion 后，按 finishing-a-development-branch 技能处理集成；不未经授权部署或发布。建议消息 `docs(replay): document and verify Lucid evaluation replay`。

## 自检与执行方式

spec的12个BDD场景分别由任务1（1/2/12）、2（3/5/6/7/10）、3（4/8/9/10）、4（4/5/7/8/10/11）、5（11）及6–7的可见交互覆盖；任务8完成全量、实际界面与评审证据。接口统一使用assessment，不混用evaluation状态字段；前端Evaluation仅用于组件名称。没有把当前撮合rules_version改成账户版本。

推荐由主代理在当前会话顺序实施，接口稳定后的独立调查/验收可并行，结束后安排独立代码评审；原因是规则、撮合和事务依赖紧密，集中实施可减少接口往返。另一选项是每个任务交给独立子代理实施并逐项评审，检查更细但上下文开销更高。用户审阅本计划并选择方式后进入执行。
