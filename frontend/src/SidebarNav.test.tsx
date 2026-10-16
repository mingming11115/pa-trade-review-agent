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
          a1: [{ id: "e1", status: "completed", createdAt: "2026-08-13T00:00:00Z", runId: "e1", direction: "long", period: "5m" }],
        }}
        expandedAnalysisTasks={{ a1: true }}
        onToggleAnalysisTask={onToggleAnalysisTask}
        onSelectTask={() => undefined}
        onSelectAnalysisRun={onSelectAnalysisRun}
        onNewReviewTask={() => undefined}
        onNewAnalysisTask={() => undefined}
      />,
    );

    expect(screen.queryByText("工作空间")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "历史复盘" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "K线分析" })).toBeInTheDocument();
    expect(screen.queryByText("多任务")).not.toBeInTheDocument();
    expect(screen.queryByText("分析历史")).not.toBeInTheDocument();
    expect(screen.queryByText("实时K线分析")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未命名分析任务 · NQ · 5m" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "5m · 完成 · 多" })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "+ 新建复盘任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ 新建分析任务" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "历史复盘" }));
    expect(onToggleSection).toHaveBeenCalledWith("review");
    fireEvent.click(screen.getByRole("button", { name: "5m · 完成 · 多" }));
    expect(onSelectAnalysisRun).toHaveBeenCalled();
  });

  it.each([false, true])("offers one new analysis action and respects loading=%s", (loading) => {
    const onNewAnalysisTask = vi.fn();
    render(<SidebarNav collapsed={false} onToggleCollapsed={() => undefined} onExpand={() => undefined}
      openSections={{ review: true, analysis: true }} onToggleSection={() => undefined} activeLeaf={null}
      reviewTasks={[]} analysisTasks={[]} analysisRunsByTask={{}} expandedAnalysisTasks={{}}
      onToggleAnalysisTask={() => undefined} onSelectTask={() => undefined} onSelectAnalysisRun={() => undefined}
      onNewReviewTask={() => undefined} onNewAnalysisTask={onNewAnalysisTask} loading={loading} />);
    expect(screen.getAllByRole("button", { name: /新建分析任务/ })).toHaveLength(1);
    const action = screen.getByRole("button", { name: "+ 新建分析任务" });
    if (loading) expect(action).toBeDisabled();
    else expect(action).toBeEnabled();
    fireEvent.click(action);
    expect(onNewAnalysisTask).toHaveBeenCalledTimes(loading ? 0 : 1);
  });

  it.each([false, true])("opens the unified trading records when collapsed=%s", (collapsed) => {
    const onOpenRecords = vi.fn();
    render(<SidebarNav collapsed={collapsed} onOpenRecords={onOpenRecords}
      onToggleCollapsed={() => undefined} onExpand={() => undefined}
      openSections={{ review: true, analysis: true }} onToggleSection={() => undefined} activeLeaf={null}
      reviewTasks={[]} analysisTasks={[]} analysisRunsByTask={{}} expandedAnalysisTasks={{}}
      onToggleAnalysisTask={() => undefined} onSelectTask={() => undefined} onSelectAnalysisRun={() => undefined}
      onNewReviewTask={() => undefined} onNewAnalysisTask={() => undefined} />);

    const action = screen.getByRole("button", { name: "交易记录" });
    expect(action.closest("[data-records-entry]")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "回放记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "真实交易回放" })).not.toBeInTheDocument();
    fireEvent.click(action);
    expect(onOpenRecords).toHaveBeenCalledTimes(1);
  });
});
