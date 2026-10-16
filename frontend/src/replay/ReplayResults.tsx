import type { ReplayEvents, ReplaySession } from '../types';

export function ReplayResults({session,events}:{session:ReplaySession;events:ReplayEvents}) {
  const a=session.state.account;
  const points=events.items.flatMap(e=>'equity'in e?[Number(e.equity)]:[]);
  const lo=Math.min(...points), hi=Math.max(...points);
  const polyline=points.map((p,i)=>`${10+i/Math.max(1,points.length-1)*580},${90-(p-lo)/Math.max(1,hi-lo)*70}`).join(' ');
  const stats=[['权益',a.equity],['已实现毛利',a.realized_pnl],['手续费',a.fees],['浮动盈亏',a.unrealized_pnl],['最大回撤',a.max_drawdown],['平仓次数',a.closed_trades],['胜率',a.closed_trades?`${(a.wins/a.closed_trades*100).toFixed(1)}%`:'—']];
  return <section><div className="replay-stats">{stats.map(([k,v])=><div key={k}><small>{k}</small><strong>{v}</strong></div>)}</div>
    {points.length>1&&<svg className="replay-equity" viewBox="0 0 600 100" role="img" aria-label="资金权益曲线"><polyline points={polyline} fill="none" stroke="currentColor" strokeWidth="2"/></svg>}
  </section>;
}
