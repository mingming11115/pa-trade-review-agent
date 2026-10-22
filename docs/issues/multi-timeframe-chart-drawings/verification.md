# 多周期图表与画线验证

## 最新：恢复完整工具栏和已有功能（2026-10-18）

- 用户纠正“你不能给我删掉啊”。撤回仅保留竖线的删减，恢复第三方原版 demo 的 86 工具、选择栏、文字／表格编辑、图片及导入导出；旧水平线、竖线重新显示。
- 图表仍为 TradingView 官方 Lightweight Charts；官方 VerticalLine 只用于非活动图的只读竖线。活动图使用完整原画线引擎，被动图过滤社区竖线避免重复。完整侧栏来自第三方作者，不能称 TradingView 官方 UI。
- 指标、重置、回放、保存／继续、开平仓与账户业务保持。浏览器实际确认四图、完整工具组、已有两条标注、指标／回放／重置入口和回放起点面板。未在此次浏览器验收实际提交订单；既有 35 项交易组件测试覆盖订单交互。
- 24 项初始局部测试通过；另补异步导入期间加载的回归用例：提交前检查可编辑态，忙碌时明确提示重试，避免成功提示后被旧存储覆盖。图片添加同样检查异步完成状态。
- 全量后端 428 passed（`/private/tmp/pa-restored-tools-all-tests-final.log`）；最新前端 380 passed / 45 files（`/private/tmp/pa-restored-tools-frontend-final.log`）；构建通过（`/private/tmp/pa-restored-tools-build-final.log`，仅有现有主包体积提示）。首次沙箱运行被禁止连接本机数据库，获准重跑后通过。
- 六份原始 UI 文件独立复核与上游一致；适配层保留生命周期、交易互斥、持久化，并增加异步提交状态检查。
- 浏览器截图：`/Users/l/.codex/visualizations/2026/10/17/01a148a1-94ba-7a11-991e-6a3196405717/pa-restored-full-toolbar.jpg`。
- 图片实际上传／刷新链路未重新做浏览器验收；图片持久化与失败路径有独立测试。

以下为历史验收记录，官方单竖线删减方案已撤回，以本节为准。

## 深色主题统一（2026-10-18）

绘图区和宿主图表控制区统一采用上游 demo 的背景 #0f0f0f、文字 #dbdbdb、网格 #1f1f1f、边界 #2e2e2e；原版工具栏源码未修改。图内指标图例、倒计时及各槽位标题同步适配。TradingChart、ChartWorkspace、IndicatorLegend 相关 26 项测试通过，构建通过。浏览器重新加载后已检查四图、图例、坐标轴及原版工具栏色调一致。日志：`/private/tmp/pa-dark-chart-tests.log`、`/private/tmp/pa-dark-chart-build.log`。截图：`pa-unified-dark-chart.jpg`。

日期：2026-10-18。

## 实现范围

- 保留 Lightweight Charts 5，锁定 MIT 画线插件 0.6.2。
- 主工作台、模拟交易回放、真实交易回放接入单/双/四图。默认 1m/5m/15m/1h，共享品种，各自周期与指标。
- 十字线按 UTC 时间桶映射；时间定位保留各图跨度。隐藏槽位不维持图表实例或行情请求。
- 回放共用会话和推进命令；各可见周期使用固定 cursor，完整批次统一提交。失败保留旧批次并禁用交易；不同步时不将新持仓/报价叠加到旧行情。
- 六种画线工具、样式、锁定/删除、跨周期共享/单周期可见、本地保存与游标修订；保存与继续草稿复制本地作用域。

## 自动验证

- 最新 `make test`：后端 **428 passed**，前端 **371 passed / 43 files**。日志：`/private/tmp/pa-multichart-all-tests-final.log`。
- 首次沙箱执行因本机数据库连接权限失败；获准在沙箱外重新运行后通过。
- 最新 `make build`：TypeScript、Vite 构建通过。日志：`/private/tmp/pa-multichart-build-final.log`。Vite 提示主包约 849 kB（gzip 264 kB）。
- 局部测试覆盖标注存储/修订、工具交互、联动、StrictMode、父级周期选择、pending 推进期间切布局、刷新失败和旧持仓覆盖层隔离。

