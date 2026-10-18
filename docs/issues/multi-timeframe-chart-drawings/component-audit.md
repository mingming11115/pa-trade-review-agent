# 图表组件来源核查

日期：2026-10-18。用户要求直接使用原始组件，并核查此前接入。

## 当前结论：完整功能恢复，按实际来源区分

此前卸载完整工具栏的做法误解了用户要求，现已撤回。当前同时保留官方图表／竖线插件，以及第三方原版绘图工具栏，不能称为全官方插件。

| 当前功能 | 实际来源 |
| --- | --- |
| K 线、坐标轴、十字线、缩放 | TradingView 官方 Lightweight Charts 5.2.0 |
| 完整 86 工具、分组 SVG、磁吸、连续绘制、编辑器、图片、导入导出 | 第三方 deepentropy/lightweight-charts-drawing 0.6.2 引擎及其原版 demo UI |
| 非活动图的只读竖线 | TradingView 官方 VerticalLine 1.0.0；无重复竖线叠加 |
| 标注存储、跨周期作用域、回放修订、图片持久化 | PA 数据适配层 |
| 指标面板、重置、多图协调、回放、订单和账户 | 保留 PA 业务组件；没有对应官方成品插件 |

旧非竖线标注重新显示，不再只提示存档。工具栏原始六文件及完整适配代码已恢复，图片资产与标注历史没有删除。优先官方的要求不授权删减任何既有功能。

以下为原版 UI 的详细来源核查。其中“上游官方 demo”指第三方作者发布的 demo，不是 TradingView 官方插件。

## 原版 UI 来源核查

此前只有图表和画线引擎直接使用原库；我编写的六工具工具栏、字符图标和属性表单不是原始 UI，不能称为完整 demo 接入。现已删除这层简化 UI，改用官方仓库 0.6.2 同版本的原版 demo UI 源码。

| 功能 | 实际来源 | 本次处理 |
| --- | --- | --- |
| K 线、价格轴、时间轴、十字线、缩放 | npm `lightweight-charts` 的 `createChart`、`CandlestickSeries` | 直接保留原库，PA 配置行情和显示选项 |
| 画线渲染、锚点、命中和拖动 | npm `lightweight-charts-drawing` 的 `DrawingManager` | 直接保留原库，供给各周期的真实 OHLCV |
| 原先六工具工具栏、字符图标、属性表单 | PA `ChartDrawingTools.tsx` / CSS 自制 | 已删除，不再作为 UI 提供 |
| 画线 SVG、86 工具分组、弹出菜单、磁吸、连续绘制、显隐、清除 | 官方 `demo/icons.ts`、`tool-groups.ts`、`demo.ts`、`demo.css` | 直接复用原版 demo 源码，使用 Shadow DOM 隔离全局样式 |
| 选择栏、锚定、隐藏、删除、文字／表格编辑、图片、JSON 导入导出 | 同一官方 demo | 保留原版交互，适配监听清理、交易互斥和图片持久化 |
| MA/EMA/BOLL/MACD/RSI/VOL | PA `indicators.ts` 计算；LWC 原生 Line/Histogram series 渲染 | 不是原库自带指标套件，保留既有计算口径 |
| 指标选择、参数设置和图例 | PA `IndicatorPanel.tsx` / `IndicatorLegend.tsx` | 自有 UI；所选两个原库均未提供可替换的指标面板 |
| Gap、K#、H/L | PA `PaioPrimitive.ts` / `paio.ts` | 自有分析叠加，不是第三方原始组件 |
| 单／双／四图布局与跨周期同步 | PA `ChartWorkspace.tsx` / `chartSync.ts` 调用 LWC 公共 API | 自有协调层，所选原库未提供成品多图工作台 |
| 回放时间、保存／继续、订单、BUY/SELL、TP/SL、账户 | PA replay 目录及后端回放引擎 | PA 业务组件，不属于这两个开源图表库的原始 UI |
| 用户／品种／场次隔离、标注历史修订、图片字节保存 | PA `chartDrawings.ts` / `drawingImages.ts` | 必需的数据适配层，不冒充原库组件 |

因此不能声称整个 PA 图表区域“全是第三方原始组件”。有上游成品的画线 UI 已使用原版；上游没有的指标面板、多图和交易业务组件保留 PA 实现，明确来源。未擅自迁移到另一个图表库或删除业务功能。

## 可核对的来源

官方仓库：<https://github.com/deepentropy/lightweight-charts-drawing>

固定提交：`0291d9c7373e3795373c915a3a64f4f663b5a0db`。上游 package version 为 `0.6.2`，与已安装包一致。

`frontend/src/chartWorkspace/upstream/` 中的 `demo.ts.txt`、`demo.css`、`icons.ts`、`tool-groups.ts`、`index.html`、`LICENSE` 六份文件与该提交逐字节相同；适配范围记录在同目录 README.md。独立只读复审核实了提交、版本和六份文件的一致性。

原库 README 明确指出 toolbar、settings dialogs、text/table editors 和 storage 由宿主提供，所以 npm 包中没有可直接 import 的原始 UI 组件。这里复用的是官方发布 demo 的源代码，不是重画一个相似外观。
