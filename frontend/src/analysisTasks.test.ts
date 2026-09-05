import { describe, expect, it } from "vitest";
import { findLiveAnalysisTask, sidebarTaskFromApi, taskActions } from "./analysisTasks";

describe("analysis task helpers", () => {
  it("offers actions based on durable status", () => {
    expect(taskActions("pending")).toEqual(["run"]);
    expect(taskActions("running")).toEqual(["open"]);
    expect(taskActions("failed")).toEqual(["open"]);
    expect(taskActions("completed")).toEqual(["open"]);
    expect(taskActions("completed", "analysis")).toEqual(["open"]);
    expect(taskActions("failed", "analysis")).toEqual(["open"]);
    expect(taskActions("running", "analysis")).toEqual(["open"]);
  });

  it("restores an analysis task into sidebar configuration", () => {
    const restored = sidebarTaskFromApi({
      id: "t1", kind: "analysis", title: "NQ", description: "", status: "pending",
      config: { symbol: "NQ", period: "15m", start: "2026-08-12T01:00:00Z", end: "2026-08-12T02:00:00Z", analysis_mode: "historical" },
      version: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z", archived_at: null,
    });
    expect(restored.id).toBe("t1");
    expect(restored.config.symbol).toBe("NQ");
    expect(restored.config.analysisMode).toBe("range");
  });

  it("finds the unique live analysis task by symbol and period", () => {
    const tasks = [
      sidebarTaskFromApi({
        id: "t1", kind: "analysis", title: "ES 5m", description: "", status: "completed",
        config: { symbol: "ES", period: "5m" },
        version: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z", archived_at: null,
      }),
      sidebarTaskFromApi({
        id: "t2", kind: "analysis", title: "ES 15m", description: "", status: "pending",
        config: { symbol: "ES", period: "15m" },
        version: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z", archived_at: null,
      }),
    ];
    expect(findLiveAnalysisTask(tasks, " es ", "5m")?.id).toBe("t1");
    expect(findLiveAnalysisTask(tasks, "ES", "1m")).toBeUndefined();
  });

  it("restores analysis tasks without a historical mode into the live workbench", () => {
    const restored = sidebarTaskFromApi({
      id: "live-1", kind: "analysis", title: "ES 5m", description: "", status: "pending",
      config: { symbol: "ES", period: "5m" },
      version: 1, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z", archived_at: null,
    });

    expect(restored.config.analysisMode).toBe("live");
    expect(restored.config.streamEnabled).toBe(true);
  });
});
