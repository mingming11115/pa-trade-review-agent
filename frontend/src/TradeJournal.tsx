import type { Trade, TradeImportBatch } from './types';
import './tradeRecords.css';

interface Props {
  trades: Trade[];
  imports: TradeImportBatch[];
  loading: boolean;
  error: string;
  query: string;
  onQueryChange: (query: string) => void;
  onRefresh: () => void;
  onImport: () => void;
  onAdd: () => void;
  onEdit: (trade: Trade) => void;
  onDelete: (trade: Trade) => void;
  onReview: (trade: Trade) => void;
  onReplay: (trade: Trade) => void;
  onReplayHistory: () => void;
}

const beijingDate = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? beijingDate.format(date) : '—';
}

export function TradeJournal({ trades, imports, loading, error, query, onQueryChange, onRefresh, onImport, onAdd, onEdit, onDelete, onReview, onReplay, onReplayHistory }: Props) {
  const needle = query.trim().toLocaleLowerCase();
  const filtered = trades.filter(trade => [trade.contract_name, trade.symbol_root, trade.strategy, trade.account, ...(trade.tags ?? []), trade.notes]
    .filter(Boolean).join(' ').toLocaleLowerCase().includes(needle));

  return <section className="trade-journal" aria-label="真实交易记录" aria-busy={loading}>
    <div className="trade-journal-toolbar">
      <input type="search" aria-label="搜索交易记录" placeholder="搜索合约、策略、账户、标签或笔记" value={query} onChange={event => onQueryChange(event.target.value)} />
      <div className="trade-journal-actions">
        <button onClick={onImport} disabled={loading} type="button">导入 Excel / CSV</button>
        <button onClick={onAdd} disabled={loading} type="button">手工新增</button>
        <button onClick={onReplayHistory} type="button">回放历史交易</button>
        <button onClick={onRefresh} disabled={loading} type="button">刷新</button>
      </div>
    </div>
    <div className="trade-journal-caption"><span className="trade-source-badge">真实交易</span><span>{needle ? `${filtered.length} / ${trades.length}` : trades.length} 笔交易</span><span>时间均为北京时间</span></div>
    {error && <div className="trade-journal-error" role="alert"><span>{error}</span><button onClick={onRefresh} disabled={loading} type="button">重试加载</button></div>}
    {loading && <p className="trade-records-status" role="status">正在加载交易记录…</p>}
    {filtered.length > 0 ? <div className="trade-journal-table-scroll" tabIndex={0} role="region" aria-label="真实交易明细">
      <table className="trade-journal-table">
        <thead><tr><th scope="col">合约 / 方向</th><th scope="col">开仓 / 平仓时间</th><th scope="col" className="numeric">开仓 / 平仓价</th><th scope="col" className="numeric">手数</th><th scope="col" className="numeric">报告盈亏</th><th scope="col">策略 / 账户 / 笔记</th><th scope="col">操作</th></tr></thead>
        <tbody>{filtered.map(trade => <tr key={trade.id}>
          <td><strong>{trade.contract_name}</strong><span className={`trade-direction ${trade.direction}`}>{trade.direction === 'long' ? '多' : '空'}</span></td>
          <td className="trade-time"><time dateTime={trade.entered_at}>{formatTime(trade.entered_at)}</time><time dateTime={trade.exited_at}>{formatTime(trade.exited_at)}</time></td>
          <td className="numeric"><span>{trade.entry_price}</span><span>{trade.exit_price}</span></td>
          <td className="numeric">{trade.size}</td>
          <td className={`numeric trade-pnl ${trade.reported_pnl === null ? '' : Number(trade.reported_pnl) > 0 ? 'positive' : Number(trade.reported_pnl) < 0 ? 'negative' : ''}`}>{trade.reported_pnl ?? '—'}</td>
          <td className="trade-journal-notes"><span>{trade.strategy || '未标记策略'}{trade.account ? ` · ${trade.account}` : ''}</span>{trade.tags?.length ? <small>{trade.tags.join(' · ')}</small> : null}<small>{trade.notes || '暂无笔记'}</small></td>
          <td><div className="trade-row-actions"><button onClick={() => onReview(trade)} type="button">复盘</button><button onClick={() => onReplay(trade)} type="button">回放</button><button disabled={loading} onClick={() => onEdit(trade)} type="button">编辑</button><button className="trade-delete" disabled={loading} onClick={() => onDelete(trade)} type="button">删除</button></div></td>
        </tr>)}</tbody>
      </table>
    </div> : !loading && !error ? <div className="trade-records-empty"><strong>{needle ? '没有匹配的交易记录' : '暂无真实交易记录'}</strong><p>{needle ? '调整搜索条件后重试。' : '导入 Excel / CSV 或手工新增交易后，可在这里复盘与回放。'}</p></div> : null}
    {imports.length > 0 && <details className="trade-import-history"><summary>导入历史 <span>{imports.length}</span></summary><div>{imports.map(batch => <article key={batch.id}><div><strong>{batch.file_name}</strong><time dateTime={batch.created_at}>{formatTime(batch.created_at)} 北京时间</time></div><p>导入 {batch.imported_rows} · 重复 {batch.skipped_duplicates} · 异常 {batch.invalid_rows}</p>{batch.errors.slice(0, 3).map(item => <small key={`${item.row}-${item.message}`}>第 {item.row} 行：{item.message}</small>)}</article>)}</div></details>}
  </section>;
}

export default TradeJournal;
