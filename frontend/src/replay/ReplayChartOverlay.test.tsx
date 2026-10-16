import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ChartOverlayContext } from '../chartOverlay';
import type { ReplaySession } from '../types';
import { ReplayChartOverlay } from './ReplayChartOverlay';

function session(): ReplaySession {
  return {id:'one',mode:'training',symbol:'ES',status:'active',revision:7,created_at:'2025-01-01T00:00:00Z',
    config:{instrument:{symbol:'ES',tick_size:'0.25',multiplier:'50'},rules_version:1,start:'2025-01-01T00:00:00Z',end:null},
    state:{cursor:'2025-01-01T00:01:00Z',last_price:'110',instrument_id:1,config:{initial_cash:'100000',fee_per_side:'0',slippage_ticks:0},
      position:{quantity:2,average_price:'100.125',entry_fees:'0',stop_loss:'95',take_profit:'120'},
      account:{cash:'100000',realized_pnl:'0',fees:'0',unrealized_pnl:'987.5',equity:'100987.5',peak_equity:'100987.5',max_drawdown:'0',closed_trades:0,wins:0},orders:[]}};
}

function setup(overrides: Partial<ComponentProps<typeof ReplayChartOverlay>> = {}) {
  const container = document.createElement('div');
  const canvas = document.createElement('canvas');
  container.append(canvas);
  document.body.append(container);
  container.getBoundingClientRect = () => ({left:50,top:100,right:500,bottom:430,width:450,height:330,x:50,y:100,toJSON(){}});
  let priceOffset = 200;
  const listeners = new Set<() => void>();
  const lines = new Set<{options: Record<string, unknown>; applyOptions: ReturnType<typeof vi.fn>}>();
  const series = {
    priceToCoordinate:(price:number) => priceOffset-price,
    coordinateToPrice:(y:number) => priceOffset-y,
    createPriceLine:vi.fn((options:Record<string, unknown>)=>{const line={options,applyOptions:vi.fn((next:Record<string, unknown>)=>Object.assign(options,next))};lines.add(line);return line;}),
    removePriceLine:vi.fn(line=>lines.delete(line)),
  };
  const locked = vi.fn();
  const fitContent=vi.fn(),applyPriceOptions=vi.fn();
  const context = {container,series,chart:{paneSize:()=>({width:400,height:300}),timeScale:()=>({fitContent}),priceScale:()=>({applyOptions:applyPriceOptions})},subscribeViewportChange:(cb:()=>void)=>{listeners.add(cb);return()=>listeners.delete(cb);},setInteractionLocked:locked} as unknown as ChartOverlayContext;
  const props = {context,session:session(),disabled:false,hidden:false,contextKey:'one:7:5m:latest',pending:false,quantity:1,onQuantityChange:vi.fn(),onMarketOrder:vi.fn(async()=>true),onPause:vi.fn(),onOrder:vi.fn(),onProtection:vi.fn(async()=>true),onClosePosition:vi.fn(async()=>true),...overrides};
  const view=render(<ReplayChartOverlay {...props}/>);
  return {...view,props,context,canvas,container,series,lines,locked,listeners,fitContent,applyPriceOptions,
    update:(next:Partial<ComponentProps<typeof ReplayChartOverlay>>)=>{Object.assign(props,next);view.rerender(<ReplayChartOverlay {...props}/>);},
    viewport:(offset:number)=>act(()=>{priceOffset=offset;listeners.forEach(cb=>cb());})};
}

function startDrag(name:string, clientY=200) {
  const handle=screen.getByRole('button',{name});
  Object.assign(handle,{setPointerCapture:vi.fn(),releasePointerCapture:vi.fn(),hasPointerCapture:()=>true});
  fireEvent.pointerDown(handle,{pointerId:1,button:0,clientX:80,clientY});
  return handle;
}
function dragTo(handle:HTMLElement, clientY:number) {
  fireEvent.pointerMove(handle,{pointerId:1,clientX:80,clientY});
  fireEvent.pointerUp(handle,{pointerId:1,clientX:80,clientY});
}

beforeEach(()=>{
  class TestPointerEvent extends MouseEvent {pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??0;}}
  vi.stubGlobal('PointerEvent',TestPointerEvent);
});
afterEach(()=>{cleanup();document.body.replaceChildren();vi.unstubAllGlobals();vi.restoreAllMocks();});