## 浏览器验收

使用本机 Vite、FastAPI、真实 Lightweight Charts 和插件：

1. 1600×1000：四个周期实际绘制，活动图边框及工具栏正常。
2. 顶栏 5m → 15m 后活动槽位跟随，再切回正常。
3. 单图中用真实工具放置水平线，出现价格线及属性面板。
4. 恢复四图并刷新后，同一 7786.87 水平线在 5m/15m/1h 继续显示。1m 当前价格范围之外不强制拉伸图表。
5. 500px 窄屏显示活动图和周期标签，切换 1h 后显示对应单图。已重置临时 viewport。

截图：`/Users/l/.codex/visualizations/2026/10/17/01a148a1-94ba-7a11-991e-6a3196405717/pa-multichart.jpg`。

## 评审与边界

独立复审确认已修复重绘反馈环、StrictMode 协调器清理、恢复周期覆盖顶栏、切图丢分析、pending 推进丢布局更新，以及新持仓叠加旧行情。最终复审未发现新增阻断问题。

- 标注仅保存在当前浏览器，不提供跨设备同步。
- 其他五种工具的所有命中、锚点拖动、触摸及右键组合未逐项完成真实浏览器手动验收；插件接入、保存和互斥有模拟行为测试覆盖。
- 时间联动在已加载范围内定位并钳制边界；未额外为每个被动图创建自动补历史队列。
- 交易覆盖层目前只显示于活动图。
- 本次未改后端契约或数据库，未提交、合并或部署。
- 开发服务与测试生成的运行日志保留在 logs 下，未作为功能代码修改。

## Sidebar follow-up (2026-10-18)

Drawing tools now occupy a reserved 44px vertical rail on the active chart left edge. Icons keep accessible names and hover titles; annotation properties open beside the rail. The 15 drawing tests passed. Desktop browser inspection confirmed all seven controls fit without covering the candle canvas. Latest build output: `/private/tmp/pa-sidebar-build.log`.

## 原版 UI 替换与来源核查（2026-10-18）

- 删除自制六工具 UI 和属性表单，直接接入官方 0.6.2 demo 原版 UI。六份原始文件经 SHA-256/字节比较及独立复审核对；固定提交和适配范围见 upstream/README.md 与 component-audit.md。
- 画线模型改用原库 OVERLAY_SPECS 全量工具；三锚点通道已覆盖保存及复制测试，原生 import/clear 通过 change 事件持久化。
- 图片字节写入 IndexedDB，事务成功后才添加 Image，通过公共 setImageReader 恢复。文字和表格编辑生命周期加入交易互斥。
- 后续稳定性修复保留原版 UI DOM 和绘图模式，忙碌／游标变化只更新可编辑状态和图表数据，不销毁文件输入或控件。增加对应回归用例。
- 最新后端全量：428 passed（`/private/tmp/pa-original-ui-all-tests-final.log`）。最新前端全量：375 passed / 44 files（`/private/tmp/pa-original-ui-frontend-final.log`）。最新构建通过（`/private/tmp/pa-original-ui-build-final.log`）。
- 浏览器实际验证：原版 SVG、完整工具分组、弹出菜单以及原标注恢复正常；截图 `pa-original-drawing-ui.jpg`。原版 Image 选择入口能触发文件选择，但自动设置测试文件时浏览器控制返回失效节点，因此图片的实际上传／刷新链路未声称已完成浏览器验收；图片存储和失败路径有独立测试。
- 最终限定复审确认图片持久化、编辑器互斥两项阻断已解决。

## 2026-10-18 顶部按钮位置调整

