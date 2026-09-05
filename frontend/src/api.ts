import type {
  ApiError,
  DemoAnalysisResponse,
  HealthResponse,
  HistoricalQuery,
  MarketRange,
  Period,
  Trade,
  TradeImportResult,
  TradePreview,
  DebugPreview,
  PersonalSettings,
  TokenUsageSummary,
  OrchestrationView,
  PromptFileDocument,
  AnalysisHistorySummary,
  CollectionStatus,
  AlertRule,
  AlertRecord,
  UserSession,
  PromptVersion,
  TradeImportBatch,
  AnalysisStreamEvent,
  FollowupStreamEvent,
  FollowupMessage,
  Bar,
  AnalysisTask,
  AnalysisTaskPage,
  AnalysisRun,
  AnalysisRunStartItem,
  AnalysisRunListItem,
  AnalysisRunDetail,
} from "./types";

export function createTraceId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `trace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
  traceId: string = createTraceId(),
): Promise<Response> {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  if (!headers.has("X-Trace-ID")) headers.set("X-Trace-ID", traceId);
  return globalThis.fetch(input, { ...init, headers });
}

function withResponseTrace(error: ApiError, response: Response): ApiError {
  const traceId = response.headers.get("X-Trace-ID") || response.headers.get("X-Request-ID");
  if (traceId) {
    error.trace_id ??= traceId;
    error.request_id ??= traceId;
  }
  return error;
}

async function parseResponse<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    const error = body as ApiError;
    throw withResponseTrace(error, response);
  }
  return body as T;
}

export async function getHealth(): Promise<HealthResponse> {
  return parseResponse<HealthResponse>(await apiFetch("/api/v1/health"));
}

export async function getMarketBars(
  symbol: string,
  period: Period,
  start: string,
  end: string,
  signal?: AbortSignal,
  includePartial: boolean = false,
  requestKind: "live_poll" | "history_range" | "history_prefetch" | "future_prefetch" = "history_range",
): Promise<MarketRange> {
  const params = new URLSearchParams({ symbol, period, start, end });
  if (includePartial) params.set("include_partial", "true");
  const url = `/api/v1/market/bars?${params.toString()}`;
  const requestId = createTraceId();
  const startedAt = performance.now();
  let response: Response | undefined;
  console.info("[market] request_start", { requestId, kind: requestKind, symbol, period, start, end, includePartial, url });
  try {
    response = await apiFetch(url, { signal, headers: { "X-Market-Request-Kind": requestKind } }, requestId);
    const market = await parseResponse<MarketRange>(response);
    console.info("[market] request_success", { requestId: response.headers.get("X-Trace-ID") || response.headers.get("X-Request-ID") || requestId, kind: requestKind, symbol, period, status: response.status, durationMs: Math.round(performance.now() - startedAt), bars: market.bars.length, coverage: market.coverage });
    return market;
  } catch (caught) {
    const apiError = caught as Partial<ApiError>;
    console.error("[market] request_failure", { requestId: response?.headers.get("X-Trace-ID") || response?.headers.get("X-Request-ID") || requestId, kind: requestKind, symbol, period, start, end, includePartial, url, status: response?.status ?? null, durationMs: Math.round(performance.now() - startedAt), code: apiError?.code ?? null, errorType: caught instanceof Error ? caught.name : typeof caught, error: apiError?.message ?? (caught instanceof Error ? caught.message : String(caught)) });
    throw caught;
  }
}

export async function analyzeRange(query: HistoricalQuery, signal?: AbortSignal): Promise<DemoAnalysisResponse> {
  return parseResponse<DemoAnalysisResponse>(await apiFetch("/api/v1/demo/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query), signal }));
}

export async function analyzeRangeStream(query: HistoricalQuery, onEvent: (event: AnalysisStreamEvent) => void, signal?: AbortSignal): Promise<DemoAnalysisResponse> {
  const response = await apiFetch("/api/v1/demo/analyze/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query), signal });
  if (!response.ok || !response.body) return parseResponse<DemoAnalysisResponse>(response);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: DemoAnalysisResponse | undefined;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as AnalysisStreamEvent;
      onEvent(event);
      if (event.type === "error") throw withResponseTrace({ code: event.code ?? "analysis_error", message: event.message, details: event.details ?? [] }, response);
      if (event.result) result = event.result;
    }
    if (done) break;
  }
  if (!result) throw { code: "stream_incomplete", message: "分析数据流意外结束" } satisfies ApiError;
  return result;
}

export async function getDebugPreview(query: HistoricalQuery): Promise<DebugPreview> {
  return parseResponse<DebugPreview>(await apiFetch("/api/v1/analysis/debug-preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query) }));
}

export async function getPersonalSettings(): Promise<PersonalSettings> {
  return parseResponse<PersonalSettings>(await apiFetch("/api/v1/personal/settings"));
}

export async function savePersonalSettings(settings: PersonalSettings): Promise<PersonalSettings> {
  return parseResponse<PersonalSettings>(await apiFetch("/api/v1/personal/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) }));
}

export async function getTokenUsage(): Promise<TokenUsageSummary> {
  return parseResponse<TokenUsageSummary>(await apiFetch("/api/v1/personal/token-usage?limit=200"));
}

export async function getAdminOrchestration(): Promise<OrchestrationView> {
  return parseResponse<OrchestrationView>(await apiFetch("/api/v1/admin/orchestration"));
}

export async function getAdminPromptFile(filename: string): Promise<PromptFileDocument> {
  const params = new URLSearchParams({ filename });
  return parseResponse<PromptFileDocument>(await apiFetch(`/api/v1/admin/prompt-file?${params.toString()}`));
}

export async function saveAdminPromptFile(document: PromptFileDocument): Promise<PromptFileDocument> {
  const params = new URLSearchParams({ filename: document.filename });
  return parseResponse<PromptFileDocument>(await apiFetch(`/api/v1/admin/prompt-file?${params.toString()}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: document.content, expected_version: document.version }) }));
}