it.each([
  ['ES','0.25','7780.5','7,780.50'],
  ['MGC','0.1','3924.1','3,924.1'],
])('formats %s quotes to instrument precision without changing the submitted price', (symbol,tick,lastPrice,display) => {
  const s=session();s.symbol=symbol;s.config.instrument.tick_size=tick;s.state.last_price=lastPrice;
  const t=setup({session:s});
  expect(screen.getByRole('button',{name:'市价买入 BUY'})).toHaveTextContent(display);
  expect(screen.getByRole('button',{name:'市价卖出 SELL'})).toHaveTextContent(display);
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180});
  const menu=screen.getByRole('menu');
  expect(within(menu).getByRole('menuitem',{name:/买入限价/})).toHaveTextContent(`Buy 1 ${symbol} @ ${symbol==='ES'?'120.00':'120.0'}`);
  fireEvent.click(within(menu).getByRole('menuitem',{name:/买入限价/}));
  expect(t.props.onOrder).toHaveBeenCalledWith(expect.objectContaining({price:'120',side:'buy',kind:'limit'}));
});

function mockOverlayGeometry(height:()=>number) {
  const original=HTMLElement.prototype.getBoundingClientRect;
  const rect=(left:number,top:number,width:number,h:number)=>({left,top,right:left+width,bottom:top+h,width,height:h,x:left,y:top,toJSON(){}});
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement){
    if(this.classList.contains('replay-chart-overlay'))return rect(50,100,450,330);
    if(this.classList.contains('chart-quick-trade'))return rect(58,100+Number.parseFloat(this.style.top||'8'),300,height());
    if(this.classList.contains('chart-position-row')||this.classList.contains('chart-protection-row')){
      if(this.style.display==='none')return rect(0,0,0,0);
      return rect(50+Number.parseFloat(this.style.left||'8'),100+Number.parseFloat(this.style.top)-12,285,24);
    }
    return original.call(this);
  });
}

it('moves quick trading clear of a near-top SL without moving the price label and restores its original spot after pan',()=>{
  mockOverlayGeometry(()=>40);
  const s=session();s.state.position={...s.state.position,quantity:-1,average_price:'40',stop_loss:'163',take_profit:'10'};
  const t=setup({session:s});
  const quote=screen.getByRole('group',{name:'图上快捷下单'});
  const sl=screen.getByRole('button',{name:'拖动 SL'}).parentElement!;
  expect(sl).toHaveStyle({top:'37px'});
  expect(quote.getBoundingClientRect().top).toBeGreaterThan(sl.getBoundingClientRect().bottom);
  expect(quote).toHaveStyle({top:'55px'});
  t.viewport(280);
  expect(sl).toHaveStyle({top:'117px'});
  expect(quote).toHaveStyle({top:'42px'});
  expect(t.props.onProtection).not.toHaveBeenCalled();
});

it('repositions the whole wrapped quote group after resize and clears every intersecting price label',()=>{
  let height=40,notifyResize=()=>{};
  mockOverlayGeometry(()=>height);
  vi.stubGlobal('ResizeObserver',class {constructor(callback:()=>void){notifyResize=callback;}observe(){}disconnect(){}});
  const s=session();s.state.position={...s.state.position,quantity:-1,average_price:'80',stop_loss:'163',take_profit:'10'};
  setup({session:s});
  const quote=screen.getByRole('group',{name:'图上快捷下单'});
  expect(quote).toHaveStyle({top:'55px'});
  act(()=>{height=84;notifyResize();});
  expect(quote).toHaveStyle({top:'208px'});
  for(const label of [screen.getByTestId('chart-position-label'),screen.getByRole('button',{name:'拖动 SL'}),screen.getByRole('button',{name:'拖动 TP'})]){
    const bounds=label.parentElement!.getBoundingClientRect();
    expect(quote.getBoundingClientRect().top).toBeGreaterThan(bounds.bottom);
  }
});

