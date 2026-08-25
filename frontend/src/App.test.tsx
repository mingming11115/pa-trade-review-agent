import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { chartSpy } = vi.hoisted(() => ({
  chartSpy: vi.fn(),
}));

vi.mock("./TradingChart", () => ({
  TradingChart: ({ bars, markers = [], timezoneOffsetMinutes }: { bars: Array<unknown>; markers?: Array<unknown>; timezoneOffsetMinutes?: number }) => {
    chartSpy({ bars, markers, timezoneOffsetMinutes });
    return <div aria-label="K 线图" data-bars={bars.length} data-markers={markers.length} role="img" />;
  },
}));

import App from "./App";

function liveBackgroundResponse(url: string): Response | null {
  if (url.startsWith("/api/v1/market/bars?")) {
    return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars: [], coverage: { source_period: "1m", expected_bars: 0, actual_bars: 0, complete: true, missing_buckets: [] } }), { status: 200 });
  }
  if (url === "/api/v1/market/status" || url === "/api/v1/alert-rules" || url === "/api/v1/alerts?limit=200") {
    return new Response(JSON.stringify([]), { status: 200 });
  }
  if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
  return null;
}

async function pickSymbolAndPeriod(symbol = "ES", period = "5m") {
  const symbolSelect = await screen.findByLabelText("K 线标的切换");
  fireEvent.change(symbolSelect, { target: { value: symbol } });
  const periodSelect = await screen.findByLabelText("K 线周期切换");
  fireEvent.change(periodSelect, { target: { value: period } });
}

