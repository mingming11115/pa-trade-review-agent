import { ChangeEvent, CSSProperties, FormEvent, KeyboardEvent, PointerEvent, ReactNode, useEffect, useRef, useState } from "react";

import { analyzeRangeStream, cancelRun, confirmTradeImport, createAnalysisTask, createTrade, deleteTrade, ensureLiveAnalysisTask, getAdminOrchestration, getAdminPromptFile, getAlerts, getAlertRules, getAnalysisDetail, getAnalysisHistory, getCollectionStatus, getCurrentUser, getDebugPreview, getFollowupHistory, getHealth, getMarketBars, getPersonalSettings, getPromptVersions, getRecentTrades, getRunDetail, getTokenUsage, getTradeImports, listAnalysisTaskRuns, listAnalysisTasks, login, logout, markAlertRead, previewTradeImport, rollbackPromptVersion, saveAdminPromptFile, saveAlertRule, savePersonalSettings, sendFollowupStream, startAnalysisTaskRun, updateAnalysisTask, updateTrade, waitForRunsTerminal } from "./api";
import { TradingChart } from "./TradingChart";
import { DecisionFlowViz } from "./DecisionFlowViz";
import { MoreMenu } from "./MoreMenu";
import { useDialogFocus } from "./useDialogFocus";
import { useTypewriterText } from "./useTypewriterText";
import { barSummaryDisplay, formatBarRange } from "./barDisplay";
import { ANALYSIS_LOOKBACK_BARS, chartIdentity, closedBars, lastClosedTs, liveLookbackWindow, LIVE_POLL_MS, mergeBars, PERIOD_MINUTES } from "./feed";
import type {
  ApiError,
  Bar,
  BasicAnalysis,
  DemoAnalysisResponse,
  FeedState,
  HealthResponse,
  HistoricalQuery,
  Period,
  TradeMarker,
  TradePreview,
  Trade,
  DebugPreview,
  PersonalSettings,
  TokenUsageSummary,
  OrchestrationView,
  PromptFileDocument,
  PipelineStage,
  Stage1Result,
  Stage2Result,
  TradeReviewResult,
  AnalysisAudit,
  AnalysisHistorySummary,
  CollectionStatus,
  AlertRule,
  AlertRecord,
  UserSession,
  PromptVersion,
  TradeImportBatch,
  AnalysisStreamEvent,
  FollowupMessage,
  AnalysisTask,
  AnalysisRunListItem,
} from "./types";
import "./styles.css";
import { findLiveAnalysisTask, normalizeAnalysisSymbol, sidebarTaskFromApi } from "./analysisTasks";
import { SidebarNav, type SidebarLeafId, type SidebarNavAnalysisRun } from "./SidebarNav";

const emptyFeed = (kind: FeedState["kind"] = "history"): FeedState => ({
  kind,
  symbol: "",
  period: "",
  bars: [],
  lastClosedTs: null,
  pollError: null,
});

const CHART_TIMEZONE = { offsetMinutes: -300, label: "UTC-5" } as const;

type Mode = "historical" | "live" | "range";
type WorkbenchMode = "review" | "analysis";
type StageStatus = "done" | "running" | "wait" | "failed" | "skipped";
type DecisionView = "realtime" | "bars" | "tree";

const defaultComposer = (type: WorkbenchMode): TaskComposer => ({
  type,
  title: type === "review" ? "未命名复盘任务" : "未命名分析任务",
  description: type === "review" ? "先选择交易，再保存复盘任务。" : "先配置行情区间或实时策略，再执行分析。",
  symbol: type === "review" ? "ES" : "NQ",
  period: type === "review" ? "5m" : "1m",
  start: "",
  end: "",
  selectedTradeIds: [],
  selectedTradeSymbol: "ES",
  includeOrders: true,
  overlayOrders: true,
  analysisMode: "range",
  streamEnabled: false,
});

const createSidebarTask = (composer: TaskComposer): SidebarTask => {
  const taskId = createId(composer.type);
  return {
    id: taskId,
    type: composer.type,
    title: composer.title.trim() || (composer.type === "review" ? "未命名复盘任务" : "未命名分析任务"),
    description: composer.type === "review" ? "交易复盘任务" : "K 线分析任务",
    createdAt: new Date().toISOString(),
    locked: true,
    version: 1,
    config: composer,
    summary: composer.type === "review"
      ? [
          { label: "方式", value: "逐笔交易" },
          { label: "周期", value: composer.period || "自动" },
          { label: "交易", value: composer.selectedTradeIds.length ? `${composer.selectedTradeIds.length} 笔` : "0 笔" },
        ]
      : [
          { label: "类型", value: "最新行情" },
          { label: "标的", value: composer.symbol },
          { label: "周期", value: composer.period || "自动" },
        ],
  };
};

const decisionViews: Array<{ id: DecisionView; label: string }> = [
  { id: "realtime", label: "实时" },
  { id: "bars", label: "逐根分析" },
  { id: "tree", label: "决策图" },
];

const ROLE_LABELS: Record<string, string> = {
  structure: "结构",
  signal: "信号",
  entry: "入场",
  confirmation: "确认",
  noise: "噪音",
  trap: "陷阱",
  climax: "高潮",
  test: "测试",
};

const CONTEXT_LABELS: Record<string, string> = {
  strengthens_bull: "强化多头",
  weakens_bull: "削弱多头",
  strengthens_bear: "强化空头",
  weakens_bear: "削弱空头",
  weakened_bull: "削弱多头",
  weakened_bear: "削弱空头",
  neutral: "中性",
  transition: "转换",
};

function barSummaryText(item: { summary?: string; reason?: string }): string {
  return item.summary || item.reason || "无解析";
}

const symbols = [
  ["ES", "ES · 标普 500 E-mini"],
  ["NQ", "NQ · 纳斯达克 100 E-mini"],
] as const;

