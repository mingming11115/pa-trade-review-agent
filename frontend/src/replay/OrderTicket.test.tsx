import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OrderTicket } from './OrderTicket';
import type { ReplayOrder, ReplaySession } from '../types';
afterEach(cleanup);
it('uses the shared new-order quantity while keeping amendment quantities independent',async()=>{
 const command=vi.fn().mockResolvedValue(false),onNewOrderQuantityChange=vi.fn();
 const session={config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{position:{quantity:0}}} as ReplaySession;
 const props={session,disabled:false,command,newOrderQuantity:3,onNewOrderQuantityChange};
 const view=render(<OrderTicket {...props}/>);
 expect(screen.getByLabelText('下单手数')).toHaveValue(3);
 fireEvent.change(screen.getByLabelText('下单手数'),{target:{value:'4'}});
 expect(onNewOrderQuantityChange).toHaveBeenCalledWith(4);
 view.rerender(<OrderTicket {...props} newOrderQuantity={4}/>);
 fireEvent.click(screen.getByRole('button',{name:'买入'}));
 await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({type:'place_order',quantity:4})));
 const editing={id:'pending',kind:'limit',side:'buy',quantity:7,limit_price:'100'} as ReplayOrder;
 view.rerender(<OrderTicket key="pending" {...props} editing={editing}/>);
 expect(screen.getByLabelText('下单手数')).toHaveValue(7);
 onNewOrderQuantityChange.mockClear();
 fireEvent.change(screen.getByLabelText('下单手数'),{target:{value:'8'}});
 expect(onNewOrderQuantityChange).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'买入改单'}));
 await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({type:'amend_order',quantity:8})));
 view.rerender(<OrderTicket key="new" {...props} newOrderQuantity={4}/>);
 expect(screen.getByLabelText('下单手数')).toHaveValue(4);
});
it('submits a buy stop limit with both prices and keeps inputs after rejection',async()=>{
 const command=vi.fn().mockResolvedValue(false);
 const session={config:{instrument:{tick_size:'0.25'}},state:{position:{quantity:0}}} as ReplaySession;
 render(<OrderTicket session={session} disabled={false} command={command}/>);
 fireEvent.change(screen.getByLabelText('订单类型'),{target:{value:'stop_limit'}});
 fireEvent.change(screen.getByLabelText('触发价'),{target:{value:'101'}});
 fireEvent.change(screen.getByLabelText('限价'),{target:{value:'102'}});
 fireEvent.click(screen.getByRole('button',{name:'买入'}));
 await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({side:'buy',kind:'stop_limit',stop_price:'101',limit_price:'102'})));
 expect(screen.getByLabelText('限价')).toHaveValue(102);
 expect(screen.getByRole('button',{name:'卖出'})).toBeInTheDocument();
});

it.each([
 ['limit','sell',{limit_price:'101.25',stop_price:null}],
 ['stop_market','buy',{limit_price:null,stop_price:'101.25'}],
 ['stop_limit','sell',{limit_price:'101.25',stop_price:'101.25'}],
 ['market','buy',{limit_price:null,stop_price:null}],
] as const)('prefills a chart %s order and submits its price fields',async(kind,side,prices)=>{
 const command=vi.fn().mockResolvedValue(false);
 const session={config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{position:{quantity:0}}} as ReplaySession;
 render(<OrderTicket session={session} disabled={false} command={command} prefill={{id:'context',side,kind,price:'101.25'}}/>);
 expect(screen.getByLabelText('订单类型')).toHaveValue(kind);
 expect(screen.getByLabelText('下单方向')).toHaveValue(side);
 fireEvent.click(screen.getByRole('button',{name:side==='buy'?'买入':'卖出'}));
 await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({type:'place_order',side,kind,quantity:1,...prices})));
});

it('moves one draft into the chart and back without losing the quantity or rejected inputs',async()=>{
 const command=vi.fn().mockResolvedValue(false),onClose=vi.fn();
 const session={config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{position:{quantity:0}}} as ReplaySession;
 const {rerender,container}=render(<OrderTicket session={session} disabled={false} command={command}/>);
 fireEvent.change(screen.getByLabelText('下单手数'),{target:{value:'3'}});
 const placement={container,anchor:{x:100,y:100}};
 const prefill={id:'context',side:'buy' as const,kind:'limit' as const,price:'99.75'};
 rerender(<OrderTicket session={session} disabled={false} command={command} placement={placement} prefill={prefill} onClose={onClose}/>);
 expect(screen.getByRole('dialog',{name:'图表下单'})).toBeInTheDocument();
 expect(screen.getAllByLabelText('下单手数')).toHaveLength(1);
 expect(screen.getByLabelText('下单手数')).toHaveValue(3);
 fireEvent.click(screen.getByRole('button',{name:'买入'}));
 await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({quantity:3,limit_price:'99.75'})));
 expect(screen.getByRole('dialog',{name:'图表下单'})).toBeInTheDocument();
 fireEvent.keyDown(screen.getByRole('dialog',{name:'图表下单'}),{key:'Escape'});
 expect(onClose).toHaveBeenCalledOnce();
 rerender(<OrderTicket session={session} disabled={false} command={command} prefill={prefill}/>);
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 expect(screen.getByLabelText('下单手数')).toHaveValue(3);
 expect(screen.getByLabelText('限价')).toHaveValue(99.75);
});

it('submits the selected sell direction when Enter is pressed inside an input',async()=>{
 const command=vi.fn().mockResolvedValue(true);
 const session={config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{position:{quantity:0}}} as ReplaySession;
 render(<OrderTicket session={session} disabled={false} command={command} prefill={{id:'sell',side:'sell',kind:'limit',price:'105'}}/>);
 fireEvent.keyDown(screen.getByLabelText('下单手数'),{key:'Enter'});
 await waitFor(()=>expect(command).toHaveBeenCalledWith(expect.objectContaining({side:'sell',kind:'limit',limit_price:'105'})));
});

it('closes the newly opened chart ticket on Escape even before an input is focused',()=>{
 const command=vi.fn(),onClose=vi.fn();
 const session={config:{instrument:{tick_size:'0.25',multiplier:'50'}},state:{position:{quantity:0}}} as ReplaySession;
 render(<OrderTicket session={session} disabled={false} command={command} placement={{container:document.body,anchor:{x:0,y:0}}} onClose={onClose}/>);
 fireEvent.keyDown(document.body,{key:'Escape'});
 expect(onClose).toHaveBeenCalledOnce();
});
