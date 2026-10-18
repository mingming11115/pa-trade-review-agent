# 同品种多周期与画线实施计划

**Goal:** 在现有 Lightweight Charts 上提供同品种 1m/5m/15m/1h 分屏、联动、画线和共用回放。

**Spec:** `docs/issues/multi-timeframe-chart-drawings/spec.md`

**Architecture:** 保留现有图表和交易覆盖层。新建多图协调组件；通过现有回放 bars API 获取固定游标的多周期批次。画线插件只负责图表交互，PA 负责共享模型和保存。

**Tech Stack:** React 19、TypeScript、Lightweight Charts 5、lightweight-charts-drawing 0.6.2、Vitest。

## 约束与评审重点

- 本次用户“实现”授权按已确认的 Lightweight Charts 路线执行，不再采用旧候选 KLineChart。
- 新代码先加行为测试并观察失败，再实现；局部验证后运行全量测试和构建。
- 四图必须共用截止时间；每次回放只提交一个命令。
- 登录用户、品种、回放场次的画线存储隔离；旧响应不能覆盖新批次。
- 缺失时间桶、不同周期跨度、画线跨周期锚点映射、绘图与交易拖动冲突、卸载监听清理为重点。

## 任务

### 1. 画线工具与持久化

- [x] 在 `frontend/src/chartWorkspace/` 添加画线存储、工具栏组件及样式。
- [x] 先测保存/恢复、共享修改、非法存储、回放游标与用户隔离；运行红灯。
- [x] 使用插件公开 API 接入，输出 `ChartDrawingTools`，由根图表提供 `ChartOverlayContext`、scope、period、active、cursor、disabled 和 onInteractionStart。
- [x] 运行局部测试，记录结果。

### 2. 跨周期联动

- [x] 在 `frontend/src/chartWorkspace/` 添加联动协调器及测试。
- [x] 先测时间桶映射、空缺行情、不同跨度和来源去重；运行红灯。
- [x] 提供基于现有 context、周期和 bars 的注册/更新/清理接口，支持十字线和时间锚点联动。
- [x] 运行局部测试，记录结果。

### 3. 多图布局与数据协调

- [x] 添加工作台组件和测试，默认 1m/5m/15m/1h，支持 1/2/4 图及活动图。
- [x] 扩展 `useReplaySession` 的可见周期集合和 `barsByPeriod`；对固定 cursor 去重请求、原子提交，保留旧批次并暂停失败播放。
- [x] 普通行情通过现有 market API 按共同截止时间补取不同周期，取消旧请求。
- [x] 接入主工作台、真实交易回放和图上交易；类型补齐已有响应字段，测试单会话与竞态。

### 4. 集成与验收

- [x] 在 `TradingChart` 中以可选入口接入画线、联动和独立指标设置；旧单图调用保持兼容。
- [x] 将保存版本与继续草稿的本地标注衔接起来，检查活动图的交易与绘图互斥。
- [x] 运行相关测试、`make test`、`make build`，浏览器检查实际画线、四图和窄屏。
- [x] 独立代码评审，修复重要问题；更新 README 和 `verification.md`，如实记录未完成验证。

## 进度记录

- 已核实 npm 版本 0.6.2、MIT 许可、Lightweight Charts v5 peer dependency；未改后端 API 或数据库。
- 四项实现任务及独立评审已完成。最新 `make test`：后端 428 passed、前端 371 passed（43 文件）；`make build` 通过。
- 实际浏览器完成桌面四图、500px 窄屏、活动周期切换、水平线绘制、跨周期共享与刷新恢复。其他工具的所有手势未逐项手动穷举，见 verification.md。

## 官方插件切换

用户最新要求仅使用官方插件。第三方包及其 demo 从运行代码移除，改接官方竖线 1.0.0；保留通用标注存储与旧版记录，原生拖动由插件处理。补齐官方构造／销毁、周期映射、缺桶、绘制删除、只读、拖动同步测试。文档旧路线已标记历史。

## 恢复完整功能（用户纠正）

- [x] 恢复第三方原版 demo UI、86 工具和图片持久化，旧标注可显示和编辑。
- [x] 官方竖线用于被动图；不删除回放、指标、重置及交易功能。
- [x] 新增完整工具组、旧多锚点标注、显隐删除、编辑器互斥、批量导入、忙碌与 Esc 回归检查。
- [x] 完成全量测试、构建和浏览器检查，更新最新验收。