it.each(['buy','sell'] as const)('submits one quick %s at the selected quantity and waits for the server',async side=>{
  let resolve:(value:boolean)=>void=()=>{};
  const market=vi.fn(()=>new Promise<boolean>(r=>{resolve=r;}));
  const t=setup({quantity:3,onMarketOrder:market});
  const buy=screen.getByRole('button',{name:'市价买入 BUY'});
  const sell=screen.getByRole('button',{name:'市价卖出 SELL'});
  expect(buy).toHaveTextContent('110');expect(sell).toHaveTextContent('110');
  expect(screen.getByText('回放价')).toBeInTheDocument();
  const button=side==='buy'?buy:sell;
  act(()=>{fireEvent.click(button);fireEvent.click(button);});
  expect(market).toHaveBeenCalledExactlyOnceWith(side,3,{sessionId:'one',revision:7});
  expect(t.props.onPause).toHaveBeenCalledOnce();
  expect(buy).toBeDisabled();expect(sell).toBeDisabled();
  expect(screen.getByLabelText('快捷下单手数')).toBeDisabled();
  expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
  expect(t.props.session.state.cursor).toBe('2025-01-01T00:01:00Z');
  await act(async()=>resolve(false));
  expect(buy).toBeEnabled();expect(sell).toBeEnabled();
  expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
});

it('shares quick quantity edits and blocks invalid quantities, absent prices, dragging and unavailable trading',()=>{
  const t=setup();const buy=screen.getByRole('button',{name:'市价买入 BUY'});
  fireEvent.change(screen.getByLabelText('快捷下单手数'),{target:{value:'4'}});
  expect(t.props.onQuantityChange).toHaveBeenCalledWith(4);
  for(const quantity of [0,1.5,100001,NaN]){t.update({quantity});expect(buy).toBeDisabled();fireEvent.click(buy);}
  t.update({quantity:100000});expect(buy).toBeEnabled();
  const sl=startDrag('拖动 SL');expect(buy).toBeDisabled();fireEvent.click(buy);
  fireEvent.pointerCancel(sl,{pointerId:1});
  t.update({disabled:true});expect(buy).toBeDisabled();fireEvent.click(buy);
  t.update({disabled:false,pending:true});expect(buy).toBeDisabled();fireEvent.click(buy);
  const empty=session();empty.state.last_price=null;
  t.update({pending:false,session:empty});expect(buy).toBeDisabled();expect(buy).toHaveTextContent('—');fireEvent.click(buy);
  t.update({session:session(),hidden:true});expect(screen.queryByRole('button',{name:'市价买入 BUY'})).not.toBeInTheDocument();
  expect(t.props.onMarketOrder).not.toHaveBeenCalled();
});

it('retains the confirmed position after quick-order failure and leaves pending retry in control',async()=>{
  const t=setup({onMarketOrder:vi.fn().mockRejectedValue(new Error('network'))});
  fireEvent.click(screen.getByRole('button',{name:'市价买入 BUY'}));
  await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('下单提交失败'));
  expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
  t.update({pending:true});
  expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('待确认');
});

it('resets the chart viewport without changing the replay account or placing orders',()=>{
  const t=setup();const before=JSON.stringify(t.props.session);
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:200});
  fireEvent.click(screen.getByRole('menuitem',{name:/Reset/}));
  expect(t.fitContent).toHaveBeenCalledOnce();
  expect(t.applyPriceOptions).toHaveBeenCalledExactlyOnceWith({autoScale:true});
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(JSON.stringify(t.props.session)).toBe(before);
  expect(t.props.onMarketOrder).not.toHaveBeenCalled();expect(t.props.onOrder).not.toHaveBeenCalled();
  expect(t.props.onClosePosition).not.toHaveBeenCalled();expect(t.props.onProtection).not.toHaveBeenCalled();
});

it('opens a price-aware menu only over the plot canvas and hands off an overlay-local ticket',()=>{
  const t=setup();
  expect(fireEvent.contextMenu(t.canvas,{clientX:490,clientY:190})).toBe(true);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(fireEvent.contextMenu(t.canvas,{clientX:100,clientY:200.12})).toBe(false);
  expect(screen.getByRole('menu')).toHaveTextContent('100');
  expect(t.props.onPause).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('menuitem',{name:/买入限价/}));
  expect(t.props.onOrder).toHaveBeenCalledWith({side:'buy',kind:'limit',price:'100',anchor:{x:50,y:100.12},container:screen.getByTestId('replay-chart-overlay')});
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
});

it('supports market entries and closes the menu outside or on Escape',()=>{
  const t=setup();
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180});
  expect(screen.getByRole('menuitem',{name:/买入止损市价/})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('menuitem',{name:'市价卖出'}));
  expect(t.props.onOrder).toHaveBeenCalledWith(expect.objectContaining({side:'sell',kind:'market',price:'120'}));
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180});
  fireEvent.keyDown(document,{key:'Escape'});
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180});
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
});

