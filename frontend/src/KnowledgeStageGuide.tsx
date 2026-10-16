import { useState } from 'react';
import type { OrchestrationView, PipelineStage } from './types';

function executionLabel(stage: PipelineStage): string {
  if (stage.kind === 'llm') return '模型分析';
  if (stage.kind === 'gate') return '规则判断';
  if (stage.kind === 'review') return '结果复盘';
  return '程序处理';
}

export function KnowledgeStageGuide({ view }: { view: OrchestrationView }) {
  const [selectedId, setSelectedId] = useState(() => view.stages.find(stage => stage.kind === 'llm')?.id ?? view.stages[0]?.id);
  const selected = view.stages.find(stage => stage.id === selectedId) ?? view.stages[0];
  const total = new Set(view.stages.flatMap(stage => stage.prompt_files.map(file => file.filename))).size;

  return <section aria-label="阶段加载说明" className="knowledge-guide">
    <div className="knowledge-guide-intro">
      <div><span className="knowledge-guide-eyebrow">分析流程</span><h2>了解每份提示词何时生效</h2><p>选择一个阶段，查看它的用途、提示词和加载条件。</p></div>
      <span className="knowledge-guide-total">{view.stages.length} 个阶段 · {total} 份提示词</span>
    </div>
    <div className="knowledge-guide-layout">
      <nav aria-label="选择分析阶段" className="knowledge-guide-nav">
        {view.stages.map((stage, index) => <button aria-pressed={stage.id === selected?.id} className={stage.id === selected?.id ? 'active' : ''} key={stage.id} onClick={() => setSelectedId(stage.id)} type="button">
          <span className="knowledge-guide-number">{String(index + 1).padStart(2, '0')}</span>
          <span><strong>{stage.name}</strong><small>{executionLabel(stage)} · {stage.prompt_files.length ? `${stage.prompt_files.length} 份引用` : '无需提示词'}</small></span>
        </button>)}
      </nav>
      <div className="knowledge-guide-content" key={selected?.id} tabIndex={0}>
        {selected ? <>
          <div className="knowledge-guide-stage-heading"><span className={`knowledge-guide-kind kind-${selected.kind}`}>{executionLabel(selected)}</span><h2>{selected.name}</h2><p>{selected.description}</p></div>
          {selected.prompt_files.length ? <>
            <div className="knowledge-guide-section-title"><h3>提示词与加载条件</h3><span>{selected.prompt_files.length} 份引用</span></div>
            <table aria-label="本阶段的提示词与加载条件" className="knowledge-guide-table">
              <thead><tr><th>提示词文件</th><th>用途</th><th>什么时候加载</th></tr></thead>
              <tbody>{selected.prompt_files.map(file => <tr key={file.filename}><td>{file.filename}</td><td><span className={`knowledge-guide-placement placement-${file.placement}`}>{file.placement === 'system' ? '系统提示词' : '任务提示词'}</span></td><td>{file.condition}</td></tr>)}</tbody>
            </table>
          </> : <div className="knowledge-guide-empty"><strong>无需提示词</strong><p>该阶段由程序处理，不加载提示词文件。</p></div>}
          <p className="knowledge-guide-note">加载条件由分析流程决定。修改提示词正文，请切回“提示词编辑”。</p>
        </> : <p>暂无阶段说明。</p>}
      </div>
    </div>
  </section>;
}
