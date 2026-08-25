import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SidebarNav } from "./SidebarNav";

describe("SidebarNav", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("nests analyses under analysis tasks", () => {
    const onToggleSection = vi.fn();
    const onToggleAnalysisTask = vi.fn();
    const onSelectAnalysisRun = vi.fn();
    render(
      <SidebarNav
        collapsed={false}
        onToggleCollapsed={() => undefined}
        onExpand={() => undefined}
        openSections={{ review: true, analysis: true }}
        onToggleSection={onToggleSection}
        activeLeaf="run:e1"
        reviewTasks={[]}
        analysisTasks={[{ id: "a1", type: "analysis", title: "未命名分析任务", symbol: "NQ", period: "5m" }]}
        analysisRunsByTask={{
          a1: [{ id: "e1", sequence: 2, status: "completed", createdAt: "2026-08-13T00:00:00Z", resultId: "r1", direction: "long" }],
        }}
        expandedAnalysisTasks={{ a1: true }}
        onToggleAnalysisTask={onToggleAnalysisTask}
        onSelectTask={() => undefined}
        onSelectAnalysisRun={onSelectAnalysisRun}
        onNewReviewTask={() => undefined}
        onNewAnalysisTask={() => undefined}
      />,
    );

    expect(screen.getByText("项目")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "历史复盘" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "K线分析" })).toBeInTheDocument();
    expect(screen.queryByText("多任务")).not.toBeInTheDocument();
    expect(screen.queryByText("分析历史")).not.toBeInTheDocument();
    expect(screen.queryByText("实时K线分析")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未命名分析任务 · NQ · 5m" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "#2 · 完成 · 多" })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "+ 新建复盘任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ 新建分析任务" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "历史复盘" }));
    expect(onToggleSection).toHaveBeenCalledWith("review");
    fireEvent.click(screen.getByRole("button", { name: "#2 · 完成 · 多" }));
    expect(onSelectAnalysisRun).toHaveBeenCalled();
  });
});
