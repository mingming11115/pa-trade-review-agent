import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChartReplayTrading } from './ChartReplayTrading';
import { useReplayTradingController } from './useReplayTradingController';
// Drawing hit-testing/rendering is covered by its own tests and browser acceptance.
vi.mock('../chartWorkspace/ChartDrawingTools',()=>({ChartDrawingTools:()=>null}));
// jsdom has no canvas; retain the original chart's controls and isolate its renderer.
vi.mock('lightweight-charts',()=>({
 CandlestickSeries:'candles',HistogramSeries:'volume',LineSeries:'ema',ColorType:{Solid:'solid'},CrosshairMode:{Normal:0},LineStyle:{Solid:0,Dashed:2},
 createSeriesMarkers:()=>({setMarkers(){}}),
 createChart:(container:HTMLElement)=>{container.appendChild(document.createElement('canvas'));const pane={setStretchFactor(){},paneIndex:()=>0,getHTMLElement:()=>null,getHeight:()=>430};return {panes:()=>[pane],subscribeCrosshairMove(){},unsubscribeCrosshairMove(){},addSeries:()=>({getPane:()=>pane,setData(){},update(){},applyOptions(){},attachPrimitive(){},detachPrimitive(){},priceToCoordinate:(p:number)=>200-(p-100)*10,coordinateToPrice:(y:number)=>100+(200-y)/10,createPriceLine(){return {applyOptions(){}};},removePriceLine(){}}),priceScale:()=>({applyOptions(){}}),paneSize:()=>({width:640,height:430}),options:()=>({handleScroll:{pressedMouseMove:true,mouseWheel:true,horzTouchDrag:true,vertTouchDrag:false},handleScale:{axisPressedMouseMove:true,mouseWheel:true,pinch:true,axisDoubleClickReset:true}}),applyOptions(){},remove(){container.replaceChildren();},timeScale:()=>({fitContent(){},setVisibleLogicalRange(){},getVisibleLogicalRange:()=>null,subscribeVisibleLogicalRangeChange(){},unsubscribeVisibleLogicalRangeChange(){}})};}
}));
beforeEach(()=>{
 const values=new Map<string,string>();
 vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value)});
 vi.stubGlobal('ResizeObserver',class {observe(){}unobserve(){}disconnect(){}});
 vi.stubGlobal('PointerEvent',class extends MouseEvent {pointerId:number;constructor(type:string,init:PointerEventInit={}){super(type,init);this.pointerId=init.pointerId??1;}});
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers();});
const session={id:'one',symbol:'ES',mode:'training',status:'active',revision:0,config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{cursor:'2025-01-01T00:00:00Z',last_price:'100',config:{initial_cash:'100000',fee_per_side:'0',slippage_ticks:0},orders:[],position:{quantity:0,average_price:'0',stop_loss:null,take_profit:null},account:{equity:'100000',unrealized_pnl:'0',fees:'0',closed_trades:0,wins:0}}};
function Harness(){const controller=useReplayTradingController();return <ChartReplayTrading controller={controller} symbol="ES" bars={[]} chartKey="main" period="5m"/>;}
function replayChoice(name:string){if(!screen.queryByRole('menu',{name:'回放起点方式'}))fireEvent.click(screen.getByRole('button',{name:'选择起点'}));return screen.getByRole('menuitem',{name});}
function replayDateInput(){if(!screen.queryByLabelText('回放开始时间'))fireEvent.click(replayChoice('选择日期…'));return screen.getByLabelText('回放开始时间');}
function start(){fireEvent.click(screen.getByRole('button',{name:/^回放$/}));fireEvent.change(replayDateInput(),{target:{value:'2025-01-01T00:00'}});fireEvent.click(screen.getByRole('button',{name:'开始回放'}));}
function openDetails(tab?:string){fireEvent.click(screen.getByRole('button',{name:'交易明细'}));if(tab)fireEvent.click(screen.getByRole('tab',{name:tab}));}

it('keeps start/cancel together in an action group and cancels setup',()=>{
  render(<Harness/>);
  fireEvent.click(screen.getByRole('button',{name:/^回放$/}));
  fireEvent.change(replayDateInput(),{target:{value:'2025-01-01T00:00'}});
  const panel=screen.getByRole('group',{name:'K 线回放'});
  const actions=within(panel).getByRole('group',{name:'回放启动操作'});
  expect(within(actions).getByRole('button',{name:'开始回放'})).toBeEnabled();
  fireEvent.click(within(actions).getByRole('button',{name:'取消'}));
  expect(screen.queryByRole('group',{name:'K 线回放'})).not.toBeInTheDocument();
});

