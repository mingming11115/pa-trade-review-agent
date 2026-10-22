# 前端工作台重设计 Implementation Plan

> **For agentic workers:** 使用 superpowers:executing-plans 在当前会话逐项实施；未经用户选择不派生子代理。

**Goal:** 将用户认可的浅色工作台预览应用到正式 PA 前端，并保留真实业务交互。

**Architecture:** 沿用现有 React 状态、API 和路由。收敛 CSS 为统一视觉规范，修改工作台与导航的展示层；图表继续使用 lightweight-charts。不新增生产依赖，不全面重构业务逻辑。

**Tech Stack:** React 19、TypeScript、CSS、lightweight-charts、Vitest、Vite。

**Spec:** [spec.md](./spec.md)

## Global Constraints

- 保留现有路由、API、数据契约、任务执行与取消机制、实时行情与历史回放、图表交互。
- 不新增业务功能，不改后端，不引入主题切换。
- 本期不新增生产依赖。
- 保护当前工作区已有修改；不以 Git HEAD 覆盖 App.tsx 或测试文件，不提交无关文件。
- 正式页面不得使用预览中的虚构价格、分析或运行状态。

## Review Focus

- 无行情或未完成分析时，摘要不应显示假价格与结构：用现有空状态测试和浏览器验证。
- 长任务名称与长分析文本，不能遮挡按钮：浏览器检查导航及结果换行。
- 窄屏与弹窗长内容，不能导致页面横向溢出：检查 1440px、1024px、390px。
- 折叠导航和结果标签页，键盘操作及名称继续有效：运行 SidebarNav、App、MoreMenu、useDialogFocus 测试。
- 正在运行、失败及历史回放状态，原有取消与数据更新规则继续有效：运行 App 和 analysisTasks 回归测试。

---

### Task 1: 工作台布局与导航

**Files:** 修改 frontend/src/App.tsx、SidebarNav.tsx、styles.css；验证 App.test.tsx、SidebarNav.test.tsx。

**Interfaces:** 保留 ReviewWorkbenchShell 和 SidebarNav 现有 props、任务回调及状态来源；仅调整 JSX 容器、呈现与样式。

- [x] 保存当前 diff 基线，运行 `cd frontend && npm test -- --run src/SidebarNav.test.tsx src/App.test.tsx`，记录基线。
- [x] 将顶部应用栏、导航与页面标题调整为预览风格；统一任务层级与蓝色选中状态。
- [x] 默认图表/结果使用现有 56/44 分配，保留分隔条和折叠行为；不新增假指标卡。
- [x] 修改会影响可观察交互时，先添加对应行为测试并确认失败；纯视觉变化以浏览器验证，不添加镜像样式断言。
- [x] 重跑上述组件测试并检查无行情、已有任务、运行记录场景。

### Task 2: 组件视觉与分析阅读体验

**Files:** 修改 frontend/src/styles.css、App.tsx；按需修改 MoreMenu.tsx、TradingChart.tsx、DecisionFlowViz.tsx 的视觉配置。

**Interfaces:** 保留 TradingChart、DecisionFlowViz、MoreMenu 的公开接口；沿用已有结论与证据来源，不扩大推理展示范围。

- [x] 统一冷灰背景、白色面板、蓝色主操作、字体、间距、边框、圆角；收敛重复样式，去除渐变及玻璃效果。
- [x] 统一输入框、按钮、标签页、表格、弹窗和通知；状态仍以真实数据和文字表达。
- [x] 将已有结论突出为摘要，保留实时、逐根分析、决策图视图与追问操作。
- [x] 图表配色与容器适配统一；不更改行情或图表事件逻辑。
- [x] 运行 `cd frontend && npm test -- --run src/MoreMenu.test.tsx src/DecisionFlowViz.test.tsx src/useDialogFocus.test.tsx src/App.test.tsx`。

### Task 3: 响应式、验收与交付

**Files:** 修改 styles.css；创建 docs/issues/frontend-workbench-redesign/verification.md。

**Interfaces:** 桌面三栏、紧凑宽度自适应、窄屏图表先于结果；弹窗与表格内部滚动。

- [x] 完成 1024–1279px 紧凑布局和小于 1024px 的纵向布局；提供可见焦点及 reduced-motion 支持。
- [x] 浏览器检查 1440px、1024px、390px，记录截图或检查证据；确认按钮可操作、长文本可读、页面无横向溢出。
- [x] 运行 `make frontend-test`、`make test`、`make build`；记录最新输出，已有失败按项目诊断流程处理。
- [x] 检查最终 diff 的作用域、真实数据、公共接口和已有改动保护情况，修正发现的问题。
- [x] 更新计划勾选与 verification.md，报告实际验证范围及剩余限制。

## 执行方式

推荐在当前会话由本代理实施：各项共享同一套样式和工作台结构，不需要独立子代理。用户已批准按计划在当前会话直接实施。所有代码与自动验证步骤已完成；真实后端数据的视觉验收限制见 verification.md。

## 本轮入口调整（用户已批准）

- [x] 将分析新增按钮移到“K线分析”标题旁，与复盘一致，清理旧按钮专用样式。
- [x] 运行局部测试、全量测试及构建，记录结果。

## 2026-10-20 悬浮任务导航（用户已确认）

- [x] 先增加默认收起、点击展开、内部点击保持、外部点击及 Esc 收起的行为测试，确认新增测试失败后实施。
- [x] 移除导航的网格占位与随模式、悬停展开逻辑；保留任务及交易记录回调。
- [x] 回放中点击悬浮入口不触发退出确认，增加回归断言并确认先失败后通过。
- [x] 原任务测试先展开导航，局部 45 项通过；执行全量测试与构建。
- [x] 检查 1440px、1024px、390px 视口，记录导航不挤压图表、无横向溢出及收起行为。
- [x] 验收记录已说明全量测试剩余的画线导入测试失败，本轮未修改画线代码。

## 2026-10-21 右侧分析面板抽拉（用户已确认）

- [x] 添加开关、关闭和标签保留测试，先确认失败。
- [x] 实现默认收起的右侧抽拉面板与响应式布局，修正宽屏拖动宽度跳变。
- [x] 执行局部、全量测试与构建，检查界面并记录证据；保留既有画线测试失败。