const periods: Period[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

const providerModels: Record<Exclude<PersonalSettings["models"][number]["provider"], "compatible">, string[]> = {
  openai: ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini"],
  anthropic: ["claude-opus-4-1", "claude-sonnet-4", "claude-3-7-sonnet-latest"],
  gemini: ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
  deepseek: ["deepseek-v4-flash", "deepseek-v4-pro"],
};

/** Backend alert rules still require a threshold; UI no longer exposes it. */
const DEFAULT_ALERT_THRESHOLD = 0.8;

const defaultSessions: Record<Mode, SessionState> = {
  historical: {
    symbol: "",
    period: "",
    start: "",
    end: "",
    includeOrders: true,
    overlayOrders: true,
    streamEnabled: false,
  },
  live: {
    symbol: "ES",
    period: "5m",
    start: "",
    end: "",
    includeOrders: false,
    overlayOrders: false,
    streamEnabled: true,
  },
  range: {
    symbol: "",
    period: "",
    start: "",
    end: "",
    includeOrders: false,
    overlayOrders: false,
    streamEnabled: false,
  },
};

function getWorkbenchRoute(pathname: string): { workbenchMode: WorkbenchMode; mode: Mode; pathname: string } {
  const cleaned = pathname.replace(/\/+$/, "") || "/";
  if (cleaned === "/review" || cleaned.startsWith("/review/")) {
    return { workbenchMode: "review", mode: "historical", pathname: "/review" };
  }

  if (cleaned === "/analysis" || cleaned === "/analysis/live" || cleaned === "/") {
    return { workbenchMode: "analysis", mode: "live", pathname: "/analysis/live" };
  }

  if (cleaned === "/analysis/range") {
    return { workbenchMode: "analysis", mode: "range", pathname: "/analysis/range" };
  }

  if (cleaned === "/analysis/historical") {
    return { workbenchMode: "analysis", mode: "historical", pathname: "/analysis/historical" };
  }

  return { workbenchMode: "analysis", mode: "live", pathname: "/analysis/live" };
}

function buildWorkbenchPath(workbenchMode: WorkbenchMode, mode: Mode): string {
  if (workbenchMode === "review") return "/review";
  if (mode === "range") return "/analysis/range";
  if (mode === "historical") return "/analysis/historical";
  return "/analysis/live";
}

function shouldAutoCollapseLeftSidebar(workbenchMode: WorkbenchMode, mode: Mode): boolean {
  return workbenchMode === "analysis" && mode === "historical";
}

interface SessionState {
  symbol: string;
  period: Period | "";
  start: string;
  end: string;
  includeOrders: boolean;
  overlayOrders: boolean;
  streamEnabled: boolean;
}

interface WorkbenchData {
  resolvedSymbol: string;
  analysis: BasicAnalysis;
  bars: Bar[];
  tradeMarkers: TradeMarker[];
  sourceLabel: string;
  detailLabel: string;
  stages: StageEntry[];
  notes: string[];
  stage1?: Stage1Result;
  stage2?: Stage2Result;
  runId?: string;
  reviewResult?: TradeReviewResult[] | null;
  audit?: AnalysisAudit;
}

interface StageEntry {
  title: string;
  status: StageStatus;
  detail: string;
}

interface SidebarTask {
  id: string;
  type: WorkbenchMode;
  title: string;
  description: string;
  createdAt: string;
  locked: true;
  config: TaskComposer;
  summary: Array<{ label: string; value: string }>;
  status?: AnalysisTask["status"];
  version: number;
}

interface TaskDetailSelection {
  task: SidebarTask;
  run: SidebarNavAnalysisRun | null;
}

interface TaskComposer {
  type: WorkbenchMode;
  title: string;
  description?: string;
  symbol: string;
  period: Period | "";
  start: string;
  end: string;
  selectedTradeIds: string[];
  selectedTradeSymbol: string;
  includeOrders: boolean;
  overlayOrders: boolean;
  analysisMode: "range" | "live";
  streamEnabled: boolean;
}

function toUtcIso(value: string): string {
  // datetime-local 没有时区信息；本工作台约定用户输入均为北京时间。
  const beijingValue = `${value}${value.length === 16 ? ":00" : ""}+08:00`;
  return new Date(beijingValue).toISOString();
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(value);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

/** 将 UTC 时间转为北京时间的 datetime-local 值（与 toUtcIso 约定一致）。 */
function toBeijingDateTimeLocal(value: string | number): string {
  const shifted = new Date(new Date(value).getTime() + 8 * 60 * 60 * 1000);
  return shifted.toISOString().slice(0, 16);
}

function deriveDateTimeRange(trades: Trade[]): { start: string; end: string } {
  if (!trades.length) return { start: "", end: "" };
  const startMs = Math.min(...trades.map((trade) => Date.parse(trade.entered_at)));
  const endMs = Math.max(...trades.map((trade) => Date.parse(trade.exited_at)));
  return { start: toBeijingDateTimeLocal(startMs), end: toBeijingDateTimeLocal(endMs) };
}

function createId(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`;
}

function stageBasisLabel(stage: PipelineStage): string {
  if (stage.kind === "llm") return "LLM 推理";
  if (stage.kind === "gate") return "代码/规则";
  if (stage.kind === "review") return "后处理/审查";
  return "确定性代码";
}

function buildNodeBasis(stage: PipelineStage): string[] {
  const basis = [`执行类型：${stageBasisLabel(stage)}`, `节点 ID：${stage.id}`];
  if (stage.description) basis.push(`节点说明：${stage.description}`);
  if (stage.prompt_files.length) {
    basis.push(
      ...stage.prompt_files.flatMap((file) => [
        `提示词：${file.filename}`,
        `加载位置：${file.placement}`,
        `触发条件：${file.condition}`,
        `是否可编辑：${file.editable ? "是" : "否"}`,
      ]),
    );
  } else {
    basis.push("该节点不加载提示词，依据来自代码或规则逻辑");
  }
  return basis;
}

function buildNotes(source: string, bars: Bar[]): string[] {
  return [
    `${source} 已标准化导入 ${bars.length} 根 K 线`,
    "左侧控制区会保留当前模式参数",
    "切换模式后可继续使用对应的会话快照",
  ];
}

function buildStages(mode: Mode, loading: boolean, hasData: boolean, offline: boolean): StageEntry[] {
  const commonFinished: StageStatus = hasData ? "done" : loading ? "running" : "wait";
  const secondStage: StageStatus = offline ? "failed" : hasData ? "done" : loading ? "running" : "wait";
  const finalStage: StageStatus = offline ? "failed" : hasData ? "done" : loading ? "running" : "wait";
  const historicalStageTwo = mode === "historical" ? secondStage : hasData ? "done" : loading ? "running" : "wait";

  if (mode === "historical") {
    return [
      { title: "阶段一 · 数据复盘", status: commonFinished, detail: "加载快照、行情和订单窗口" },
      { title: "阶段二 · 结构识别", status: historicalStageTwo, detail: "识别趋势、反转、突破和失败信号" },
      { title: "阶段三 · 行动建议", status: finalStage, detail: "输出复盘结论与下一步关注点" },
    ];
  }

  if (mode === "range") {
    return [];
  }

  return [
    { title: "阶段一 · 实时订阅", status: commonFinished, detail: "连接数据流并记录最新 K 线" },
    { title: "阶段二 · 触发判断", status: secondStage, detail: "按收盘或定时策略触发分析" },
    { title: "阶段三 · 告警输出", status: finalStage, detail: "展示告警与操作建议" },
  ];
}

function buildStats(mode: Mode, session: SessionState, data: WorkbenchData | null, health: HealthResponse | null) {
  if (mode === "historical") {
    return [
      { label: "会话", value: "历史复盘" },
      { label: "窗口", value: session.start && session.end ? `${session.start} → ${session.end}` : "待选交易" },
      { label: "K 线", value: data ? String(data.analysis.bar_count) : "0" },
      { label: "方向", value: data ? data.analysis.direction : "待分析" },
    ];
  }

  if (mode === "range") {
    return [
      { label: "会话", value: "时间选段" },
      { label: "窗口", value: `${session.start} → ${session.end}` },
      { label: "刷选", value: "可回填" },
      { label: "K 线", value: data ? String(data.analysis.bar_count) : "0" },
      { label: "状态", value: data ? data.analysis.direction : "待分析" },
    ];
  }

  return [
    { label: "会话", value: "实时分析" },
    { label: "流开关", value: session.streamEnabled ? "开启" : "暂停" },
    { label: "近期振幅", value: data ? `${formatNumber(data.analysis.change_percent)}%` : "—" },
    { label: "告警", value: data ? String(Math.max(1, Math.min(9, data.analysis.bullish_bars + data.analysis.bearish_bars))) : "0" },
  ];
}

function toWorkbenchData(result: DemoAnalysisResponse, mode: Mode, loading = false, offline = false): WorkbenchData {
  const tradeMarkers = result.trade_markers ?? [];

  return {
    resolvedSymbol: result.resolved_symbol,
    analysis: result.analysis,
    bars: result.bars,
    tradeMarkers,
    sourceLabel: `${mode === "historical" ? "复盘" : mode === "live" ? "实时" : "区间"} · ${result.resolved_symbol}`,
    detailLabel: `${result.analysis.bar_count} 根 K 线 · ${result.analysis.method}`,
    stages: buildStages(mode, loading, true, offline),
    notes: result.stage2?.terminal.reason
      ? [result.stage2.terminal.reason, ...buildNotes(result.resolved_symbol, result.bars)]
      : buildNotes(result.resolved_symbol, result.bars),
    stage1: result.stage1,
    stage2: result.stage2,
    runId: result.run_id,
    reviewResult: result.review_result,
    audit: result.audit,
  };
}

export function ReviewWorkbenchShell() {
  const initialRoute = getWorkbenchRoute(window.location.pathname);
  const [mode, setMode] = useState<Mode>(initialRoute.mode);
  const [workbenchMode, setWorkbenchMode] = useState<WorkbenchMode>(initialRoute.workbenchMode);
  const [sessions, setSessions] = useState<Record<Mode, SessionState>>(defaultSessions);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [results, setResults] = useState<Record<Mode, WorkbenchData | null>>({
    historical: null,
    live: null,
    range: null,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);
  const [reviewNotice, setReviewNotice] = useState<string | null>(null);
  const [pendingTradeFile, setPendingTradeFile] = useState<File | null>(null);
  const [tradePreview, setTradePreview] = useState<TradePreview | null>(null);
  const [availableTrades, setAvailableTrades] = useState<Trade[]>([]);
  const [selectedTradeIds, setSelectedTradeIds] = useState<string[]>([]);
  const [orderPickerOpen, setOrderPickerOpen] = useState(false);
  const [debugPreview, setDebugPreview] = useState<DebugPreview | null>(null);
  const [personalOpen, setPersonalOpen] = useState(false);
  const [personalSettings, setPersonalSettings] = useState<PersonalSettings | null>(null);
  const [tokenUsage, setTokenUsage] = useState<TokenUsageSummary | null>(null);
  const [personalTab, setPersonalTab] = useState<"models" | "usage" | "trades">("models");
  const [adminOpen, setAdminOpen] = useState(false);
  const [orchestration, setOrchestration] = useState<OrchestrationView | null>(null);
  const [promptDocument, setPromptDocument] = useState<PromptFileDocument | null>(null);
  const [adminNotice, setAdminNotice] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [analysisHistory, setAnalysisHistory] = useState<AnalysisHistorySummary[]>([]);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);

  const [collectionStatus, setCollectionStatus] = useState<CollectionStatus[]>([]);
  const [tasksByType, setTasksByType] = useState<Record<WorkbenchMode, SidebarTask[]>>({ review: [], analysis: [] });
  const [taskComposer, setTaskComposer] = useState<TaskComposer | null>(null);
  const [editingTask, setEditingTask] = useState<SidebarTask | null>(null);
  const [taskDetails, setTaskDetails] = useState<TaskDetailSelection | null>(null);
  const [copiedRunId, setCopiedRunId] = useState<string | null>(null);
  const [journalOpen, setJournalOpen] = useState(false);
  const [journalQuery, setJournalQuery] = useState("");
  const [alertRules, setAlertRules] = useState<AlertRule[]>([]);
  const [alerts, setAlerts] = useState<AlertRecord[]>([]);
  const [user, setUser] = useState<UserSession | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);
  const [loginName, setLoginName] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [promptVersions, setPromptVersions] = useState<PromptVersion[]>([]);
  const [tradeImports, setTradeImports] = useState<TradeImportBatch[]>([]);
  const [decisionView, setDecisionView] = useState<DecisionView>("realtime");
  const [rightPanelPercent, setRightPanelPercent] = useState(44);
  const [streamEvents, setStreamEvents] = useState<AnalysisStreamEvent[]>([]);
  const [llmLive, setLlmLive] = useState<{
    stage1: { reasoning: string; content: string };
    stage2: { reasoning: string; content: string };
  }>({ stage1: { reasoning: "", content: "" }, stage2: { reasoning: "", content: "" } });
  const [historyQuery, setHistoryQuery] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState<Record<WorkbenchMode, boolean>>({ review: true, analysis: true });
  const [activeSidebarLeaf, setActiveSidebarLeaf] = useState<SidebarLeafId | null>(null);
  const [leftSidebarAutoCollapsed, setLeftSidebarAutoCollapsed] = useState(false);
  const [leftSidebarManualOverride, setLeftSidebarManualOverride] = useState<boolean | null>(null);
  const [leftSidebarHoverExpanded, setLeftSidebarHoverExpanded] = useState(false);
  const [expandedAnalysisTasks, setExpandedAnalysisTasks] = useState<Record<string, boolean>>({});
  const [analysisRunsByTask, setAnalysisRunsByTask] = useState<Record<string, SidebarNavAnalysisRun[]>>({});
  const effectiveLeftCollapsed = leftSidebarManualOverride !== null
    ? leftSidebarManualOverride
    : (leftSidebarAutoCollapsed && !leftSidebarHoverExpanded);
  const [feed, setFeed] = useState<FeedState>(() => emptyFeed("history"));
  const [snapshotAsOf, setSnapshotAsOf] = useState<string | null>(null);
  /** Opened from analysis history: freeze chart at analysis tip and enable replay. */
  const [historyReplayActive, setHistoryReplayActive] = useState(false);
  const typewriterActive = loading && !historyReplayActive;
  const llmDisplayed = {
    stage1: {
      reasoning: useTypewriterText(llmLive.stage1.reasoning, typewriterActive),
      content: useTypewriterText(llmLive.stage1.content, typewriterActive),
    },
    stage2: {
      reasoning: useTypewriterText(llmLive.stage2.reasoning, typewriterActive),
      content: useTypewriterText(llmLive.stage2.content, typewriterActive),
    },
  };
  const [followupByAnalysis, setFollowupByAnalysis] = useState<Record<string, FollowupMessage[]>>({});
  const [followupDraft, setFollowupDraft] = useState("");
  const [followupSending, setFollowupSending] = useState(false);
  const debugResolver = useRef<((confirmed: boolean) => void) | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const analysisAbortRef = useRef<AbortController | null>(null);
  const followupAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => {
    analysisAbortRef.current?.abort();
    followupAbortRef.current?.abort();
  }, []);
  const centerPanelRef = useRef<HTMLElement>(null);
  const rightPanelRef = useRef<HTMLElement>(null);
  const decisionTabRefs = useRef<Partial<Record<DecisionView, HTMLButtonElement | null>>>({});
  const taskDialogRef = useDialogFocus({ open: Boolean(taskComposer), onDismiss: () => { setTaskComposer(null); setEditingTask(null); } });
  const taskDetailsDialogRef = useDialogFocus({ open: Boolean(taskDetails), onDismiss: () => setTaskDetails(null) });
  const historyDialogRef = useDialogFocus({ open: historyOpen, onDismiss: () => setHistoryOpen(false) });
  const journalDialogRef = useDialogFocus({ open: journalOpen, onDismiss: () => setJournalOpen(false) });
  const orderPickerDialogRef = useDialogFocus({ open: orderPickerOpen, onDismiss: () => setOrderPickerOpen(false) });
  const tradePreviewDialogRef = useDialogFocus({ open: Boolean(tradePreview), onDismiss: () => { setPendingTradeFile(null); setTradePreview(null); } });
  const personalDialogRef = useDialogFocus({ open: personalOpen, onDismiss: () => setPersonalOpen(false) });
  const adminDialogRef = useDialogFocus({ open: adminOpen, onDismiss: () => setAdminOpen(false) });
  const loginDialogRef = useDialogFocus({ open: loginOpen, dismissible: false });
  const debugDialogRef = useDialogFocus({ open: Boolean(debugPreview), dismissible: false });
  const splitDraggingRef = useRef(false);

  useEffect(() => {
    const syncFromLocation = () => {
      const route = getWorkbenchRoute(window.location.pathname);
      setWorkbenchMode(route.workbenchMode);
      setMode(route.mode);
    };

    syncFromLocation();
    window.addEventListener("popstate", syncFromLocation);
    return () => window.removeEventListener("popstate", syncFromLocation);
  }, []);

  useEffect(() => {
    const nextPath = buildWorkbenchPath(workbenchMode, mode);
    if (window.location.pathname !== nextPath) {
      window.history.replaceState({}, "", nextPath);
    }
  }, [mode, workbenchMode]);

  useEffect(() => {
    const autoCollapsed = shouldAutoCollapseLeftSidebar(workbenchMode, mode);
    setLeftSidebarAutoCollapsed(autoCollapsed);
    if (!autoCollapsed) {
      setLeftSidebarHoverExpanded(false);
    }
  }, [mode, workbenchMode]);

  useEffect(() => {
    if (!effectiveLeftCollapsed) {
      setLeftSidebarHoverExpanded(false);
    }
  }, [effectiveLeftCollapsed]);

  function syncLeftSidebarAutoCollapse(nextCollapsed: boolean) {
    setLeftSidebarAutoCollapsed(nextCollapsed);
    if (leftSidebarManualOverride !== null) return;
    if (!nextCollapsed) {
      setLeftSidebarManualOverride(null);
      setLeftSidebarHoverExpanded(false);
    }
  }

  function setLeftSidebarManualCollapsed(nextCollapsed: boolean) {
    setLeftSidebarManualOverride(nextCollapsed);
    setLeftSidebarHoverExpanded(false);
  }

  function handleLeftSidebarMouseEnter() {
    if (!leftSidebarAutoCollapsed || leftSidebarManualOverride !== null) return;
    setLeftSidebarHoverExpanded(true);
  }

  function handleLeftSidebarMouseLeave() {
    if (!leftSidebarAutoCollapsed || leftSidebarManualOverride !== null) return;
    setLeftSidebarHoverExpanded(false);
  }

  function handleLeftSidebarToggle() {
    setLeftSidebarManualCollapsed(!effectiveLeftCollapsed);
  }

  useEffect(() => {
    getHealth().then((status) => { setHealth(status); if (status.auth_required) void getCurrentUser().then(setUser).catch(() => setLoginOpen(true)); else setUser({ id: null, username: "local", role: "admin", auth_required: false }); }).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    if (!health) return;
    void listAnalysisTasks().then((page) => {
      const restored = page.items.map(sidebarTaskFromApi) as SidebarTask[];
      setTasksByType({ review: restored.filter((task) => task.type === "review"), analysis: restored.filter((task) => task.type === "analysis") });
    }).catch(() => undefined);
  }, [health]);


  useEffect(() => {
    if (workbenchMode !== "analysis" || mode !== "live") return;
    const refresh = () => Promise.all([getCollectionStatus(), getAlertRules(), getAlerts()]).then(([status, rules, records]) => { setCollectionStatus(status); setAlertRules(rules); setAlerts(records); }).catch(() => undefined);
    void refresh(); const timer = window.setInterval(refresh, 60_000); return () => window.clearInterval(timer);
  }, [workbenchMode, mode]);

  const session = sessions[mode];
  const feedKind: FeedState["kind"] =
    workbenchMode === "review" || mode !== "live" || historyReplayActive ? "history" : "live";
  const feedPeriod = session.period || "";

  useEffect(() => {
    if (!session.symbol || !session.period) return;
    const period = session.period as Period;

    const controller = new AbortController();
    let cancelled = false;
    let timer = 0;

    async function pull(kind: FeedState["kind"], merge: boolean) {
      try {
        const range = kind === "live"
          ? liveLookbackWindow(period)
          : { start: toUtcIso(session.start), end: toUtcIso(session.end) };
        if (kind === "history" && (!session.start || !session.end)) return;
        const market = await getMarketBars(session.symbol, period, range.start, range.end, controller.signal, kind === "live", kind === "live" ? "live_poll" : "history_range");
        if (cancelled) return;
        setFeed((current) => {
          const bars = merge ? mergeBars(current.bars, market.bars) : market.bars;
          return {
            kind,
            symbol: session.symbol,
            period: session.period,
            bars,
            lastClosedTs: lastClosedTs(bars),
            pollError: null,
          };
        });
      } catch (caught) {
        if (cancelled || (caught instanceof DOMException && caught.name === "AbortError")) return;
        const message = (caught as ApiError)?.message ?? "行情刷新失败";
        setFeed((current) => ({
          ...current,
          kind,
          symbol: session.symbol,
          period: session.period,
          pollError: message,
        }));
      }
    }

    void pull(feedKind, false);
    if (feedKind === "live") {
      timer = window.setInterval(() => { void pull("live", true); }, LIVE_POLL_MS);
    }

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) window.clearInterval(timer);
    };
  }, [feedKind, feedPeriod, historyReplayActive, mode, session.end, session.period, session.start, session.symbol, workbenchMode]);

  const currentResult = results[mode];
  const unavailable = health === null;
  const activeData = currentResult;
  const chartBars = feed.bars.length
    ? feed.bars
    : activeData?.bars ?? [];
  const emptyStateVisible = chartBars.length === 0 && !activeData;
  const chartTimezone = CHART_TIMEZONE;
  const formatEvidence = (text: string) => text;
  const chartLocked = mode !== "live" || historyReplayActive;
  const analysisStale = Boolean(
    mode === "live"
    && !historyReplayActive
    && snapshotAsOf
    && feed.lastClosedTs
    && feed.lastClosedTs > snapshotAsOf,
  );
  const chartKey = chartIdentity({
    symbol: session.symbol,
    period: session.period || "multi",
    feedKind: historyReplayActive ? "history" : feedKind,
    start: session.start,
    end: session.end,
  });
  const stageCards = buildStages(mode, loading, Boolean(activeData), unavailable);
  const stats = buildStats(mode, session, activeData, health);
  const _activeRunId = activeData?.runId;
  const followupMessages = _activeRunId ? followupByAnalysis[_activeRunId] ?? [] : [];
  const rightPanelTitle = workbenchMode === "review" ? "AI 复盘面板" : mode === "live" ? "AI 实时面板" : "AI 区间面板";
  const rightPanelDetail = workbenchMode === "review"
    ? "思考 · 行动 · 观察"
    : mode === "live"
      ? "接口轮询跟随 · 点分析才跑模型"
      : "区间分析";

  function updateSession(field: keyof SessionState, value: string | number | boolean) {
    setReviewNotice(null);
    setSessions((current) => ({
      ...current,
      [mode]: {
        ...current[mode],
        [field]: value,
      },
    }));
  }

  function resetCurrentChartState() {
    setFeed((current) => ({
      ...current,
      bars: [],
      lastClosedTs: null,
      pollError: null,
    }));
    setResults((current) => ({ ...current, [mode]: null }));
    setSnapshotAsOf(null);
    setHistoryReplayActive(false);
  }

  function selectDecisionView(view: DecisionView) {
    setDecisionView(view);
  }

  function onDecisionTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, currentView: DecisionView) {
    const currentIndex = decisionViews.findIndex((view) => view.id === currentView);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % decisionViews.length;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + decisionViews.length) % decisionViews.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = decisionViews.length - 1;
    if (nextIndex == null) return;
    event.preventDefault();
    const nextView = decisionViews[nextIndex].id;
    setDecisionView(nextView);
    decisionTabRefs.current[nextView]?.focus();
  }

  function clampRightPanelPercent(value: number) {
    return Math.min(70, Math.max(30, value));
  }

  function resizePanels(clientX: number) {
    if (!Number.isFinite(clientX)) return;
    const left = centerPanelRef.current?.getBoundingClientRect().left;
    const right = rightPanelRef.current?.getBoundingClientRect().right;
    if (left == null || right == null || right <= left) return;
    setRightPanelPercent(clampRightPanelPercent(((right - clientX) / (right - left)) * 100));
  }

  function onSplitPointerDown(event: PointerEvent<HTMLDivElement>) {
    splitDraggingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    resizePanels(event.clientX);
  }

  function onSplitPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (splitDraggingRef.current) resizePanels(event.clientX);
  }

  function onSplitKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Home") setRightPanelPercent(44);
    else if (event.key === "ArrowRight") setRightPanelPercent((value) => clampRightPanelPercent(value + 2));
    else if (event.key === "ArrowLeft") setRightPanelPercent((value) => clampRightPanelPercent(value - 2));
    else return;
    event.preventDefault();
  }

  async function openTaskComposer(type: WorkbenchMode) {
    setEditingTask(null);
    const sourceSession = type === "review"
      ? sessions.historical
      : mode === "live"
        ? sessions.live
        : sessions.range;
    let trades = availableTrades;
    if (type === "review") {
      setLoading(true);
      setError(null);
      try {
        trades = await getRecentTrades();
        setAvailableTrades(trades);
      } catch (caught) {
        setError(caught as ApiError);
        setLoading(false);
        return;
      }
      setLoading(false);
    }
    const validSelectedTradeIds = selectedTradeIds.filter((id) => trades.some((trade) => trade.id === id));
    const selectedTrades = trades.filter((trade) => validSelectedTradeIds.includes(trade.id));
    const selectedTradeSymbol = selectedTrades[0]?.symbol_root
      ?? trades[0]?.symbol_root
      ?? sourceSession.symbol;
    const reviewRange = deriveDateTimeRange(selectedTrades);
    setTaskComposer(
      type === "review"
        ? {
            ...defaultComposer(type),
            start: reviewRange.start,
            end: reviewRange.end,
            selectedTradeSymbol,
            selectedTradeIds: validSelectedTradeIds,
            period: sourceSession.period,
          }
        : {
            ...defaultComposer(type),
            symbol: sourceSession.symbol,
            period: sourceSession.period,
            start: sourceSession.start,
            end: sourceSession.end,
            analysisMode: mode === "live" ? "live" : "range",
            streamEnabled: sourceSession.streamEnabled,
          },
    );
    setTaskDetails(null);
  }

  function openTaskDetail(task: SidebarTask) {
    setTaskDetails({ task, run: null });
    setCopiedRunId(null);
    setTaskComposer(null);
  }

  function openRunDetail(task: SidebarTask, run: SidebarNavAnalysisRun) {
    setTaskDetails({ task, run });
    setCopiedRunId(null);
    setTaskComposer(null);
  }

  async function copySelectedRunId(runId: string) {
    try {
      await navigator.clipboard.writeText(runId);
      setCopiedRunId(runId);
    } catch {
      setError({ code: "clipboard_unavailable", message: "复制运行 ID 失败，请手动选择复制。" });
    }
  }

  async function editPendingTask(task: SidebarTask) {
    if (task.status !== "pending") return;
    if (task.type === "review") {
      setLoading(true);
      try { setAvailableTrades(await getRecentTrades()); }
      catch (caught) { setError(caught as ApiError); return; }
      finally { setLoading(false); }
    }
    setEditingTask(task);
    setTaskComposer({ ...task.config, title: task.title, description: task.description });
    setTaskDetails(null);
  }

  function updateTaskComposer(field: keyof TaskComposer, value: string | number | boolean) {
    setTaskComposer((current) => current ? { ...current, [field]: value } as TaskComposer : current);
  }

  function toggleReviewTradeId(tradeId: string) {
    setTaskComposer((current) => {
      if (!current || current.type !== "review") return current;
      const trade = availableTrades.find((item) => item.id === tradeId);
      if (!trade) return current;
      const isSelected = current.selectedTradeIds.includes(tradeId);
      const nextIds = isSelected ? current.selectedTradeIds.filter((id) => id !== tradeId) : [...current.selectedTradeIds, tradeId];
      const selectedTrades = availableTrades.filter((item) => nextIds.includes(item.id));
      const { start, end } = deriveDateTimeRange(selectedTrades);
      return {
        ...current,
        selectedTradeIds: nextIds,
        selectedTradeSymbol: selectedTrades[0]?.symbol_root ?? current.selectedTradeSymbol,
        start,
        end,
      };
    });
  }

  /** 按已选交易同步历史复盘工作区的时间窗与标的。 */
  function syncReviewSessionFromTrades(tradeIds: string[], trades: Trade[] = availableTrades) {
    const selectedTrades = trades.filter((trade) => tradeIds.includes(trade.id));
    const { start, end } = deriveDateTimeRange(selectedTrades);
    setSelectedTradeIds(tradeIds);
    setSessions((current) => ({
      ...current,
      historical: {
        ...current.historical,
        start,
        end,
        symbol: selectedTrades[0]?.symbol_root ?? current.historical.symbol,
      },
    }));
  }

  /** 仅保存任务；执行由用户在任务详情中显式触发。 */
  async function saveTaskComposer() {
    if (!taskComposer) return;
    if (taskComposer.type === "review" && taskComposer.selectedTradeIds.length === 0) {
      setReviewNotice("请先选择需要复盘的交易记录。");
      return;
    }
    if (taskComposer.type === "analysis") {
      const duplicate = findLiveAnalysisTask(
        tasksByType.analysis.filter((task) => task.id !== editingTask?.id),
        taskComposer.symbol,
        taskComposer.period || "5m",
      );
      if (duplicate) {
        setError({
          code: "analysis_task_symbol_period_conflict",
          message: `${normalizeAnalysisSymbol(taskComposer.symbol)} ${taskComposer.period || "5m"} 已有实时分析任务，请打开原任务运行。`,
        });
        return;
      }
    }
    setLoading(true);
    setError(null);
    try {
      const config = taskComposer.type === "review"
        ? { selected_trade_ids: taskComposer.selectedTradeIds, periods: [taskComposer.period || "5m"] }
        : {
            symbol: taskComposer.symbol,
            period: taskComposer.period || "5m",
          };
      const title = taskComposer.title.trim() || (taskComposer.type === "review" ? "未命名复盘任务" : "未命名分析任务");
      const saved = editingTask
        ? await updateAnalysisTask(editingTask.id, { version: editingTask.version, title, description: taskComposer.description ?? "", config })
        : await createAnalysisTask({ kind: taskComposer.type, title, description: taskComposer.description ?? "", config });
      const durableTask = sidebarTaskFromApi(saved) as SidebarTask;
      setTasksByType((current) => ({ ...current, [durableTask.type]: editingTask ? current[durableTask.type].map((item) => item.id === durableTask.id ? durableTask : item) : [durableTask, ...current[durableTask.type]] }));
      setSidebarOpen((current) => ({ ...current, [durableTask.type]: true }));
      setTaskComposer(null);
      setEditingTask(null);
      setTaskDetails(null);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  async function refreshDurableTasks() {
    const page = await listAnalysisTasks();
    const restored = page.items.map(sidebarTaskFromApi) as SidebarTask[];
    setTasksByType({ review: restored.filter((task) => task.type === "review"), analysis: restored.filter((task) => task.type === "analysis") });
    if (taskDetails) {
      const restoredTask = restored.find((task) => task.id === taskDetails.task.id);
      setTaskDetails(restoredTask ? { ...taskDetails, task: restoredTask } : null);
    }
  }

  function mapExecutionListItem(item: AnalysisRunListItem): SidebarNavAnalysisRun {
    return {
      id: item.run_id,
      status: item.status,
      createdAt: item.created_at,
      runId: item.run_id,
      direction: item.direction,
      symbol: item.symbol,
      period: item.period,
    };
  }

  async function loadAnalysisRuns(taskId: string) {
    try {
      const items = await listAnalysisTaskRuns(taskId);
      setAnalysisRunsByTask((current) => ({ ...current, [taskId]: items.map(mapExecutionListItem) }));
    } catch (caught) {
      setError(caught as ApiError);
    }
  }

  async function toggleAnalysisTaskExpand(taskId: string) {
    setExpandedAnalysisTasks((current) => {
      const nextOpen = !current[taskId];
      if (nextOpen) void loadAnalysisRuns(taskId);
      return { ...current, [taskId]: nextOpen };
    });
  }

  async function openAnalysisRun(taskId: string, run: SidebarNavAnalysisRun) {
    setActiveSidebarLeaf(`run:${run.id}`);
    setWorkbenchMode("analysis");
    setSidebarOpen((current) => ({ ...current, analysis: true }));
    if (!(run.runId ?? run.id) || !["completed", "completed_with_warnings"].includes(run.status)) {
      const task = tasksByType.analysis.find((item) => item.id === taskId);
      if (task) openRunDetail(task, run);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const detail = await getRunDetail(run.runId ?? run.id);
      const result = detail.result as DemoAnalysisResponse;
      const targetMode: Mode = result.query?.analysis_mode === "realtime" ? "live" : "range";
      const transcript = result.llm_transcript ?? {
        stage1: { reasoning: "", content: "" },
        stage2: { reasoning: "", content: "" },
      };
      setMode(targetMode);
      setResults((current) => ({ ...current, [targetMode]: toWorkbenchData(result, targetMode, false, unavailable) }));
      if (result.bars?.length) {
        setFeed({
          kind: targetMode === "live" ? "live" : "history",
          symbol: result.query.symbol,
          period: result.query.period,
          bars: result.bars,
          lastClosedTs: lastClosedTs(result.bars),
          pollError: null,
        });
      }
      setHistoryReplayActive(true);
      setDecisionView("realtime");
      setLlmLive(transcript);
      setStreamEvents([
        {
          type: "status" as const,
          stage: "complete" as const,
          message: transcript.stage1.content || transcript.stage2.content || transcript.stage1.reasoning || transcript.stage2.reasoning
            ? "已从历史记录恢复"
            : "历史记录已恢复；当时未保存到模型原文",
        },
      ]);
      setTaskDetails(null);
    } catch (caught) {
      if (run.runId) {
        await restoreHistory(run.runId);
        return;
      }
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  async function runSavedTask(task: SidebarTask) {
    setLoading(true); setError(null);
    setDecisionView("realtime");
    setHistoryReplayActive(false);
    setStreamEvents([]);
    setLlmLive({ stage1: { reasoning: "", content: "" }, stage2: { reasoning: "", content: "" } });
    analysisAbortRef.current?.abort();
    const controller = new AbortController();
    analysisAbortRef.current = controller;
    try {
      const executions = await startAnalysisTaskRun(task.id);
      setTaskDetails((current) => current?.task.id === task.id
        ? { ...current, task: { ...current.task, status: "running" } }
        : current);
      setTasksByType((current) => ({ ...current, [task.type]: current[task.type].map((item) => item.id === task.id ? { ...item, status: "running" } : item) }));
      const details = await waitForRunsTerminal(executions.map((item) => item.run_id), { intervalMs: 500, timeoutMs: 60_000, signal: controller.signal });
      const detail = details[0];
      if (!detail) return;
      const payloadResult = detail.result;
      if (detail.mode === "trade_review") {
        setStreamEvents((current) => [...current, { type: "status", stage: "complete", message: `复盘 ${detail.period} · ${detail.status}` }]);
        await refreshDurableTasks();
        await loadAnalysisRuns(task.id);
        return;
      }
      if (payloadResult && "review_children" in payloadResult) {
        setStreamEvents((current) => [...current, { type: "status", stage: "complete", message: "复盘完成" }]);
        const primary = payloadResult.review_children[0];
        if (!primary) return;
        const data = toWorkbenchData(primary, "historical", false, unavailable);
        data.reviewResult = payloadResult.review_result;
        data.notes = payloadResult.review_children.map((child) => `交易 ${child.query.trades?.[0]?.trade_id ?? "—"} · ${child.query.period} · ${child.analysis.direction}`);
        data.detailLabel = `${payloadResult.review_children.length} 个复盘子项完成`;
        setWorkbenchMode("review"); setMode("historical");
        setResults((current) => ({ ...current, historical: data }));
        setFeed({ kind: "history", symbol: primary.query.symbol, period: primary.query.period, bars: primary.bars, lastClosedTs: lastClosedTs(primary.bars), pollError: null });
        const primaryRunId = primary.run_id;
        if (primaryRunId) {
          setFollowupByAnalysis((current) => ({ ...current, [primaryRunId]: [] }));
        }
        return;
      }
      if (payloadResult) {
        const result = payloadResult;
        setStreamEvents((current) => [...current, { type: "result", stage: "complete", message: "分析完成", result }]);
        const targetMode: Mode = result.query.analysis_mode === "realtime" ? "live" : "range";
        setMode(targetMode);
        setResults((current) => ({ ...current, [targetMode]: toWorkbenchData(result, targetMode, false, unavailable) }));
        setFeed({ kind: targetMode === "live" ? "live" : "history", symbol: result.query.symbol, period: result.query.period, bars: result.bars, lastClosedTs: lastClosedTs(result.bars), pollError: null });
        const runIdForFollowup = result.run_id;
        if (runIdForFollowup) {
          setFollowupByAnalysis((current) => ({ ...current, [runIdForFollowup]: [] }));
        }
      }
      await refreshDurableTasks();
      await loadAnalysisRuns(task.id);
      setExpandedAnalysisTasks((current) => ({ ...current, [task.id]: true }));
      setTaskDetails(null);
    } catch (caught) { setError(caught as ApiError); }
    finally {
      if (analysisAbortRef.current === controller) analysisAbortRef.current = null;
      setLoading(false);
    }
  }

  async function cancelSavedTask(task: SidebarTask) {
    const runIdToCancel = analysisRunsByTask[task.id]?.find((run) => run.status === "queued" || run.status === "running")?.runId;
    if (!runIdToCancel) return;
    try { await cancelRun(runIdToCancel); await refreshDurableTasks(); }
    catch (caught) { setError(caught as ApiError); }
  }

  /** 将任务配置写入工作区，并返回可立即用于分析的会话快照。 */
  function activateTaskWorkspace(task: SidebarTask): { nextMode: Mode; nextSession: SessionState } {
    if (task.type === "review") {
      const nextSession: SessionState = {
        ...sessions.historical,
        symbol: task.config.selectedTradeSymbol || sessions.historical.symbol,
        period: task.config.period,
        start: task.config.start,
        end: task.config.end,
        includeOrders: task.config.includeOrders,
        overlayOrders: task.config.overlayOrders,
      };
      setWorkbenchMode("review");
      setMode("historical");
      setHistoryReplayActive(false);
      setSessions((current) => ({ ...current, historical: nextSession }));
      setSelectedTradeIds(task.config.selectedTradeIds);
      setError(null);
      return { nextMode: "historical", nextSession };
    }

    const nextMode: Mode = task.config.analysisMode === "live" ? "live" : "range";
    const nextSession: SessionState = {
      ...sessions[nextMode],
      symbol: task.config.symbol,
      period: task.config.period,
      start: task.config.start,
      end: task.config.end,
      streamEnabled: task.config.streamEnabled,
    };
    setWorkbenchMode("analysis");
    setMode(nextMode);
    setHistoryReplayActive(false);
    setSessions((current) => ({ ...current, [nextMode]: nextSession }));
    setError(null);
    return { nextMode, nextSession };
  }

  function exportCurrent() {
    const payload = { generated_at: new Date().toISOString(), mode, session, analysis: activeData, health, risk_notice: "AI 结果仅用于研究与复盘，不构成投资建议。" };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `pa-${mode}-${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function exportCsv() {
    if (!activeData) return;
    const headers = ["timestamp", "open", "high", "low", "close", "volume"];
    const rows = activeData.bars.map((bar) => [bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume ?? ""]);
    const metadata = [["generated_at", new Date().toISOString()], ["symbol", activeData.resolvedSymbol], ["risk_notice", "AI 结果仅用于研究与复盘，不构成投资建议。"], []];
    const csv = [...metadata, headers, ...rows].map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(",")).join("\n");
    const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = `pa-${mode}-${Date.now()}.csv`; link.click(); URL.revokeObjectURL(url);
  }

  function printReport() {
    if (!activeData) return;
    window.print();
  }

  async function openHistory() {
    setLoading(true);
    try {
      const [history, status] = await Promise.all([getAnalysisHistory(), getCollectionStatus()]);
      setAnalysisHistory(history); setCollectionStatus(status); setHistoryOpen(true);
    } catch (caught) { setError(caught as ApiError); } finally { setLoading(false); }
  }

  async function loadFollowupHistory(runId: string) {
    try {
      const messages = await getFollowupHistory(runId);
      setFollowupByAnalysis((current) => ({ ...current, [runId]: messages }));
    } catch {
      setFollowupByAnalysis((current) => ({ ...current, [runId]: [] }));
    }
  }

  async function restoreHistory(runId: string) {
    setLoading(true);
    try {
      const detail = await getAnalysisDetail(runId);
      const targetMode: Mode = detail.query.analysis_mode === "realtime" ? "live" : detail.query.analysis_mode === "trade_review" ? "historical" : "range";
      const restoredBars = detail.bars ?? [];
      const asOf = lastClosedTs(restoredBars);
      const transcript = detail.llm_transcript ?? {
        stage1: { reasoning: "", content: "" },
        stage2: { reasoning: "", content: "" },
      };
      setMode(targetMode);
      setWorkbenchMode(detail.query.analysis_mode === "trade_review" ? "review" : "analysis");
      setSessions((current) => ({
        ...current,
        [targetMode]: {
          ...current[targetMode],
          symbol: detail.query.symbol,
          period: detail.query.period,
          start: detail.query.start.slice(0, 16),
          end: detail.query.end.slice(0, 16),
        } as SessionState,
      }));
      setFeed({
        kind: "history",
        symbol: detail.query.symbol,
        period: detail.query.period,
        bars: restoredBars,
        lastClosedTs: asOf,
        pollError: null,
      });
      setSnapshotAsOf(asOf);
      setHistoryReplayActive(true);
      setResults((current) => ({ ...current, [targetMode]: toWorkbenchData(detail, targetMode) }));
      setDecisionView("realtime");
      setLlmLive(transcript);
      setStreamEvents([
        {
          type: "status",
          stage: "complete",
          message: transcript.stage1.content || transcript.stage2.content || transcript.stage1.reasoning || transcript.stage2.reasoning
            ? "已从历史记录恢复"
            : "历史记录已恢复；当时未保存到模型原文",
        },
      ]);
      setHistoryOpen(false);
      void loadFollowupHistory(runId);
    } catch (caught) { setError(caught as ApiError); } finally { setLoading(false); }
  }


  function useVisibleRange(start: string, end: string) {
    const startValue = new Date(start).toISOString().slice(0, 16);
    const endValue = new Date(end).toISOString().slice(0, 16);
    setSessions((current) => ({ ...current, [mode]: { ...current[mode], start: startValue, end: endValue } }));
  }

  async function loadMoreHistory(start: string, end: string) {
    if (!session.symbol || !session.period) return;
    const period = session.period as Period;
    try {
      const market = await getMarketBars(session.symbol, period, start, end, undefined, false, "history_prefetch");
      if (!market.bars.length) return;
      setFeed((current) => {
        if (current.symbol !== session.symbol || current.period !== session.period) return current;
        const bars = mergeBars(current.bars, market.bars);
        return {
          ...current,
          bars,
          lastClosedTs: lastClosedTs(bars),
          pollError: null,
        };
      });
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      const message = (caught as ApiError)?.message ?? "加载更多历史行情失败";
      setFeed((current) => ({ ...current, pollError: message }));
    }
  }

  async function loadMoreFuture(start: string, end: string) {
    if (!session.symbol || !session.period) return;
    const period = session.period as Period;
    try {
      const market = await getMarketBars(session.symbol, period, start, end, undefined, false, "future_prefetch");
      if (!market.bars.length) return;
      setFeed((current) => {
        if (current.symbol !== session.symbol || current.period !== session.period) return current;
        const bars = mergeBars(current.bars, market.bars);
        return {
          ...current,
          bars,
          lastClosedTs: lastClosedTs(bars),
          pollError: null,
        };
      });
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      const message = (caught as ApiError)?.message ?? "加载后续行情失败";
      setFeed((current) => ({ ...current, pollError: message }));
    }
  }

  async function confirmDebug(query: HistoricalQuery): Promise<boolean> {
    const preview = await getDebugPreview(query);
    if (!preview.requires_confirmation) return true;
    setDebugPreview(preview);
    return new Promise((resolve) => { debugResolver.current = resolve; });
  }

  function closeDebug(confirmed: boolean) {
    debugResolver.current?.(confirmed);
    debugResolver.current = null;
    setDebugPreview(null);
  }

  async function executeAnalysis(query: HistoricalQuery, signal?: AbortSignal): Promise<DemoAnalysisResponse> {
    if (!await confirmDebug(query)) {
      throw { code: "debug_cancelled", message: "已取消本次分析" } satisfies ApiError;
    }
    syncLeftSidebarAutoCollapse(true);
    setDecisionView("realtime");
    setHistoryReplayActive(false);
    setStreamEvents([]);
    setLlmLive({ stage1: { reasoning: "", content: "" }, stage2: { reasoning: "", content: "" } });
    return analyzeRangeStream(query, (event) => {
      if (event.type === "llm_delta" && event.text) {
        const stageKey = event.stage === "stage2" ? "stage2" : "stage1";
        const field = event.kind === "content" ? "content" : "reasoning";
        setLlmLive((current) => ({
          ...current,
          [stageKey]: {
            ...current[stageKey],
            [field]: current[stageKey][field] + event.text,
          },
        }));
        return;
      }
      setStreamEvents((current) => [...current, event]);
    }, signal);
  }


  async function openPersonalCenter() {
    setLoading(true);
    try {
      const [settings, usage, imports] = await Promise.all([getPersonalSettings(), getTokenUsage(), getTradeImports()]);
      setPersonalSettings(settings);
      setTokenUsage(usage);
      setTradeImports(imports);
      setPersonalOpen(true);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  async function selectPromptFile(filename: string) {
    setLoading(true);
    setAdminNotice(null);
    try {
      setPromptDocument(await getAdminPromptFile(filename));
      setPromptVersions([]);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  async function openAdminCenter() {
    setLoading(true);
    setAdminNotice(null);
    try {
      const view = await getAdminOrchestration();
      setOrchestration(view);
      setAdminOpen(true);
      const firstFile = view.stages.flatMap((stage) => stage.prompt_files)[0];
      if (firstFile) { setPromptDocument(await getAdminPromptFile(firstFile.filename)); setPromptVersions([]); }
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  async function submitLogin(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError(null);
    try { setUser(await login(loginName, loginPassword)); setLoginOpen(false); setLoginPassword(""); }
    catch (caught) { setError(caught as ApiError); } finally { setLoading(false); }
  }

  async function signOut() { await logout(); setUser(null); setLoginOpen(true); }

  async function restorePromptVersion(version: PromptVersion) {
    if (!promptDocument || !window.confirm(`确认回滚到 ${formatDate(version.created_at)} 的版本？`)) return;
    setPromptDocument(await rollbackPromptVersion(promptDocument.filename, version.id));
    setPromptVersions(await getPromptVersions(promptDocument.filename));
    setAdminNotice("已回滚并生成新的当前版本");
  }

  async function savePromptDocument() {
    if (!promptDocument) return;
    setLoading(true);
    setAdminNotice(null);
    try {
      setPromptDocument(await saveAdminPromptFile(promptDocument));
      setAdminNotice(`${promptDocument.filename} 已保存，下一次分析立即生效。`);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  function updateModel(index: number, field: string, value: string) {
    setPersonalSettings((current) => current ? {
      ...current,
      models: current.models.map((model, modelIndex) => modelIndex === index ? { ...model, [field]: value } : model),
    } : current);
  }

  function updateProvider(index: number, provider: PersonalSettings["models"][number]["provider"]) {
    const defaultModel = provider === "compatible" ? "" : providerModels[provider][0];
    setPersonalSettings((current) => current ? {
      ...current,
      models: current.models.map((model, modelIndex) => modelIndex === index ? {
        ...model,
        provider,
        model: defaultModel,
        base_url: provider === "deepseek" ? "https://api.deepseek.com" : provider === "compatible" ? model.base_url : null,
      } : model),
    } : current);
  }

  function addModel() {
    const id = createId("model");
    setPersonalSettings((current) => current ? {
      ...current,
      active_model_id: current.active_model_id ?? id,
      models: [...current.models, { id, name: "新模型", provider: "openai", model: "gpt-5-mini", base_url: null, has_api_key: false, api_key_masked: null }],
    } : current);
  }

  async function savePersonalCenter() {
    if (!personalSettings) return;
    setLoading(true);
    try {
      setPersonalSettings(await savePersonalSettings(personalSettings));
      setTokenUsage(await getTokenUsage());
      setImportNotice("个人中心配置已保存");
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setLoading(false);
    }
  }

  async function sendFollowup() {
    const runId = activeData?.runId;
    const question = followupDraft.trim();
    if (!runId || !question || followupSending) return;

    followupAbortRef.current?.abort();
    const controller = new AbortController();
    followupAbortRef.current = controller;

    const userId = `fu-user-${Date.now()}`;
    const assistantId = `fu-assistant-${Date.now()}`;
    setFollowupDraft("");
    setFollowupSending(true);
    setError(null);
    setFollowupByAnalysis((current) => ({
      ...current,
      [runId]: [
        ...(current[runId] ?? []),
        { id: userId, role: "user", content: question },
        { id: assistantId, role: "assistant", content: "", pending: true },
      ],
    }));

    try {
      await sendFollowupStream(
        runId,
        {
          question,
          bars: chartBars,
          symbol: activeData?.resolvedSymbol ?? session.symbol,
          period: session.period || undefined,
        },
        (event) => {
          if (event.type !== "delta" && event.type !== "done") return;
          setFollowupByAnalysis((current) => {
            const list = current[runId] ?? [];
            return {
              ...current,
              [runId]: list.map((message) => {
                if (message.id !== assistantId) return message;
                if (event.type === "done") {
                  return { ...message, content: event.content ?? message.content, pending: false };
                }
                return { ...message, content: `${message.content}${event.content ?? ""}`, pending: true };
              }),
            };
          });
        },
        controller.signal,
      );
      setFollowupByAnalysis((current) => ({
        ...current,
        [runId]: (current[runId] ?? []).map((message) => (
          message.id === assistantId ? { ...message, pending: false } : message
        )),
      }));
    } catch (caught) {
      if ((caught as Error)?.name === "AbortError") return;
      setError(caught as ApiError);
      setFollowupByAnalysis((current) => ({
        ...current,
        [runId]: (current[runId] ?? []).filter((message) => message.id !== assistantId),
      }));
      setFollowupDraft(question);
    } finally {
      setFollowupSending(false);
    }
  }

  /** 执行 K 线分析；实时一键分析按 标的+周期 匹配唯一任务，结果挂到该任务历史下。 */
  async function runAnalysis(nextMode: Mode = mode, sessionOverride?: SessionState) {
    const currentSession = sessionOverride ?? sessions[nextMode];
    if (!currentSession.symbol || !currentSession.period) {
      setError({ code: "analysis_missing_selection", message: "请先选择品种与周期" });
      return;
    }
    const period = currentSession.period as Period;

    if (nextMode === "live") {
      setHistoryReplayActive(false);
      setMode("live");
      syncLeftSidebarAutoCollapse(true);
      setWorkbenchMode("analysis");
      setLoading(true);
      setError(null);
      setCsvError(null);
      try {
        const ensured = await ensureLiveAnalysisTask({
          symbol: currentSession.symbol,
          period,
          title: `${normalizeAnalysisSymbol(currentSession.symbol)} · ${period}`,
        });
        await refreshDurableTasks();
        const page = await listAnalysisTasks();
        const restored = page.items.map(sidebarTaskFromApi) as SidebarTask[];
        const task = restored.find((item) => item.id === ensured.id)
          ?? (sidebarTaskFromApi(ensured) as SidebarTask);
        setTasksByType({
          review: restored.filter((item) => item.type === "review"),
          analysis: restored.filter((item) => item.type === "analysis"),
        });
        setActiveSidebarLeaf(`task:${task.id}`);
        setSidebarOpen((current) => ({ ...current, analysis: true }));
        setExpandedAnalysisTasks((current) => ({ ...current, [task.id]: true }));
        await runSavedTask(task);
      } catch (caught) {
        setError(caught as ApiError);
        setLoading(false);
      }
      return;
    }

    const analysisMode = "historical" as const;
    let start = toUtcIso(currentSession.start);
    let end = toUtcIso(currentSession.end);
    let asOf = feed.lastClosedTs;

    setHistoryReplayActive(false);

    if (feed.kind === "history" && feed.bars.length && feed.symbol === currentSession.symbol) {
      asOf = feed.lastClosedTs;
    }

    setMode(nextMode);
    syncLeftSidebarAutoCollapse(true);
    setLoading(true);
    setError(null);
    setCsvError(null);
    setSnapshotAsOf(asOf);
    const controller = new AbortController(); analysisAbortRef.current = controller;
    try {
      const response = await executeAnalysis({
        symbol: currentSession.symbol,
        period,
        start,
        end,
        analysis_mode: analysisMode,
      }, controller.signal);
      const responseAsOf = lastClosedTs(response.bars) ?? asOf;
      setSnapshotAsOf(responseAsOf);
      setFeed({
        kind: "history",
        symbol: currentSession.symbol,
        period: currentSession.period,
        bars: response.bars,
        lastClosedTs: responseAsOf,
        pollError: null,
      });
      setResults((current) => ({
        ...current,
        [nextMode]: toWorkbenchData(response, nextMode, false, unavailable),
      }));
      const responseRunId = response.run_id;
      if (responseRunId) {
        setFollowupByAnalysis((current) => ({ ...current, [responseRunId]: [] }));
      }
    } catch (caught) {
      setResults((current) => ({ ...current, [nextMode]: null }));
      setError(caught instanceof DOMException && caught.name === "AbortError" ? { code: "analysis_cancelled", message: "分析已取消" } : caught as ApiError);
    } finally {
      analysisAbortRef.current = null;
      setLoading(false);
    }
  }

  async function toggleLiveRule() {
    const currentSession = sessions.live;
    if (!currentSession.symbol || !currentSession.period) {
      setError({ code: "analysis_missing_selection", message: "请先选择品种与周期" });
      return;
    }
    const existing = alertRules.find((rule) => rule.symbol === currentSession.symbol && rule.period === currentSession.period);
    const rule = await saveAlertRule({ name: `${currentSession.symbol} ${currentSession.period} 分钟监控`, symbol: currentSession.symbol, period: currentSession.period, trigger_type: "bar_close", threshold: existing?.threshold ?? DEFAULT_ALERT_THRESHOLD, enabled: existing ? !existing.enabled : true }, existing?.id);
    setAlertRules((current) => existing ? current.map((item) => item.id === rule.id ? rule : item) : [rule, ...current]);
    updateSession("streamEnabled", rule.enabled);
    setImportNotice(rule.enabled ? "分钟监控已启用，新 K 线落库后自动评估" : "分钟监控已暂停");
    if (rule.enabled) void runAnalysis("live");
  }

  /** 执行交易复盘；保存任务时直接使用新任务的会话和交易选择。 */
  async function runReview(sessionOverride?: SessionState, selectedTradeIdsOverride?: string[]) {
    const currentSession = sessionOverride ?? session;
    const currentSelectedTradeIds = selectedTradeIdsOverride ?? selectedTradeIds;
    setLoading(true);
    setError(null);
    setCsvError(null);
    setReviewNotice(null);
    const controller = new AbortController(); analysisAbortRef.current = controller;
    try {
      const reviewTrades = availableTrades.filter((trade) => currentSelectedTradeIds.includes(trade.id));
      if (!reviewTrades.length) {
        setReviewNotice("请先选择需要复盘的交易记录。");
        return;
      }
      const { start: reviewStartLocal, end: reviewEndLocal } = deriveDateTimeRange(reviewTrades);
      if (sessionOverride == null) {
        setSessions((current) => ({
          ...current,
          historical: { ...current.historical, start: reviewStartLocal, end: reviewEndLocal, symbol: reviewTrades[0]?.symbol_root ?? current.historical.symbol },
        }));
      }
      syncLeftSidebarAutoCollapse(true);
      const tradeMarkers: TradeMarker[] = reviewTrades.flatMap((trade) => [
        { timestamp: trade.entered_at, position: trade.direction === "long" ? "belowBar" : "aboveBar", shape: trade.direction === "long" ? "arrowUp" : "arrowDown", color: trade.direction === "long" ? "#22c55e" : "#ef4444", text: `${trade.direction === "long" ? "多" : "空"} ${trade.contract_name}` },
        { timestamp: trade.exited_at, position: "inBar", shape: "circle", color: "#64748b", text: "平仓" },
      ]);
      const reviewPeriods: Period[] = currentSession.period ? [currentSession.period] : ["5m", "1h", "4h", "1d"];
      const reviewQueries = reviewTrades.flatMap((trade) => reviewPeriods.map((period): HistoricalQuery => ({
        symbol: trade.symbol_root,
        period,
        start: trade.entered_at,
        end: trade.exited_at,
        analysis_mode: "trade_review",
        trades: [{
          trade_id: trade.id,
          symbol: trade.symbol_root,
          entered_at: trade.entered_at,
          exited_at: trade.exited_at,
          direction: trade.direction,
          entry_price: Number(trade.entry_price),
          exit_price: Number(trade.exit_price),
          size: Number(trade.size),
          reported_pnl: trade.reported_pnl === null ? null : Number(trade.reported_pnl),
        }],
      })));
      const responses: DemoAnalysisResponse[] = [];
      for (const query of reviewQueries) responses.push(await executeAnalysis(query, controller.signal));
      const primaryData = toWorkbenchData(responses[0], "historical", false, unavailable);
      primaryData.tradeMarkers = tradeMarkers;
      primaryData.reviewResult = [...new Map(
        responses.flatMap((response) => response.review_result ?? []).map((review) => [review.trade_id, review]),
      ).values()];
      primaryData.notes = responses.map((response) => (
        `交易 ${response.query.trades?.[0]?.trade_id ?? "—"} · ${response.query.symbol} · ${response.query.period}: ${response.analysis.direction}，${formatNumber(response.analysis.change_percent)}% · Gate ${response.stage1?.gate_result ?? "待分析"}`
      ));
      primaryData.detailLabel = `${reviewTrades.length} 笔逐笔复盘 · ${reviewPeriods.join(" / ")}`;
      const reviewAsOf = lastClosedTs(primaryData.bars);
      setFeed({
        kind: "history",
        symbol: reviewTrades[0]?.symbol_root || currentSession.symbol,
        period: currentSession.period,
        bars: primaryData.bars,
        lastClosedTs: reviewAsOf,
        pollError: null,
      });
      setSnapshotAsOf(reviewAsOf);
      setResults((current) => ({ ...current, historical: primaryData }));
      const reviewRunId = responses[0]?.run_id ?? "";
      if (reviewRunId) {
        setFollowupByAnalysis((current) => ({ ...current, [reviewRunId]: [] }));
      }
    } catch (caught) {
      setResults((current) => ({ ...current, historical: null }));
      setError(caught instanceof DOMException && caught.name === "AbortError" ? { code: "analysis_cancelled", message: "复盘已取消" } : caught as ApiError);
    } finally {
      analysisAbortRef.current = null;
      setLoading(false);
    }
  }

  async function openOrderPicker() {
    setLoading(true);
    setCsvError(null);
    try {
      setAvailableTrades(await getRecentTrades());
      setOrderPickerOpen(true);
    } catch (caught) {
      const apiError = caught as ApiError;
      setCsvError(apiError.message || "交易记录加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function openJournal() {
    setLoading(true);
    try { const [trades, imports] = await Promise.all([getRecentTrades(), getTradeImports()]); setAvailableTrades(trades); setTradeImports(imports); setJournalOpen(true); }
    catch (caught) { setError(caught as ApiError); } finally { setLoading(false); }
  }

  async function editJournalTrade(trade: Trade) {
    const notes = window.prompt("交易笔记", trade.notes ?? "");
    if (notes === null) return;
    const strategy = window.prompt("策略标签", trade.strategy ?? "");
    if (strategy === null) return;
    const updated = await updateTrade(trade.id, { notes, strategy });
    setAvailableTrades((current) => current.map((item) => item.id === updated.id ? updated : item));
  }

  async function removeJournalTrade(trade: Trade) {
    if (!window.confirm(`确认删除交易 ${trade.contract_name} · ${formatDate(trade.entered_at)}？`)) return;
    await deleteTrade(trade.id);
    setAvailableTrades((current) => current.filter((item) => item.id !== trade.id));
  }

  async function addManualTrade() {
    const contract = window.prompt("合约代码，例如 ESU6"); if (!contract) return;
    const direction = window.prompt("方向：long 或 short", "long"); if (direction !== "long" && direction !== "short") return;
    const enteredAt = window.prompt("开仓时间（ISO）", new Date().toISOString()); if (!enteredAt) return;
    const exitedAt = window.prompt("平仓时间（ISO）", new Date().toISOString()); if (!exitedAt) return;
    const entryPrice = Number(window.prompt("开仓价", "0")); const exitPrice = Number(window.prompt("平仓价", "0")); const size = Number(window.prompt("数量", "1"));
    const created = await createTrade({ contract_name: contract, direction, entered_at: enteredAt, exited_at: exitedAt, entry_price: entryPrice, exit_price: exitPrice, size });
    setAvailableTrades((current) => [created, ...current]);
  }

  async function handleTradeImport(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;

    setLoading(true);
    setCsvError(null);
    setError(null);
    try {
      const preview = await previewTradeImport(file);
      setPendingTradeFile(file);
      setTradePreview(preview);
      setImportNotice(null);
    } catch (caught) {
      const apiError = caught as ApiError;
      setCsvError(apiError.message || "交易文件解析失败");
    } finally {
      setLoading(false);
    }
  }

  async function confirmImport() {
    if (!pendingTradeFile) return;
    setLoading(true);
    setCsvError(null);
    try {
      const result = await confirmTradeImport(pendingTradeFile);
      setImportNotice(`已导入 ${result.imported} 笔交易，跳过 ${result.skipped_duplicates} 笔重复记录。`);
      setPendingTradeFile(null);
      setTradePreview(null);
      try {
        const [trades, imports] = await Promise.all([getRecentTrades(), getTradeImports()]);
        setAvailableTrades(trades);
        setTradeImports(imports);
      } catch {
        setCsvError("交易已导入，但列表刷新失败；重新打开复盘任务时会再次加载。");
      }
    } catch (caught) {
      const apiError = caught as ApiError;
      setCsvError(apiError.message || "交易导入失败");
    } finally {
      setLoading(false);
    }
  }

  const reviewComposerTrades = taskComposer?.type === "review" ? availableTrades : [];

  return (
    <main className="workbench-root">
      <input ref={fileInputRef} accept=".xlsx,.csv" aria-label="Excel 交易文件" hidden onChange={handleTradeImport} type="file" />

      <header className="top-nav glass">
        <div className="top-nav-brand">
          <span className="brand-mark">PA</span>
          <div>
            <strong>PA 智能体工作台</strong>
            <small>Market intelligence workspace</small>
          </div>
        </div>

        <div className="top-nav-actions">
          <MoreMenu items={[
            { label: "分析历史", onSelect: () => void openHistory() },
            { label: "导出当前分析", onSelect: exportCurrent, disabled: !activeData },
            { label: "交易日志", onSelect: () => void openJournal() },
            { label: "管理后台", onSelect: () => void openAdminCenter(), hidden: Boolean(health?.auth_required && user?.role !== "admin") },
            { label: "个人中心", onSelect: () => void openPersonalCenter() },
            { label: `退出 ${user?.username ?? ""}`.trim(), onSelect: () => void signOut(), hidden: !health?.auth_required, danger: true },
          ]} />
        </div>
      </header>
      <div className="risk-banner"><span aria-hidden="true">i</span> AI 结果仅用于研究与复盘，不构成投资建议。行情超过预期更新时间时请勿据此决策。{loading && analysisAbortRef.current && <button onClick={() => analysisAbortRef.current?.abort()} type="button">取消当前任务</button>}</div>

      <section
        className={effectiveLeftCollapsed ? "shell-grid collapsed" : "shell-grid"}
        data-left-sidebar-mode={leftSidebarManualOverride !== null ? "manual" : leftSidebarAutoCollapsed ? "auto" : "default"}
        style={{
          "--center-panel-fr": `${100 - rightPanelPercent}fr`,
          "--right-panel-fr": `${rightPanelPercent}fr`,
        } as CSSProperties}
      >
        <aside
          className="shell-panel left-panel glass"
          onMouseEnter={handleLeftSidebarMouseEnter}
          onMouseLeave={handleLeftSidebarMouseLeave}
        >
          <SidebarNav
            collapsed={effectiveLeftCollapsed}
            onToggleCollapsed={handleLeftSidebarToggle}
            onExpand={() => setLeftSidebarManualCollapsed(false)}
            openSections={sidebarOpen}
            onToggleSection={(section) => setSidebarOpen((current) => ({ ...current, [section]: !current[section] }))}
            activeLeaf={activeSidebarLeaf}
            reviewTasks={tasksByType.review.map((task) => ({ id: task.id, type: task.type, title: task.title }))}
            analysisTasks={tasksByType.analysis.map((task) => ({
              id: task.id,
              type: task.type,
              title: task.title,
              symbol: task.config.symbol,
              period: task.config.period || undefined,
            }))}
            analysisRunsByTask={analysisRunsByTask}
            expandedAnalysisTasks={expandedAnalysisTasks}
            onToggleAnalysisTask={(taskId) => { void toggleAnalysisTaskExpand(taskId); }}
            loading={loading}
            unavailable={unavailable}
            analysisRunning={Boolean(loading && analysisAbortRef.current)}
            onSelectTask={(taskId, type) => {
              const task = tasksByType[type].find((item) => item.id === taskId);
              if (!task) return;
              setActiveSidebarLeaf(`task:${taskId}`);
              setSidebarOpen((current) => ({ ...current, [type]: true }));
              if (type === "analysis") {
                setExpandedAnalysisTasks((current) => ({ ...current, [taskId]: true }));
                void loadAnalysisRuns(taskId);
              }
              window.history.pushState({}, "", buildWorkbenchPath(task.type === "review" ? "review" : "analysis", task.type === "review" ? "historical" : (task.config.analysisMode === "live" ? "live" : "range")));
              openTaskDetail(task);
            }}
            onSelectAnalysisRun={(taskId, run) => { void openAnalysisRun(taskId, run); }}
            onNewReviewTask={() => {
              setSidebarOpen((current) => ({ ...current, review: true }));
              void openTaskComposer("review");
            }}
            onNewAnalysisTask={() => {
              setSidebarOpen((current) => ({ ...current, analysis: true }));
              void openTaskComposer("analysis");
            }}
          />
        </aside>

        <section className="shell-panel center-panel glass" ref={centerPanelRef}>
          <header className="panel-header">
            <div>
              <strong>K 线图</strong>
              <span>{activeData ? activeData.sourceLabel : feed.bars.length ? `${feedKind === "live" ? "实时" : "区间"} · ${session.symbol}` : "空状态 · 等待输入"}</span>
            </div>
            <div className="panel-chips" style={{ display: "flex", flexWrap: "nowrap" }}>
              {historyReplayActive && (
                <span className="panel-chip-action">历史回放</span>
              )}
              <div className="panel-chip-action panel-chip-select-button-group">
                <select
                  aria-label="K 线标的切换"
                  className="panel-chip-native-select"
                  value={session.symbol}
                  onChange={(event) => {
                    const nextSymbol = event.target.value;
                    if (nextSymbol === session.symbol) return;
                    updateSession("symbol", nextSymbol);
                    resetCurrentChartState();
                  }}
                >
                  <option value="">选择品种</option>
                  {symbols.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </div>
              <div className="panel-chip-action panel-chip-select-button-group">
                <select
                  aria-label="K 线周期切换"
                  className="panel-chip-native-select"
                  value={session.period}
                  onChange={(event) => {
                    const nextPeriod = event.target.value as Period;
                    if (nextPeriod === session.period) return;
                    updateSession("period", nextPeriod);
                    resetCurrentChartState();
                  }}
                >
                  <option value="">选择周期</option>
                  {periods.map((period) => (
                    <option key={period} value={period}>{period}</option>
                  ))}
                </select>
              </div>
              <span className="panel-chip-action panel-chip-action--timezone" title="K 线数据时区">{chartTimezone.label}</span>
              {feed.pollError && <span className="panel-chip-action" title={feed.pollError}>行情刷新失败</span>}
              {analysisStale && <span className="panel-chip-action" title={`结果截至 ${snapshotAsOf}`}>结果已陈旧</span>}
              {workbenchMode === "analysis" && mode === "live" && !historyReplayActive && (
                <button
                  className="primary-action one-click-analysis"
                  disabled={loading || unavailable || feed.bars.length === 0}
                  onClick={() => void runAnalysis("live")}
                  type="button"
                >
                  {loading ? "分析中…" : "一键分析"}
                </button>
              )}
            </div>
          </header>

          <div className="center-body">
            {emptyStateVisible ? (
              <div className="empty-canvas">
                <div className="empty-canvas-graphic" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                  <i />
                  <i />
                </div>
                <strong>{workbenchMode === "review" ? "尚未加载复盘数据" : "尚未加载行情数据"}</strong>
                <p>{workbenchMode === "review" ? "新建复盘任务并勾选交易后，会自动填入持仓时间窗并开始复盘。" : mode === "live" ? "配置品种与周期后开始实时行情。" : "选择品种、周期与时间窗口，然后执行区间分析。"}</p>
              </div>
            ) : (
              <>
                <div className="chart-card">
                  {analysisStale && (
                    <p className="chart-stale-hint" role="status">
                      分析基于 {snapshotAsOf} 的切片；图表已有更新的已收盘 K 线。再次点击分析才会重跑模型。
                    </p>
                  )}
                  <TradingChart
                    bars={chartBars}
                    chartKey={chartKey}
                    locked={chartLocked}
                    markers={activeData?.tradeMarkers}
                    onNeedHistory={loadMoreHistory}
                    onNeedFuture={loadMoreFuture}
                    onRangeSelected={useVisibleRange}
                    onPeriodChange={(nextPeriod) => {
                      updateSession("period", nextPeriod);
                      setFeed((current) => ({
                        ...current,
                        period: nextPeriod,
                        bars: [],
                        lastClosedTs: null,
                        pollError: null,
                      }));
                      setResults((current) => ({ ...current, [mode]: null }));
                    }}
                    analysisBars={activeData?.bars}
                    period={feedPeriod}
                    timezoneOffsetMinutes={chartTimezone.offsetMinutes}
                    stage1={activeData?.stage1}
                    stage2={activeData?.stage2}
                  />
                </div>

              </>
            )}
          </div>
        </section>

        <div
          aria-label="调整分析栏宽度"
          aria-orientation="vertical"
          aria-valuemax={70}
          aria-valuemin={30}
          aria-valuenow={Math.round(rightPanelPercent)}
          className="panel-resizer"
          onDoubleClick={() => setRightPanelPercent(44)}
          onKeyDown={onSplitKeyDown}
          onPointerCancel={() => { splitDraggingRef.current = false; }}
          onPointerDown={onSplitPointerDown}
          onPointerMove={onSplitPointerMove}
          onPointerUp={() => { splitDraggingRef.current = false; }}
          role="separator"
          tabIndex={0}
        />

        <aside className="shell-panel right-panel glass" ref={rightPanelRef}>
          <header className="panel-header">
            <div>
              <strong>{rightPanelTitle}</strong>
              <span>{rightPanelDetail}</span>
            </div>
            <div className="panel-chips">
              <span>{activeData ? "已加载" : loading ? "运行中" : "空闲"}</span>
            </div>
          </header>

          <div className="panel-body right-body">
            <nav aria-label="决策视图" className="decision-tabs" role="tablist">
              {decisionViews.map((view) => <button aria-controls={`decision-panel-${view.id}`} aria-selected={decisionView === view.id} className={decisionView === view.id ? "active" : ""} id={`decision-tab-${view.id}`} key={view.id} onClick={() => selectDecisionView(view.id)} onKeyDown={(event) => onDecisionTabKeyDown(event, view.id)} ref={(node) => { decisionTabRefs.current[view.id] = node; }} role="tab" tabIndex={decisionView === view.id ? 0 : -1} type="button">{view.label}</button>)}
            </nav>
            <div aria-labelledby={`decision-tab-${decisionView}`} id={`decision-panel-${decisionView}`} role="tabpanel" tabIndex={0}>
            {decisionView === "realtime" && (
              <section aria-live="polite" className="stream-console">
                <header>
                  <strong>{loading || llmLive.stage1.reasoning || llmLive.stage1.content || llmLive.stage2.reasoning || llmLive.stage2.content ? "思考过程 / 分析结论" : "分析过程"}</strong>
                  <span>{loading ? "输出中…" : streamEvents.length || llmLive.stage1.content || llmLive.stage2.content || activeData ? "已完成" : "等待任务"}</span>
                </header>
                {activeData?.stage2?.terminal.reason || activeData?.stage2?.decision.entry_reason || activeData?.notes?.length ? (
                  <div className="llm-readable">
                    <strong>【分析结论】</strong>
                    {activeData.stage2?.terminal.reason ? <p>{activeData.stage2.terminal.reason}</p> : null}
                    {activeData.stage2?.decision.entry_reason && activeData.stage2.decision.entry_reason !== activeData.stage2.terminal.reason ? (
                      <p>{activeData.stage2.decision.entry_reason}</p>
                    ) : null}
                    {!activeData.stage2?.terminal.reason && activeData.notes.length ? (
                      <p>{activeData.notes.join("；")}</p>
                    ) : null}
                  </div>
                ) : null}
                {(llmLive.stage1.reasoning || llmLive.stage1.content || llmLive.stage2.reasoning || llmLive.stage2.content) ? (
                  <div className="llm-transcript">
                    {(["stage1", "stage2"] as const).map((stageKey) => {
                      const sourceBlock = llmLive[stageKey];
                      const block = llmDisplayed[stageKey];
                      if (!sourceBlock.reasoning && !sourceBlock.content) return null;
                      const showRawOpen = loading || historyReplayActive;
                      return (
                        <div className="llm-stage" key={stageKey}>
                          <b>{stageKey === "stage1" ? "阶段一" : "阶段二"}</b>
                          {sourceBlock.reasoning ? (
                            <div className="llm-reasoning">
                              <strong>【思考过程】</strong>
                              <pre>{block.reasoning}</pre>
                            </div>
                          ) : loading ? (
                            <p className="llm-missing-reasoning">等待思考流…（部分模型只输出 JSON 正文）</p>
                          ) : (
                            <p className="llm-missing-reasoning">本次未返回独立思考流（仅有 JSON 正文）</p>
                          )}
                          {sourceBlock.content ? (
                            showRawOpen ? (
                              <div className="llm-answer">
                                <strong>{historyReplayActive ? "【实时流式原文】" : "【模型原文】"}</strong>
                                <pre>{block.content}</pre>
                              </div>
                            ) : (
                              <details className="llm-answer">
                                <summary>【模型原文 JSON】</summary>
                                <pre>{block.content}</pre>
                              </details>
                            )
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                ) : historyReplayActive ? (
                  <p className="stream-placeholder">这条历史没有保存到模型流式原文（当时可能未落库，或分析未调用大模型）。</p>
                ) : null}
                {streamEvents.filter((event) => event.type !== "llm_delta").length ? streamEvents.filter((event) => event.type !== "llm_delta").map((event, index) => (
                  <article className={`stream-event ${event.type}`} key={`${event.stage}-${index}`}>
                    <i />
                    <div><b>{event.stage === "stage1" ? "阶段一" : event.stage === "stage2" ? "阶段二" : event.stage === "market" ? "行情" : "系统"}</b><p>{event.message}</p>
                    </div>
                  </article>
                )) : !(llmLive.stage1.reasoning || llmLive.stage1.content || llmLive.stage2.reasoning || llmLive.stage2.content || activeData) ? (
                  <p className="stream-placeholder">开始分析后，这里会先显示思考过程与可读结论；模型 JSON 原文可展开查看。</p>
                ) : null}
                {loading && <span className="stream-cursor" aria-label="正在输出" />}
              </section>
            )}
            {decisionView === "realtime" && (activeData ? (
              <>
                {stageCards.map((stage) => (
                  <article key={stage.title} className={`stage-card ${stage.status}`}>
                    <div>
                      <strong>{stage.title}</strong>
                      <p>{stage.detail}</p>
                    </div>
                    <span>{stage.status === "done" ? "完成" : stage.status === "running" ? "运行" : stage.status === "failed" ? "失败" : stage.status === "skipped" ? "跳过" : "等待"}</span>
                  </article>
                ))}

                {activeData.stage1 && <section className="panel-card evidence-card">
                  <header><strong>Stage 1 · 市场证据</strong><span>置信度 {Math.round(activeData.stage1.confidence <= 1 ? activeData.stage1.confidence * 100 : activeData.stage1.confidence)}%</span></header>
                  <dl>
                    <div><dt>方向</dt><dd>{activeData.stage1.direction === "bullish" ? "看多" : activeData.stage1.direction === "bearish" ? "看空" : "中性"}</dd></div>
                    <div><dt>周期位置</dt><dd>{activeData.stage1.cycle_position || "—"}</dd></div>
                    <div><dt>闸门</dt><dd>{activeData.stage1.gate_result === "proceed" ? "继续" : activeData.stage1.gate_result === "wait" ? "等待" : "未知"}</dd></div>
                    <div><dt>形态</dt><dd>{activeData.stage1.detected_patterns.join("、") || "未识别"}</dd></div>
                    <div><dt>支撑</dt><dd>{activeData.stage1.support_levels.join("、") || "—"}</dd></div>
                    <div><dt>阻力</dt><dd>{activeData.stage1.resistance_levels.join("、") || "—"}</dd></div>
                  </dl>
                  <div className="gate-trace">{activeData.stage1.gate_trace.map((gate) => <article key={`${gate.node_id}-${gate.question}`}><b>{gate.answer}</b><span>{gate.source === "ai" ? "AI" : "程序"}</span><p>{gate.question}</p><small>{gate.reason} · {formatBarRange(gate.bar_range)}</small><div className="gate-evidence"><strong>依据</strong><p>{gate.reason || "无明确依据"}</p></div></article>)}</div>
                </section>}

                {activeData.reviewResult?.map((review) => <section className="panel-card review-result-card" key={review.trade_id}>
                  <header><strong>交易 {review.trade_id}</strong><span>{review.summary}</span></header>
                  <h4>做得好</h4><ul>{review.strengths.map((item) => <li key={item}>{item}</li>)}</ul>
                  <h4>发现的问题</h4><ul>{review.issues.map((item, index) => <li key={index}>{String(item.evidence ?? item.type ?? JSON.stringify(item))}</li>)}</ul>
                  <h4>改进建议</h4><ul>{review.improvements.map((item) => <li key={item}>{item}</li>)}</ul>
                </section>)}

                {activeData.audit && <section className="panel-card audit-card"><strong>分析审计</strong><p>{activeData.audit.stage1_model_called ? "Stage 1 已调用模型" : "Stage 1 使用确定性骨架"} · {activeData.audit.stage2_model_called ? "Stage 2 已调用模型" : "Stage 2 未调用模型"}</p>{activeData.audit.warnings.map((warning) => <small key={warning}>{warning}</small>)}</section>}

                {activeData.stage2 && (
                  <section className="panel-card stage2-result-card">
                    <header>
                      <div>
                        <strong>Stage 2 决策</strong>
                        <span>{activeData.stage2.result_kind}</span>
                      </div>
                      <b className={`outcome-${activeData.stage2.terminal.outcome}`}>{activeData.stage2.terminal.outcome}</b>
                    </header>
                    <p className="stage2-reason">{activeData.stage2.terminal.reason || "模型未提供决策理由"}</p>
                    <div className="stage2-basis"><strong>依据</strong><p>{activeData.stage2.decision.entry_reason || activeData.stage2.terminal.reason || "无明确依据"}</p></div>
                    <dl>
                      <div><dt>订单类型</dt><dd>{activeData.stage2.decision.order_type || "不下单"}</dd></div>
                      <div><dt>方向</dt><dd>{activeData.stage2.decision.direction || "—"}</dd></div>
                      <div><dt>入场价</dt><dd>{activeData.stage2.decision.entry_price ?? "—"}</dd></div>
                      <div><dt>止损价</dt><dd>{activeData.stage2.decision.stop_loss_price ?? "—"}</dd></div>
                      <div><dt>止盈价</dt><dd>{activeData.stage2.decision.take_profit_price ?? "—"}</dd></div>
                      <div><dt>第二目标</dt><dd>{activeData.stage2.decision.take_profit_price_2 ?? "—"}</dd></div>
                      <div><dt>预估胜率</dt><dd>{activeData.stage2.decision.estimated_win_rate == null ? "—" : `${activeData.stage2.decision.estimated_win_rate}%`}</dd></div>
                      <div><dt>终止节点</dt><dd>{activeData.stage2.terminal.terminal_node || "—"}</dd></div>
                    </dl>
                    {activeData.stage2.decision.entry_reason && <p className="stage2-entry-reason">入场依据：{activeData.stage2.decision.entry_reason}</p>}
                  </section>
                )}

                {activeData.runId && (
                  <section className="panel-card followup-card">
                    <header>
                      <strong>追问助手</strong>
                      <span>{followupSending ? "回答中…" : followupMessages.length ? `${Math.ceil(followupMessages.length / 2)} 轮` : "围绕本次分析追问"}</span>
                    </header>
                    <div className="followup-thread" aria-live="polite">
                      {followupMessages.length ? followupMessages.map((message) => (
                        <article className={`followup-bubble ${message.role}${message.pending ? " pending" : ""}`} key={message.id}>
                          <b>{message.role === "user" ? "你" : "助手"}</b>
                          <p>{message.content || (message.pending ? "…" : "")}</p>
                        </article>
                      )) : (
                        <p className="followup-empty">例如：止损要不要挪？还能不能持有？目标位怎么看？</p>
                      )}
                    </div>
                    <form
                      className="followup-composer"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void sendFollowup();
                      }}
                    >
                      <textarea
                        value={followupDraft}
                        onChange={(event) => setFollowupDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" && !event.shiftKey) {
                            event.preventDefault();
                            void sendFollowup();
                          }
                        }}
                        placeholder="就本次分析结果追问…"
                        rows={2}
                        disabled={followupSending}
                      />
                      <button disabled={followupSending || !followupDraft.trim()} type="submit">
                        {followupSending ? "发送中" : "发送"}
                      </button>
                    </form>
                  </section>
                )}

              </>
            ) : (
              <div className="empty-ai">
                <strong>等待 AI 任务</strong>
                <p>{workbenchMode === "review" ? "执行复盘后，这里会显示事实摘要、推理链路、结论建议与交易诊断。" : "执行区间分析或开始实时行情后，这里会显示对应的多阶段结果。"}</p>
              </div>
            ))}
            {decisionView === "bars" && (
              <section className="decision-detail-view bars-view">
                <header>
                  <strong>逐根分析</strong>
                  <span>{activeData?.stage1?.bar_summaries?.length ?? 0} 根</span>
                </header>
                {activeData?.stage1?.bar_summaries?.length ? (
                  <div className="bar-summary-list">
                    {[...activeData.stage1.bar_summaries]
                      .sort((a, b) => barSummaryDisplay(a).sequence - barSummaryDisplay(b).sequence)
                      .map((item, index) => {
                        const role = item.role || "structure";
                        const emphasis = role === "signal" || role === "trap" || role === "climax" || role === "entry";
                        const display = barSummaryDisplay(item);
                        return (
                          <article className={`bar-summary-item role-${role}${emphasis ? " emphasis" : ""}`} key={`${display.label}-${index}`}>
                            <header>
                              <b>{display.label}</b>
                              <span className="bar-role">{ROLE_LABELS[role] ?? role}</span>
                            </header>
                            <div className="bar-summary-meta">
                              <span>{item.bar_type || "—"}</span>
                              <span>{CONTEXT_LABELS[item.context_effect] ?? item.context_effect ?? "—"}</span>
                              {item.follow_through ? <span>跟进 {item.follow_through}</span> : null}
                              {item.trapped_side && item.trapped_side !== "none" ? <span>套牢 {item.trapped_side}</span> : null}
                            </div>
                            <p>{barSummaryText(item)}</p>
                          </article>
                        );
                      })}
                  </div>
                ) : (
                  <p className="view-empty">阶段一完成后显示最近 5 根的时间戳与日内开盘序号解析。</p>
                )}
              </section>
            )}
            {decisionView === "tree" && (
              <section className="decision-detail-view">
                <header><strong>决策图</strong><span>{(activeData?.stage1?.gate_trace.length ?? 0) + (activeData?.stage2?.decision_trace?.length ?? 0)} 个节点</span></header>
                {activeData?.stage1 ? (
                  <DecisionFlowViz
                    decisionTrace={activeData.stage2?.decision_trace}
                    formatEvidence={formatEvidence}
                    gateTrace={activeData.stage1.gate_trace}
                    graphTrail={activeData.audit?.graph_trail ?? []}
                    terminal={activeData.stage2?.terminal ?? {
                      outcome: activeData.stage1.gate_result,
                      reason: activeData.stage1.gate_result === "proceed" ? "Stage 1 已放行" : "Stage 1 未放行",
                      terminal_node: activeData.stage1.gate_trace.at(-1)?.node_id ?? "stage1",
                    }}
                  />
                ) : <p className="view-empty">阶段一完成后显示完整 LangGraph 决策图。</p>}
              </section>
            )}
            </div>
          </div>
        </aside>

      </section>

      {taskComposer && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => { setTaskComposer(null); setEditingTask(null); }}>
          <section aria-label={editingTask ? "编辑任务" : taskComposer.type === "review" ? "新建复盘任务" : "新建分析任务"} aria-modal="true" className="task-modal" ref={taskDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <strong>{editingTask ? "编辑任务" : taskComposer.type === "review" ? "新建复盘任务" : "新建分析任务"}</strong>
              </div>
              <button aria-label="关闭" onClick={() => { setTaskComposer(null); setEditingTask(null); }} type="button">×</button>
            </header>
            <div className="task-modal-body">
              <div className="task-modal-intro">
                <strong>{taskComposer.type === "review" ? "复盘任务草图" : "分析任务草图"}</strong>
              </div>
              <label>
                任务名称
                <input value={taskComposer.title} onChange={(event) => updateTaskComposer("title", event.target.value)} />
              </label>

              {taskComposer.type === "review" ? (
                <>
                  <div className="control-group">
                    <label>
                      周期
                      <select value={taskComposer.period} onChange={(event) => updateTaskComposer("period", event.target.value)}>
                        <option value="">自动</option>
                        {periods.map((period) => <option key={period} value={period}>{period}</option>)}
                      </select>
                    </label>
                  </div>
                  <section className="trade-pick-panel">
                    <header>
                      <strong>勾选交易</strong>
                      <span>{taskComposer.selectedTradeIds.length} 笔已选</span>
                      <button disabled={loading} onClick={() => fileInputRef.current?.click()} type="button">导入</button>
                    </header>
                    <div className="trade-pick-list">
                      {reviewComposerTrades.length ? reviewComposerTrades.map((trade) => (
                        <label key={trade.id} className="trade-pick-row">
                          <input checked={taskComposer.selectedTradeIds.includes(trade.id)} onChange={() => toggleReviewTradeId(trade.id)} type="checkbox" />
                          <span>
                            <b>{trade.contract_name}</b>
                            <small>{formatDate(trade.entered_at)} → {formatDate(trade.exited_at)} · {trade.direction === "long" ? "多" : "空"} · PnL {trade.reported_pnl ?? "—"}</small>
                          </span>
                        </label>
                      )) : (
                        <div className="sidebar-empty-note">
                          <p>没有可选交易。</p>
                          <button disabled={loading} onClick={() => fileInputRef.current?.click()} type="button">导入 Excel / CSV</button>
                        </div>
                      )}
                    </div>
                  </section>
                  <div className="control-group">
                    <label>开始时间<input aria-label="开始时间" readOnly value={taskComposer.start} type="datetime-local" /></label>
                    <label>结束时间<input aria-label="结束时间" readOnly value={taskComposer.end} type="datetime-local" /></label>
                  </div>
                  <p className="sidebar-empty-note">时间窗由勾选交易的开平仓时间自动生成。</p>
                </>
              ) : (
                <>
                  <div className="control-group">
                    <label>
                      标的
                      <select value={taskComposer.symbol} onChange={(event) => updateTaskComposer("symbol", event.target.value)}>
                        {symbols.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </label>
                  </div>
                  <div className="control-group">
                    <label>
                      周期
                      <select value={taskComposer.period} onChange={(event) => updateTaskComposer("period", event.target.value)}>
                        {periods.map((period) => <option key={period} value={period}>{period}</option>)}
                      </select>
                    </label>
                  </div>
                  <p className="sidebar-empty-note">运行时自动分析最近 100 根已收盘 K 线。</p>
                </>
              )}
            </div>
            <footer>
              <div className="footer-actions">
                <button onClick={() => { setTaskComposer(null); setEditingTask(null); }} type="button">取消</button>
                <button className="primary-action" onClick={saveTaskComposer} type="button">{editingTask ? "保存修改" : "保存任务"}</button>
              </div>
            </footer>
          </section>
        </div>
      )}

      {taskDetails && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setTaskDetails(null)}>
          <section aria-label="任务详情" aria-modal="true" className="task-modal" ref={taskDetailsDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <strong>{taskDetails.task.title}</strong>
              </div>
              <button aria-label="关闭" onClick={() => setTaskDetails(null)} type="button">×</button>
            </header>
            <div className="task-modal-body">
              <div className="task-modal-intro">
                <strong>{taskDetails.task.type === "review" ? "复盘任务详情卡" : "分析任务详情卡"}</strong>
              </div>
              <div className="task-summary-grid">
                <article><span>创建时间</span><strong>{formatDate(taskDetails.task.createdAt)}</strong></article>
                <article><span>状态</span><strong>{taskDetails.task.status === "running" ? "运行中" : taskDetails.task.status === "completed" ? "已完成" : taskDetails.task.status === "failed" ? "失败" : taskDetails.task.status === "cancelled" ? "已取消" : "待运行"}</strong></article>
                <article><span>类型</span><strong>{taskDetails.task.type === "review" ? "交易复盘" : "K 线分析"}</strong></article>
                <article><span>{taskDetails.task.type === "review" ? "方式" : "标的"}</span><strong>{taskDetails.task.config.type === "review" ? "逐笔交易" : taskDetails.task.config.symbol}</strong></article>
              </div>
              <div className="task-detail-list">
                {taskDetails.run && (
                  <article className="task-run-id-row">
                    <span>运行 ID</span>
                    <div>
                      <code>{taskDetails.run.id}</code>
                      <button aria-label={copiedRunId === taskDetails.run.id ? "已复制" : "复制运行 ID"} onClick={() => void copySelectedRunId(taskDetails.run!.id)} type="button">
                        {copiedRunId === taskDetails.run.id ? "已复制" : "复制"}
                      </button>
                    </div>
                  </article>
                )}
                {taskDetails.task.summary
                  .filter((item) => item.label !== "类型" && item.label !== (taskDetails.task.type === "review" ? "方式" : "标的"))
                  .map((item) => <article key={item.label}><span>{item.label}</span><strong>{item.value}</strong></article>)}
              </div>
              <div className="task-config-block">
                <strong>完整参数</strong>
                <pre>{JSON.stringify(taskDetails.task.config, null, 2)}</pre>
              </div>
            </div>
            <footer>
              <div className="footer-actions">
                <button onClick={() => setTaskDetails(null)} type="button">关闭</button>
                {taskDetails.task.status === "pending" && <button disabled={loading} onClick={() => void editPendingTask(taskDetails.task)} type="button">编辑</button>}
                {(taskDetails.task.type === "analysis"
                  ? taskDetails.task.status === "pending" || taskDetails.task.status === "completed" || taskDetails.task.status === "failed" || taskDetails.task.status === "cancelled"
                  : taskDetails.task.status === "pending" || taskDetails.task.status === "failed" || taskDetails.task.status === "cancelled") && (
                  <button className="primary-action" disabled={loading} onClick={() => void runSavedTask(taskDetails.task)} type="button">
                    {taskDetails.task.type === "analysis" ? "分析最新行情" : taskDetails.task.status === "pending" ? "运行" : "重试"}
                  </button>
                )}
                {taskDetails.task.status === "running" && <button disabled={loading} onClick={() => void cancelSavedTask(taskDetails.task)} type="button">取消运行</button>}
              </div>
            </footer>
          </section>
        </div>
      )}

      {historyOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setHistoryOpen(false)}>
          <section aria-label="分析历史" aria-modal="true" className="history-modal" ref={historyDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><strong>分析历史</strong><span>{analysisHistory.length} 条记录 · 数据库行情状态</span></div><button aria-label="关闭" onClick={() => setHistoryOpen(false)} type="button">×</button></header>
            <div className="collection-status-row">{collectionStatus.map((status) => <article className={status.status} key={status.symbol}><strong>{status.symbol}</strong><span>{status.status === "ok" ? "正常" : status.status === "failed" ? "失败" : "待采集"}</span><small>{status.stale_seconds == null ? "尚无数据" : `${Math.round(status.stale_seconds / 60)} 分钟前`}</small></article>)}</div>
            <div className="history-toolbar"><input aria-label="筛选分析历史" onChange={(event) => setHistoryQuery(event.target.value)} placeholder="筛选 ID、品种、周期、模式" value={historyQuery} /></div>
            <div className="history-list">{analysisHistory.length ? analysisHistory.filter((item) => `${item.run_id} ${item.symbol} ${item.period} ${item.mode}`.toLowerCase().includes(historyQuery.toLowerCase())).map((item) => {
              const isSelected = selectedHistoryId === item.run_id;
              return <article key={item.run_id} className={isSelected ? "selected" : ""}>
                <label><input checked={isSelected} onChange={(event) => setSelectedHistoryId(event.target.checked ? item.run_id : null)} type="radio" name="history-select" /><span><strong>{item.symbol} · {item.period}</strong><code className="history-id" title={item.run_id}>{item.run_id}</code><small>{formatDate(item.created_at)} · {item.mode} · {item.direction}</small></span></label>
              <div><button onClick={() => void restoreHistory(item.run_id)} type="button">打开</button></div>
              </article>;
            }) : <p>暂无分析历史。完成一次分析后会自动保存。</p>}</div>
            <footer><span>历史结果包含模型、证据与生成时间，可恢复到工作台。</span></footer>
          </section>
        </div>
      )}

      {loginOpen && <div className="modal-backdrop"><form aria-label="登录" aria-modal="true" className="login-modal" onSubmit={submitLogin} ref={loginDialogRef as React.RefObject<HTMLFormElement | null>} role="dialog"><header><strong>登录 PA</strong><span>交易数据与管理功能受保护</span></header><label>用户名<input autoComplete="username" onChange={(event) => setLoginName(event.target.value)} required value={loginName} /></label><label>密码<input autoComplete="current-password" minLength={8} onChange={(event) => setLoginPassword(event.target.value)} required type="password" value={loginPassword} /></label><button className="primary-action" disabled={loading} type="submit">登录</button></form></div>}

      {journalOpen && <div className="modal-backdrop" role="presentation" onMouseDown={() => setJournalOpen(false)}><section aria-label="交易日志" aria-modal="true" className="history-modal journal-modal" ref={journalDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><strong>交易日志</strong><span>维护、检索并进入复盘</span></div><button aria-label="关闭" onClick={() => setJournalOpen(false)} type="button">×</button></header>
        <div className="history-toolbar">
          <input aria-label="搜索交易日志" onChange={(event) => setJournalQuery(event.target.value)} placeholder="搜索合约、策略、账户、标签或笔记" value={journalQuery} />
          <button disabled={loading} onClick={() => fileInputRef.current?.click()} type="button">导入 Excel / CSV</button>
          <button onClick={() => void addManualTrade()} type="button">手工新增</button>
        </div>
        <div className="history-list">{availableTrades.filter((trade) => `${trade.contract_name} ${trade.strategy ?? ""} ${trade.account ?? ""} ${(trade.tags ?? []).join(" ")} ${trade.notes ?? ""}`.toLowerCase().includes(journalQuery.toLowerCase())).map((trade) => <article key={trade.id}><div><strong>{trade.contract_name} · {trade.direction === "long" ? "多" : "空"}</strong><small>{formatDate(trade.entered_at)} → {formatDate(trade.exited_at)} · {trade.entry_price} → {trade.exit_price}</small><p>{trade.strategy || "未标记策略"} · {trade.notes || "暂无笔记"}</p></div><div><button onClick={() => { syncReviewSessionFromTrades([trade.id]); setJournalOpen(false); setWorkbenchMode("review"); setMode("historical"); }} type="button">复盘</button><button onClick={() => void editJournalTrade(trade)} type="button">编辑</button><button onClick={() => void removeJournalTrade(trade)} type="button">删除</button></div></article>)}</div>
        {tradeImports.length > 0 && <section className="import-history"><strong>导入历史</strong>{tradeImports.map((batch) => <article key={batch.id}><span>{batch.file_name} · {formatDate(batch.created_at)}</span><small>导入 {batch.imported_rows} · 重复 {batch.skipped_duplicates} · 异常 {batch.invalid_rows}</small>{batch.errors.slice(0, 3).map((error) => <em key={`${error.row}-${error.message}`}>第 {error.row} 行：{error.message}</em>)}</article>)}</section>}
      </section></div>}

      {orderPickerOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setOrderPickerOpen(false)}>
          <section aria-label="选择交易记录" aria-modal="true" className="trade-modal" ref={orderPickerDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><strong>选择交易记录</strong><span>最近 {availableTrades.length} 笔</span></div><button aria-label="关闭" onClick={() => setOrderPickerOpen(false)} type="button">×</button></header>
            <div className="trade-list">
              {availableTrades.length ? availableTrades.map((trade) => (
                <label key={trade.id}>
                  <input
                    checked={selectedTradeIds.includes(trade.id)}
                    onChange={(event) => {
                      const nextIds = event.target.checked
                        ? [...selectedTradeIds, trade.id]
                        : selectedTradeIds.filter((id) => id !== trade.id);
                      syncReviewSessionFromTrades(nextIds);
                    }}
                    type="checkbox"
                  />
                  <span><strong>{trade.contract_name} · {trade.direction === "long" ? "多" : "空"}</strong><small>{formatDate(trade.entered_at)} · PnL {trade.reported_pnl ?? "—"}</small></span>
                </label>
              )) : <p>暂无可选择的交易记录。</p>}
            </div>
            <footer><span>已选择 {selectedTradeIds.length} 笔</span><button className="primary-action" onClick={() => { syncReviewSessionFromTrades(selectedTradeIds); setOrderPickerOpen(false); }} type="button">确认选择</button></footer>
          </section>
        </div>
      )}

      {tradePreview && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => { setPendingTradeFile(null); setTradePreview(null); }}>
          <section aria-label="交易导入预览" aria-modal="true" className="trade-modal import-preview-modal" ref={tradePreviewDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                <strong>{tradePreview.file_name}</strong>
                <span>共 {tradePreview.total_rows} 笔 · 有效 {tradePreview.valid_rows} 笔 · 异常 {tradePreview.invalid_rows} 笔</span>
              </div>
              <button aria-label="关闭" onClick={() => { setPendingTradeFile(null); setTradePreview(null); }} type="button">×</button>
            </header>
            <div className="usage-table">
              <table>
                <thead><tr><th>合约</th><th>开仓时间</th><th>方向</th><th>数量</th><th>PnL</th></tr></thead>
                <tbody>
                  {tradePreview.rows.slice(0, 8).map((trade) => (
                    <tr key={trade.source_trade_id}>
                      <td>{trade.contract_name}</td>
                      <td>{formatDate(trade.entered_at)}</td>
                      <td>{trade.direction === "long" ? "多" : "空"}</td>
                      <td>{String(trade.size)}</td>
                      <td>{trade.reported_pnl == null ? "—" : String(trade.reported_pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {tradePreview.errors.slice(0, 5).map((item) => (
              <p className="review-notice error" key={`${item.row}-${item.message}`}>第 {item.row} 行：{item.message}</p>
            ))}
            <footer>
              <button onClick={() => { setPendingTradeFile(null); setTradePreview(null); }} type="button">取消</button>
              <button className="primary-action" disabled={loading || tradePreview.valid_rows === 0} onClick={() => void confirmImport()} type="button">确认导入</button>
            </footer>
          </section>
        </div>
      )}

      {debugPreview && (
        <div className="modal-backdrop" role="presentation">
          <section aria-label="Debug 调用确认" aria-modal="true" className="debug-modal" ref={debugDialogRef} role="dialog">
            <header><div><strong>Debug · 确认 LLM 调用</strong><span>{debugPreview.model ? `${debugPreview.model.name} · ${debugPreview.model.model}` : "未配置模型"}</span></div></header>
            <div className="debug-token-grid">
              <article><span>预计输入</span><strong>{debugPreview.estimated_prompt_tokens}</strong><small>tokens</small></article>
              <article><span>最大输出</span><strong>{debugPreview.estimated_max_completion_tokens}</strong><small>tokens</small></article>
              <article><span>预计上限</span><strong>{debugPreview.estimated_prompt_tokens + debugPreview.estimated_max_completion_tokens}</strong><small>tokens</small></article>
            </div>
            <label className="prompt-preview"><span>发送给 LLM 的输入</span><textarea readOnly value={JSON.stringify(debugPreview.llm_input, null, 2)} /></label>
            <footer><button onClick={() => closeDebug(false)} type="button">取消</button><button className="primary-action" onClick={() => closeDebug(true)} type="button">确认并执行</button></footer>
          </section>
        </div>
      )}

      {personalOpen && personalSettings && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setPersonalOpen(false)}>
          <section aria-label="个人中心" aria-modal="true" className="personal-modal" ref={personalDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><strong>个人中心</strong><span>模型、Token 与交易数据</span></div><button aria-label="关闭" onClick={() => setPersonalOpen(false)} type="button">×</button></header>
            <nav className="personal-tabs"><button className={personalTab === "models" ? "active" : ""} onClick={() => setPersonalTab("models")} type="button">模型配置</button><button className={personalTab === "usage" ? "active" : ""} onClick={() => setPersonalTab("usage")} type="button">Token 用量</button><button className={personalTab === "trades" ? "active" : ""} onClick={() => setPersonalTab("trades")} type="button">交易记录</button></nav>
            {personalTab === "models" ? <div className="personal-content">
              <label className="debug-switch"><input checked={personalSettings.debug_enabled} onChange={(event) => setPersonalSettings({ ...personalSettings, debug_enabled: event.target.checked })} type="checkbox" /><span><strong>Debug 二次确认</strong><small>展示 LLM 输入和预计 Token 后再执行</small></span></label>
              <div className="model-list">{personalSettings.models.map((model, index) => <article className="model-card" key={model.id}>
                <label><span>配置名称</span><input value={model.name} onChange={(event) => updateModel(index, "name", event.target.value)} /></label>
                <label><span>服务商</span><select value={model.provider} onChange={(event) => updateProvider(index, event.target.value as PersonalSettings["models"][number]["provider"])}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="anthropic">Anthropic</option><option value="gemini">Gemini</option><option value="compatible">OpenAI Compatible</option></select></label>
                <label><span>模型</span>{model.provider === "compatible" ? <input placeholder="输入模型 ID" value={model.model} onChange={(event) => updateModel(index, "model", event.target.value)} /> : <select value={model.model} onChange={(event) => updateModel(index, "model", event.target.value)}>{providerModels[model.provider].map((modelName) => <option key={modelName} value={modelName}>{modelName}</option>)}</select>}</label>
                <label><span>Base URL</span><input placeholder="使用服务商默认地址" value={model.base_url ?? ""} onChange={(event) => updateModel(index, "base_url", event.target.value)} /></label>
                <label className="wide"><span>API Key {model.api_key_masked && `· 当前 ${model.api_key_masked}`}</span><input autoComplete="off" placeholder={model.has_api_key ? "留空则保留当前 Key" : "输入 API Key"} type="password" value={model.api_key ?? ""} onChange={(event) => updateModel(index, "api_key", event.target.value)} /></label>
                <label className="active-model"><input checked={personalSettings.active_model_id === model.id} name="active-model" onChange={() => setPersonalSettings({ ...personalSettings, active_model_id: model.id })} type="radio" />设为当前模型</label>
                <button className="remove-model" onClick={() => setPersonalSettings({ ...personalSettings, models: personalSettings.models.filter((item) => item.id !== model.id), active_model_id: personalSettings.active_model_id === model.id ? null : personalSettings.active_model_id })} type="button">删除</button>
              </article>)}</div>
              <button onClick={addModel} type="button">＋ 添加模型</button>
            </div> : personalTab === "usage" ? <div className="personal-content">
              <div className="usage-summary"><article><span>总 Token</span><strong>{tokenUsage?.total_tokens ?? 0}</strong></article><article><span>输入</span><strong>{tokenUsage?.prompt_tokens ?? 0}</strong></article><article><span>输出</span><strong>{tokenUsage?.completion_tokens ?? 0}</strong></article><article><span>分析次数</span><strong>{tokenUsage?.analysis_count ?? 0}</strong></article></div>
              <div className="usage-table"><table><thead><tr><th>时间</th><th>模型</th><th>分析</th><th>输入</th><th>输出</th><th>合计</th></tr></thead><tbody>{tokenUsage?.records.map((record) => <tr key={record.id}><td>{formatDate(record.occurred_at)}</td><td>{record.model ?? "确定性骨架"}</td><td>{record.symbol} · {record.period}</td><td>{record.prompt_tokens}</td><td>{record.completion_tokens}</td><td>{record.total_tokens}</td></tr>)}</tbody></table></div>
            </div> : <div className="personal-content trade-upload-content">
              <section className="trade-upload-card">
                <div><strong>上传交易记录</strong><small>支持 .xlsx 和 .csv，确认前会先校验并预览。</small></div>
                <button disabled={loading} onClick={() => fileInputRef.current?.click()} type="button">选择 Excel / CSV</button>
              </section>
              <section className="import-history"><strong>导入历史</strong>{tradeImports.length ? tradeImports.map((batch) => <article key={batch.id}><span>{batch.file_name} · {formatDate(batch.created_at)}</span><small>导入 {batch.imported_rows} · 重复 {batch.skipped_duplicates} · 异常 {batch.invalid_rows}</small></article>) : <p>暂无导入记录。</p>}</section>
            </div>}
            <footer><span>{personalTab === "trades" ? "导入成功后，交易会自动出现在新建复盘任务中。" : "密钥仅保存在本机后端，不会通过读取接口返回。"}</span>{personalTab === "models" && <button className="primary-action" disabled={loading} onClick={() => void savePersonalCenter()} type="button">保存设置</button>}</footer>
          </section>
        </div>
      )}

      {adminOpen && orchestration && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setAdminOpen(false)}>
          <section aria-label="管理后台" aria-modal="true" className="admin-modal" ref={adminDialogRef} role="dialog" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><strong>阶段分析管理后台</strong><span>编排流程 · Stage 文本 · 在线编辑</span></div><button aria-label="关闭" onClick={() => setAdminOpen(false)} type="button">×</button></header>
            <div className="admin-layout">
              <aside className="pipeline-browser">
                <div className="pipeline-title"><strong>分析编排</strong><span>{orchestration.stages.length} 个节点</span></div>
                {orchestration.stages.map((stage, index) => <article className={`pipeline-stage kind-${stage.kind}`} key={stage.id}>
                  <header><span className="stage-index">{String(index + 1).padStart(2, "0")}</span><div><strong>{stage.name}</strong><small>{stage.description}</small></div><span className="stage-basis-badge">{stageBasisLabel(stage)}</span></header>
                  <div className="stage-basis">
                    <strong>依据</strong>
                    <ul>{buildNodeBasis(stage).map((item) => <li key={item}>{item}</li>)}</ul>
                  </div>
                  {stage.prompt_files.length ? <div className="stage-files">{stage.prompt_files.map((file) => <button className={promptDocument?.filename === file.filename ? "active" : ""} key={`${stage.id}-${file.filename}`} onClick={() => void selectPromptFile(file.filename)} title={file.condition} type="button"><span>{file.filename}</span><small>{file.placement} · {file.condition}</small></button>)}</div> : <p>该节点不加载文本</p>}
                </article>)}
              </aside>
              <section className="prompt-editor">
                {promptDocument ? <>
                  <header><div><strong>{promptDocument.filename}</strong><span>{promptDocument.size.toLocaleString()} bytes · version {promptDocument.version}</span></div><div><button onClick={() => void getPromptVersions(promptDocument.filename).then(setPromptVersions)} type="button">查看版本</button><span className="editor-status">在线编辑</span></div></header>
                  <textarea aria-label="提示词文本内容" spellCheck={false} value={promptDocument.content} onChange={(event) => setPromptDocument({ ...promptDocument, content: event.target.value })} />
                </> : <div className="prompt-empty">选择左侧文本开始编辑</div>}
                {promptVersions.length > 0 && <div className="prompt-history"><strong>版本历史</strong>{promptVersions.map((version) => <article key={version.id}><span>{formatDate(version.created_at)} · {version.actor} · {version.action}</span><code>{version.version}</code><button onClick={() => void restorePromptVersion(version)} type="button">回滚</button></article>)}</div>}
              </section>
            </div>
            <footer><span>{adminNotice ?? "修改会影响后续 Stage 分析，请确认后保存。"}</span><button className="primary-action" disabled={loading || !promptDocument} onClick={() => void savePromptDocument()} type="button">保存文本</button></footer>
          </section>
        </div>
      )}

      {(importNotice || reviewNotice || csvError || error) && (
        <section className="toast-row">
          {importNotice && <div className="toast success" role="status"><strong>导入成功</strong><span>{importNotice}</span></div>}
          {reviewNotice && <div className="toast error" role="alert"><strong>复盘提示</strong><span>{reviewNotice}</span></div>}
          {csvError && <div className="toast error" role="alert"><strong>导入失败</strong><span>{csvError}</span></div>}
          {error && <div className="toast error" role="alert"><strong>{error.code}</strong><span>{error.message}</span>{error.request_id && <small>Request ID: {error.request_id}</small>}</div>}
        </section>
      )}
    </main>
  );
}

export default ReviewWorkbenchShell;