it.each(['', '0', '-1'])('blocks both replay starts for invalid required capital %s', cash=>{
 render(<Harness/>);
 fireEvent.click(screen.getByRole('button',{name:/^回放$/}));
 const input=screen.getByLabelText('初始资金 USD');
 expect(input).toBeRequired();
 fireEvent.change(input,{target:{value:cash}});
 expect(screen.getByRole('button',{name:'开始回放'})).toBeDisabled();
 expect(replayChoice('随机 K 线')).toBeDisabled();
 expect(screen.getByText('请输入大于 0 的初始资金。')).toBeVisible();
});

it('starts with positive fractional capital and no optional targets',async()=>{
 const requests:Array<Record<string,unknown>>=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(init?.method==='POST')requests.push(JSON.parse(String(init.body)));
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 render(<Harness/>);
 fireEvent.click(screen.getByRole('button',{name:/^回放$/}));
 fireEvent.change(screen.getByLabelText('初始资金 USD'),{target:{value:'0.50'}});
 fireEvent.change(replayDateInput(),{target:{value:'2025-01-01T00:00'}});
 fireEvent.click(screen.getByRole('button',{name:'开始回放'}));
 await screen.findByRole('button',{name:'市价买入 BUY'});
 expect(requests[0].config).toMatchObject({initial_cash:'0.50',profit_target:null,loss_limit:null});
});

it('sends optional replay profit and loss limits with the training session config',async()=>{
 const requests:Array<{url:string;body:Record<string,unknown>}>=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(init?.method==='POST')requests.push({url,body:JSON.parse(String(init.body))});
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 render(<Harness/>);
 fireEvent.click(screen.getByRole('button',{name:/^回放$/}));
 fireEvent.click(screen.getByText('选填设置'));
 fireEvent.change(screen.getByLabelText('盈利目标 USD'),{target:{value:'500'}});
 fireEvent.change(screen.getByLabelText('亏损限制 USD'),{target:{value:'250'}});
 fireEvent.change(replayDateInput(),{target:{value:'2025-01-01T00:00'}});
 fireEvent.click(screen.getByRole('button',{name:'开始回放'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 expect(requests[0].body.config).toMatchObject({profit_target:'500',loss_limit:'250'});
});

it('shows latched replay limit alerts without disabling trading',async()=>{
 const alerted={...session,state:{...session.state,config:{...session.state.config,profit_target:'500'},account:{...session.state.account,profit_target_reached:true}}};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:alerted)));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 expect(screen.getByRole('status')).toHaveTextContent(/盈利目标 500 USD/);
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled();
});

it('shows one details tab at a time without recreating the chart when switching or collapsing',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session)));
 const {container}=render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 expect(screen.getByRole('button',{name:'市价卖出 SELL'})).toBeEnabled();
 const canvas=container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas');
 expect(screen.getByRole('button',{name:'交易明细'})).toHaveAttribute('aria-expanded','false');
 expect(screen.queryByRole('heading',{name:'持仓'})).not.toBeInTheDocument();
 expect(screen.queryByRole('table')).not.toBeInTheDocument();
 const controls=screen.getByRole('group',{name:'回放控制'});
 expect(within(controls).getByRole('button',{name:'播放'})).toBeEnabled();
 expect(within(controls).getByRole('button',{name:'退出回放'})).toBeEnabled();
 openDetails();
 expect(screen.getByRole('tab',{name:'成交记录'})).toHaveAttribute('aria-selected','true');
 for(const name of ['成交记录','持仓','订单']){
  fireEvent.click(screen.getByRole('tab',{name}));
  expect(screen.getByRole('tab',{name})).toHaveAttribute('aria-selected','true');
  expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
  expect(screen.getAllByRole('table')).toHaveLength(1);
  expect(screen.getByRole('table',{name})).toBeVisible();
  expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(canvas);
 }
 fireEvent.click(screen.getByRole('tab',{name:'统计'}));
 expect(screen.getByRole('tab',{name:'统计'})).toHaveAttribute('aria-selected','true');
 expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
 expect(screen.queryByRole('table')).not.toBeInTheDocument();
 expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(canvas);
 fireEvent.click(screen.getByRole('button',{name:'交易明细'}));
 expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument();
 expect(screen.queryByRole('table')).not.toBeInTheDocument();
 expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(canvas);
 expect(screen.queryByRole('heading',{name:'下单'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('下单手数')).not.toBeInTheDocument();
});

