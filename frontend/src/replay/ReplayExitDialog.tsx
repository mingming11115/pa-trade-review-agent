import { useEffect, useRef, useState } from 'react';
import type { ReplayTradingController } from './useReplayTradingController';

export function ReplayExitDialog({controller:c}:{controller:ReplayTradingController}) {
 const [name,setName]=useState(`${c.replay.session?.symbol??''} ${new Date().toLocaleString('zh-CN')} 回放`);
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const d=dialog.current;if(d?.showModal)d.showModal();else d?.setAttribute("open", "");},[]);
 const locked=c.busy||c.replay.busy||c.replay.retryAvailable||!c.replay.session;
 return <dialog ref={dialog} className="replay-exit-dialog" aria-label="保存本次回放？" onCancel={e=>{e.preventDefault();c.cancelLeave();}}>
  <h3>保存本次回放？</h3><p>保存行情进度、订单、成交和持仓，之后可继续回放或分析。</p>
  <label>记录名称<input autoFocus maxLength={200} value={name} onChange={e=>setName(e.target.value)} disabled={c.busy}/></label>
  {c.error&&<p role="alert">{c.error}</p>}
  {c.replay.retryAvailable&&<p role="alert">上次订单结果待确认，请先继续回放并重试确认。</p>}
  {c.replay.busy&&<p role="status">正在等待当前操作完成…</p>}
  <div className="replay-exit-actions"><button className="primary-action" disabled={locked} onClick={()=>void c.saveAndLeave(name)}>保存并退出</button><button disabled={locked} onClick={()=>void c.discardAndLeave()}>不保存退出</button><button disabled={c.busy} onClick={c.cancelLeave}>继续回放</button></div>
 </dialog>;
}
