import { afterEach, describe, expect, it, vi } from "vitest";

import { analyzeRangeStream, apiFetch, deleteTrade, getHealth, getMarketBars } from "./api";


describe("market request diagnostics", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("correlates a successful market request with request id and timing logs", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => undefined);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      symbol: "ES",
      period: "5m",
      bars: [],
      coverage: { source_period: "1m", expected_bars: 400, actual_bars: 398, complete: false, missing_buckets: [] },
    }), { status: 200, headers: { "Content-Type": "application/json", "X-Request-ID": "server-request-id" } }));

    await getMarketBars("ES", "5m", "2026-08-18T00:00:00Z", "2026-08-18T06:40:00Z", undefined, true, "live_poll");

    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get("X-Trace-ID")).toBeTruthy();
    expect(new Headers(init?.headers).get("X-Market-Request-Kind")).toBe("live_poll");
    expect(info).toHaveBeenCalledWith("[market] request_start", expect.objectContaining({ kind: "live_poll", symbol: "ES", period: "5m" }));
    expect(info).toHaveBeenCalledWith("[market] request_success", expect.objectContaining({ requestId: "server-request-id", status: 200, bars: 0 }));
  });

  it("logs network failures with the request context", async () => {
    vi.spyOn(console, "info").mockImplementation(() => undefined);
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(getMarketBars("ES", "5m", "2026-08-18T00:00:00Z", "2026-08-18T06:40:00Z", undefined, true, "live_poll"))
      .rejects.toThrow("Failed to fetch");

    expect(error).toHaveBeenCalledWith("[market] request_failure", expect.objectContaining({
      kind: "live_poll",
      symbol: "ES",
      period: "5m",
      errorType: "TypeError",
      error: "Failed to fetch",
    }));
  });
});

describe("API trace propagation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("adds a trace id to every API request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      status: "ok",
      api_version: "v1",
      provider_configured: true,
      provider_transport: "https",
      storage_status: "postgresql_configured",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    await getHealth();

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("X-Trace-ID")).toMatch(/^[A-Za-z0-9._-]{1,128}$/);
  });

  it("reuses an explicit trace id while preserving caller headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await apiFetch("/first", {
      headers: { "Content-Type": "application/json" },
    }, "operation-trace-1");
    await apiFetch("/second", {
      headers: { "X-Market-Request-Kind": "live_poll" },
    }, "operation-trace-1");

    const firstHeaders = new Headers(fetchMock.mock.calls[0][1]?.headers);
    const secondHeaders = new Headers(fetchMock.mock.calls[1][1]?.headers);
    expect(firstHeaders.get("X-Trace-ID")).toBe("operation-trace-1");
    expect(firstHeaders.get("Content-Type")).toBe("application/json");
    expect(secondHeaders.get("X-Trace-ID")).toBe("operation-trace-1");
    expect(secondHeaders.get("X-Market-Request-Kind")).toBe("live_poll");
  });

  it("preserves headers carried by a Request object", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));

    await apiFetch(new Request("http://localhost/example", { headers: { Authorization: "Bearer test" } }), {}, "request-trace");

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer test");
    expect(headers.get("X-Trace-ID")).toBe("request-trace");
  });

  it("adds the response trace id to API errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      code: "internal_error",
      message: "failed",
    }), {
      status: 500,
      headers: {
        "Content-Type": "application/json",
        "X-Trace-ID": "server-trace-1",
      },
    }));

    await expect(getHealth()).rejects.toMatchObject({
      code: "internal_error",
      trace_id: "server-trace-1",
      request_id: "server-trace-1",
    });
  });

  it("adds the response trace id to delete errors", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ code: "delete_failed", message: "failed" }), {
      status: 500,
      headers: { "Content-Type": "application/json", "X-Trace-ID": "delete-trace" },
    }));

    await expect(deleteTrade("trade-1")).rejects.toMatchObject({ trace_id: "delete-trace" });
  });

  it("adds the response trace id to stream event errors", async () => {
    const body = `${JSON.stringify({ type: "error", code: "analysis_failed", message: "failed" })}\n`;
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "X-Trace-ID": "stream-trace" },
    }));

    await expect(analyzeRangeStream({} as never, () => undefined)).rejects.toMatchObject({ trace_id: "stream-trace" });
  });
});