it('shows fills through the observed cursor and restores later fills at the current progress',async()=>{
 const fills=[
  {order_id:'before',side:'buy',quantity:1,price:'100',fee:'0',timestamp:'2025-01-01T00:01:00Z',reason:'entry',ambiguous:false},
  {order_id:'boundary',side:'buy',quantity:1,price:'101',fee:'0',timestamp:'2025-01-01T00:05:00Z',reason:'entry',ambiguous:false},
  {order_id:'after',side:'buy',quantity:1,price:'102',fee:'0',timestamp:'2025-01-01T00:10:00Z',reason:'entry',ambiguous:false},
 ];
 const opened={...session,revision:3,state:{...session.state,cursor:'2025-01-01T00:10:00Z',last_price:'102',position:{...session.state.position,quantity:3,average_price:'101'},orders:fills.map(fill=>({id:fill.order_id,side:'buy',kind:'market',quantity:1,limit_price:null,stop_price:null,status:'filled',created_at:fill.timestamp}))}};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:fills.map((fill,index)=>({revision:index+1,type:'place_order',cursor:fill.timestamp,equity:'100000',fills:[fill]})),next_offset:null}:opened)));
 let controller:ReturnType<typeof useReplayTradingController>;
 function ObservationHarness(){controller=useReplayTradingController();return <ChartReplayTrading controller={controller} symbol="ES" bars={[]} chartKey="main" period="5m"/>;}
 render(<ObservationHarness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 openDetails();
 expect(within(screen.getByRole('table',{name:'成交记录'})).getAllByRole('row')).toHaveLength(4);
 await act(async()=>{await controller.replay.observe('2025-01-01T00:05:00Z');});
 const historicalFills=within(screen.getByRole('table',{name:'成交记录'}));
 expect(historicalFills.getAllByRole('row')).toHaveLength(3);
 expect(historicalFills.getByRole('cell',{name:'100.00'})).toBeInTheDocument();
 expect(historicalFills.getByRole('cell',{name:'101.00'})).toBeInTheDocument();
 expect(historicalFills.queryByRole('cell',{name:'102.00'})).not.toBeInTheDocument();
 expect(screen.getByText(/正在回看，持仓与资金为最新回放进度/)).toBeVisible();
 await act(async()=>{await controller.replay.observe();});
 const currentFills=within(screen.getByRole('table',{name:'成交记录'}));
 expect(currentFills.getAllByRole('row')).toHaveLength(4);
 expect(currentFills.getByRole('cell',{name:'102.00'})).toBeInTheDocument();
 expect(screen.queryByText(/正在回看，持仓与资金为最新回放进度/)).not.toBeInTheDocument();
});

it.each(['exit dialog','failed read'] as const)('disables every details trading action during an %s',async guard=>{
 let failing=guard==='failed read';
 const opened={...session,state:{...session.state,position:{quantity:2,average_price:'100',stop_loss:'95',take_profit:'110'},orders:[{id:'pending',side:'sell',kind:'limit',quantity:2,limit_price:'105',stop_price:null,status:'pending',created_at:session.state.cursor}]}};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars')&&failing)throw new TypeError('history unavailable');
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:opened);
 }));
 render(<Harness/>);start();
 if(failing)await screen.findByRole('button',{name:'重试加载'});
 else await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 openDetails('持仓');
 fireEvent.click(screen.getByRole('button',{name:'修改保护价'}));
 const controls=[screen.getByLabelText('持仓止损'),screen.getByLabelText('持仓止盈'),screen.getByRole('button',{name:'更新保护价'}),screen.getByRole('button',{name:'提交全部平仓'})];
 fireEvent.click(screen.getByRole('tab',{name:'订单'}));
 controls.push(screen.getByRole('button',{name:'改单'}),screen.getByRole('button',{name:'撤单'}));
 if(guard==='exit dialog')fireEvent.click(screen.getByRole('button',{name:'退出回放'}));
 for(const control of controls)expect(control).toBeDisabled();
 if(guard==='exit dialog')fireEvent.click(screen.getByRole('button',{name:'继续回放'}));
 else {failing=false;fireEvent.click(screen.getByRole('button',{name:'重试加载'}));}
 await waitFor(()=>{for(const control of controls)expect(control).toBeEnabled();});
});