function tradeForm(file: File): FormData {
  const form = new FormData();
  form.append("file", file);
  return form;
}

export async function previewTradeImport(file: File): Promise<TradePreview> {
  return parseResponse<TradePreview>(await apiFetch("/api/v1/trades/import/preview", { method: "POST", body: tradeForm(file) }));
}

export async function confirmTradeImport(file: File): Promise<TradeImportResult> {
  return parseResponse<TradeImportResult>(await apiFetch("/api/v1/trades/import/confirm", { method: "POST", body: tradeForm(file) }));
}

export async function getTrades(start: string, end: string): Promise<Trade[]> {
  const params = new URLSearchParams({ start, end });
  return parseResponse<Trade[]>(await apiFetch(`/api/v1/trades?${params.toString()}`));
}

export async function getRecentTrades(): Promise<Trade[]> {
  return parseResponse<Trade[]>(await apiFetch("/api/v1/trades/recent?limit=200"));
}

export async function updateTrade(tradeId: string, update: Partial<Trade>): Promise<Trade> {
  return parseResponse(await apiFetch(`/api/v1/trades/${encodeURIComponent(tradeId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update) }));
}

export async function deleteTrade(tradeId: string): Promise<void> {
  const response = await apiFetch(`/api/v1/trades/${encodeURIComponent(tradeId)}`, { method: "DELETE" });
  if (!response.ok) throw withResponseTrace(await response.json() as ApiError, response);
}

export async function createTrade(trade: Record<string, unknown>): Promise<Trade> {
  return parseResponse(await apiFetch("/api/v1/trades", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(trade) }));
}
export async function getTradeImports(): Promise<TradeImportBatch[]> { return parseResponse(await apiFetch("/api/v1/trades/imports?limit=100")); }

export async function createAnalysisTask(payload: { kind: "analysis" | "review"; title: string; description?: string; config: Record<string, unknown>; }): Promise<AnalysisTask> {
  return parseResponse(await apiFetch("/api/v1/analysis-tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
}

export async function updateAnalysisTask(taskId: string, payload: { version: number; title: string; description?: string; config: Record<string, unknown>; }): Promise<AnalysisTask> {
  return parseResponse(await apiFetch(`/api/v1/analysis-tasks/${encodeURIComponent(taskId)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
}

export async function listAnalysisTasks(): Promise<AnalysisTaskPage> {
  return parseResponse(await apiFetch("/api/v1/analysis-tasks?limit=200"));
}

export async function ensureLiveAnalysisTask(payload: { symbol: string; period: string; title?: string; }): Promise<AnalysisTask> {
  return parseResponse(await apiFetch("/api/v1/analysis-tasks/ensure-live", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }));
}


export async function startAnalysisTaskRun(taskId: string): Promise<AnalysisRunStartItem[]> {
  return parseResponse(await apiFetch(`/api/v1/analysis-tasks/${encodeURIComponent(taskId)}/runs`, { method: "POST" }));
}

const TERMINAL_RUN_STATUSES = new Set(["completed", "completed_with_warnings", "degraded", "failed", "cancelled", "timed_out"]);

export async function waitForRunsTerminal(
  runIds: string[],
  options: { intervalMs: number; timeoutMs: number; signal: AbortSignal },
): Promise<AnalysisRunDetail[]> {
  const startedAt = Date.now();
  const details = new Map<string, AnalysisRunDetail>();
  let pending = [...runIds];
  while (pending.length > 0) {
    if (options.signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (Date.now() - startedAt >= options.timeoutMs) throw new Error("等待分析运行终态超时");
    const polled = await Promise.all(pending.map(getRunDetail));
    for (const detail of polled) details.set(detail.run_id, detail);
    pending = pending.filter((runId) => !TERMINAL_RUN_STATUSES.has(details.get(runId)?.status ?? ""));
    if (pending.length > 0 && options.intervalMs > 0) {
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(resolve, options.intervalMs);
        options.signal.addEventListener("abort", () => {
          window.clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      });
    }
  }
  return runIds.map((runId) => details.get(runId)!);
}

export async function listAnalysisTaskRuns(taskId: string): Promise<AnalysisRunListItem[]> {
  return parseResponse(await apiFetch(`/api/v1/analysis-tasks/${encodeURIComponent(taskId)}/runs`));
}

export async function getRunDetail(runId: string): Promise<AnalysisRunDetail> {
  return parseResponse(await apiFetch(`/api/v1/analysis-runs/${encodeURIComponent(runId)}`));
}

export async function cancelRun(runId: string): Promise<AnalysisRun> {
  return parseResponse(await apiFetch(`/api/v1/analysis-runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }));
}

export async function getAnalysisHistory(): Promise<AnalysisHistorySummary[]> {
  return parseResponse<AnalysisHistorySummary[]>(await apiFetch("/api/v1/analyses?limit=200"));
}

export async function getAnalysisDetail(runId: string): Promise<DemoAnalysisResponse & { favorite?: boolean; notes?: string; tags?: string[]; llm_transcript?: DemoAnalysisResponse["llm_transcript"]; }> {
  return parseResponse(await apiFetch(`/api/v1/analyses/${encodeURIComponent(runId)}`));
}


export async function getCollectionStatus(): Promise<CollectionStatus[]> { return parseResponse<CollectionStatus[]>(await apiFetch("/api/v1/market/status")); }
export async function getAlertRules(): Promise<AlertRule[]> { return parseResponse(await apiFetch("/api/v1/alert-rules")); }
export async function saveAlertRule(rule: Omit<AlertRule, "id" | "last_run_at" | "created_at" | "updated_at">, id?: string): Promise<AlertRule> { return parseResponse(await apiFetch(id ? `/api/v1/alert-rules/${id}` : "/api/v1/alert-rules", { method: id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rule) })); }
export async function removeAlertRule(id: string): Promise<void> { const response = await apiFetch(`/api/v1/alert-rules/${id}`, { method: "DELETE" }); if (!response.ok) throw withResponseTrace(await response.json() as ApiError, response); }
export async function getAlerts(): Promise<AlertRecord[]> { return parseResponse(await apiFetch("/api/v1/alerts?limit=200")); }
export async function markAlertRead(id: string): Promise<AlertRecord> { return parseResponse(await apiFetch(`/api/v1/alerts/${id}/read`, { method: "PATCH" })); }

export async function getCurrentUser(): Promise<UserSession> { return parseResponse(await apiFetch("/api/v1/auth/me")); }
export async function login(username: string, password: string): Promise<UserSession> { return parseResponse(await apiFetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) })); }
export async function logout(): Promise<void> { await apiFetch("/api/v1/auth/logout", { method: "POST" }); }
export async function getPromptVersions(filename: string): Promise<PromptVersion[]> { const params = new URLSearchParams({ filename }); return parseResponse(await apiFetch(`/api/v1/admin/prompt-versions?${params}`)); }
export async function rollbackPromptVersion(filename: string, versionId: string): Promise<PromptFileDocument> { const params = new URLSearchParams({ filename }); return parseResponse(await apiFetch(`/api/v1/admin/prompt-versions/${versionId}/rollback?${params}`, { method: "POST" })); }

export async function sendFollowupStream(
  runId: string,
  payload: { question: string; bars: Bar[]; symbol?: string; period?: string },
  onEvent: (event: FollowupStreamEvent) => void,
  signal?: AbortSignal,
): Promise<string> {
  const response = await apiFetch(`/api/v1/analyses/${encodeURIComponent(runId)}/followup/stream`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal });
  if (!response.ok || !response.body) { throw withResponseTrace(await response.json() as ApiError, response); }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as FollowupStreamEvent;
      onEvent(event);
      if (event.type === "error") { throw withResponseTrace({ code: event.code ?? "followup_error", message: event.message ?? "追问失败", details: event.details ?? [] }, response); }
      if (event.type === "delta" && event.content) answer += event.content;
      if (event.type === "done" && event.content) answer = event.content;
    }
    if (done) break;
  }
  if (!answer.trim()) throw { code: "followup_empty", message: "追问助手未返回内容" } satisfies ApiError;
  return answer;
}

export async function getFollowupHistory(runId: string): Promise<FollowupMessage[]> {
  return parseResponse(await apiFetch(`/api/v1/analyses/${encodeURIComponent(runId)}/followup/history`));
}
