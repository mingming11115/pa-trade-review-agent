# 内置指标验证

日期：2026-10-04（Asia/Shanghai）。

## TradingView 交互对齐（最新验证，23:53）

- 参考官方指标列表、图例设置截图：搜索/分类/收藏/立即添加，图例隐藏/设置/删除，参数/样式/可见性。完整 TradingView 页面多次加载超时，未声称完成其在线交互验证。
- 本次仅改前端，没有新增依赖或修改 API；v1 设置迁移到 v2，并区分已添加和可见状态。
- 局部设置/renderer 16 项、面板 17 项、图表及回放集成 45 项、图例布局 6 项通过；补充原生副图 widget 尚未挂载时的回归测试，先复现 `Value is undefined` 再修正空元素保护。
- 最新 `make frontend-test`：**33 文件 / 297 passed**，完整输出 `/tmp/pa-indicator-frontend-test.log`。
- 最新 `make build`：TypeScript、Vite 成功；主 bundle 594.80 kB，保留已有超过 500 kB 的构建警告。
- `git diff --check`：通过。
- 原生浏览器在真实 ES 行情验证 MACD/RSI 添加、独立参数设置、可见性标签、隐藏/恢复与删除 MACD 后 RSI 上移。修复后完整流程无指标崩溃。
- 当前 IAB 实际 319×827，指标库和设置页窄屏可用，周期复选框固定16×16。viewport 工具请求1280×900未改变实际尺寸，已重置覆盖；本轮没有新的桌面尺寸截图，桌面几何由布局测试补充。
- 窄屏截图 `/tmp/pa-tv-indicator-library.jpg`；完成后通过 UI 移除 QA 添加的副图，恢复 EMA20、成交量，并打开指标库展示结果。
- 独立评审发现并修复：交易控件占满主图时图例不得溢入副图；同周期切换图表身份时清除旧十字线时间。对应测试通过。未触发模型分析或交易操作。

以下为上一阶段内置指标的验证记录，旧面板截图与测试数量不代表当前版本。

## 自动验证

- 基线：TradingChart、chartOverlay、ReplayChartOverlay 共 44 项通过。
- 指标计算：`npm test -- --run src/indicators.test.ts`，29 项通过；包含独立手算结果、暖机不足、非法参数、恒定/单向行情、回放前缀、毫秒时间戳与蜡烛按整数秒对齐。
- 设置与面板：21 项通过，覆盖参数验证、本地存储无效/受限、草稿应用/取消、恢复默认和 Escape 焦点恢复。
- 图表集成：5 项通过，覆盖设置应用与持久化、不重置视口、主副图分配、关闭副图、历史前插与同长度修正、清空行情、回放推进与退出。
- `make test`：最终后端 **412 passed**、前端 **31 files / 274 passed**。首次沙箱执行被本地 PostgreSQL 连接权限拦截，获准在沙箱外重跑后全部通过。完整输出 `/tmp/pa-indicators-test-unrestricted.log`。
- `make build`：TypeScript 与 Vite 构建成功。Vite 提示主包超过 500 kB，未阻断构建；没有新增依赖。
- `git diff --check`：通过。

## 浏览器检查

在现有本地 PA 页面和真实 ES 行情上检查：

- 1440px 桌面：全部指标同时开启后主图曲线、成交量、MACD、RSI 正常绘制；关闭 MACD 后 RSI 与蜡烛保留。
- 刷新后重新打开设置，全部勾选和参数恢复。
- 390px 窄屏：设置对话框内部滚动，无横向溢出（clientWidth 与 scrollWidth 均为 356px）；修正全局 input 样式造成的复选框放大后，六个复选框均为 16×16px。
- 通过 UI 恢复默认并应用，最终设置为 EMA20、成交量开启，其他关闭。
- 设置面板截图：`/tmp/pa-built-in-indicators.jpg`。

浏览器观察到一次行情请求失败日志，随后页面成功加载已有行情；未出现指标相关运行时错误。没有触发模型分析或交易操作。

## 评审