it.each(['close','cancel','escape'] as const)('dismisses an amendment with %s and clears editing before a new chart order',async dismiss=>{
 const order={id:'pending',side:'sell',kind:'limit',quantity:4,limit_price:'105',stop_price:null,status:'pending',created_at:session.state.cursor};
 const opened={...session,state:{...session.state,orders:[order]}};
 const commands:Array<Record<string,unknown>>=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands'))commands.push(JSON.parse(String(init?.body)));
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:opened);
 }));
 const {container}=render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 fireEvent.change(screen.getByLabelText('快捷下单手数'),{target:{value:'2'}});
 fireEvent.click(screen.getByRole('button',{name:'播放'}));
 openDetails('订单');
 const scrollToChart=vi.fn();
 Object.assign(container.querySelector('.replay-chart-frame')!,{scrollIntoView:scrollToChart});
 fireEvent.click(screen.getByRole('button',{name:'改单'}));
 expect(scrollToChart).toHaveBeenCalledWith({block:'start',behavior:'smooth'});
 const popup=screen.getByRole('dialog',{name:'图表下单'});
 expect(container.querySelector('.replay-chart-frame')).toContainElement(popup);
 expect(screen.getByRole('button',{name:'播放'})).toBeInTheDocument();
 expect(within(popup).getByRole('heading',{name:'修改挂单'})).toBeInTheDocument();
 expect(screen.getByLabelText('下单手数')).toHaveValue(4);
 fireEvent.change(screen.getByLabelText('下单手数'),{target:{value:'5'}});
 expect(screen.getByLabelText('快捷下单手数')).toHaveValue(2);
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 if(dismiss==='close')fireEvent.click(screen.getByRole('button',{name:'关闭图表下单'}));
 else if(dismiss==='cancel')fireEvent.click(screen.getByRole('button',{name:'取消修改'}));
 else fireEvent.keyDown(popup,{key:'Escape'});
 expect(screen.queryByRole('dialog',{name:'图表下单'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText('下单手数')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled();
 fireEvent.contextMenu(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')!,{clientX:100,clientY:205});
 fireEvent.click(await screen.findByRole('menuitem',{name:/买入.*限价/}));
 expect(screen.getByLabelText('下单手数')).toHaveValue(2);
 expect(screen.getByRole('heading',{name:'下单'})).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'买入改单'})).not.toBeInTheDocument();
 expect(commands).toHaveLength(0);
});

it('amends a pending order once through a temporary chart ticket and removes the ticket after confirmation',async()=>{
 const order={id:'pending',side:'sell',kind:'limit',quantity:4,limit_price:'105',stop_price:null,status:'pending',created_at:session.state.cursor};
 let current={...session,state:{...session.state,orders:[order]}};
 const commands:Array<Record<string,unknown>>=[];
 let finish:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   commands.push(JSON.parse(String(init?.body)));
   return new Promise<Response>(resolve=>{finish=resolve;});
  }
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 openDetails('订单');
 await waitFor(()=>expect(screen.getByRole('button',{name:'改单'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'改单'}));
 expect(screen.getByRole('dialog',{name:'图表下单'})).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('限价'),{target:{value:'106'}});
 const amend=screen.getByRole('button',{name:'卖出改单'});
 fireEvent.click(amend);fireEvent.click(amend);
 expect(commands).toHaveLength(1);
 expect(commands[0]).toMatchObject({type:'amend_order',order_id:'pending',side:'sell',kind:'limit',quantity:4,limit_price:'106',expected_revision:0});
 expect(amend).toBeDisabled();
 current={...current,revision:1,state:{...current.state,orders:[{...order,limit_price:'106'}]}};
 await act(async()=>{finish(Response.json(current));});
 await waitFor(()=>expect(screen.queryByRole('dialog',{name:'图表下单'})).not.toBeInTheDocument());
 expect(screen.queryByLabelText('下单手数')).not.toBeInTheDocument();
 expect(within(screen.getByRole('table',{name:'订单'})).getByRole('cell',{name:'106.00'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled();
});

it.each(['buy','sell'] as const)('submits a quick %s once with the shared quantity without advancing',async side=>{
 const commands:Array<Record<string,unknown>>=[];
 let current:Record<string,unknown>=session;
 let finish:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   commands.push(JSON.parse(String(init?.body)));
   return new Promise<Response>(resolve=>{finish=resolve;});
  }
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 const {container}=render(<Harness/>);start();
 const name=side==='buy'?'市价买入 BUY':'市价卖出 SELL';
 await waitFor(()=>expect(screen.getByRole('button',{name})).toBeEnabled());
 const quick=screen.getByRole('button',{name});
 expect(container.querySelector('.replay-chart-frame')).toContainElement(quick);
 fireEvent.change(screen.getByLabelText('快捷下单手数'),{target:{value:'3'}});
 const cursor=screen.getByText(/北京时间 2025/).textContent;
 fireEvent.click(quick);fireEvent.click(quick);
 expect(commands).toHaveLength(1);
 expect(commands[0]).toMatchObject({type:'place_order',kind:'market',side,quantity:3,expected_revision:0});
 expect(commands[0].reduce_only).not.toBe(true);
 expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument();
 expect(quick).toBeDisabled();
 current={...session,revision:1,state:{...session.state,position:{...session.state.position,quantity:side==='buy'?3:-3,average_price:'100'}}};
 await act(async()=>{finish(Response.json(current));});
 expect(await screen.findByTestId('chart-position-label')).toHaveTextContent(`${side==='buy'?'多':'空'} 3 手`);
 expect(screen.getByText(/北京时间 2025/).textContent).toBe(cursor);
 expect(commands).toHaveLength(1);
});

it('retries an unknown quick order with the original operation and keeps new orders locked',async()=>{
 const commands:Array<Record<string,unknown>>=[];
 let current:Record<string,unknown>=session;
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   commands.push(JSON.parse(String(init?.body)));
   if(commands.length===1)throw new TypeError('network lost');
   current={...session,revision:1,state:{...session.state,position:{...session.state.position,quantity:1,average_price:'100'}}};
   return Response.json(current);
  }
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'市价买入 BUY'}));
 await screen.findByRole('button',{name:'重试确认提交结果'});
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'市价卖出 SELL'})).toBeDisabled();
 expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'重试确认提交结果'}));
 expect(await screen.findByTestId('chart-position-label')).toHaveTextContent('多 1 手');
 expect(commands).toHaveLength(2);
 expect(commands[1]).toEqual(commands[0]);
 expect(commands[0].operation_id).toEqual(expect.any(String));
});