it('leaves the native menu outside the canvas plot and disables suggested orders without a reference price',()=>{
  const s=session();s.state.last_price=null;
  const t=setup({session:s});
  expect(fireEvent.contextMenu(t.container,{clientX:100,clientY:180})).toBe(true);
  expect(fireEvent.contextMenu(t.canvas,{clientX:100,clientY:410})).toBe(true);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180});
  expect(screen.getByRole('menuitem',{name:/买入限价/})).toBeDisabled();
  expect(screen.getByRole('menuitem',{name:'市价买入'})).not.toBeDisabled();
});

it('uses unsnapped server average and account PnL, and follows viewport and position updates',()=>{
  const t=setup();
  const position=screen.getByTestId('chart-position-label');
  expect(position).toHaveTextContent('多 2 手');
  expect(position).toHaveTextContent('100.125');
  expect(position).toHaveTextContent('+987.50 USD');
  expect(position).toHaveClass('is-profit');
  expect([...t.lines].some(line=>line.options.price===100.125)).toBe(true);
  expect(position.parentElement).toHaveStyle({top:'99.875px'});
  t.viewport(220);
  expect(position.parentElement).toHaveStyle({top:'119.875px'});
  const next=session();next.state.position.quantity=-1;next.state.account.unrealized_pnl='-12.5';
  t.update({session:next});
  expect(position).toHaveTextContent('空 1 手');
  expect(position).toHaveTextContent('-12.50 USD');
  expect(position).toHaveClass('is-loss');
  next.state.position.quantity=0;t.update({session:{...next}});
  expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument();
  expect(t.lines.size).toBe(0);
});

it('pauses and submits chart close only once while preserving server position until confirmation',async()=>{
  let resolve:(value:boolean)=>void=()=>{};
  const close=vi.fn(()=>new Promise<boolean>(r=>{resolve=r;}));
  const t=setup({onClosePosition:close});
  const button=screen.getByRole('button',{name:'按市价全部平仓'});
  fireEvent.click(button);
  fireEvent.click(button);
  expect(t.props.onPause).toHaveBeenCalledOnce();
  expect(close).toHaveBeenCalledExactlyOnceWith({sessionId:'one',revision:7});
  expect(button).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('正在平仓');
  expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
  expect(screen.getByRole('button',{name:'拖动 SL'})).toBeDisabled();
  await act(async()=>resolve(false));
  expect(button).toBeEnabled();
  expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
  const flat=session();flat.revision++;flat.state.position.quantity=0;
  t.update({session:flat});
  expect(screen.queryByRole('button',{name:'按市价全部平仓'})).not.toBeInTheDocument();
  expect(t.lines.size).toBe(0);
});

it('prevents chart close while dragging protection or trading is unavailable',()=>{
  const t=setup();
  const button=screen.getByRole('button',{name:'按市价全部平仓'});
  const sl=startDrag('拖动 SL');
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(t.props.onClosePosition).not.toHaveBeenCalled();
  fireEvent.pointerCancel(sl,{pointerId:1});
  expect(button).toBeEnabled();
  t.update({disabled:true});
  expect(button).toBeDisabled();
  t.update({disabled:false,pending:true});
  expect(button).toBeDisabled();
  t.update({pending:false,hidden:true});
  expect(screen.queryByRole('button',{name:'按市价全部平仓'})).not.toBeInTheDocument();
  expect(t.props.onClosePosition).not.toHaveBeenCalled();
});

it('drags a long stop to profitable territory using viewport coordinates and keeps take profit',async()=>{
  const t=setup();
  const handle=startDrag('拖动 SL');
  expect(t.props.onPause).toHaveBeenCalledOnce();
  expect(t.locked).toHaveBeenLastCalledWith(true);
  fireEvent.pointerMove(handle,{pointerId:1,clientX:80,clientY:195.13});
  expect(handle).toHaveTextContent('104.75');
  expect(handle).toHaveTextContent('+462.50 USD');
  fireEvent.pointerUp(handle,{pointerId:1,clientX:80,clientY:195.13});
  await waitFor(()=>expect(t.props.onProtection).toHaveBeenCalledOnce());
  expect(t.props.onProtection).toHaveBeenCalledWith({stop_loss:'104.75',take_profit:'120'},{sessionId:'one',revision:7});
  expect(t.locked).toHaveBeenLastCalledWith(false);
  expect(handle).toHaveTextContent('95');
});

