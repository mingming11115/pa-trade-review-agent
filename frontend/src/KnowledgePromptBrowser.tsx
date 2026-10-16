import { useState } from 'react';
import type { OrchestrationView, PipelineStage, PromptFileRef } from './types';

interface Props {
  view: OrchestrationView;
  selectedFilename: string | null;
  busy: boolean;
  onSelect: (filename: string) => void;
}

interface PromptChoice {
  filename: string;
  uses: Array<{ stage: PipelineStage; file: PromptFileRef }>;
}

export function KnowledgePromptBrowser({ view, selectedFilename, busy, onSelect }: Props) {
  const [query, setQuery] = useState('');
  const choices = new Map<string, PromptChoice>();
  for (const stage of view.stages) {
    for (const file of stage.prompt_files) {
      let choice = choices.get(file.filename);
      if (!choice) {
        choice = { filename:file.filename, uses:[] };
        choices.set(file.filename, choice);
      }
      choice.uses.push({ stage, file });
    }
  }
  const search = query.trim().toLocaleLowerCase();
  const visible = [...choices.values()].filter(choice => choice.filename.toLocaleLowerCase().includes(search));

  return <aside className="pipeline-browser knowledge-browser">
    <div className="knowledge-browser-header">
      <div className="pipeline-title"><strong>全部提示词</strong><span role="status">{search ? `${visible.length} / ${choices.size} 份` : `共 ${choices.size} 份`}</span></div>
      <input aria-label="搜索提示词" placeholder="搜索提示词文件名" type="search" value={query} onChange={event => setQuery(event.target.value)} />
    </div>
    <nav aria-label="提示词文件列表" className="knowledge-file-list stage-files" tabIndex={0}>
      {visible.map(choice => <button
        aria-pressed={choice.filename === selectedFilename}
        className={choice.filename === selectedFilename ? 'active' : ''}
        disabled={busy || choice.uses.every(use => !use.file.editable)}
        key={choice.filename}
        onClick={() => onSelect(choice.filename)}
        title={choice.uses.map(use => `${use.stage.name} · ${use.file.placement} · ${use.file.condition}`).join('\n')}
        type="button"
      >
        <span>{choice.filename}</span>
        <small>{choice.uses.map(use => `${use.stage.id.toUpperCase()} · ${use.file.placement === 'system' ? '系统提示词' : '任务提示词'}`).join(' / ')}</small>
      </button>)}
      {!visible.length && <p className="knowledge-empty" role="status">没有匹配的提示词</p>}
    </nav>
  </aside>;
}