it('resets the chart view without issuing a trading command or changing the cursor',async()=>{
 const commands:Array<Record<string,unknown>>=[];
 const opened={...session,state:{...session.state,position:{...session.state.position,quantity:2,average_price:'100'}}};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands'))commands.push(JSON.parse(String(init?.body)));
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:opened);
 }));
 const {container}=render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 const cursor=screen.getByText(/北京时间 2025/).textContent;
 fireEvent.contextMenu(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')!,{clientX:100,clientY:205});
 fireEvent.click(await screen.findByRole('menuitem',{name:/Reset/}));
 expect(screen.queryByRole('menu')).not.toBeInTheDocument();
 expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
 expect(screen.getByText(/北京时间 2025/).textContent).toBe(cursor);
 expect(commands).toHaveLength(0);
});

it.each(['buy','sell'] as const)('shows an immediate %s position and fill without advancing the replay',async side=>{
 const commands:Array<Record<string,unknown>>=[];
 let current:Record<string,unknown>=session;
 const fill={order_id:'instant',side,quantity:1,price:'100',fee:'0',timestamp:session.state.cursor,reason:'entry',ambiguous:false};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   const input=JSON.parse(String(init?.body));commands.push(input);
   current={...session,revision:1,state:{...session.state,position:{...session.state.position,quantity:side==='buy'?1:-1,average_price:'100'},orders:[{...input,id:'instant',created_at:session.state.cursor,status:'filled'}]}};
   return Response.json(current);
  }
  return Response.json(url.includes('/bars')?{bars:[{timestamp:'2024-12-31T23:55:00Z',open:100,high:101,low:99,close:100,volume:10}]}:url.includes('/events')?{items:commands.length?[{revision:1,type:'place_order',cursor:session.state.cursor,equity:'100000',fills:[fill]}]:[],next_offset:null}:current);
 }));
 render(<Harness/>);start();
 const buttonName=side==='buy'?'市价买入 BUY':'市价卖出 SELL';
 await waitFor(()=>expect(screen.getByRole('button',{name:buttonName})).toBeEnabled());
 const cursorText=screen.getByText(/北京时间 2025/).textContent;
 fireEvent.click(screen.getByRole('button',{name:buttonName}));
 expect(await screen.findByTestId('chart-position-label')).toHaveTextContent(`${side==='buy'?'多':'空'} 1 手 · 均价 100 · 0.00 USD`);
 openDetails();
 await waitFor(()=>expect(within(screen.getByRole('table',{name:'成交记录'})).getByRole('cell',{name:'100.00'})).toBeInTheDocument());
 fireEvent.click(screen.getByRole('tab',{name:'订单'}));
 expect(within(screen.getByRole('table',{name:'订单'})).getByRole('cell',{name:'已成交'})).toBeInTheDocument();
 expect(screen.getByText(/北京时间 2025/).textContent).toBe(cursorText);
 expect(commands).toHaveLength(1);
 expect(commands[0]).toMatchObject({type:'place_order',kind:'market',side,quantity:1});
 expect(screen.getByRole('button',{name:'添加 TP'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'添加 SL'})).toBeEnabled();
});