describe("App", () => {
  afterEach(() => {
    cleanup();
    window.history.replaceState(null, "", "/");
    vi.useRealTimers();
    vi.restoreAllMocks();
    chartSpy.mockClear();
  });

  it("does not request demo-era bars before a range is selected", async () => {
    window.history.replaceState(null, "", "/analysis/range");
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url.startsWith("/api/v1/market/bars?")) return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars: [], coverage: { source_period: "1m", expected_bars: 0, actual_bars: 0, complete: true, missing_buckets: [] } }), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/health", expect.any(Object)));
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith("/api/v1/market/bars?"))).toBe(false);
  });

  it("only offers ES and NQ for K-line analysis", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    }));
    render(<App />);

    const button = await screen.findByRole("button", { name: "+ 新建分析任务" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const symbolSelect = screen.getByLabelText("标的");
    const options = within(symbolSelect).getAllByRole("option");

    expect(options.map((option) => (option as HTMLOptionElement).value)).toEqual(["ES", "NQ"]);
  });

  it("uses a resizable chart-first analysis split without fullscreen controls", async () => {
    render(<App />);

    const separator = await screen.findByRole("separator", { name: "调整分析栏宽度" });
    expect(separator).toHaveAttribute("aria-valuenow", "44");
    expect(screen.queryByRole("button", { name: "展开右栏" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退出全屏" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "决策图" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "决策树" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "决策树可视化" })).not.toBeInTheDocument();

    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator).toHaveAttribute("aria-valuenow", "46");
    fireEvent.keyDown(separator, { key: "Home" });
    expect(separator).toHaveAttribute("aria-valuenow", "44");
    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    fireEvent.doubleClick(separator);
    expect(separator).toHaveAttribute("aria-valuenow", "44");

    const center = document.querySelector(".center-panel") as HTMLElement;
    const right = document.querySelector(".right-panel") as HTMLElement;
    vi.spyOn(center, "getBoundingClientRect").mockReturnValue({ left: 100, right: 600 } as DOMRect);
    vi.spyOn(right, "getBoundingClientRect").mockReturnValue({ left: 600, right: 1100 } as DOMRect);
    Object.defineProperty(separator, "setPointerCapture", { value: vi.fn() });
    const pointerEvent = (type: string, clientX: number) => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperties(event, { clientX: { value: clientX }, pointerId: { value: 1 } });
      return event;
    };
    fireEvent(separator, pointerEvent("pointerdown", 400));
    expect(separator).toHaveAttribute("aria-valuenow", "70");
    fireEvent(separator, pointerEvent("pointermove", 800));
    expect(separator).toHaveAttribute("aria-valuenow", "30");
    fireEvent(separator, pointerEvent("pointerup", 800));
  });

  it("uses keyboard-operable tabs for decision views", async () => {
    const bars = [
      { timestamp: "2026-08-11T01:00:00Z", open: 6400, high: 6402, low: 6399, close: 6401, volume: 10 },
      { timestamp: "2026-08-11T01:05:00Z", open: 6401, high: 6404, low: 6400, close: 6403, volume: 12 },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url.startsWith("/api/v1/market/bars?")) return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars, coverage: { source_period: "1m", expected_bars: 10, actual_bars: 10, complete: true, missing_buckets: [] } }), { status: 200 });
      if (url === "/api/v1/market/status" || url === "/api/v1/alert-rules" || url === "/api/v1/alerts?limit=200") return new Response(JSON.stringify([]), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
      return new Response(JSON.stringify([]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await pickSymbolAndPeriod();
    const tablist = await screen.findByRole("tablist", { name: "决策视图" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[0].id);

    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveFocus();
    fireEvent.keyDown(tabs[1], { key: "End" });
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tabs[2], { key: "Home" });
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  });

  it("opens on the live ES 5m chart", async () => {
    const bars = [
      { timestamp: "2026-08-11T01:00:00Z", open: 6400, high: 6402, low: 6399, close: 6401, volume: 10 },
      { timestamp: "2026-08-11T01:05:00Z", open: 6401, high: 6404, low: 6400, close: 6403, volume: 12 },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url.startsWith("/api/v1/market/bars?")) return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars, coverage: { source_period: "1m", expected_bars: 10, actual_bars: 10, complete: true, missing_buckets: [] } }), { status: 200 });
      if (url === "/api/v1/market/status" || url === "/api/v1/alert-rules" || url === "/api/v1/alerts?limit=200") return new Response(JSON.stringify([]), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await pickSymbolAndPeriod();

    await waitFor(() => expect(screen.getByLabelText("K 线图")).toHaveAttribute("data-bars", "2"));
    const marketUrl = String(fetchMock.mock.calls.find(([url]) => String(url).startsWith("/api/v1/market/bars?"))?.[0]);
    expect(marketUrl).toContain("symbol=ES");
    expect(marketUrl).toContain("period=5m");
    expect(marketUrl).toContain("include_partial=true");
    expect(screen.getByText("实时 · ES")).toBeInTheDocument();
    expect(screen.getByText("UTC-5")).toBeInTheDocument();
    expect(chartSpy).toHaveBeenLastCalledWith(expect.objectContaining({ timezoneOffsetMinutes: -300 }));
    const chartControls = screen.getByRole("button", { name: "一键分析" }).parentElement;
    expect(chartControls).not.toBeNull();
    expect(window.getComputedStyle(chartControls!).display).toBe("flex");
    expect(window.getComputedStyle(chartControls!).flexWrap).toBe("nowrap");
    expect(screen.queryByText("尚未加载复盘数据")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ 新建复盘任务" })).toBeInTheDocument();
    expect(screen.queryByLabelText("复盘方式")).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "按时间复盘" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("期货代码")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建" })).not.toBeInTheDocument();
    expect(screen.queryByText("数据源状态")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Dataset")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Schema")).not.toBeInTheDocument();
    expect(screen.queryByText("数据预览")).not.toBeInTheDocument();
  });

  it("keeps chart display fixed at UTC-5 regardless of source offset", async () => {
    const bars = [
      { timestamp: "2026-08-11T09:00:00+08:00", open: 6400, high: 6402, low: 6399, close: 6401, volume: 10 },
      { timestamp: "2026-08-11T09:05:00+08:00", open: 6401, high: 6404, low: 6400, close: 6403, volume: 12 },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url.startsWith("/api/v1/market/bars?")) return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars, coverage: { source_period: "1m", expected_bars: 10, actual_bars: 10, complete: true, missing_buckets: [] } }), { status: 200 });
      if (url === "/api/v1/market/status" || url === "/api/v1/alert-rules" || url === "/api/v1/alerts?limit=200") return new Response(JSON.stringify([]), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await pickSymbolAndPeriod();

    await waitFor(() => expect(screen.getByLabelText("K 线图")).toHaveAttribute("data-bars", "2"));
    expect(screen.getByText("UTC-5")).toBeInTheDocument();
    expect(chartSpy).toHaveBeenLastCalledWith(expect.objectContaining({ timezoneOffsetMinutes: -300 }));
  });

  it("refreshes the chart without automatic analysis", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const initialBars = [
      { timestamp: "2026-08-11T01:00:00Z", open: 6400, high: 6402, low: 6399, close: 6401, volume: 10 },
      { timestamp: "2026-08-11T01:05:00Z", open: 6401, high: 6404, low: 6400, close: 6403, volume: 12 },
    ];
    const nextBars = [...initialBars, { timestamp: "2026-08-11T01:10:00Z", open: 6403, high: 6405, low: 6402, close: 6404, volume: 11 }];
    let marketRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url.startsWith("/api/v1/market/bars?")) {
        marketRequests += 1;
        return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars: marketRequests === 1 ? initialBars : nextBars, coverage: { source_period: "1m", expected_bars: 15, actual_bars: 15, complete: true, missing_buckets: [] } }), { status: 200 });
      }
      if (url === "/api/v1/market/status" || url === "/api/v1/alert-rules" || url === "/api/v1/alerts?limit=200") return new Response(JSON.stringify([]), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 });
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await pickSymbolAndPeriod();
    await waitFor(() => expect(screen.getByLabelText("K 线图")).toHaveAttribute("data-bars", "2"));
    await vi.advanceTimersByTimeAsync(30_000);
    await waitFor(() => expect(screen.getByLabelText("K 线图")).toHaveAttribute("data-bars", "3"));

    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/demo/analyze/stream")).toBe(false);
  });

  it("runs one-click analysis through the matching live task", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-11T01:07:00Z"));
    const bars = [
      { timestamp: "2026-08-11T01:00:00Z", open: 6400, high: 6402, low: 6399, close: 6401, volume: 10 },
      { timestamp: "2026-08-11T01:05:00Z", open: 6401, high: 6404, low: 6400, close: 6403, volume: 12 },
    ];
    const result = {
      query: { symbol: "ES", period: "5m", start: "2026-08-11T01:00:00Z", end: "2026-08-11T01:10:00Z", analysis_mode: "realtime" },
      resolved_symbol: "ESU6",
      analysis: { bar_count: 2, start: "2026-08-11T01:00:00Z", end: "2026-08-11T01:10:00Z", first_open: 6400, latest_close: 6403, period_high: 6404, period_low: 6399, change_percent: 0.05, bullish_bars: 2, bearish_bars: 0, neutral_bars: 0, direction: "bullish", method: "test" },
      bars,
    };
    const task = {
      id: "task-live-1", kind: "analysis", title: "ES · 5m", description: "实时 K 线分析任务", status: "pending",
      config: { symbol: "ES", period: "5m" }, latest_execution_id: null, version: 1,
      created_at: "2026-08-11T01:00:00Z", updated_at: "2026-08-11T01:00:00Z", archived_at: null,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url.startsWith("/api/v1/market/bars?")) return new Response(JSON.stringify({ symbol: "ES", period: "5m", bars, coverage: { source_period: "1m", expected_bars: 10, actual_bars: 10, complete: true, missing_buckets: [] } }), { status: 200 });
      if (url === "/api/v1/market/status" || url === "/api/v1/alert-rules" || url === "/api/v1/alerts?limit=200") return new Response(JSON.stringify([]), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [task], next_cursor: null }), { status: 200 });
      if (url === "/api/v1/analysis-tasks/ensure-live" && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toMatchObject({ symbol: "ES", period: "5m" });
        return new Response(JSON.stringify(task), { status: 200 });
      }
      if (url === "/api/v1/analysis-tasks/task-live-1/preview" && init?.method === "POST") {
        return new Response(JSON.stringify({
          snapshot_id: "snap-1", confirmation_id: "confirm-1", expires_at: "2099-01-01T00:00:00Z",
          resolved_symbol: "ESU6", bars_hash: "a".repeat(64), bar_count: 2,
        }), { status: 200 });
      }
      if (url === "/api/v1/analysis-tasks/task-live-1/runs" && init?.method === "POST") {
        return new Response(JSON.stringify({
          analysis_id: "exec-1", task_id: "task-live-1", parent_analysis_id: null, work_key: null, sequence: 1,
          status: "queued", current_stage: "prepare", failure_stage: null, failure_code: null,
          failure_message: null, terminal_reason: null, started_at: null, completed_at: null,
          created_at: "2026-08-11T01:07:00Z",
        }), { status: 202 });
      }
      if (url.startsWith("/api/v1/analysis-executions/exec-1/events")) {
        return new Response(`${JSON.stringify({
          sequence: 1, type: "result", stage: "complete", message: "分析完成",
          payload: { result_id: "result-1", result }, terminal: true,
        })}\n`, { status: 200 });
      }
      if (url === "/api/v1/analysis-tasks/task-live-1/runs") {
        return new Response(JSON.stringify([{
          id: "exec-1", task_id: "task-live-1", sequence: 1, status: "completed",
          created_at: "2026-08-11T01:07:00Z", completed_at: "2026-08-11T01:07:05Z",
          result_id: "result-1", direction: "bullish", symbol: "ES", period: "5m",
        }]), { status: 200 });
      }
      throw new Error(`未预期的请求：${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await pickSymbolAndPeriod();
    const analyzeButton = await screen.findByRole("button", { name: "一键分析" });
    await waitFor(() => expect(analyzeButton).toBeEnabled());
    fireEvent.click(analyzeButton);

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/v1/analysis-tasks/ensure-live" && init?.method === "POST")).toBe(true));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/v1/analysis-tasks/task-live-1/runs" && init?.method === "POST")).toBe(true));
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/demo/analyze/stream")).toBe(false);
  });

  it("shows and copies the selected run ID instead of the task's latest execution ID", async () => {
    const task = {
      id: "task-run-id", kind: "analysis", title: "ES 运行记录", description: "", status: "running",
      config: { symbol: "ES", period: "5m" }, latest_execution_id: "exec-latest", version: 1,
      created_at: "2026-08-11T01:00:00Z", updated_at: "2026-08-11T01:05:00Z", archived_at: null,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [task], next_cursor: null }), { status: 200 });
      if (url === "/api/v1/analysis-tasks/task-run-id/runs") {
        return new Response(JSON.stringify([{
          analysis_id: "exec-selected", task_id: "task-run-id", parent_analysis_id: null, work_key: null,
          sequence: 1, status: "running",
          created_at: "2026-08-11T01:04:00Z", completed_at: null, result_id: null,
          direction: null, symbol: "ES", period: "5m",
        }]), { status: 200 });
      }
      if (url === "/api/v1/analysis-runs/exec-selected") {
        return new Response(JSON.stringify({
          analysis_id: "exec-selected", task_id: "task-run-id", parent_analysis_id: null,
          work_key: null, sequence: 1, status: "running", mode: "realtime", symbol: "ES",
          period: "5m", direction: "neutral", terminal_outcome: "running",
          created_at: "2026-08-11T01:04:00Z", updated_at: "2026-08-11T01:04:00Z",
          result: { query: { symbol: "ES", period: "5m", analysis_mode: "realtime" }, bars: [] },
        }), { status: 200 });
      }
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const expandButton = await screen.findByRole("button", { name: "展开ES 运行记录 · ES · 5m的分析" });
    fireEvent.click(expandButton);
    fireEvent.click(await screen.findByRole("button", { name: "#1 · 进行中" }));

    const dialog = await screen.findByRole("dialog", { name: "任务详情" });
    expect(within(dialog).getByText("运行 ID", { exact: true })).toBeInTheDocument();
    expect(within(dialog).getByText("exec-selected", { exact: true })).toBeInTheDocument();
    expect(within(dialog).queryByText("exec-latest", { exact: true })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "复制运行 ID" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("exec-selected"));
    expect(within(dialog).getByRole("button", { name: "已复制" })).toBeInTheDocument();
  });

  it("requires selected trades instead of time-window review", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured" }), { status: 200 });
      if (url === "/api/v1/trades/recent?limit=200") return new Response(JSON.stringify([]), { status: 200 });
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    }));

    render(<App />);
    const newTaskButton = await screen.findByRole("button", { name: "+ 新建复盘任务" });
    await waitFor(() => expect(newTaskButton).toBeEnabled());
    fireEvent.click(newTaskButton);

    const dialog = await screen.findByRole("dialog", { name: "新建复盘任务" });
    expect(within(dialog).getByLabelText("开始时间")).toHaveValue("");
    expect(within(dialog).getByLabelText("结束时间")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请先选择需要复盘的交易记录。");
  });

  it("auto-fills the review time window from selected trades", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured" }), { status: 200 });
      if (url === "/api/v1/trades/recent?limit=200") return new Response(JSON.stringify([
          {
            id: "trade-1", source_trade_id: "1", contract_name: "MNQU6", symbol_root: "MNQ",
            entered_at: "2026-08-01T01:00:00Z", exited_at: "2026-08-01T01:05:00Z",
            entry_price: "100", exit_price: "101", direction: "long", size: "1", reported_pnl: "2",
            source_file_name: "trades.csv", imported_at: "2026-08-01T01:06:00Z",
          },
          {
            id: "trade-2", source_trade_id: "2", contract_name: "ESU6", symbol_root: "ES",
            entered_at: "2026-08-02T01:00:00Z", exited_at: "2026-08-02T01:05:00Z",
            entry_price: "200", exit_price: "199", direction: "short", size: "1", reported_pnl: "1",
            source_file_name: "trades.csv", imported_at: "2026-08-02T01:06:00Z",
          },
        ]), { status: 200 });
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const newTaskButton = await screen.findByRole("button", { name: "+ 新建复盘任务" });
    await waitFor(() => expect(newTaskButton).toBeEnabled());
    fireEvent.click(newTaskButton);

    const dialog = await screen.findByRole("dialog", { name: "新建复盘任务" });
    expect(screen.getByText("MNQU6")).toBeInTheDocument();
    expect(screen.getByText("ESU6")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/trades/recent?limit=200", expect.any(Object));

    fireEvent.click(within(dialog).getAllByRole("checkbox")[0]);
    expect(within(dialog).getByLabelText("开始时间")).toHaveValue("2026-08-01T09:00");
    expect(within(dialog).getByLabelText("结束时间")).toHaveValue("2026-08-01T09:05");

    fireEvent.click(within(dialog).getAllByRole("checkbox")[1]);
    expect(within(dialog).getByLabelText("开始时间")).toHaveValue("2026-08-01T09:00");
    expect(within(dialog).getByLabelText("结束时间")).toHaveValue("2026-08-02T09:05");
  });

  it("保存分析任务时只持久化配置，不立即预览或分析", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured" }), { status: 200 });
      if (url === "/api/v1/analysis-tasks" && init?.method === "POST") return new Response(JSON.stringify({ id: "task-1", kind: "analysis", title: "NQ 结构分析", description: "", status: "pending", config: JSON.parse(String(init.body)).config, latest_execution_id: null, version: 1, created_at: "2026-08-12T01:00:00Z", updated_at: "2026-08-12T01:00:00Z", archived_at: null }), { status: 201 });
      if (url === "/api/v1/analysis-tasks/task-1" && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ id: "task-1", kind: "analysis", title: body.title, description: body.description, status: "pending", config: body.config, latest_execution_id: null, version: 2, created_at: "2026-08-12T01:00:00Z", updated_at: "2026-08-12T01:05:00Z", archived_at: null }), { status: 200 });
      }
      if (url === "/api/v1/analysis-tasks/task-1/executions" && (!init || !init.method || init.method === "GET")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const newTaskButton = await screen.findByRole("button", { name: "+ 新建分析任务" });
    await waitFor(() => expect(newTaskButton).toBeEnabled());
    fireEvent.click(newTaskButton);
    fireEvent.change(screen.getByLabelText("任务名称"), { target: { value: "NQ 结构分析" } });
    fireEvent.change(screen.getByLabelText("标的"), { target: { value: "NQ" } });
    fireEvent.change(screen.getByLabelText("周期"), { target: { value: "15m" } });
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/analysis-tasks",
      expect.objectContaining({ body: expect.stringContaining('"symbol":"NQ"') }),
    ));
    const createCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/analysis-tasks");
    expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
      kind: "analysis",
      title: "NQ 结构分析",
      config: { symbol: "NQ", period: "15m" },
    });
    expect(JSON.parse(String(createCall?.[1]?.body)).config).not.toHaveProperty("start");
    expect(JSON.parse(String(createCall?.[1]?.body)).config).not.toHaveProperty("analysis_mode");
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/analysis/debug-preview")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/demo/analyze/stream")).toBe(false);
    expect(screen.queryByRole("dialog", { name: "新建分析任务" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "NQ 结构分析 · NQ · 15m" }));
    const taskDetailsDialog = await screen.findByRole("dialog", { name: "任务详情" });
    expect(taskDetailsDialog).toBeInTheDocument();
    expect(within(taskDetailsDialog).getAllByText("类型", { exact: true })).toHaveLength(1);
    expect(within(taskDetailsDialog).getAllByText("标的", { exact: true })).toHaveLength(1);
    expect(within(taskDetailsDialog).getByText("周期", { exact: true })).toBeInTheDocument();
    expect(within(taskDetailsDialog).queryByText("运行 ID", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "应用到当前工作区" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    const editDialog = await screen.findByRole("dialog", { name: "编辑任务" });
    fireEvent.change(within(editDialog).getByLabelText("任务名称"), { target: { value: "NQ 修改后" } });
    fireEvent.click(within(editDialog).getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/v1/analysis-tasks/task-1" && init?.method === "PATCH" && JSON.parse(String(init.body)).version === 1)).toBe(true));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/preview"))).toBe(false);
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).includes("/executions") && init?.method === "POST")).toBe(false);
    expect(await screen.findByText("NQ 修改后 · NQ · 15m")).toBeInTheDocument();
  });

  it("keeps the primary navigation usable at narrow viewports", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }) }));
    render(<App />);
    expect(screen.getByRole("button", { name: "K线分析" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(screen.getByRole("menuitem", { name: "交易日志" })).toBeVisible();
    expect(screen.getByRole("menuitem", { name: "分析历史" })).toBeVisible();
    expect(screen.getByRole("button", { name: "+ 新建复盘任务" })).toBeVisible();
  });

  it("opens the admin orchestration and edits a stage prompt", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured" }), { status: 200 });
      if (url === "/api/v1/admin/orchestration") return new Response(JSON.stringify({
          stages: [
            { id: "stage1", name: "Stage 1 · 市场诊断", kind: "llm", description: "市场诊断", prompt_files: [{ filename: "市场诊断框架.txt", placement: "user", condition: "每次 Stage 1 固定加载", editable: true }] },
            { id: "gate", name: "Gate · 阶段闸门", kind: "gate", description: "闸门", prompt_files: [] },
          ],
          edges: [{ source: "stage1", target: "gate", condition: "Stage 1 完成" }],
        }), { status: 200 });
      if (url.startsWith("/api/v1/admin/prompt-file?")) {
        const saved = init?.method === "PUT";
        return new Response(JSON.stringify({ filename: "市场诊断框架.txt", content: saved ? "更新后的提示词" : "原始提示词", version: saved ? "version-2" : "version-1", size: saved ? 21 : 15 }), { status: 200 });
      }
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "管理后台" }));

    expect(await screen.findByRole("dialog", { name: "管理后台" })).toBeInTheDocument();
    expect(screen.getByText("Stage 1 · 市场诊断")).toBeInTheDocument();
    expect(screen.getByText("该节点不加载文本")).toBeInTheDocument();
    const editor = await screen.findByLabelText("提示词文本内容");
    fireEvent.change(editor, { target: { value: "更新后的提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "保存文本" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).startsWith("/api/v1/admin/prompt-file?") && init?.method === "PUT")).toBe(true));
    expect(await screen.findByText(/下一次分析立即生效/)).toBeInTheDocument();
  });

  it("previews and confirms an Excel trade import", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured" }), { status: 200 });
      if (url === "/api/v1/trades/recent?limit=200" || url === "/api/v1/trades/imports?limit=100") return new Response(JSON.stringify([]), { status: 200 });
      if (url === "/api/v1/trades/import/preview") return new Response(JSON.stringify({
          file_name: "trades.xlsx",
          file_hash: "hash",
          total_rows: 1,
          valid_rows: 1,
          invalid_rows: 0,
          rows: [{ source_trade_id: "1", contract_name: "MNQU6", symbol_root: "MNQ", entered_at: "2026-08-01T01:00:00Z", exited_at: "2026-08-01T01:05:00Z", entry_price: "100", exit_price: "101", direction: "long", size: "1", reported_pnl: "2" }],
          errors: [],
        }), { status: 200 });
      if (url === "/api/v1/trades/import/confirm") return new Response(JSON.stringify({ imported: 1, skipped_duplicates: 0, total: 1 }), { status: 200 });
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    }));

    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "交易日志" }));
    expect(await screen.findByRole("dialog", { name: "交易日志" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "导入 Excel / CSV" }));

    const fileInput = screen.getByLabelText("Excel 交易文件");
    const csvFile = new File(["content"], "trades.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    fireEvent.change(fileInput, {
      target: { files: [csvFile] },
    });

    expect(await screen.findByText("trades.xlsx")).toBeInTheDocument();
    expect(screen.getByText("共 1 笔 · 有效 1 笔 · 异常 0 笔")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已导入 1 笔交易");
  });

  it("saves a one-time review task without starting analysis", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") {
        return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "not_implemented" }), { status: 200 });
      }
      if (url.startsWith("/api/v1/trades/recent")) {
        return new Response(JSON.stringify([{
          id: "1", source_trade_id: "1", contract_name: "ESM2", symbol_root: "ES",
          entered_at: "2022-06-06T00:00:00Z", exited_at: "2022-06-06T00:01:00Z",
          entry_price: "100", exit_price: "101", direction: "long", size: "1", reported_pnl: "1",
          source_file_name: "trades.csv", imported_at: "2022-06-06T00:00:00Z",
        }]), { status: 200 });
      }
      if (url === "/api/v1/analysis-tasks" && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ id: "review-task-1", owner_id: "local", kind: body.kind, title: body.title, description: body.description, config: body.config, status: "pending", latest_execution_id: null, version: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z" }), { status: 201 });
      }
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    const newTaskButton = await screen.findByRole("button", { name: "+ 新建复盘任务" });
    await waitFor(() => expect(newTaskButton).toBeEnabled());
    fireEvent.click(newTaskButton);

    const dialog = await screen.findByRole("dialog", { name: "新建复盘任务" });
    fireEvent.change(within(dialog).getByLabelText("周期"), { target: { value: "1m" } });
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "保存任务" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url) === "/api/v1/analysis-tasks" && init?.method === "POST")).toBe(true));
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/analysis/debug-preview")).toBe(false);
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/v1/demo/analyze/stream")).toBe(false);
    expect(await screen.findByText("未命名复盘任务")).toBeInTheDocument();
  });
});
