export type WorkbenchMode = "review" | "analysis";

export type SidebarLeafId = "analysis-history" | `task:${string}` | `run:${string}`;

export interface SidebarNavTask {
  id: string;
  type: WorkbenchMode;
  title: string;
  symbol?: string;
  period?: string;
}

export interface SidebarNavAnalysisRun {
  id: string;
  sequence: number;
  status: string;
  createdAt: string;
  resultId?: string | null;
  analysisId?: string | null;
  direction?: string | null;
  symbol?: string | null;
  period?: string | null;
  source?: "execution" | "history";
}

interface SidebarNavProps {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  openSections: Record<WorkbenchMode, boolean>;
  onToggleSection: (section: WorkbenchMode) => void;
  activeLeaf: SidebarLeafId | null;
  reviewTasks: SidebarNavTask[];
  analysisTasks: SidebarNavTask[];
  analysisRunsByTask: Record<string, SidebarNavAnalysisRun[]>;
  expandedAnalysisTasks: Record<string, boolean>;
  onToggleAnalysisTask: (taskId: string) => void;
  loading?: boolean;
  unavailable?: boolean;
  analysisRunning?: boolean;
  onSelectTask: (taskId: string, type: WorkbenchMode) => void;
  onSelectAnalysisRun: (taskId: string, run: SidebarNavAnalysisRun) => void;
  onNewReviewTask: () => void;
  onNewAnalysisTask: () => void;
  onExpand: () => void;
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg aria-hidden="true" className={open ? "sidebar-chevron open" : "sidebar-chevron"} fill="none" height="12" viewBox="0 0 12 12" width="12">
      <path d="M4.2 2.4 7.8 6 4.2 9.6" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function DocIcon() {
  return (
    <svg aria-hidden="true" className="sidebar-icon" fill="none" height="14" viewBox="0 0 14 14" width="14">
      <path d="M3.5 1.75h5.1L10.5 3.65V12.25H3.5V1.75Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.2" />
      <path d="M8.4 1.75V3.8H10.5" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.2" />
      <path d="M5 6.5h4M5 8.5h4M5 10.5h2.5" stroke="currentColor" strokeLinecap="round" strokeWidth="1.1" />
    </svg>
  );
}

function ChartIcon() {
  return (
    <svg aria-hidden="true" className="sidebar-icon" fill="none" height="14" viewBox="0 0 14 14" width="14">
      <path d="M2 11.5V9.2L4.4 7.1l2.1 1.6L9.8 5.2 12 7" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.2" />
      <path d="M2 12h10" stroke="currentColor" strokeLinecap="round" strokeWidth="1.2" />
    </svg>
  );
}

function LeafIcon() {
  return (
    <svg aria-hidden="true" className="sidebar-icon sidebar-icon-sm" fill="none" height="12" viewBox="0 0 12 12" width="12">
      <circle cx="6" cy="6" fill="currentColor" r="1.35" />
    </svg>
  );
}

function taskLabel(task: SidebarNavTask): string {
  if (task.type !== "analysis") return task.title;
  const parts = [task.title.trim() || "未命名分析任务"];
  if (task.symbol) parts.push(task.symbol);
  if (task.period) parts.push(task.period);
  return parts.join(" · ");
}

function runLabel(run: SidebarNavAnalysisRun): string {
  const statusLabel =
    run.status === "completed" || run.status === "completed_with_warnings" || run.status === "degraded"
      ? "完成"
      : run.status === "running" || run.status === "queued"
        ? "进行中"
        : run.status === "failed" || run.status === "timed_out"
          ? "失败"
          : run.status === "cancelled" || run.status === "cancel_requested"
            ? "已取消"
            : run.status;
  const direction =
    run.direction === "long" ? "多" : run.direction === "short" ? "空" : run.direction === "neutral" ? "中性" : null;
  const parts = [`#${run.sequence}`, statusLabel];
  if (direction) parts.push(direction);
  return parts.join(" · ");
}

export function SidebarNav({
  collapsed,
  onToggleCollapsed,
  openSections,
  onToggleSection,
  activeLeaf,
  reviewTasks,
  analysisTasks,
  analysisRunsByTask,
  expandedAnalysisTasks,
  onToggleAnalysisTask,
  loading = false,
  unavailable = false,
  analysisRunning = false,
  onSelectTask,
  onSelectAnalysisRun,
  onNewReviewTask,
  onNewAnalysisTask,
  onExpand,
}: SidebarNavProps) {
  const disabled = loading || unavailable;

  if (collapsed) {
    return (
      <>
        <header className="panel-header sidebar-header">
          <button aria-label="展开侧边栏" className="sidebar-toggle" onClick={onExpand} title="展开侧边栏" type="button">
            <span aria-hidden="true">☰</span>
          </button>
        </header>
        <nav aria-label="侧边栏快捷操作" className="collapsed-rail">
          <button aria-label="展开侧边栏" onClick={onExpand} title="侧边栏" type="button">
            <span aria-hidden="true">☰</span>
          </button>
          <span
            aria-hidden="true"
            className={analysisRunning ? "rail-status running" : "rail-status"}
            title={analysisRunning ? "分析运行中" : "当前空闲"}
          />
        </nav>
      </>
    );
  }

  return (
    <div className="sidebar-shell">
      <header className="panel-header sidebar-header">
        <button
          aria-label="收起侧边栏"
          className="sidebar-toggle"
          onClick={onToggleCollapsed}
          title="收起侧边栏"
          type="button"
        >
          <span aria-hidden="true">☰</span>
        </button>
      </header>

      <div className="sidebar-scroll">
        <div className="sidebar-group-title">项目</div>

        <div className="sidebar-tree">
          <section className="sidebar-branch">
            <div className="sidebar-l1-row">
              <button
                aria-expanded={openSections.review}
                className="sidebar-l1"
                onClick={() => onToggleSection("review")}
                type="button"
              >
                <span className="sidebar-l1-main">
                  <DocIcon />
                  <span>历史复盘</span>
                </span>
              </button>
              <button
                aria-label="+ 新建复盘任务"
                className="sidebar-add"
                disabled={disabled}
                onClick={onNewReviewTask}
                title="新建复盘任务"
                type="button"
              >
                +
              </button>
              <button
                aria-expanded={openSections.review}
                aria-label={openSections.review ? "收起历史复盘" : "展开历史复盘"}
                className="sidebar-chevron-btn"
                onClick={() => onToggleSection("review")}
                type="button"
              >
                <Chevron open={openSections.review} />
              </button>
            </div>

            {openSections.review && (
              <div className="sidebar-children">
                {reviewTasks.length ? (
                  <div className="sidebar-leaves">
                    {reviewTasks.map((task) => (
                      <button
                        key={task.id}
                        className={activeLeaf === `task:${task.id}` ? "sidebar-leaf active" : "sidebar-leaf"}
                        onClick={() => onSelectTask(task.id, "review")}
                        type="button"
                      >
                        {taskLabel(task)}
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="sidebar-empty-note">还没有复盘任务。</p>
                )}
              </div>
            )}
          </section>

          <section className="sidebar-branch">
            <div className="sidebar-l1-row">
              <button
                aria-expanded={openSections.analysis}
                className="sidebar-l1"
                onClick={() => onToggleSection("analysis")}
                type="button"
              >
                <span className="sidebar-l1-main">
                  <ChartIcon />
                  <span>K线分析</span>
                </span>
              </button>
              <button
                aria-label="+ 新建分析任务"
                className="sidebar-add"
                disabled={disabled}
                onClick={onNewAnalysisTask}
                title="新建分析任务"
                type="button"
              >
                +
              </button>
              <button
                aria-expanded={openSections.analysis}
                aria-label={openSections.analysis ? "收起K线分析" : "展开K线分析"}
                className="sidebar-chevron-btn"
                onClick={() => onToggleSection("analysis")}
                type="button"
              >
                <Chevron open={openSections.analysis} />
              </button>
            </div>

            {openSections.analysis && (
              <div className="sidebar-children">
                {analysisTasks.length ? (
                  analysisTasks.map((task) => {
                    const expanded = Boolean(expandedAnalysisTasks[task.id]);
                    const runs = analysisRunsByTask[task.id] ?? [];
                    return (
                      <div className="sidebar-task-branch" key={task.id}>
                        <div className="sidebar-l3-row">
                          <button
                            className={activeLeaf === `task:${task.id}` ? "sidebar-l3 active" : "sidebar-l3"}
                            onClick={() => onSelectTask(task.id, "analysis")}
                            type="button"
                          >
                            <LeafIcon />
                            <span>{taskLabel(task)}</span>
                          </button>
                          <button
                            aria-expanded={expanded}
                            aria-label={expanded ? `收起${taskLabel(task)}的分析` : `展开${taskLabel(task)}的分析`}
                            className="sidebar-chevron-btn"
                            onClick={() => onToggleAnalysisTask(task.id)}
                            type="button"
                          >
                            <Chevron open={expanded} />
                          </button>
                        </div>
                        {expanded && (
                          <div className="sidebar-leaves">
                            {runs.length ? (
                              runs.map((run) => (
                                <button
                                  key={run.id}
                                  className={activeLeaf === `run:${run.id}` ? "sidebar-leaf active" : "sidebar-leaf"}
                                  onClick={() => onSelectAnalysisRun(task.id, run)}
                                  type="button"
                                >
                                  {runLabel(run)}
                                </button>
                              ))
                            ) : (
                              <p className="sidebar-empty-note">还没有分析，打开任务后可运行。</p>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <p className="sidebar-empty-note">还没有分析任务。点 + 新建一个任务。</p>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