- 活动图工具栏通过 React portal 渲染到「时间定位联动」右侧，复用现有指标、回放和重置状态及处理函数；独立单图仍在原位显示。
- `npm test -- --run src/TradingChart.indicators.test.tsx src/chartWorkspace/ChartWorkspace.test.tsx`：17 passed。新增实际 TradingChart 工具栏挂载测试，覆盖指标面板、回放面板、重置及隐藏清理。
- `make build`：通过；存在包体大小提示。
- `make frontend-test`：393 passed，1 failed。失败为 `ChartDrawingTools.test.tsx:90`，测试查询 `importFile` 返回 null，无法触发 change。
- 单独运行 `npm test -- --run src/chartWorkspace/ChartDrawingTools.test.tsx`：7 passed，1 failed，复现同一错误。该测试直接渲染画线工具，不加载本次修改的 TradingChart/ChartWorkspace；本次未修改画线导入实现及测试。
- 日志：`/private/tmp/pa-toolbar-focused-tests.log`、`/private/tmp/pa-toolbar-frontend-tests.log`、`/private/tmp/pa-toolbar-build.log`、`/private/tmp/pa-toolbar-drawing-tests.log`。
- 未进行浏览器视觉验收；未修改后端，未运行后端测试。

## 活动图切换重复加载修复（2026-10-18）

- 修复前新增 App 集成测试失败：首次四图 4 个行情请求，点击其他图后累计 8 个请求。
- 将 SessionState.activePeriod（选中图和分析目标）与 period（源行情周期）分开，ChartWorkspace.sourcePeriod 保持批次与图表身份稳定；恢复任务／历史结果或顶部主动改周期时清理活动周期覆盖。
- 新增回归验证：连续点击四个槽位不增加行情请求、不清空 K 线、不改变各槽位 chartKey；一键分析 ensure-live 使用所选 15m；顶部修改 30m 正常加载。
- 最新局部检查：App 与 ChartWorkspace 45 项通过；包含 ChartDrawingTools 的相关检查为 52 passed、1 failed，失败为异步导入测试找不到 importFile 元素。
- make test：解除沙箱对本机测试数据库连接的限制后，后端 437 passed；当次前端 391 passed、2 failed（回放指标按钮测试与画线异步导入测试）。回放指标按钮测试在随后相关测试重跑中通过。
- make build 通过，有现有 bundle 大小警告；git diff --check 通过。未执行本次浏览器手动验收。

### 补充回归与评审

- App + ChartWorkspace 最新局部检查：46 passed。增加顶部切回源周期、非源周期独立补历史的红灯／绿灯证据。
- 后续全量前端检查为 394 passed、1 failed；剩余失败为 ChartDrawingTools 异步导入测试找不到 importFile，未改动该导入实现或测试。
- 非源周期历史请求使用批次 AbortController 生命周期取消，按当前批次身份仅合并目标周期。
- 独立只读评审确认无当前可达路径阻断项；评审最初指出的历史分析路径经调用搜索确认目前无 UI 调用，已撤回为现行 UI 回归的判断。仍防御不同周期分析结果覆盖已有源行情。

## 固定布局与共享画线栏（2026-10-18）

- 根因：双图 visible 使用 [active,(active+1)%4] 导致点击即重排；活动图内部画线 UI 通过 :has 附加边距导致绘图区挪动。
- 双图改为固定 [0,1]；画线 UI portal 到整组外侧固定容器，保留原版工具和管理器。新增 chartContainer 适配原版编辑器 chartWrap 的坐标，ResizeObserver 追踪尺寸。
- 固定双图、共享容器、四转双活动周期与窄屏双图回归均先观察失败。最新 App + ChartWorkspace 49 passed。
- make frontend-test：398 passed、1 failed，剩余为既有异步导入测试找不到 importFile。make build 通过，有 bundle 大小警告。
- 独立评审发现窄屏双图可激活隐藏槽位，已限制窄屏标签为两个槽位并补充回归。
- 浏览器实测：双图左 1m / 右 5m，切换前后绘图区 x=296/576、y=392.1875、width=276、height=280 完全相同。四图上下行 y=392.1875/723.1875，点击第四图前后坐标不变。四转双自动选第一图，顶部周期为 1m。
- 验收截图：/private/tmp/pa-fixed-double-layout.png。
- 参考 TradingView 官方文档：https://www.tradingview.com/charting-library-docs/latest/trading_terminal/multiple-chart-layout/ ，固定 chart index 与 activeChart 分离。