it('retains an immediate confirmed entry when reading the updated ledger fails',async()=>{
 let posted=false;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/commands')){
   posted=true;
   return Response.json({...session,revision:1,state:{...session.state,position:{...session.state.position,quantity:1,average_price:'100'},orders:[{id:'instant',side:'buy',kind:'market',quantity:1,status:'filled',created_at:session.state.cursor}]}});
  }
  if(url.includes('/events')&&posted)throw new TypeError('ledger unavailable');
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'市价买入 BUY'}));
 await screen.findByRole('button',{name:'重试加载'});
 expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 1 手 · 均价 100');
 expect(screen.getByRole('button',{name:'交易明细'})).toHaveAttribute('aria-expanded','false');
 openDetails('订单');
 expect(within(screen.getByRole('table',{name:'订单'})).getByRole('cell',{name:'已成交'})).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 expect(screen.queryByRole('button',{name:'重试确认提交结果'})).not.toBeInTheDocument();
});

it.each([2,-3])('closes the full chart position %s once at the current cursor',async quantity=>{
 const commands:Array<Record<string,unknown>>=[];
 const opened={...session,state:{...session.state,position:{quantity,average_price:'100',stop_loss:quantity>0?'95':'105',take_profit:quantity>0?'110':'90'}}};
 let current:Record<string,unknown>=opened;
 let resolveCommand:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   const input=JSON.parse(String(init?.body));commands.push(input);
   return new Promise<Response>(resolve=>{resolveCommand=resolve;});
  }
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 const {container}=render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'按市价全部平仓'})).toBeEnabled());
 const close=screen.getByRole('button',{name:'按市价全部平仓'});
 expect(container.querySelector('.replay-chart-frame')).toContainElement(close);
 const cursorText=screen.getByText(/北京时间 2025/).textContent;
 fireEvent.click(close);fireEvent.click(close);
 expect(commands).toHaveLength(1);
 expect(commands[0]).toMatchObject({type:'place_order',kind:'market',side:quantity>0?'sell':'buy',quantity:Math.abs(quantity),reduce_only:true,expected_revision:0});
 expect(close).toBeDisabled();
 expect(screen.getByTestId('chart-position-label')).toBeInTheDocument();
 current={...session,revision:1};
 await act(async()=>{resolveCommand(Response.json(current));});
 await waitFor(()=>expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument());
 expect(screen.queryByRole('button',{name:'按市价全部平仓'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'拖动 TP'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'拖动 SL'})).not.toBeInTheDocument();
 expect(screen.getByText('空仓')).toBeInTheDocument();
 expect(screen.getByText(/北京时间 2025/).textContent).toBe(cursorText);
 expect(commands).toHaveLength(1);
});

it('keeps an unconfirmed chart close visible and retries the original operation',async()=>{
 const commands:Array<Record<string,unknown>>=[];
 let current:Record<string,unknown>={...session,state:{...session.state,position:{quantity:2,average_price:'100',stop_loss:'95',take_profit:'110'}}};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   commands.push(JSON.parse(String(init?.body)));
   if(commands.length===1)throw new TypeError('network lost');
   current={...session,revision:1};return Response.json(current);
  }
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'按市价全部平仓'})).toBeEnabled());
 const cursorText=screen.getByText(/北京时间 2025/).textContent;
 fireEvent.click(screen.getByRole('button',{name:'按市价全部平仓'}));
 await screen.findByRole('button',{name:'重试确认提交结果'});
 expect(screen.getByTestId('chart-position-label')).toHaveTextContent('多 2 手');
 expect(screen.getByRole('button',{name:'按市价全部平仓'})).toBeDisabled();
 fireEvent.click(screen.getByRole('button',{name:'按市价全部平仓'}));
 expect(commands).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'重试确认提交结果'}));
 await waitFor(()=>expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument());
 expect(screen.getByText('空仓')).toBeInTheDocument();
 expect(commands).toHaveLength(2);
 expect(commands[0].operation_id).toEqual(expect.any(String));
 expect(commands[1]).toEqual(commands[0]);
 expect(screen.getByText(/北京时间 2025/).textContent).toBe(cursorText);
});

it('keeps a confirmed chart close flat when the subsequent ledger read fails',async()=>{
 const commands:Array<Record<string,unknown>>=[];
 let current:Record<string,unknown>={...session,state:{...session.state,position:{quantity:-2,average_price:'100',stop_loss:'105',take_profit:'90'}}};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   commands.push(JSON.parse(String(init?.body)));
   current={...session,revision:1};return Response.json(current);
  }
  if(url.includes('/events')&&commands.length)throw new TypeError('ledger unavailable');
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'按市价全部平仓'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'按市价全部平仓'}));
 await screen.findByRole('button',{name:'重试加载'});
 expect(screen.queryByTestId('chart-position-label')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'按市价全部平仓'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'拖动 TP'})).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'拖动 SL'})).not.toBeInTheDocument();
 expect(screen.getByText('空仓')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 expect(screen.queryByRole('button',{name:'重试确认提交结果'})).not.toBeInTheDocument();
 expect(commands).toHaveLength(1);
});