独立只读评审核对了计算口径、回放数据边界、设置持久化、对话框焦点及 lightweight-charts 5.2.0 副图移除/时间轴行为，未发现阻断问题。真实画布操作由上述浏览器检查补充。

设置按浏览器保存，不同步账户。测试保留了仓库原有 Python 弃用警告及 Node 存储实验性提示；新存储相关测试使用隔离的 Storage 替身避免 Node 26 的全局存储行为影响测试。


## 2026-10-16 回放图例操作按钮错位修正

- 用户指出 EMA/VOL 图例旁眼睛、设置及删除按钮错位，恢复既定图例布局，不改变指标行为或需求。
- 浏览器复现：回放通用 `.chart-replay-trading button` 覆盖图例按钮，实际 padding 为 8px 12px、border 为 1px，眼睛按钮宽 26px/高 22px；16px 图标溢出按钮下缘。
- 修正 `indicatorLegend.css` 图例按钮 reset 选择器，提高到图例行作用域，避免被通用回放按钮样式覆盖。
- 浏览器复验：EMA/VOL 六个操作按钮均为 24×22px、padding 0，图标横纵中心均与按钮中心重合；聚焦 EMA 参数后眼睛、设置和删除同排居中可见。
- 局部验证：`npm test -- --run src/IndicatorLegend.test.tsx src/TradingChart.indicators.test.tsx` → 13 passed。
- 构建：`make build` → 成功，保留现有 chunk 大小提示，日志 `/private/tmp/pa-legend-alignment-build.log`。


## 2026-10-16 工具栏开关集中到指标库

- 用户回复“改”确认位置修订。TradingChart 将原有 EMA/Gap/K#/H/L JSX 和回调移入 IndicatorPanel 的 displayControls 插槽，指标库增加“图表显示”区域，顶栏只保留指标入口与其他图表操作；锁定区间提示与图内管理仍在。
- 状态沿用原行为：EMA 保存到现有 v2 浏览器设置，Gap/K#/H/L 保持原组件内状态，不新增持久化语义。
- TDD：集成回归先 2 failed / 7 passed（关闭面板时顶栏仍出现四个开关）；实现后指标、图例相关 32 passed。覆盖普通/locked 图表、四开关切换、EMA 绘图可见性和存储、重开面板保持状态、重挂恢复 EMA、图内眼睛操作保留。
- 页面发现并修正活动回放的指标 portal 按钮被 App 离开保护拦截：共用指标 Dialog section 添加现有 data-replay-interactive 标记。真实指标面板接入 App 的 canvas mock，验证分类和关闭不会弹保存确认，同时保留原品种、记录入口及浏览器历史的保护断言。此用例先 1 failed，修正后通过。独立复审无阻断问题。
- 构建检查曾因新测试误用了 Testing Library 不支持的 exact 参数失败；移除该参数后重新运行全量与构建。
- 最终 `make frontend-test` → 36 文件 / 331 passed，日志 `/private/tmp/pa-indicator-placement-tests-final.log`。最终 `make build` → 成功，日志 `/private/tmp/pa-indicator-placement-build-final.log`，保留现有 chunk 大小提示。
- 浏览器实测：回放顶部不再显示四个复选框；指标库“图表显示”统一呈现 EMA20/Gap/K#/H/L。全部关闭后均变未选中，图上 EMA 标为已隐藏，均线、Gap、K#、H/L 消失；关闭面板不触发保存退出，重新打开状态保持，验证后恢复四项开启。


## 2026-10-16 移除使用可视区间

- 按用户明确指定移除按钮，同时删除 TradingChart 的 onRangeSelected/selectVisibleRange 与 App 的 useVisibleRange 接线；检索确认无其他调用方。
- 浏览器普通图表验证：工具栏显示指标、回放、放大、缩小和重置，不再显示使用可视区间。
- `make frontend-test`：36 文件 / 331 passed；`make build` 成功，保留现有 chunk 体积提示。日志 `/private/tmp/pa-remove-visible-range-tests.log`、`/private/tmp/pa-remove-visible-range-build.log`。