it('creates absent short take profit at a 0.1 tick and preserves stop loss',async()=>{
  const s=session();s.config.instrument.tick_size='0.1';s.config.instrument.multiplier='10';s.state.position.quantity=-2;s.state.position.take_profit=null;s.state.position.stop_loss='115';
  const t=setup({session:s});
  const handle=startDrag('添加 TP');dragTo(handle,195.26);
  await waitFor(()=>expect(t.props.onProtection).toHaveBeenCalledWith({stop_loss:'115',take_profit:'104.7'},{sessionId:'one',revision:7}));
});

it('rejects invalid protection and never submits a click without movement',()=>{
  const t=setup();
  let handle=startDrag('拖动 SL');dragTo(handle,180);
  expect(screen.getByRole('alert')).toHaveTextContent('低于现价');
  expect(t.props.onProtection).not.toHaveBeenCalled();
  handle=startDrag('拖动 SL');fireEvent.pointerUp(handle,{pointerId:1,clientY:200});
  expect(t.props.onProtection).not.toHaveBeenCalled();
});

it('cancels a drag when the server revision changes even if the caller context key stays stable',()=>{
  const t=setup();const handle=startDrag('拖动 SL');
  fireEvent.pointerMove(handle,{pointerId:1,clientY:195});
  const next=session();next.revision=8;
  t.update({session:next});
  fireEvent.pointerUp(handle,{pointerId:1,clientY:195});
  expect(t.props.onProtection).not.toHaveBeenCalled();
  expect(t.locked).toHaveBeenLastCalledWith(false);
});

it('cancels instead of submitting when the pointer leaves the plot',()=>{
  const t=setup();const handle=startDrag('拖动 SL');dragTo(handle,450);
  expect(t.props.onProtection).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('绘图区内');
  expect(t.locked).toHaveBeenLastCalledWith(false);
});

it('retains the portal host while disabled so an open order form remains visible',()=>{
  const t=setup();const host=screen.getByTestId('replay-chart-overlay');
  t.update({disabled:true});
  expect(screen.getByTestId('replay-chart-overlay')).toBe(host);
  expect(host).toBeVisible();
});

it.each(['escape','pointercancel','lostpointercapture','disabled','context','unmount'])('cancels %s without submitting and restores gestures',reason=>{
  const t=setup();const handle=startDrag('拖动 SL');
  fireEvent.pointerMove(handle,{pointerId:1,clientY:195});
  if(reason==='escape')fireEvent.keyDown(document,{key:'Escape'});
  if(reason==='pointercancel')fireEvent.pointerCancel(handle,{pointerId:1});
  if(reason==='lostpointercapture')fireEvent.lostPointerCapture(handle,{pointerId:1});
  if(reason==='disabled')t.update({disabled:true});
  if(reason==='context')t.update({contextKey:'one:8:1m:latest'});
  if(reason==='unmount')t.unmount();
  fireEvent.pointerUp(handle,{pointerId:1,clientY:195});
  expect(t.props.onProtection).not.toHaveBeenCalled();
  expect(t.locked).toHaveBeenLastCalledWith(false);
  if(reason==='unmount'){expect(t.listeners.size).toBe(0);expect(t.lines.size).toBe(0);}
});

it('blocks interactions when disabled or hidden and removes historical position lines',()=>{
  const t=setup({disabled:true});
  fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180});
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'拖动 SL'})).toBeDisabled();
  t.update({disabled:false,hidden:true});
  expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument();
  expect(t.lines.size).toBe(0);
  expect(fireEvent.contextMenu(t.canvas,{clientX:100,clientY:180})).toBe(true);
});

it('shows submission and unknown states, preserving confirmed server prices even when the follow-up fails',async()=>{
  let resolve:(value:boolean)=>void=()=>{};
  const t=setup({onProtection:vi.fn(()=>new Promise<boolean>(r=>{resolve=r;}))});
  const handle=startDrag('拖动 SL');dragTo(handle,195);
  expect(screen.getByRole('status')).toHaveTextContent('正在提交保护价');
  const next=session();next.revision=8;next.state.position.stop_loss='105';
  t.update({session:next,contextKey:'one:8:5m:latest'});
  await act(async()=>resolve(false));
  expect(screen.getByRole('button',{name:'拖动 SL'})).toHaveTextContent('105');
  t.update({pending:true});
  expect(screen.getByRole('status')).toHaveTextContent('待确认');
  expect(screen.getByRole('button',{name:'拖动 SL'})).toBeDisabled();
});