it('keeps the chart visible without flashing a loading notice during a quick next candle',async()=>{
 vi.useFakeTimers();
 let advanced=false;
 const pendingBars:Array<(response:Response)=>void>=[];
 const bar={timestamp:'2024-12-31T23:55:00Z',open:100,high:101,low:99,close:100,volume:10};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/commands')){advanced=true;return Response.json({...session,revision:1,state:{...session.state,cursor:'2025-01-01T00:05:00Z'}});}
  if(url.includes('/bars')&&advanced)return new Promise<Response>(resolve=>{pendingBars.push(resolve);});
  return Response.json(url.includes('/bars')?{bars:[bar]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 const {container}=render(<Harness/>);start();
 await act(async()=>{});
 const chart=container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas');
 expect(screen.getByRole('button',{name:'下一根'})).toBeEnabled();
 fireEvent.click(screen.getByRole('button',{name:'下一根'}));
 await act(async()=>{});
 await act(async()=>{await vi.advanceTimersByTimeAsync(500);});
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'下一根'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(chart);
 await act(async()=>{pendingBars.forEach(resolve=>resolve(Response.json({bars:[bar,{...bar,timestamp:'2025-01-01T00:00:00Z',close:101}]})));});
 expect(screen.getByRole('button',{name:'下一根'})).toBeEnabled();
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
 expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(chart);
});

it('shows a cancellable in-chart notice for a slow refresh while retaining the chart',async()=>{
 vi.useFakeTimers();
 let advanced=false;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/commands')){advanced=true;return Response.json({...session,revision:1});}
  if(url.includes('/bars')&&advanced)return new Promise<Response>(()=>{});
  return Response.json(url.includes('/bars')?{bars:[{timestamp:'2024-12-31T23:55:00Z',open:100,high:101,low:99,close:100,volume:10}]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 const {container}=render(<Harness/>);start();
 await act(async()=>{});
 const chart=container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas');
 fireEvent.click(screen.getByRole('button',{name:'下一根'}));
 await act(async()=>{});
 await act(async()=>{await vi.advanceTimersByTimeAsync(1100);});
 expect(screen.getByRole('status')).toHaveTextContent('正在加载行情及成交记录');
 expect(container.querySelector('.replay-chart-frame')).toContainElement(screen.getByRole('status'));
 expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(chart);
 fireEvent.click(screen.getByRole('button',{name:'取消加载'}));
 await act(async()=>{});
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'重试加载'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 expect(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')).toBe(chart);
});

it('starts trading inside the original chart and prompts on exit',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session))));
 render(<Harness/>);
 start();
 await screen.findByRole('button',{name:'市价买入 BUY'});
 expect(screen.getByRole('button',{name:'市价卖出 SELL'})).toBeInTheDocument();
 expect(screen.queryByText('开始训练')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'退出回放'}));
 expect(screen.getByRole('dialog',{name:'保存本次回放？'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'继续回放'}));
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('shows creation progress in the original chart and cancellation ignores a late creation',async()=>{
 let resolve:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(r=>{resolve=r;})));
 render(<Harness/>);start();
 expect(screen.getByRole('status')).toHaveTextContent('正在创建回放');
 fireEvent.click(screen.getByRole('button',{name:'取消创建'}));
 await act(async()=>{resolve(Response.json(session));});
 expect(screen.queryByRole('button',{name:'市价买入 BUY'})).not.toBeInTheDocument();
 expect(screen.getByRole('alert')).toHaveTextContent('交易记录');
});

it('renders disabled trading controls during a slow chart load and lets the user save and exit',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars'))return new Promise<Response>(()=>{});
  return Response.json(url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 render(<Harness/>);start();
 expect(await screen.findByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 expect(screen.getByRole('status')).toHaveTextContent('正在加载行情及成交记录');
 fireEvent.click(screen.getByRole('button',{name:'退出回放'}));
 expect(screen.getByRole('button',{name:'保存并退出'})).toBeEnabled();
 fireEvent.click(screen.getByRole('button',{name:'保存并退出'}));
 await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('keeps trading disabled after a failed read and enables it after retry',async()=>{
 let failing=true;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/bars')&&failing)throw new Error('history unavailable');
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 render(<Harness/>);start();
 await screen.findByRole('alert');
 expect(await screen.findByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 failing=false;fireEvent.click(screen.getByRole('button',{name:'重试加载'}));
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
});

it('does not enable trading until all ledger pages have loaded',async()=>{
 let resolve:(response:Response)=>void=()=>{};
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/events?offset=100'))return new Promise<Response>(r=>{resolve=r;});
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:100}:session);
 }));
 render(<Harness/>);start();
 expect(await screen.findByRole('button',{name:'市价买入 BUY'})).toBeDisabled();
 await act(async()=>{resolve(Response.json({items:[],next_offset:null}));});
 expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled();
});