## 独立周期选择修复（2026-10-18）

- 根因：selectedPeriod effect 根据周期值 findIndex，把顶部操作路由到使用该周期的其他槽位；顶部选择同时改变全局源行情。
- 修改：周期更新只修改当前槽位；图内 select 修改自身槽位并激活它；顶部更新 activePeriod，初次尚无源周期时才初始化源行情。允许重复周期。十字线和时间定位开关保持原有独立行为。
- 红灯：匹配兄弟周期时活动槽位错误，App 和 ChartWorkspace 两项测试失败。修复后 App + ChartWorkspace 50 passed。新回归覆盖顶部与兄弟同周期、右图改30m不影响左5m、直接修改非活动左图15m不影响右30m。
- make frontend-test：399 passed、1 failed，剩余仍为既有异步导入找不到 importFile；make build 通过，存在 bundle 大小警告。独立只读评审无阻断项。
- 浏览器实际修改左图为5m保持左图选中，然后改右图为30m，DOM 明确为左5m／右30m；截图 /private/tmp/pa-independent-periods.png。


## 2026-10-22 回放启动布局修复

- 首次新增测试失败：设置区仍在单图容器里，公共 host 不包含 K 线回放面板。修复后同一测试通过，并验证开始按钮提交原起点、非活动图不残留设置区。
- `npm test -- --run src/TradingChart.indicators.test.tsx src/chartWorkspace/ChartWorkspace.test.tsx src/App.test.tsx`：63 passed。
- `make test`：沙箱阻止本机数据库连接，获准在沙箱外重跑后后端 437 passed，前端 402 passed / 1 failed。
- 前端失败：`ChartDrawingTools.test.tsx > does not report a successful async import while loading would discard it`，找不到 `importFile`，`fireEvent.change` 没有目标 DOM。此前验证文档已有同类失败记录；未修改该导入逻辑。
- `make build`：通过，保留已有 bundle 大小提示。
- 浏览器（Chrome，127.0.0.1:5174）：设置区位于整组四图上方，资金文字可读；点击开始回放后四图行边界对齐，播放、下一根、推进周期、时间和退出完整可见。实际行情起点为 2026-10-05T22:25:00Z，未下单。
- 前后端原开发服务已停止，为验收恢复 5174 前端及 8000 后端；后端关闭行情采集和实时 WebSocket 循环。
- 尚未人工覆盖窄屏及展开可选设置时的布局。
- 独立只读评审：本次 portal、共享容器、高度样式及新增回归测试无阻断发现。

## 2026-10-22 四图隐藏快捷下单

- 仅在实际四图布局对 `.chart-quick-trade` 设置 `display:none`；单图和双图仍使用原有规则。
- 回放相关两文件 Vitest：65 passed；构建通过。
- 前端全量：402 passed / 1 failed，仍是已记录的 ChartDrawingTools 异步导入测试。
- Chrome 可访问性状态确认四图无 BUY/SELL 及手数输入，持仓、平仓、添加 TP/SL 和回放控制仍在。切双图验证遇到原生自动化超时，未宣称人工覆盖。

## 2026-10-22 四图高度调整

- 根据用户反馈，将四图绘图区最小高度从 160px 恢复为 280px，允许页面滚动。仅改 CSS。
- `make build` 通过；本轮未人工复查浏览器，未重跑逻辑测试。

