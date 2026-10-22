import { barCloseAtMs, inTradingHours } from '../tradingHours';
import type { Bar,Period,ReplayCommandInput,ReplayOrder,TradeMarker,TradingHours } from '../types';

export async function endTraining(orders:ReplayOrder[],quantity:number,command:(c:ReplayCommandInput)=>Promise<boolean>):Promise<boolean> {
  for(const order of orders.filter(o=>o.status==='pending')) {
    if(!await command({type:'cancel_order',order_id:order.id}))return false;
  }
  if(quantity) {
    if(!await command({type:'place_order',reduce_only:true,side:quantity>0?'sell':'buy',quantity:Math.abs(quantity)}))return false;
  }
  return command({type:'finish'});
}

export function mapReplayMarkers(markers:TradeMarker[],bars:Bar[],symbol="",hours:TradingHours="ETH",period:Period="1m"):TradeMarker[] {
  return markers.flatMap(marker=>{
    if(!inTradingHours(marker.timestamp,symbol,hours))return [];
    const time=Date.parse(marker.timestamp);
    let low=0,high=bars.length-1,index=-1;
    while(low<=high){const mid=Math.floor((low+high)/2);if(Date.parse(bars[mid].timestamp)<=time){index=mid;low=mid+1;}else high=mid-1;}
    if(index>=0 && hours === "RTH" && time >= barCloseAtMs(bars[index],(bars[index].timeframe??period) as Period,symbol))return [];
    return index<0?[]:[{...marker,timestamp:bars[index].timestamp}];
  }).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
}
