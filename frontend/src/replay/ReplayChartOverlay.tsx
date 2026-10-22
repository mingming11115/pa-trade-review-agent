import { canMarketTrade } from '../tradingHours';
import { useLayoutEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { LineStyle } from 'lightweight-charts';
import type { IPriceLine } from 'lightweight-charts';
import type { ChartOverlayContext } from '../chartOverlay';
import type { ReplayOrder, ReplaySession } from '../types';
import { protectionError, signedUsd, snapChartPrice, suggestedOrderKind } from './chartTrading';
import type { ProtectionField } from './chartTrading';
import './chartTrading.css';

export interface ChartOrderRequest {
  side: 'buy' | 'sell';
  kind: ReplayOrder['kind'];
  price: string;
  anchor: { x: number; y: number };
  container: HTMLElement;
}

interface Props {
  context: ChartOverlayContext;
  session: ReplaySession;
  period?: string;
  disabled: boolean;
  hidden: boolean;
  stale?: boolean;
  contextKey: string;
  pending: boolean;
  quantity: number;
  onQuantityChange: (quantity: number) => void;
  onMarketOrder: (side: 'buy' | 'sell', quantity: number, expected: {sessionId: string; revision: number}) => Promise<boolean>;
  onPause: () => void;
  onOrder: (request: ChartOrderRequest) => void;
  onClosePosition: (expected: {sessionId: string; revision: number}) => Promise<boolean>;
  onProtection: (input: {stop_loss: string | null; take_profit: string | null}, expected: {sessionId: string; revision: number}) => Promise<boolean>;
}

interface Drag {
  field: ProtectionField;
  pointerId: number;
  target: HTMLButtonElement;
  startY: number;
  moved: boolean;
  price: string | null;
  expected: {sessionId: string; revision: number};
  contextKey: string;
  context: ChartOverlayContext;
}
interface Preview { field: ProtectionField; price: string; error: string | null }
type LineKey = 'position' | ProtectionField;
const lineColors: Record<LineKey, string> = {position:'#4d83c8',stop_loss:'#ce5560',take_profit:'#168367'};

function displayPrice(price: string, tick: string) {
  const decimals = Number(tick).toLocaleString('en-US',{useGrouping:false,maximumFractionDigits:20}).split('.')[1]?.length ?? 0;
  return Number(price).toLocaleString('en-US',{minimumFractionDigits:decimals,maximumFractionDigits:decimals});
}

function MenuIcon({kind}: {kind:'reset'|'buy'|'sell'}) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="chart-menu-icon" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
    {kind==='reset'?<><path d="M4.5 8.5A8 8 0 1 1 4 16"/><path d="M4.5 3.5v5h5"/></>:kind==='buy'?<><path d="m5 13 7-7 7 7"/><path d="m5 19 7-7 7 7"/></>:<><path d="m5 5 7 7 7-7"/><path d="m5 11 7 7 7-7"/></>}
  </svg>;
}