## 2026-10-22 回放控制栏深色

- 紧凑控制栏黑底浅字，按钮悬停、下拉框、时间文字与键盘焦点同步使用深色主题。
- `make build` 通过；`npm test -- --run src/replay/ChartReplayTrading.test.tsx`：35 passed。仅改 CSS，未新增镜像样式断言，未人工覆盖浏览器。

## 2026-10-22 回放启动白条与换行修正

- 复现：5174 `/analysis/live`，四图点击回放出现白色横条，取消按钮独占下一行。共享设置区显式白底及平铺 flex 换行导致。
- 修改：起点独立分组，开始/取消同一操作组；共享设置区深色网格布局，620px 以下单列，输入、菜单与焦点同步深色。
- 新增操作组及取消回归测试，旧结构无法找到操作组而失败；设置明确历史日期后新结构测试通过。
- 局部 `npm test -- --run src/replay/ChartReplayTrading.test.tsx src/TradingChart.test.tsx src/chartWorkspace/ChartWorkspace.test.tsx`：61 passed。
- `make frontend-test`：403 passed / 1 failed。仍为既有 `ChartDrawingTools.test.tsx > does not report a successful async import while loading would discard it`，缺少 importFile 节点，非本次修改范围。
- `make build`：退出码 0，有既有大包体积提示。未改后端，未重跑后端测试。
- 浏览器在当前 728px 宽页面确认深色启动区、日期/时区、展开/折叠选填设置、开始/取消排列。620px 以下规则已代码检查，未人工覆盖该尺寸。
- 截图 `/private/tmp/pa-replay-setup-fixed.png`；命令日志 `/private/tmp/pa-replay-setup-{local,all,build}.log`。
- 独立只读评审检查 JSX 分组、CSS 覆盖关系和窄屏布局，无阻断问题；`git diff --check` 退出码 0。

## 2026-10-22 去掉回放图内标题毛玻璃

- 实际启动回放复现“ES · 1m · 回放”的白色光晕；根因为 chartTrading.css 中两层 `0 0 5px #fff` 的 text-shadow。删除阴影，标题颜色改为 #dbdbdb。
- `npm test -- --run src/replay/ReplayChartOverlay.test.tsx src/replay/ChartReplayTrading.test.tsx`：66 passed；`make build`：退出码 0（原有大包提示）。
- `make frontend-test`：402 passed / 2 failed。既有 `ChartDrawingTools.test.tsx > does not report a successful async import while loading would discard it` 缺少 importFile；另有 `TradingChart.indicators.test.tsx > uses only the current replay prefix, advances indicators and restores them on exit` 全量时未及时更新 MA 数据。
- 单独复验 `npm test -- --run src/TradingChart.indicators.test.tsx`：11 passed，指标问题在该次单独运行中未复现；未修改这两处测试或业务代码。
- 浏览器确认回放标题清晰且无白色光晕。截图 `/private/tmp/pa-replay-heading-fixed.png`；日志 `/private/tmp/pa-replay-heading-{tests,build,all,indicators}.log`。仅修改两行样式，未新增镜像样式测试，未运行后端测试。

## 2026-10-22 移除回放图内标题（用户纠正）

- 移除 ReplayChartOverlay 的整组品种/周期/回放标题及图标；删除相关 CSS 与 IndicatorLegend 的标题避让选择器。
- 相关 `ReplayChartOverlay`、`ChartReplayTrading`、`IndicatorLegend` Vitest：72 passed；`make build` 退出码 0；`git diff --check` 退出码 0。
- `make frontend-test`：403 passed / 1 failed，仍为既有 `ChartDrawingTools.test.tsx > does not report a successful async import while loading would discard it`；本轮回放指标测试通过。
- 当前页面确认图内标题整组消失，指标图例恢复顶部位置；截图 `/private/tmp/pa-replay-no-heading.png`。日志 `/private/tmp/pa-replay-no-heading-{tests,build,all}.log`。
