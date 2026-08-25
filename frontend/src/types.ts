export type Direction = "bullish" | "bearish" | "neutral";
export type Period = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d";

export interface HistoricalQuery {
  symbol: string;
  period: Period;
  start: string;
  end: string;
  analysis_mode?: "trade_review" | "historical" | "realtime";
  trades?: Array<{
    trade_id: string;
    symbol: string;
    entered_at: string;
    exited_at: string;
    direction: "long" | "short";
    entry_price: number;
    exit_price: number;
    size: number;
    reported_pnl: number | null;
  }>;
}

export interface Bar {
  timestamp: string;
  timeframe?: string | null;
  session?: "CME" | "US_EQUITY_RTH" | null;
  day_index?: number | null;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface BarRef {
  bar_timestamp: string;
  timeframe: string;
  session: "CME" | "US_EQUITY_RTH";
  day_index: number;
}

export interface BarRange { start: BarRef; end: BarRef; }

export interface MarketCoverage {
  source_period: string;
  expected_bars: number;
  actual_bars: number;
  complete: boolean;
  missing_buckets: string[];
}

export interface MarketRange {
  symbol: string;
  period: string;
  bars: Bar[];
  coverage: MarketCoverage;
}

/** Chart market feed — independent of LLM analysis lifecycle. */
export interface FeedState {
  kind: "history" | "live";
  symbol: string;
  period: Period | "";
  bars: Bar[];
  lastClosedTs: string | null;
  pollError: string | null;
}

export type AnalysisRunMode = "trade_review" | "historical" | "realtime";
export type AnalysisTaskStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface AnalysisTask {
  id: string;
  kind: "analysis" | "review";
  title: string;
  description: string;
  status: AnalysisTaskStatus;
  config: Record<string, unknown>;
  latest_execution_id: string | null;
  latest_analysis_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface AnalysisTaskPage {
  items: AnalysisTask[];
  next_cursor: string | null;
}

export type AnalysisExecutionStatus = "queued" | "running" | "completed" | "completed_with_warnings" | "degraded" | "failed" | "cancel_requested" | "cancelled" | "timed_out";
export interface AnalysisExecution {
  analysis_id: string;
  task_id: string | null;
  parent_analysis_id: string | null;
  work_key: string | null;
  sequence: number | null;
  status: AnalysisExecutionStatus;
  current_stage: string;
  failure_stage: string | null;
  failure_code: string | null;
  failure_message: string | null;
  terminal_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface AnalysisExecutionListItem {
  analysis_id: string;
  task_id: string | null;
  parent_analysis_id: string | null;
  work_key: string | null;
  sequence: number | null;
  status: AnalysisExecutionStatus | string;
  created_at: string;
  completed_at: string | null;
  result_id: string | null;
  direction: string | null;
  symbol: string | null;
  period: string | null;
}

export interface AnalysisResultDetail {
  analysis_id: string;
  task_id: string | null;
  parent_analysis_id: string | null;
  work_key: string | null;
  sequence: number | null;
  status: string;
  mode: string;
  symbol: string;
  period: string;
  direction: string;
  terminal_outcome: string;
  created_at: string;
  updated_at: string;
  result: DemoAnalysisResponse | ReviewExecutionResult;
}
export interface AnalysisTaskPreview {
  snapshot_id: string; confirmation_id: string; expires_at: string;
  resolved_symbol: string; bars_hash: string; bar_count: number;
}
export interface AnalysisExecutionEvent {
  sequence: number; type: string; stage: string; message: string;
  payload: { result_id?: string; result?: DemoAnalysisResponse | ReviewExecutionResult; [key: string]: unknown };
  terminal: boolean;
}
export interface ReviewExecutionResult {
  query: { analysis_mode: "trade_review"; symbol: string; period: string };
  review_children: DemoAnalysisResponse[];
  review_result: NonNullable<DemoAnalysisResponse["review_result"]>;
  status: AnalysisExecutionStatus;
}

/** On-demand analysis run state (Snapshot-based). */
export interface AnalysisState {
  status: "idle" | "running" | "done" | "error";
  mode: AnalysisRunMode | null;
  snapshotAsOf: string | null;
  stage1?: Stage1Result;
  stage2?: Stage2Result;
  result?: DemoAnalysisResponse;
  error?: ApiError | null;
}

export interface TradeMarker {
  timestamp: string;
  position: "aboveBar" | "belowBar" | "inBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  color: string;
  text: string;
}

export interface BasicAnalysis {
  bar_count: number;
  start: string;
  end: string;
  first_open: number;
  latest_close: number;
  period_high: number;
  period_low: number;
  change_percent: number;
  bullish_bars: number;
  bearish_bars: number;
  neutral_bars: number;
  direction: Direction;
  method: string;
}

export interface DemoAnalysisResponse {
  query: HistoricalQuery;
  resolved_symbol: string;
  analysis: BasicAnalysis;
  bars: Bar[];
  trade_markers?: TradeMarker[];
  analysis_id?: string;
  status?: "completed" | "failed";
  snapshot?: AnalysisSnapshot;
  stage1?: Stage1Result;
  stage2?: Stage2Result;
  review_result?: TradeReviewResult[] | null;
  audit?: AnalysisAudit;
  llm_transcript?: {
    stage1: { reasoning: string; content: string };
    stage2: { reasoning: string; content: string };
  };
}

export interface AnalysisStreamEvent {
  type: "status" | "market" | "stage1" | "stage2" | "result" | "error" | "llm_delta";
  stage: "prepare" | "market" | "stage1" | "stage2" | "persist" | "complete";
  message: string;
  kind?: "reasoning" | "content";
  text?: string;
  code?: string;
  details?: Array<Record<string, unknown>>;
  resolved_symbol?: string;
  bars?: Bar[];
  analysis?: BasicAnalysis;
  stage1?: Stage1Result;
  stage2?: Stage2Result;
  result?: DemoAnalysisResponse;
}

export interface FollowupMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

export interface FollowupStreamEvent {
  type: "status" | "delta" | "done" | "error";
  message?: string;
  content?: string;
  analysis_id?: string;
  turn_count?: number;
  code?: string;
  details?: Array<Record<string, unknown>>;
}

export interface AnalysisHistorySummary {
  analysis_id: string;
  mode: string;
  symbol: string;
  period: string;
  status: string;
  direction: string;
  favorite: boolean;
  notes: string;
  tags: string[];
  task_id?: string | null;
  execution_id?: string | null;
  result_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CollectionStatus {
  symbol: string;
  latest_closed_at: string | null;
  last_attempt_at: string | null;
  last_success_at: string | null;
  status: string;
  last_error: string | null;
  consecutive_failures: number;
  stale_seconds: number | null;
}

export interface AlertRule {
  id: string; name: string; symbol: string; period: Period; trigger_type: "bar_close" | "interval"; threshold: number; enabled: boolean; last_run_at: string | null; created_at: string; updated_at: string;
}

export interface AlertRecord {
  id: string; rule_id: string; bar_opened_at: string; signal_key: string; title: string; message: string; evidence: Record<string, unknown> | null; is_read: boolean; delivery_status: string; created_at: string;
}

export interface AnalysisSnapshot {
  analysis_id: string;
  mode: "trade_review" | "historical" | "realtime";
  trigger: { type: "manual" | "bar_closed" | "structure_changed" | "periodic"; occurred_at: string };
  market: { symbol: string; contract: string; period: Period; bars: Bar[]; indicators: Record<string, unknown>; tick_size: number | null };
  trades: unknown[];
  previous_context: { stage1: Record<string, unknown> | null; stage2: Record<string, unknown> | null; bars_since_previous: number } | null;
  generated_at: string;
}

export interface BarSummary {
  bar_ref: BarRef;
  bar_type: string;
  role: string;
  context_effect: string;
  follow_through?: string;
  trapped_side?: string;
  summary?: string;
  reason?: string;
}

export interface Stage1Result {
  result_kind: "live" | "failed";
  precheck: { passed: boolean; failure_type: string | null; reason: string | null; closed_bar_count: number };
  cycle_position: string | null;
  direction: Direction | null;
  confidence: number;
  detected_patterns: string[];
  support_levels: number[];
  resistance_levels: number[];
  bar_summaries?: BarSummary[];
  gate_trace: Array<{ node_id: string; question: string; answer: string; reason: string; bar_range: BarRange | null; source: "program" | "ai" }>;
  gate_result: "proceed" | "wait" | "unknown";
  incremental_delta: { changed: boolean; summary: string; changed_fields: string[] };
}

export interface Stage2Result {
  result_kind: "short_circuit" | "live" | "failed";
  decision: { order_type: string; direction: "long" | "short" | null; entry_price: number | null; stop_loss_price: number | null; take_profit_price: number | null; take_profit_price_2: number | null; estimated_win_rate: number | null; entry_reason: string | null };
  terminal: { outcome: "trade" | "reject" | "wait" | "error"; reason: string; terminal_node: string };
  decision_trace?: Array<{ node_id: string; question: string; answer: string; reason: string; bar_range?: BarRange | null; source?: "program" | "ai"; phase?: string; section?: string; skipped?: boolean }>;
}

export interface TradeReviewResult {
  trade_id: string;
  execution_metrics: Record<string, unknown>;
  comparison: Record<string, unknown>;
  issues: Array<Record<string, unknown>>;
  strengths: string[];
  improvements: string[];
  summary: string;
}

export interface AnalysisAudit {
  started_at: string;
  completed_at: string;
  stage1_model_called: boolean;
  stage2_model_called: boolean;
  validation_attempts: number;
  warnings: string[];
  graph_trail?: string[];
}

export interface ModelProfile {
  id: string;
  name: string;
  provider: "openai" | "anthropic" | "gemini" | "deepseek" | "compatible";
  model: string;
  base_url: string | null;
  has_api_key: boolean;
  api_key_masked: string | null;
  api_key?: string;
}

export interface PersonalSettings {
  debug_enabled: boolean;
  active_model_id: string | null;
  models: ModelProfile[];
}

export interface DebugPreview {
  confirmation_id: string;
  requires_confirmation: boolean;
  model: ModelProfile | null;
  llm_input: Record<string, unknown>;
  estimated_prompt_tokens: number;
  estimated_max_completion_tokens: number;
}

export interface TokenUsageRecord {
  id: string;
  analysis_id: string;
  occurred_at: string;
  model_id: string | null;
  model: string | null;
  mode: string;
  symbol: string;
  period: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  status: "estimated" | "completed" | "failed";
}

export interface TokenUsageSummary {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  analysis_count: number;
  records: TokenUsageRecord[];
}

export interface PromptFileRef {
  filename: string;
  placement: "system" | "user";
  condition: string;
  editable: boolean;
}

export interface PipelineStage {
  id: string;
  name: string;
  kind: "deterministic" | "llm" | "gate" | "review";
  description: string;
  prompt_files: PromptFileRef[];
}

export interface OrchestrationView {
  stages: PipelineStage[];
  edges: Array<{ source: string; target: string; condition: string }>;
}

export interface PromptFileDocument {
  filename: string;
  content: string;
  version: string;
  size: number;
}

export interface HealthResponse {
  status: string;
  api_version: string;
  provider_configured: boolean;
  provider_transport: string;
  storage_status: string;
  auth_required?: boolean;
}

export interface UserSession { id: string | null; username: string; role: "user" | "admin"; auth_required: boolean; }
export interface PromptVersion { id: string; filename: string; version: string; content: string; actor: string; action: string; created_at: string; }

export interface ApiError {
  code: string;
  message: string;
  trace_id?: string;
  request_id?: string;
  details?: Array<Record<string, unknown>>;
}

export interface TradeRow {
  source_trade_id: string;
  contract_name: string;
  symbol_root: string;
  entered_at: string;
  exited_at: string;
  entry_price: string;
  exit_price: string;
  direction: "long" | "short";
  size: string;
  reported_pnl: string | null;
}

export interface TradePreview {
  file_name: string;
  file_hash: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  rows: TradeRow[];
  errors: Array<{ row: number; message: string }>;
}

export interface TradeImportResult {
  imported: number;
  skipped_duplicates: number;
  total: number;
}
export interface TradeImportBatch { id: string; file_name: string; file_hash: string; total_rows: number; valid_rows: number; invalid_rows: number; imported_rows: number; skipped_duplicates: number; mapping: Record<string, unknown>; errors: Array<{ row: number; message: string }>; created_at: string; }

export interface Trade extends TradeRow {
  id: string;
  source_file_name: string;
  imported_at: string;
  fees?: string | null;
  commissions?: string | null;
  slippage?: string | null;
  strategy?: string | null;
  account?: string | null;
  tags?: string[];
  notes?: string;
  attachments?: string[];
}