export function ReplayChartOverlay(props: Props) {
  const {context,session,disabled,hidden,contextKey,pending,onPause,onOrder} = props;
  const root = useRef<HTMLDivElement>(null);
  const quickTrade = useRef<HTMLDivElement>(null);
  const menuElement = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const drag = useRef<Drag | null>(null);
  const mounted = useRef(false);
  const submittingRef = useRef(false);
  const lines = useRef(new Map<LineKey,IPriceLine>());
  const [menu,setMenu] = useState<{price: string; x: number; y: number} | null>(null);
  const [measuredMenuHeight,setMeasuredMenuHeight] = useState(276);
  const [preview,setPreview] = useState<Preview | null>(null);
  const [error,setError] = useState('');
  const [submitting,setSubmitting] = useState<'protection' | 'close' | 'order' | null>(null);
  const [dragging,setDragging] = useState(false);
  const [quickTop,setQuickTop] = useState(42);
  const [,setViewport] = useState(0);
  const position = session.state.position;
  const active = !hidden && !props.stale && !!position.quantity;
  const unavailable = disabled || hidden || pending || submitting !== null;
  const pane = context.chart.paneSize(0);
  const validQuantity = Number.isInteger(props.quantity) && props.quantity >= 1 && props.quantity <= 100000;
  const validPrice = !props.stale && session.state.last_price != null && Number.isFinite(Number(session.state.last_price));

  function endDrag(clearPreview = true) {
    const current = drag.current;
    drag.current = null;
    if (current) {
      // Clear the ref before release: lostpointercapture is also a cancellation path.
      if (current.target.hasPointerCapture?.(current.pointerId)) current.target.releasePointerCapture(current.pointerId);
      current.context.setInteractionLocked(false);
    }
    if (mounted.current) {
      setDragging(false);
      if (clearPreview) setPreview(null);
    }
  }

  function canInteract() {
    const current = latest.current;
    return !current.disabled && !current.hidden && !current.pending && !submittingRef.current;
  }

  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; endDrag(false); };
  },[]);

  useLayoutEffect(() => context.subscribeViewportChange(() => setViewport(value => value + 1)),[context]);

  useLayoutEffect(() => {
    const element = menuElement.current;
    if (!menu || !element) return;
    const measure = () => {
      const height = element.getBoundingClientRect().height;
      if (height > 0) setMeasuredMenuHeight(previous=>previous===height?previous:height);
    };
    measure();
    if (typeof ResizeObserver==='undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return ()=>observer.disconnect();
  },[Boolean(menu),context]);

  function positionQuickTrade() {
    const host = root.current;
    const quote = quickTrade.current;
    if (!host || !quote || !mounted.current) return;
    const quoteBounds = quote.getBoundingClientRect();
    if (!quoteBounds.height) return;
    const origin = host.getBoundingClientRect().top;
    const labels = [...host.querySelectorAll<HTMLElement>('.chart-position-row, .chart-protection-row')]
      .map(element=>element.getBoundingClientRect())
      .filter(bounds=>bounds.height>0 && bounds.right>quoteBounds.left && bounds.left<quoteBounds.right)
      .sort((a,b)=>a.top-b.top);
    let top = 42;
    for (const bounds of labels) {
      if (top+quoteBounds.height > bounds.top-origin-6 && top < bounds.bottom-origin+6) {
        top = bounds.bottom-origin+6;
      }
    }
    // Keep price labels at their real coordinates. Only the quote controls move,
    // using measured height so wrapping on narrow charts is included.
    setQuickTop(previous=>previous===top?previous:top);
  }

  useLayoutEffect(positionQuickTrade);
  useLayoutEffect(() => {
    if (hidden || typeof ResizeObserver==='undefined' || !root.current || !quickTrade.current) return;
    const observer = new ResizeObserver(positionQuickTrade);
    observer.observe(root.current);
    observer.observe(quickTrade.current);
    return ()=>observer.disconnect();
  },[context,hidden]);

  useLayoutEffect(() => {
    // This component owns only these lines, never the chart's analysis levels.
    const owned = lines.current;
    return () => {
      endDrag(false);
      owned.forEach(line => context.series.removePriceLine(line));
      owned.clear();
    };
  },[context]);

  useLayoutEffect(() => {
    endDrag();
    setMenu(null);
    setError('');
  },[contextKey,context,session.id,session.revision]);

  useLayoutEffect(() => {
    if (unavailable) { endDrag(); setMenu(null); }
  },[unavailable]);

  const stop = preview?.field === 'stop_loss' ? preview.price : position.stop_loss;
  const profit = preview?.field === 'take_profit' ? preview.price : position.take_profit;
  useLayoutEffect(() => {
    const desired: Record<LineKey,string | null> = active ? {position:position.average_price,stop_loss:stop,take_profit:profit} : {position:null,stop_loss:null,take_profit:null};
    (Object.keys(desired) as LineKey[]).forEach(key => {
      const price = desired[key];
      const existing = lines.current.get(key);
      if (price == null || !Number.isFinite(Number(price))) {
        if (existing) { context.series.removePriceLine(existing); lines.current.delete(key); }
        return;
      }
      const options = {price:Number(price),color:lineColors[key],lineWidth:1 as const,lineStyle:key==='position'?LineStyle.Solid:LineStyle.Dashed,axisLabelVisible:true,title:key==='position'?'持仓':key==='stop_loss'?'SL':'TP'};
      if (existing) existing.applyOptions(options);
      else lines.current.set(key,context.series.createPriceLine(options));
    });
  },[context,active,position.average_price,stop,profit]);

  useLayoutEffect(() => {
    const openMenu = (event: MouseEvent) => {
      if (!canInteract() || !(event.target instanceof HTMLCanvasElement)) return;
      const rect = context.container.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const size = context.chart.paneSize(0);
      if (x < 0 || y < 0 || x >= size.width || y >= size.height) return;
      const raw = context.series.coordinateToPrice(y);
      const price = raw == null ? null : snapChartPrice(raw,latest.current.session.config.instrument.tick_size);
      if (price == null) return;
      event.preventDefault();
      latest.current.onPause();
      setError('');
      setMenu({price,x,y});
    };
    context.container.addEventListener('contextmenu',openMenu);
    return () => context.container.removeEventListener('contextmenu',openMenu);
  },[context]);

  useLayoutEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!menuElement.current?.contains(event.target as Node)) setMenu(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      endDrag();
      setMenu(null);
      setError('');
    };
    document.addEventListener('pointerdown',outside);
    document.addEventListener('keydown',escape);
    return () => { document.removeEventListener('pointerdown',outside); document.removeEventListener('keydown',escape); };
  },[]);

  function beginDrag(event: ReactPointerEvent<HTMLButtonElement>, field: ProtectionField) {
    if (event.button !== 0 || !canInteract() || !position.quantity) return;
    event.preventDefault();
    event.stopPropagation();
    endDrag();
    setError('');
    setMenu(null);
    onPause();
    drag.current = {field,pointerId:event.pointerId,target:event.currentTarget,startY:event.clientY,moved:false,price:null,expected:{sessionId:session.id,revision:session.revision},contextKey,context};
    setDragging(true);
    context.setInteractionLocked(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    if (!canInteract()) { endDrag(); return; }
    current.moved ||= Math.abs(event.clientY-current.startY) >= 3;
    if (!current.moved) return;
    const y = event.clientY-context.container.getBoundingClientRect().top;
    const raw = y >= 0 && y < context.chart.paneSize(0).height ? context.series.coordinateToPrice(y) : null;
    const price = raw == null ? null : snapChartPrice(raw,latest.current.session.config.instrument.tick_size);
    current.price = price;
    if (price == null) { setPreview(null); setError('请将保护价拖至价格绘图区内'); return; }
    const value = latest.current.session;
    const invalid = protectionError(current.field,price,value.state.position.quantity,value.state.last_price);
    setError('');
    setPreview({field:current.field,price,error:invalid});
  }

  function finishDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    // The pointer may have moved between its last move event and release.
    moveDrag(event);
    const now = latest.current;
    const sameContext = now.contextKey===current.contextKey && now.session.id===current.expected.sessionId && now.session.revision===current.expected.revision;
    const price = current.price;
    const validGesture = current.moved && price != null && sameContext && canInteract();
    endDrag();
    if (!validGesture || price == null) return;
    const problem = protectionError(current.field,price,now.session.state.position.quantity,now.session.state.last_price);
    if (problem) { setError(problem); return; }
    const input = {stop_loss:now.session.state.position.stop_loss,take_profit:now.session.state.position.take_profit,[current.field]:price};
    submittingRef.current = true;
    setSubmitting('protection');
    // Display server props while the command is in flight; a later read failure must
    // never roll back an already-confirmed protection value.
    void now.onProtection(input,current.expected).catch(() => {
      if (mounted.current) setError('保护价提交失败，请重试');
    }).finally(() => {
      submittingRef.current = false;
      if (mounted.current) setSubmitting(null);
    });
  }

  async function closePosition() {
    const now = latest.current;
    if (!canInteract() || !canMarketTrade(now.session) || drag.current || !now.session.state.position.quantity) return;
    submittingRef.current = true;
    setSubmitting('close');
    setError('');
    setMenu(null);
    now.onPause();
    try {
      await now.onClosePosition({sessionId:now.session.id,revision:now.session.revision});
    } catch {
      if (mounted.current) setError('平仓提交失败，请重试');
    } finally {
      submittingRef.current = false;
      if (mounted.current) setSubmitting(null);
    }
  }

  async function marketOrder(side: 'buy' | 'sell') {
    const now = latest.current;
    const quantity = now.quantity;
    if (!canInteract() || !canMarketTrade(now.session) || drag.current || !Number.isInteger(quantity) || quantity < 1 || quantity > 100000 ||
      now.session.state.last_price == null || !Number.isFinite(Number(now.session.state.last_price))) return;
    submittingRef.current = true;
    setSubmitting('order');
    setError('');
    setMenu(null);
    now.onPause();
    try {
      await now.onMarketOrder(side,quantity,{sessionId:now.session.id,revision:now.session.revision});
    } catch {
      if (mounted.current) setError('下单提交失败，请重试');
    } finally {
      submittingRef.current = false;
      if (mounted.current) setSubmitting(null);
    }
  }

  function resetView() {
    context.chart.priceScale('right').applyOptions({autoScale:true});
    context.chart.timeScale().fitContent();
    setMenu(null);
  }

  function coordinate(price: string | null): number | null {
    if (price == null) return null;
    const y = context.series.priceToCoordinate(Number(price));
    return y != null && y >= 0 && y <= pane.height ? y : null;
  }

  function protectionHandle(field: ProtectionField, price: string | null) {
    const name = field==='stop_loss'?'SL':'TP';
    const gross = price == null ? null : (Number(price)-Number(position.average_price))*position.quantity*Number(session.config.instrument.multiplier);
    return <button type="button" className={`chart-protection-handle ${field==='stop_loss'?'is-stop':'is-target'}${preview?.field===field?' is-dragging':''}`} aria-label={`${price==null?'添加':'拖动'} ${name}`} disabled={unavailable}
      onPointerDown={event=>beginDrag(event,field)} onPointerMove={moveDrag} onPointerUp={finishDrag}
      onPointerCancel={()=>endDrag()} onLostPointerCapture={()=>endDrag()}>
      {price==null?`+ ${name}`:<>{name} {price} <span className={gross!>=0?'is-profit':'is-loss'}>预计毛盈亏 {signedUsd(gross!)}</span></>}
    </button>;
  }

  function order(side: 'buy' | 'sell', kind: ReplayOrder['kind'] | null) {
    if (!menu || !kind || !root.current || !canInteract() || (kind === "market" && !canMarketTrade(latest.current.session))) return;
    onOrder({side,kind,price:menu.price,anchor:{x:menu.x,y:menu.y},container:root.current});
    setMenu(null);
  }

  const avgY = active ? coordinate(position.average_price) : null;
  const stopY = active ? coordinate(stop) : null;
  const profitY = active ? coordinate(profit) : null;
  const buyKind = menu ? suggestedOrderKind('buy',menu.price,session.state.last_price) : null;
  const sellKind = menu ? suggestedOrderKind('sell',menu.price,session.state.last_price) : null;
  const message = preview?.error || error;
  const quotePrice = validPrice ? displayPrice(session.state.last_price!,session.config.instrument.tick_size) : '—';
  const menuPrice = menu ? displayPrice(menu.price,session.config.instrument.tick_size) : '';
  const menuWidth = Math.min(328,Math.max(0,pane.width-8));
  const menuHeight = Math.min(measuredMenuHeight,Math.max(0,pane.height-8));
  return <div ref={root} className="replay-chart-overlay" data-testid="replay-chart-overlay" hidden={hidden}>
    {!hidden && <>
      <div ref={quickTrade} className="chart-quick-trade" role="group" aria-label="图上快捷下单" style={{top:quickTop,maxWidth:Math.max(0,pane.width-16)}} onPointerDown={event=>event.stopPropagation()} onContextMenu={event=>{event.preventDefault();event.stopPropagation();}}>
        <div className="chart-quick-quotes">
          <button type="button" className="chart-quick-sell" aria-label="市价卖出 SELL" disabled={unavailable || dragging || !validQuantity || !validPrice || !canMarketTrade(session)} title="按当前回放价市价卖出，成交包含配置的滑点与手续费" onClick={()=>void marketOrder('sell')}><strong>{quotePrice}</strong><span>SELL</span></button>
          <button type="button" className="chart-quick-buy" aria-label="市价买入 BUY" disabled={unavailable || dragging || !validQuantity || !validPrice || !canMarketTrade(session)} title="按当前回放价市价买入，成交包含配置的滑点与手续费" onClick={()=>void marketOrder('buy')}><strong>{quotePrice}</strong><span>BUY</span></button>
        </div>
        <div className="chart-quick-options"><label className="chart-quick-quantity"><span>手数</span><input aria-label="快捷下单手数" type="number" min="1" max="100000" step="1" required value={Number.isFinite(props.quantity)?props.quantity:''} disabled={unavailable || dragging} onChange={event=>props.onQuantityChange(Number(event.target.value))}/></label><small title="历史行情没有独立买卖报价，两侧均使用当前回放价">回放价</small></div>
      </div>
      {avgY!=null && <div className="chart-position-row" style={{top:avgY,maxWidth:Math.max(0,pane.width-(position.take_profit==null || position.stop_loss==null?128:16))}}>
        <span data-testid="chart-position-label" className={`chart-position-label ${Number(session.state.account.unrealized_pnl)>=0?'is-profit':'is-loss'}`}>
          {position.quantity>0?'多':'空'} {Math.abs(position.quantity)} 手 · 均价 {position.average_price} · {signedUsd(session.state.account.unrealized_pnl)}
        </span>
        <button type="button" className="chart-position-close" aria-label="按市价全部平仓" title="按当前回放价全部平仓" disabled={unavailable || dragging || !canMarketTrade(session)}
          onPointerDown={event=>event.stopPropagation()} onClick={event=>{event.stopPropagation();void closePosition();}}>平仓</button>
      </div>}
      {active && (['take_profit','stop_loss'] as const).map(field => {
        const price = field==='stop_loss'?stop:profit;
        const y = price==null?avgY:field==='stop_loss'?stopY:profitY;
        // Keep the captured button mounted when an absent protection becomes a
        // preview line. Moving it to another parent would drop pointer capture.
        return <div key={field} className="chart-protection-row" style={{top:y??0,display:y==null?'none':undefined,left:price==null?Math.max(8,pane.width-(field==='stop_loss'?56:108)):8,maxWidth:Math.max(0,pane.width-16)}}>{protectionHandle(field,price)}</div>;
      })}
      {menu && <div ref={menuElement} role="menu" aria-label="图上下单" className="chart-trading-menu" style={{left:Math.max(4,Math.min(menu.x,pane.width-menuWidth-4)),top:Math.max(4,Math.min(menu.y,pane.height-menuHeight-4)),width:menuWidth,maxHeight:Math.max(0,pane.height-8)}} onContextMenu={event=>event.preventDefault()}>
        <button type="button" role="menuitem" aria-label="Reset 重置视图" onClick={resetView}><MenuIcon kind="reset"/><span>Reset chart view</span></button>
        <div className="chart-menu-separator" role="separator"/>
        <button type="button" role="menuitem" aria-label={`卖出${sellKind==='stop_market'?'止损市价':'限价'} · ${menu.price}`} disabled={!sellKind || unavailable} onClick={()=>order('sell',sellKind)}><MenuIcon kind="sell"/><span>Sell {props.quantity} {session.symbol} @ {menuPrice}<small>{sellKind==='stop_market'?'止损市价':'限价'}</small></span></button>
        <button type="button" role="menuitem" aria-label={`买入${buyKind==='stop_market'?'止损市价':'限价'} · ${menu.price}`} disabled={!buyKind || unavailable} onClick={()=>order('buy',buyKind)}><MenuIcon kind="buy"/><span>Buy {props.quantity} {session.symbol} @ {menuPrice}<small>{buyKind==='stop_market'?'止损市价':'限价'}</small></span></button>
        <div className="chart-menu-separator" role="separator"/>
        <button type="button" role="menuitem" aria-label="市价卖出" disabled={unavailable || !canMarketTrade(session)} onClick={()=>order('sell','market')}><MenuIcon kind="sell"/><span>Sell {props.quantity} {session.symbol}<small>市价卖出</small></span></button>
        <button type="button" role="menuitem" aria-label="市价买入" disabled={unavailable || !canMarketTrade(session)} onClick={()=>order('buy','market')}><MenuIcon kind="buy"/><span>Buy {props.quantity} {session.symbol}<small>市价买入</small></span></button>
        <small>市价按当前回放价立即成交</small>
      </div>}
      {(pending || submitting) && <div className="chart-trading-status" role="status">{pending?'保护价或订单结果待确认，请重试确认':submitting==='close'?'正在平仓…':submitting==='order'?'正在提交市价单…':'正在提交保护价…'}</div>}
      {message && <div className="chart-trading-error" role="alert">{message}</div>}
    </>}
  </div>;
}