it('does not bypass confirmation of an unknown order result when exiting',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/commands'))throw new TypeError('network lost');
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:session);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 fireEvent.click(screen.getByRole('button',{name:'市价买入 BUY'}));
 await screen.findByRole('button',{name:'重试确认提交结果'});
 fireEvent.click(screen.getByRole('button',{name:'退出回放'}));
 expect(screen.getByRole('button',{name:'保存并退出'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'不保存退出'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'继续回放'})).toBeEnabled();
});

it('places the right-click order through the existing ledger and keeps one ticket',async()=>{
 const commands:Array<Record<string,unknown>>=[];
 let current:Record<string,unknown>=session;
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   const input=JSON.parse(String(init?.body));commands.push(input);
   current={...session,revision:1,state:{...session.state,orders:[{...input,id:'chart-order',created_at:session.state.cursor,status:'pending'}]}};
   return Response.json(current);
  }
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 const {container}=render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'市价买入 BUY'})).toBeEnabled());
 fireEvent.change(screen.getByLabelText('快捷下单手数'),{target:{value:'2'}});
 fireEvent.contextMenu(container.querySelector('.chart-workspace-pane.is-active .trading-chart canvas')!,{clientX:100,clientY:205});
 fireEvent.click(await screen.findByRole('menuitem',{name:/买入.*限价/}));
 expect(screen.getByRole('dialog',{name:'图表下单'})).toBeInTheDocument();
 expect(screen.getByLabelText('限价')).toHaveValue(99.5);
 expect(screen.getAllByLabelText('下单手数')).toHaveLength(1);
 expect(screen.getByLabelText('下单手数')).toHaveValue(2);
 fireEvent.change(screen.getByLabelText('下单手数'),{target:{value:'3'}});
 expect(screen.getByLabelText('快捷下单手数')).toHaveValue(3);
 fireEvent.click(screen.getByRole('button',{name:'买入'}));
 await waitFor(()=>expect(screen.queryByRole('dialog',{name:'图表下单'})).not.toBeInTheDocument());
 expect(commands).toHaveLength(1);
 expect(commands[0]).toMatchObject({type:'place_order',kind:'limit',side:'buy',quantity:3,limit_price:'99.5',expected_revision:0});
 openDetails('订单');
 expect(screen.getByRole('button',{name:'撤单'})).toBeEnabled();
 expect(screen.queryByLabelText('下单手数')).not.toBeInTheDocument();
});

it('keeps confirmed chart protection when the subsequent market read fails',async()=>{
 const commands:Array<Record<string,unknown>>=[];
 let failedRead=false;
 let current={...session,state:{...session.state,last_price:'105',position:{quantity:1,average_price:'100',stop_loss:'95',take_profit:'110'},account:{...session.state.account,unrealized_pnl:'250'}}};
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{
  if(url.includes('/commands')){
   const input=JSON.parse(String(init?.body));commands.push(input);
   current={...current,revision:1,state:{...current.state,position:{...current.state.position,stop_loss:input.stop_loss,take_profit:input.take_profit}}};
   failedRead=true;return Response.json(current);
  }
  if(url.includes('/bars')&&failedRead)throw new TypeError('history unavailable');
  return Response.json(url.includes('/bars')?{bars:[]}:url.includes('/events')?{items:[],next_offset:null}:current);
 }));
 render(<Harness/>);start();
 await waitFor(()=>expect(screen.getByRole('button',{name:'拖动 SL'})).toBeEnabled());
 expect(screen.getByTestId('chart-position-label')).toHaveTextContent('+250.00 USD');
 const sl=screen.getByRole('button',{name:'拖动 SL'});
 Object.assign(sl,{setPointerCapture(){},hasPointerCapture(){return false;},releasePointerCapture(){}});
 fireEvent.pointerDown(sl,{pointerId:1,button:0,clientY:250});
 fireEvent.pointerMove(sl,{pointerId:1,clientY:170});
 fireEvent.pointerUp(sl,{pointerId:1,clientY:170});
 await screen.findByRole('button',{name:'重试加载'});
 expect(commands).toHaveLength(1);
 expect(commands[0]).toMatchObject({type:'update_protection',stop_loss:'103',take_profit:'110',expected_revision:0});
 expect(screen.getByRole('button',{name:'拖动 SL'})).toHaveTextContent('SL 103');
 openDetails('持仓');
 fireEvent.click(screen.getByRole('button',{name:'修改保护价'}));
 expect(screen.getByLabelText('持仓止损')).toHaveValue(103);
 expect(screen.queryByRole('button',{name:'重试确认提交结果'})).not.toBeInTheDocument();
});
