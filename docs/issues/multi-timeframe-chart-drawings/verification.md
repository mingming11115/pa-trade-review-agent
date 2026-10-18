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
