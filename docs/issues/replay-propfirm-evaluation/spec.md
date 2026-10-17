# 回放选择 Prop Firm 考核账户

日期：2026-10-17
状态：2026-10-17 用户回复“继续”，已批准本方案；进入实施计划编写阶段，尚未修改产品代码。
关联：`docs/issues/trade-replay/`。

## 用户目标与已确认范围

用户提供 [PropFirmVIP Lucid 规则页](https://propfirmvip.com/rule/lucid.html)，要求在回放时选择考核哪种账户，并已确认第一版“只做考核阶段：选择账户，跟踪目标、回撤和通过／失败”。

在现有回放起点设置里选择账户方案、规模和规则选项，交易时查看考核进度，退出保存后能继续同一考核。成功标准是规则真实参与服务端交易判定，保存恢复不丢失进度或重置结果。

以下具体产品范围、交互及模拟口径已随本方案获批。首版提供自由练习及 Lucid Flex / Pro / Daily；不包含出金、Direct 免考、Pre-Live、实盘或购买流程。Maxx 新版规则已研究，但关键参数未核实，不开放可执行预设。

## 方案选择

推荐扩展现有后端回放：版本化账户预设＋独立考核状态＋启动选择与进度展示。复用撮合、账户账本、保存和幂等事务。

仅将账户参数填入现有盈利目标/亏损限制无法覆盖追踪回撤、一致性、日损停牌和终态；单独建设通用规则编辑器又超出当前需求。首版不增加后台规则编辑器或第三方实时同步服务。

## 账户预设与证据

规则核验日为 2026-10-17。用户网页用于发现规则，具体参数优先采用官方帮助中心；页面含旧版表格，不直接整体复制为当前规则。

Flex / Pro / Daily 共同的考核参数如下。来源：[Flex Evaluation](https://support.lucidtrading.com/en/articles/12945790-lucidflex-evaluation-account)、[Pro Evaluation](https://support.lucidtrading.com/en/articles/12890029-lucidpro-evaluation-account)、[Daily Evaluation](https://support.lucidtrading.com/en/articles/15996664-luciddaily-evaluation)。

| 规模（USD） | 净盈利目标（USD） | 最大损失额度（USD） | 最大 mini / micro 仓位 |
| --- | ---: | ---: | ---: |
| 25K | 1,250 | 1,000 | 2 / 20 |
| 50K | 3,000 | 2,000 | 4 / 40 |
| 100K | 6,000 | 3,000 | 6 / 60 |
| 150K | 9,000 | 4,500 | 10 / 100 |

| 类型 | 考核一致性 | 回撤类型 | 日损选项 |
| --- | --- | --- | --- |
| Flex | 50% | EOD 日终追踪 | 首版只开放 OFF |
| Pro | 无 | EOD 日终追踪 | 50K/100K/150K 支持 ON / OFF；25K 只开放 OFF |
| Daily | 50% | EOD 或 Intraday 盘中追踪 | ON / OFF |

配置来源：[Flex Customization](https://support.lucidtrading.com/en/articles/16226050-lucidflex-customization)、[Pro Customization](https://support.lucidtrading.com/en/articles/16226068-lucidpro-customization)、[Daily Customization](https://support.lucidtrading.com/en/articles/16033858-luciddaily-customization)。

开启日损时，Daily 的四档额度为 600 / 1,200 / 1,800 / 2,700 USD；Pro 的后三档相同。触及后暂停至下一交易时段，未触及最大损失线则不判失败。[Daily DLL](https://support.lucidtrading.com/en/articles/16085900-luciddaily-daily-loss-limit)、[Pro DLL](https://support.lucidtrading.com/en/articles/12890122-lucidpro-daily-loss-limit)。官方 Flex 自定义页确认有 DLL 开关，但本轮未核实其开启后的具体额度，故首版只开放 Flex DLL OFF，不由 Daily 推断 Flex 数值。

Pro 25K 的考核概览和 DLL 专文写无日损，通用自定义页面却泛称可开关；不能把出金阶段的 600 USD 直接移植过来，故暂不开放 25K DLL ON。

EOD 追踪线按最高日终余额更新，只升不降，最高锁在初始资金＋100 USD。对应锁定参考余额为 26,100 / 52,100 / 103,100 / 154,600 USD。Daily 的盘中模式以含浮盈亏的权益更新追踪线。[Flex Drawdown](https://support.lucidtrading.com/en/articles/12945815-lucidflex-drawdown)、[Pro Drawdown](https://support.lucidtrading.com/en/articles/12890136-lucidpro-drawdown)、[Daily Drawdown](https://support.lucidtrading.com/en/articles/15998425-luciddaily-drawdown)。

一致性定义为最大单日净利润／累计净利润。超过 50% 时继续考核，不判失败；总利润不为正时不显示误导性的比率。首版按公开的标称 50% 判定，明确标注未模拟官方 cushion 容差；官方给了容差示例但未明确完整算法，不将示例外推成固定的 52%。[Flex Consistency](https://support.lucidtrading.com/en/articles/12945805-lucidflex-consistency-percentage)、[Daily Consistency](https://support.lucidtrading.com/en/articles/15998336-luciddaily-consistency)。

交易天数作为进度展示，不将宣传的“最快两天”另写成独立天数门槛。Pro 无考核一致性要求，不能使用其出金阶段的 40% 规则。[Pro Consistency](https://support.lucidtrading.com/en/articles/12890109-lucidpro-consistency-percentage)。

Maxx 新版 Step1 是 40% 一致性、EOD、无日损，达标后进入独立 Pre-Live Step2；旧版五天规则已标注 Legacy。新版公开说明缺少 Step1 最大仓位及追踪锁定细节，首版以禁用项显示“规则待核实”，不借用其他账户参数。[New LucidMaxx](https://support.lucidtrading.com/en/articles/17327076-new-lucidmaxx-program)。

## 用户交互

1. 原回放启动区新增“账户模式”：自由练习 / Lucid 考核。自由练习保持现有自定义资金与提示语义。
2. 选择考核后依次选账户类型、25K/50K/100K/150K、可用的日损选项；Flex 和 Pro 25K 的日损固定为 OFF，只有 Daily 显示回撤类型。初始资金及考核目标由预设确定，只读显示；费用和滑点继续可设置。默认展示 Flex 50K、EOD、DLL OFF 作为初始表单值，不表示交易建议。
3. 开始前显示净盈利目标、损失额度、追踪模式、最大手数、一致性和规则版本。规则明细说明标称一致性口径及分钟模拟限制。
4. 图表下方显示紧凑考核摘要：账户、净利润/目标、当前损失底线、剩余亏损空间、日损剩余值、一致性及状态。详细日收益和规则解释放入交易明细，维持图表优先布局。
5. 状态包含“考核中”“当日暂停”“模拟通过”“考核失败”。通过或失败后停止撮合与新交易，允许查看和保存；重新挑战必须新建场次。当日暂停允许继续推进时间，到下一允许交易时段恢复。
6. 交易记录列表和详情显示账户名称、规模、规则版本和考核结果。恢复已终结考核只能查看，不能靠恢复新草稿重新交易。

## 服务端考核语义

- 净利润包含手续费；已实现考核净利润使用现金减初始资金，持仓浮动盈亏单独展示。通过仅在空仓且净利润达标、一致性合格时判定，随即撤销未成交挂单并冻结结果；这是 PA 的即时模拟判定，不仿造官方升级的后台等待时间。
- 风控权益包含浮动盈亏。EOD 只决定损失线何时上移，不意味着只在收盘检查是否触线。初始底线＝初始资金－损失额度；EOD 底线＝min(初始资金＋100, max(初始资金, 历史最高日终余额)－损失额度)。盘中模式使用历史最高权益代替最高日终余额。
- 触及底线（含相等）判失败，自动平仓并撤销挂单，记录触发时间、权益、阈值和原因。按既有滑点和费用成交；跳空可能造成超额亏损，不人为截断为刚好触线。
- 日损基于该交易时段开始余额和当前权益计算，触发后平仓、撤单并禁单至下一时段。最大损失线和 DLL 同时触发时，硬失败优先；触发后的平仓费用若导致硬失败，也以失败为准。
- 超仓订单在提交/改单时拒绝，撮合前再次校验，防止多张挂单分别合法却合计超仓。只减仓订单不占开仓额度。ES/NQ 用 mini 档，MES/MNQ/MGC 用 micro 档；当前单品种会话不增加跨品种组合交易。
- 校验覆盖即时市价成交、每分钟撮合、保护单及跨交易日结算，不只检查大周期 advance 的最后一根。交易、考核变化及事件证据保持同事务、同 revision 和 operation_id。
- 数据不足、未知分钟缺口、网络错误和用户退出均不等于考核失败。继续沿用原回放暂停与重试流程。

## 时间与分钟行情边界

建议 PA 采用 `America/New_York` 划分交易时段并处理夏令时，以美东 18:00 开始新时段、16:45 强制平仓和停单。图表北京时间和 UTC 日线不能用于分组考核日收益。跨周末跳转只结算实际经过的有效时段，不制造盈利日。

来源 [Allowed Trading Times](https://support.lucidtrading.com/en/articles/11404729-allowed-trading-times) 明确列出 Pro/Flex/Direct、16:45 自动平仓、18:00 恢复及假日提前闭市；未单独列 Daily，且用 EST 字样。Daily 沿用该时段及 DST 是本方案明确的模拟约定，不声称已获得 Daily 独立官方确认。假日提前闭市未获可靠日历时不猜测成交价；缺失结算所需行情则暂停并说明原因。

现有数据为分钟 OHLC：

- 风控必须检查分钟内不利价格，不能只看 close，否则会漏掉触底后收回的失败。
- 既有保守撮合需扩展到风控事件；价格路径不明且影响订单/止损/风控先后时采用确定的不利结果并保存歧义标记，不使用后续未揭示行情。新开仓不得无条件套用入场前可能已出现的价格极值；实现计划必须给出明确事件顺序与边界用例。
- Daily 盘中模式的峰值先后也受 OHLC 限制；结果必须标为分钟模拟。现有“账户 balance 触线”文案未完全解释盘中浮亏语义，纳入权益硬风控属于本方案保守建模约定。
- 无法准确检查不足 5 秒交易占比或还原 tick 级执行；规则详情固定列出这些未覆盖项。界面使用“模拟通过”，不表示机构正式审核通过。

## 技术接入、兼容与可观测性

- `backend/app/replay/` 内增加版本化预设目录与纯考核逻辑模块。目录仅包含来源完整、参数确定的可执行组合；不在每次回放时抓取网页。
- 在现有 replay 路由下提供只读账户目录；创建请求提交预设标识，由服务端解析，不信任客户端自行传来的底线、手数或终态。后端契约、`frontend/src/types.ts`、`frontend/src/api.ts` 同步修改并校验全部调用方。
- `TrainingConfig` 增加可选账户选择；`EngineState` 增加独立考核状态，保存规则完整快照、版本及来源、交易日、日收益、追踪底线、日损暂停到期时间、终态原因和触发证据。规则版本日期与所回放的行情日期分开，不声称还原当年的机构规则。
- 继续使用现有 `ReplaySession.state/config`、事件和保存版本 JSON，不新增第二套资金账本或回放时钟。旧记录缺少考核字段时按自由练习处理。
- `saved.py` 恢复逻辑当前会将 session.status 设为 active，需同时检查独立考核终态；服务端与前端 `canTrade` 都不能绕过。
- 保存时冻结规则及进度，恢复不套用新版预设。应用回滚保留新增 JSON 字段与数据；旧应用不理解考核字段，因此不得由旧版本继续运行新考核场次，回滚前需将新考核入口和写命令停用。
- 复用既有 `trace_id`，结构化日志附带 session_id、operation_id、revision、preset_id、rules_version 和状态变化；事件保存用于判定的金额、时间与原因。异常沿用既有错误记录和重试机制，不记录用户凭据。
- 不增加依赖，不修改行情归档或真实交易日志。

## BDD 验收场景

1. Given 自由练习或旧回放记录，When 开始/恢复，Then 初始资金、原目标提示和交易行为保持兼容。
2. Given Lucid Flex 50K，When 选择 EOD、DLL OFF，Then 目标 3,000、损失额度 2,000、仓位 4 mini/40 micro、一致性 50%；篡改客户端参数不能改变服务端规则。
3. Given 日终净利 1,000 的 50K 考核，When 进入下一时段，Then 底线上移至 49,000；盘中利润回吐不降低该底线；达到锁定参考余额后底线不超过 50,100。
4. Given 同一分钟盘中触线而收盘恢复，When 推进，Then 按定义的保守路径记录失败或歧义结果，不遗漏触线、不用未来数据；覆盖多空、新开仓、保护单、跳空和费用。
5. Given DLL ON 且尚未触及最大损失线，When 达到日损，Then 当日暂停而非直接失败；下一时段恢复。若平仓后触及硬底线，则终态为失败。
6. Given 盈利已达目标但最大盈利日超过总利润 50%，When 检查，Then 保持考核中并展示仍需积累的净利润；不判失败。Pro 不运行该一致性条件。
7. Given 空仓且净目标及一致性均满足，When 市价成交或推进完成，Then 原子保存模拟通过、撤销挂单并禁单；有浮盈但未平仓时不提前通过。
8. Given 最大持仓限制，When 快捷下单、改单或多张挂单依次触发，Then 不得超仓；微型合约按对应上限校验，减仓仍可执行。
9. Given Daily 两种回撤配置，When 出现盘中浮盈后回吐，Then EOD 与 Intraday 的底线变化有可观察差异，歧义分钟留证。
10. Given DST 切换、周末、跨时段大周期推进或数据缺口，When 结算，Then 交易日正确、DLL 正确重置，数据问题不记考核失败，不捏造价格。
11. Given 已保存考核或终结考核，When 刷新/恢复/幂等重试，Then 规则版本、交易日收益、暂停/终态保持一致，无重复结算或重新解锁；不同用户不可访问彼此记录。
12. Given Maxx、Flex DLL ON 或 Pro 25K DLL ON 等未核实组合，When 请求创建，Then 前端不可选，后端明确拒绝，不能静默套用其他预设。

## 验证要求

批准后在 plan.md 拆解实施任务及测试，先后端规则/撮合/保存局部 pytest 与前端启动/进度/恢复局部 Vitest，再执行 `make test`、`make build`。浏览器验收账户选择、盈亏进度、日损暂停、失败/通过和保存恢复，覆盖桌面与窄屏。仅在验收阶段创建 verification.md；本方案文档不代表功能已经实现或测试通过。
