import type { AnalysisTask, Period } from "./types";

export interface SidebarTaskData {
  id: string;
  type: "analysis" | "review";
  title: string;
  description: string;
  createdAt: string;
  locked: true;
  status: AnalysisTask["status"];
  version: number;
  config: {
    type: "analysis" | "review";
    title: string;
    description: string;
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
  };
  summary: Array<{ label: string; value: string }>;
}

export function sidebarTaskFromApi(task: AnalysisTask): SidebarTaskData {
  const config = task.config;
  const review = task.kind === "review";
  const period = review ? String((config.periods as string[] | undefined)?.[0] ?? "5m") : String(config.period ?? "5m");
  const symbol = String(config.symbol ?? "ES");
  // Analysis tasks created by the realtime-task UI persist only symbol/period.
  // Treat an absent mode as realtime; only an explicit historical mode belongs
  // in the range workbench.
  const analysisMode: "range" | "live" = !review && config.analysis_mode !== "historical" ? "live" : "range";
  return {
    id: task.id,
    type: task.kind,
    title: task.title,
    description: task.description,
    createdAt: task.created_at,
    locked: true,
    status: task.status,
    version: task.version,
    config: {
      type: task.kind,
      title: task.title,
      description: task.description,
      symbol,
      period: period as Period,
      start: String(config.start ?? "").slice(0, 16),
      end: String(config.end ?? "").slice(0, 16),
      selectedTradeIds: (config.selected_trade_ids as string[] | undefined) ?? [],
      selectedTradeSymbol: symbol,
      includeOrders: true,
      overlayOrders: true,
      analysisMode,
      streamEnabled: analysisMode === "live",
    },
    summary: review
      ? [{ label: "方式", value: "逐笔交易" }, { label: "周期", value: period }, { label: "交易", value: `${((config.selected_trade_ids as string[] | undefined) ?? []).length} 笔` }]
      : [{ label: "类型", value: "最新行情" }, { label: "标的", value: symbol }, { label: "周期", value: period }],
  };
}

export function normalizeAnalysisSymbol(value: string): string {
  return value.trim().toUpperCase();
}

export function findLiveAnalysisTask<T extends { type: "analysis" | "review"; config: { symbol: string; period: string } }>(
  tasks: T[],
  symbol: string,
  period: string,
): T | undefined {
  const normalized = normalizeAnalysisSymbol(symbol);
  return tasks.find(
    (task) =>
      task.type === "analysis"
      && normalizeAnalysisSymbol(task.config.symbol) === normalized
      && task.config.period === period,
  );
}

export function taskActions(status: AnalysisTask["status"], kind: AnalysisTask["kind"] = "review"): string[] {
  if (status === "pending") return ["run"];
  return ["open"];
}
