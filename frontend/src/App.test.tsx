import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { IndicatorPanel } from './IndicatorPanel';
import { DEFAULT_INDICATOR_SETTINGS } from './indicatorSettings';
import { afterEach, describe, expect, it, vi } from "vitest";

const { chartSpy } = vi.hoisted(() => ({
  chartSpy: vi.fn(),
}));

vi.mock("./TradingChart", () => ({
  TradingChart: ({ bars, markers = [], timezoneOffsetMinutes, onStartTradingReplay, showToolbar = true }: { showToolbar?: boolean; onStartTradingReplay?:(start?:string)=>void; bars: Array<unknown>; markers?: Array<unknown>; timezoneOffsetMinutes?: number }) => {
    if (showToolbar) chartSpy({ bars, markers, timezoneOffsetMinutes });
    return <><div aria-label={showToolbar ? "K 线图" : "辅助 K 线图"} data-bars={bars.length} data-markers={markers.length} role="img" />{showToolbar && <IndicatorPanel settings={DEFAULT_INDICATOR_SETTINGS} onChange={()=>{}} />}{onStartTradingReplay&&<button onClick={()=>onStartTradingReplay("2025-01-01T00:00:00Z")}>从此处回放</button>}</>;
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

  it("shows the latest stored candles when the live window is empty and labels their age", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:00:00Z"));
    const bars = [
      { timestamp: "2026-10-02T20:50:00Z", open: 7700, high: 7702, low: 7699, close: 7701, volume: 10 },
      { timestamp: "2026-10-02T20:55:00Z", open: 7701, high: 7704, low: 7700, close: 7703, volume: 12 },
    ];
    const ranges: URL[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return Response.json({ status: "ok", auth_required: false });
      if (url === "/api/v1/market/status") return Response.json([{ symbol: "ES", latest_closed_at: "2026-10-02T21:00:00Z", status: "no_data", last_error: null }]);
      if (url.startsWith("/api/v1/market/bars?")) {
        const parsed = new URL(url, "http://localhost"); ranges.push(parsed);
        return Response.json({ bars: parsed.searchParams.get("end") === "2026-10-02T21:00:00.000Z" ? bars : [] });
      }
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("K 线图")).toHaveAttribute("data-bars", "2"));
    expect(screen.getByText(/显示最近已有行情/)).toHaveTextContent("2026-10-02 15:55");
    expect(screen.getByRole("button", { name: "从此处回放" })).toBeEnabled();
    const primaryRanges = ranges.filter(range => range.searchParams.get("period") === "5m");
    expect(primaryRanges).toHaveLength(2);
    expect(primaryRanges[1].searchParams.get("start")).toBe("2026-10-02T14:20:00.000Z");
    expect(primaryRanges[1].searchParams.get("include_partial")).toBe("true");
  });

  it("keeps replay accessible when no stored candles are available", async () => {
    const ranges: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return Response.json({ status: "ok", auth_required: false });
      if (url.startsWith("/api/v1/market/bars?")) ranges.push(url);
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    expect(await screen.findByText(/当前时段暂无行情/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "从此处回放" })).toBeEnabled();
    expect(screen.queryByText("尚未加载行情数据")).not.toBeInTheDocument();
    expect(ranges).toHaveLength(1);
  });

  it("ignores a late historical fallback after changing symbol", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:00:00Z"));
    let resolveFallback: (value: Response) => void = () => {};
    let fallbackStarted = false;
    const nqBar = { timestamp: "2026-10-03T12:55:00Z", open: 21000, high: 21002, low: 20999, close: 21001, volume: 10 };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return Response.json({ status: "ok", auth_required: false });
      if (url === "/api/v1/market/status") return Response.json([{ symbol: "ES", latest_closed_at: "2026-10-02T21:00:00Z" }]);
      if (url.startsWith("/api/v1/market/bars?")) {
        const params = new URL(url, "http://localhost").searchParams;
        if (params.get("symbol") === "NQ") return Response.json({ bars: [nqBar] });
        if (params.get("end") === "2026-10-02T21:00:00.000Z") {
          fallbackStarted = true;
          return new Promise<Response>(resolve => { resolveFallback = resolve; });
        }
        return Response.json({ bars: [] });
      }
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    await waitFor(() => expect(fallbackStarted).toBe(true));
    fireEvent.change(screen.getByLabelText("K 线标的切换"), { target: { value: "NQ" } });
    await waitFor(() => expect(screen.getByLabelText("K 线图")).toHaveAttribute("data-bars", "1"));
    resolveFallback(Response.json({ bars: [{ ...nqBar, close: 7700 }, { ...nqBar, close: 7701 }] }));
    await waitFor(() => expect(chartSpy.mock.lastCall?.[0].bars[0].close).toBe(21001));
    expect(screen.queryByText(/显示最近已有行情/)).not.toBeInTheDocument();
  });

  it("tries an empty historical window once while live polling continues", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T13:00:00Z"));
    const ends: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return Response.json({ status: "ok", auth_required: false });
      if (url === "/api/v1/market/status") return Response.json([{ symbol: "ES", latest_closed_at: "2026-10-02T21:00:00Z" }]);
      if (url.startsWith("/api/v1/market/bars?")) {
        ends.push(new URL(url, "http://localhost").searchParams.get("end")!);
        return Response.json({ bars: [] });
      }
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ends.filter(end => end === "2026-10-02T21:00:00.000Z")).toHaveLength(1);
    expect(ends.filter(end => end.startsWith("2026-10-03"))).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "重新加载行情" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(ends.filter(end => end === "2026-10-02T21:00:00.000Z")).toHaveLength(2);
  });

  it("times out a stuck archive status lookup and allows reloading", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T13:00:00Z"));
    let marketRequests = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return Response.json({ status: "ok", auth_required: false });
      if (url === "/api/v1/market/status") return new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
      if (url.startsWith("/api/v1/market/bars?")) { marketRequests++; return Response.json({ bars: [] }); }
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(25001); });
    expect(screen.getByText(/行情加载超时/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载行情" })).toBeEnabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(marketRequests).toBeGreaterThan(1);
  });

  it("opens one record workspace and returns from real-trade replay to its source", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url=String(input);
      if(url==="/api/v1/health") return Response.json({status:"ok",auth_required:false});
      if(url.startsWith('/api/v1/replay/')) return Response.json({items:[]});
      if(url.startsWith('/api/v1/trades/recent')) return Response.json([{id:'real-es',contract_name:'ESZ6',symbol_root:'ES',direction:'long',size:'1',entry_price:'100',exit_price:'101',entered_at:'2026-10-01T01:00:00Z',exited_at:'2026-10-01T01:05:00Z'}]);
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole('button',{name:'交易记录'}));
    const records=await screen.findByRole('region',{name:'交易记录'});
    expect(records.closest('.shell-grid')).not.toBeNull();
    expect(screen.getByText('PA 工作台')).toBeInTheDocument();
    expect(screen.getByRole('tab',{name:'回放交易'})).toHaveAttribute('aria-selected','true');
    expect(screen.queryByRole('button',{name:'真实交易回放'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab',{name:'真实交易'}));
    await screen.findByText('ESZ6');
    fireEvent.click(within(records).getByRole('button',{name:'回放'}));
    expect(await screen.findByRole('region',{name:'真实交易回放'})).toBeInTheDocument();
    expect(screen.getByRole('checkbox',{name:/ESZ6/})).toBeChecked();
    fireEvent.click(screen.getByRole('button',{name:'返回交易记录'}));
    expect(await screen.findByRole('tab',{name:'真实交易'})).toHaveAttribute('aria-selected','true');
    fireEvent.click(screen.getByRole('button',{name:'返回工作台'}));
    expect(await screen.findByLabelText('K 线标的切换')).toBeInTheDocument();
  });

  it('keeps real-trade load failures in the unified workspace and retries them',async()=>{
    let fail=true;
    vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL)=>{
      const url=String(input);
      if(url==='/api/v1/health')return Response.json({status:'ok',auth_required:false});
      if(url.startsWith('/api/v1/replay/'))return Response.json({items:[]});
      if(url.startsWith('/api/v1/trades/recent'))return fail?Response.json({code:'offline',message:'真实交易暂时不可用'},{status:503}):Response.json([{id:'real-es',contract_name:'ESZ6',symbol_root:'ES',direction:'long',size:'1',entry_price:'100',exit_price:'101',entered_at:'2026-10-01T01:00:00Z',exited_at:'2026-10-01T01:05:00Z'}]);
      return liveBackgroundResponse(url)??Response.json([]);
    }));
    render(<App/>);
    fireEvent.click(await screen.findByRole('button',{name:'交易记录'}));
    fireEvent.click(screen.getByRole('tab',{name:'真实交易'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('真实交易暂时不可用');
    fail=false;
    fireEvent.click(screen.getByRole('button',{name:'重试加载'}));
    await screen.findByText('ESZ6');
    expect(screen.queryByText('真实交易暂时不可用')).not.toBeInTheDocument();
  });

  it('does not restore a deleted trade from a source-switch read that was already in flight',async()=>{
    const trade={id:'delete-es',contract_name:'ESZ6',symbol_root:'ES',direction:'long',size:'1',entry_price:'100',exit_price:'101',entered_at:'2026-10-01T01:00:00Z',exited_at:'2026-10-01T01:05:00Z'};
    let deleted=false,reads=0;
    let finishDelete:(response:Response)=>void=()=>{};
    let finishStaleRead:(response:Response)=>void=()=>{};
    vi.spyOn(window,'confirm').mockReturnValue(true);
    vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
      const url=String(input);
      if(url==='/api/v1/health')return Response.json({status:'ok',auth_required:false});
      if(url.startsWith('/api/v1/replay/'))return Response.json({items:[]});
      if(url==='/api/v1/trades/delete-es'&&init?.method==='DELETE')return new Promise<Response>(resolve=>{finishDelete=response=>{deleted=true;resolve(response);};});
      if(url.startsWith('/api/v1/trades/recent')){reads++;if(reads===2)return new Promise<Response>(resolve=>{finishStaleRead=resolve;});return Response.json(deleted?[]:[trade]);}
      return liveBackgroundResponse(url)??Response.json([]);
    }));
    render(<App/>);
    fireEvent.click(await screen.findByRole('button',{name:'交易记录'}));
    fireEvent.click(screen.getByRole('tab',{name:'真实交易'}));
    await screen.findByText('ESZ6');
    fireEvent.click(screen.getByRole('button',{name:'删除'}));
    fireEvent.click(screen.getByRole('tab',{name:'回放交易'}));
    fireEvent.click(screen.getByRole('tab',{name:'真实交易'}));
    await waitFor(()=>expect(reads).toBe(2));
    await act(async()=>finishDelete(Response.json({status:'deleted'})));
    await act(async()=>finishStaleRead(Response.json([trade])));
    await waitFor(()=>expect(screen.queryByText('ESZ6')).not.toBeInTheDocument());
    expect(screen.getByText('暂无真实交易记录')).toBeInTheDocument();
  });

  it.each([
    ["failed", "分析失败"], ["cancelled", "分析已取消"], ["timed_out", "分析超时"],
    ["completed_with_warnings", "分析完成，但存在警告"],
    ["mixed_review", "15m · 分析失败：模型暂不可用"],
  ])("shows %s as a terminal outcome even with a partial payload", async (status, message) => {
    const task = { id: "terminal-task", kind: "analysis", title: "终态测试", description: "", status: "pending", config: { symbol: "ES", period: "5m" }, version: 1, created_at: "2026-08-11T01:00:00Z", updated_at: "2026-08-11T01:00:00Z", archived_at: null };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return Response.json({ status: "ok", auth_required: false });
      if (url === "/api/v1/analysis-tasks?limit=200") return Response.json({ items: [task], next_cursor: null });
      if (url === "/api/v1/analysis-tasks/terminal-task/runs") return Response.json(init?.method === "POST" ? (status === "mixed_review" ? [{ run_id: "terminal-run", period: "5m", status: "queued" }, { run_id: "failed-review", period: "15m", status: "queued" }] : [{ run_id: "terminal-run", period: "5m", status: "queued" }]) : []);
      if (url === "/api/v1/analysis-runs/terminal-run") return Response.json({ run_id: "terminal-run", task_id: task.id, status: status === "mixed_review" ? "completed" : status, mode: status === "mixed_review" ? "trade_review" : "realtime", period: "5m", failure_code: "run_" + status, result: { bars: [], llm_transcript: {} } });
      if (url === "/api/v1/analysis-runs/failed-review") return Response.json({ run_id: "failed-review", status: "failed", mode: "trade_review", period: "15m", failure_message: "模型暂不可用", result: { bars: [] } });
      return liveBackgroundResponse(url) ?? Response.json([]);
    }));
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "终态测试 · ES · 5m" }));
    fireEvent.click(await screen.findByRole("button", { name: "分析最新行情" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("分析完成", { exact: true })).not.toBeInTheDocument();
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

  it("offers five real futures roots for K-line analysis", async () => {
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

    expect(options.map((option) => (option as HTMLOptionElement).value)).toEqual(["ES", "NQ", "MES", "MNQ", "MGC"]);
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
    expect(within(chartControls!).getByLabelText("K 线标的切换")).toHaveValue("ES");
    expect(within(chartControls!).getByLabelText("K 线周期切换")).toHaveValue("5m");
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
      config: { symbol: "ES", period: "5m" }, version: 1,
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
        return new Response(JSON.stringify([{ run_id: "exec-1", period: "5m", status: "queued" }]), { status: 202 });
      }
      if (url === "/api/v1/analysis-tasks/task-live-1/runs") {
        return new Response(JSON.stringify([{
          run_id: "exec-1", task_id: "task-live-1", status: "completed",
          created_at: "2026-08-11T01:07:00Z", completed_at: "2026-08-11T01:07:05Z",
          direction: "bullish", symbol: "ES", period: "5m",
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
      config: { symbol: "ES", period: "5m" }, version: 1,
      created_at: "2026-08-11T01:00:00Z", updated_at: "2026-08-11T01:05:00Z", archived_at: null,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }), { status: 200 });
      if (url === "/api/v1/analysis-tasks?limit=200") return new Response(JSON.stringify({ items: [task], next_cursor: null }), { status: 200 });
      if (url === "/api/v1/analysis-tasks/task-run-id/runs") {
        return new Response(JSON.stringify([{
          run_id: "exec-selected", task_id: "task-run-id", status: "running",
          created_at: "2026-08-11T01:04:00Z", completed_at: null,
          direction: null, symbol: "ES", period: "5m",
        }]), { status: 200 });
      }
      if (url === "/api/v1/analysis-runs/exec-selected") {
        return new Response(JSON.stringify({
          run_id: "exec-selected", task_id: "task-run-id", status: "running", mode: "realtime", symbol: "ES",
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
    fireEvent.click(await screen.findByRole("button", { name: "5m · 进行中" }));

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
      if (url === "/api/v1/analysis-tasks" && init?.method === "POST") return new Response(JSON.stringify({ id: "task-1", kind: "analysis", title: "NQ 结构分析", description: "", status: "pending", config: JSON.parse(String(init.body)).config, version: 1, created_at: "2026-08-12T01:00:00Z", updated_at: "2026-08-12T01:00:00Z", archived_at: null }), { status: 201 });
      if (url === "/api/v1/analysis-tasks/task-1" && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ id: "task-1", kind: "analysis", title: body.title, description: body.description, status: "pending", config: body.config, version: 2, created_at: "2026-08-12T01:00:00Z", updated_at: "2026-08-12T01:05:00Z", archived_at: null }), { status: 200 });
      }
      if (url === "/api/v1/analysis-tasks/task-1/runs" && (!init || !init.method || init.method === "GET")) {
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
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).includes("/runs") && init?.method === "POST")).toBe(false);
    expect(await screen.findByText("NQ 修改后 · NQ · 15m")).toBeInTheDocument();
  });

  it("keeps the primary navigation usable at narrow viewports", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured", auth_required: false }) }));
    render(<App />);
    expect(screen.getByRole("button", { name: "K线分析" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    expect(screen.getByRole("menuitem", { name: "交易记录" })).toBeVisible();
    expect(screen.queryByRole("menuitem", { name: "回放记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "真实交易回放" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "交易日志" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "分析历史" })).toBeVisible();
    expect(screen.getByRole("button", { name: "+ 新建复盘任务" })).toBeVisible();
  });

  it("opens the personal knowledge and edits a stage prompt", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return new Response(JSON.stringify({ status: "ok", api_version: "v1", provider_configured: true, provider_transport: "https", storage_status: "postgresql_configured" }), { status: 200 });
      if (url === "/api/v1/knowledge/orchestration") return new Response(JSON.stringify({
          stages: [
            { id: "stage1", name: "Stage 1 · 市场诊断", kind: "llm", description: "市场诊断", prompt_files: [{ filename: "市场诊断框架.txt", placement: "user", condition: "每次 Stage 1 固定加载", editable: true }] },
            { id: "gate", name: "Gate · 阶段闸门", kind: "gate", description: "闸门", prompt_files: [] },
          ],
          edges: [{ source: "stage1", target: "gate", condition: "Stage 1 完成" }],
        }), { status: 200 });
      if (url.startsWith("/api/v1/knowledge/prompt?")) {
        const saved = init?.method === "PUT";
        return new Response(JSON.stringify({ filename: "市场诊断框架.txt", content: saved ? "更新后的提示词" : "原始提示词", source: saved ? "personal" : "default", revision: saved ? 2 : 1, version: saved ? "version-2" : "version-1", size: saved ? 21 : 15 }), { status: 200 });
      }
      if (url.startsWith("/api/v1/knowledge/versions?")) return new Response(JSON.stringify({ items: [], next_before_revision: null }), { status: 200 });
      const background = liveBackgroundResponse(url);
      if (background) return background;
      throw new Error(`未预期的请求：${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "更多" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "个人知识库" }));

    expect(await screen.findByRole("dialog", { name: "个人知识库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name:"阶段与加载说明" })).toBeInTheDocument();
    const editor = await screen.findByLabelText("提示词文本内容");
    fireEvent.change(editor, { target: { value: "更新后的提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "保存文本" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => String(url).startsWith("/api/v1/knowledge/prompt?") && init?.method === "PUT")).toBe(true));
    expect(await screen.findByText(/仅影响自己的后续分析/)).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("menuitem", { name: "交易记录" }));
    expect(await screen.findByRole("region", { name: "交易记录" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab',{name:'真实交易'}));
    await waitFor(()=>expect(screen.getByRole('button',{name:'导入 Excel / CSV'})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "导入 Excel / CSV" }));

    const fileInput = screen.getByLabelText("Excel 交易文件");
    const csvFile = new File(["content"], "trades.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    fireEvent.change(fileInput, {
      target: { files: [csvFile] },
    });

    expect(await screen.findByText("trades.xlsx")).toBeInTheDocument();
    expect(screen.getByText("共 1 笔 · 有效 1 笔 · 异常 0 笔")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect((await screen.findByText(/已导入 1 笔交易/)).closest('[role="status"]')).not.toBeNull();
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
        return new Response(JSON.stringify({ id: "review-task-1", owner_id: "local", kind: body.kind, title: body.title, description: body.description, config: body.config, status: "pending", version: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z" }), { status: 201 });
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

describe("personal knowledge management", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  async function openKnowledge(mode: 'conflict' | 'history' | 'network' | 'paging') {
    const request = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/health') return new Response(JSON.stringify({ status:'ok', auth_required:true }));
      if (url === '/api/v1/auth/me') return new Response(JSON.stringify({ id:'user-1', username:'normal', role:'user', auth_required:true }));
      if (url === '/api/v1/knowledge/orchestration') return new Response(JSON.stringify({ stages:[{ id:'stage1', name:'Stage 1', kind:'llm', description:'诊断', prompt_files:[{ filename:'市场诊断框架.txt', placement:'user', condition:'每次加载', editable:true }, { filename:'二元决策.txt', placement:'user', condition:'第二阶段', editable:true }] }], edges:[] }));
      if (url.startsWith('/api/v1/knowledge/prompt?')) {
        if (init?.method === 'PUT' && mode === 'network') throw new TypeError('连接中断');
        if (init?.method === 'PUT' && mode === 'conflict') return new Response(JSON.stringify({ code:'knowledge_version_conflict', message:'版本已更新，草稿保留' }), { status:409 });
        return new Response(JSON.stringify({ filename:'市场诊断框架.txt', content:'当前个人内容', version:'v1:entry:2', revision:2, source:'personal', size:18 }));
      }
      if (url.startsWith('/api/v1/knowledge/versions?') && mode === 'paging') return Response.json({ items:[{ id: url.includes('before_revision') ? 'old' : 'latest', revision: url.includes('before_revision') ? 1 : 2, actor:'本人', action:'save', created_at:'2026-01-01T00:00:00Z', can_rollback:true }], next_before_revision:url.includes('before_revision') ? null : 2 });
      if (url.startsWith('/api/v1/knowledge/versions?')) return new Response(JSON.stringify({ items:[{ id:'baseline', revision:1, actor:'本人', action:'baseline', created_at:'2026-01-01T00:00:00Z', can_rollback:true }], next_before_revision:null }));
      if (url.startsWith('/api/v1/knowledge/versions/baseline/rollback?')) return new Response(JSON.stringify({ filename:'市场诊断框架.txt', content:'初始模板', version:'v1:entry:3', revision:3, source:'personal', size:12 }));
      return liveBackgroundResponse(url) || new Response(JSON.stringify([]));
    });
    vi.stubGlobal('fetch', request);
    render(<App />);
    await waitFor(() => expect(request.mock.calls.some(([url]) => String(url) === '/api/v1/auth/me')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name:'更多' }));
    fireEvent.click(screen.getByRole('menuitem', { name:'个人知识库' }));
    const dialog = await screen.findByRole('dialog', { name:'个人知识库' });
    const editor = await within(dialog).findByLabelText('提示词文本内容');
    return { request, dialog, editor };
  }

  it('preserves conflict drafts and asks before closing for a normal user', async () => {
    const { request, dialog, editor } = await openKnowledge('conflict');
    fireEvent.change(editor, { target:{ value:'未保存的新内容' } });
    fireEvent.click(within(dialog).getByRole('button', { name:'保存文本' }));
    expect(await within(dialog).findByText('版本已更新，草稿保留')).toBeInTheDocument();
    expect(editor).toHaveValue('未保存的新内容');
    const save = request.mock.calls.find(([url, init]) => String(url).startsWith('/api/v1/knowledge/prompt?') && init?.method === 'PUT');
    expect(JSON.parse(String(save?.[1]?.body))).toEqual({ content:'未保存的新内容', expected_version:'v1:entry:2' });
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(within(dialog).getByRole('button', { name:'关闭' }));
    expect(dialog).toBeInTheDocument();
    expect(window.confirm).toHaveBeenCalled();
  });

  it('rolls back with the current version token and refreshes content', async () => {
    const { request, dialog, editor } = await openKnowledge('history');
    fireEvent.click(within(dialog).getByRole('button', { name:'查看版本' }));
    const rollback = await within(dialog).findByRole('button', { name:'回滚' });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(rollback);
    await waitFor(() => expect(editor).toHaveValue('初始模板'));
    const call = request.mock.calls.find(([url]) => String(url).startsWith('/api/v1/knowledge/versions/baseline/rollback?'));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ expected_version:'v1:entry:2' });
    expect(within(dialog).getByText('已回滚，仅影响自己的后续分析')).toBeInTheDocument();
  });
  it('keeps network failure drafts and cancels file switching', async () => {
    const { request, dialog, editor } = await openKnowledge('network');
    fireEvent.change(editor, { target:{ value:'网络失败时的草稿' } });
    fireEvent.click(within(dialog).getByRole('button', { name:'保存文本' }));
    expect(await within(dialog).findByText('连接中断')).toBeInTheDocument();
    expect(editor).toHaveValue('网络失败时的草稿');
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(within(dialog).getByRole('button', { name:/二元决策.txt/ }));
    expect(editor).toHaveValue('网络失败时的草稿');
    expect(request.mock.calls.some(([url]) => String(url).includes(encodeURIComponent('二元决策.txt')))).toBe(false);
  });

  it('opens a spacious stage guide and returns without losing the editor draft', async () => {
    const { dialog, editor } = await openKnowledge('history');
    fireEvent.change(editor, { target:{ value:'保留的草稿' } });
    fireEvent.click(within(dialog).getByRole('button', { name:'阶段与加载说明' }));
    expect(within(dialog).getByRole('heading', { name:'Stage 1' })).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('提示词文本内容')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name:'提示词编辑' }));
    expect(within(dialog).getByLabelText('提示词文本内容')).toHaveValue('保留的草稿');
  });

  it('appends the next page of version history', async () => {
    const { request, dialog } = await openKnowledge('paging');
    fireEvent.click(within(dialog).getByRole('button', { name:'查看版本' }));
    fireEvent.click(await within(dialog).findByRole('button', { name:'加载更多版本' }));
    await waitFor(() => expect(within(dialog).getAllByRole('button', { name:'回滚' })).toHaveLength(2));
    expect(request.mock.calls.some(([url]) => String(url).includes('before_revision=2'))).toBe(true);
    expect(within(dialog).queryByRole('button', { name:'加载更多版本' })).not.toBeInTheDocument();
  });
});

it('does not reveal an old personal document after logout while loading', async () => {
  let release!: (response: Response) => void;
  const pending = new Promise<Response>(resolve => { release = resolve; });
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/v1/health') return Response.json({ status:'ok', auth_required:true });
    if (url === '/api/v1/auth/me') return Response.json({ id:'A', username:'Alice', role:'user', auth_required:true });
    if (url === '/api/v1/auth/logout') return Response.json({ ok:true });
    if (url === '/api/v1/knowledge/orchestration') return Response.json({ stages:[{ id:'stage1', name:'Stage1', kind:'llm', description:'', prompt_files:[{ filename:'市场诊断框架.txt', placement:'user', condition:'', editable:true }] }], edges:[] });
    if (url.startsWith('/api/v1/knowledge/prompt?')) return pending;
    return liveBackgroundResponse(url) || Response.json([]);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<App />);
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/v1/auth/me')).toBe(true));
  fireEvent.click(screen.getByRole('button', { name:'更多' }));
  fireEvent.click(screen.getByRole('menuitem', { name:'个人知识库' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith('/api/v1/knowledge/prompt?'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name:'更多' }));
  fireEvent.click(screen.getByRole('menuitem', { name:'退出 Alice' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === '/api/v1/auth/logout')).toBe(true));
  release(Response.json({ filename:'市场诊断框架.txt', content:'Alice 私有内容', version:'v1:a:2', revision:2, source:'personal', size:12 }));
  await waitFor(() => expect(screen.queryByRole('dialog', { name:'个人知识库' })).not.toBeInTheDocument());
  expect(screen.queryByDisplayValue('Alice 私有内容')).not.toBeInTheDocument();
  cleanup();
  vi.unstubAllGlobals();
});


it('original chart replay guards symbol changes and allows restoring the original timeframe',async()=>{
 window.history.replaceState(null,'','/analysis/live');
 const requests:string[]=[];
 const replay={id:'guard',symbol:'ES',mode:'training',status:'active',revision:0,config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{cursor:'2025-01-01T00:00:00Z',config:{initial_cash:'100000',fee_per_side:'0',slippage_ticks:0},orders:[],position:{quantity:0},account:{closed_trades:0,wins:0}}};
 vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL)=>{const url=String(input);requests.push(url);
 if(url==='/api/v1/health')return Response.json({status:'ok',auth_required:false});
 if(url.startsWith('/api/v1/market/bars?'))return Response.json({bars:[{timestamp:'2025-01-01T00:00:00Z',open:100,high:102,low:99,close:101,volume:1}],coverage:{}});
 if(url.startsWith('/api/v1/replay/'))return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:replay);
 return liveBackgroundResponse(url)??Response.json([]);
 }));
 render(<App/>);
 fireEvent.click(await screen.findByRole('button',{name:'从此处回放'}));
 await screen.findByRole('button',{name:'下一根'});
 fireEvent.click(screen.getByRole('button',{name:'指标'}));
 fireEvent.click(within(screen.getByRole('dialog',{name:'指标库'})).getByRole('button',{name:'主图'}));
 expect(screen.queryByRole('dialog',{name:'保存本次回放？'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'关闭指标库'}));
 expect(screen.queryByRole('dialog',{name:'指标库'})).not.toBeInTheDocument();
 expect(screen.queryByRole('dialog',{name:'保存本次回放？'})).not.toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('回放图表周期'),{target:{value:'1m'}});
 await waitFor(()=>expect(screen.getByRole('button',{name:'下一根'})).toBeEnabled());
 fireEvent.change(screen.getByLabelText('K 线周期切换'),{target:{value:'5m'}});
 await waitFor(()=>expect(requests.some(url=>url.includes('/bars?period=5m'))).toBe(true));
 fireEvent.change(screen.getByLabelText('K 线标的切换'),{target:{value:'NQ'}});
 expect(screen.getByRole('dialog',{name:'保存本次回放？'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'继续回放'}));
 expect(screen.getByLabelText('K 线标的切换')).toHaveValue('ES');
 fireEvent.click(screen.getByRole('button',{name:'交易记录'}));
 expect(screen.getByRole('dialog',{name:'保存本次回放？'})).toBeInTheDocument();
 expect(screen.queryByRole('region',{name:'交易记录'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'继续回放'}));
 fireEvent.click(screen.getByRole('button',{name:'交易记录'}));
 expect(screen.getByRole('dialog',{name:'保存本次回放？'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'继续回放'}));
 window.history.pushState(null,'','/review/historical');
 fireEvent.popState(window);
 expect(screen.getByRole('dialog',{name:'保存本次回放？'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'继续回放'}));
 expect(window.location.pathname).toBe('/analysis/live');
 fireEvent.click(screen.getAllByRole('button',{name:'交易记录'}).at(-1)!);
 const leave=screen.getByRole('button',{name:'不保存退出'});
 fireEvent.pointerDown(leave);
 fireEvent.click(leave);
 expect(await screen.findByRole('region',{name:'交易记录'})).toBeInTheDocument();
 cleanup();vi.unstubAllGlobals();
});
