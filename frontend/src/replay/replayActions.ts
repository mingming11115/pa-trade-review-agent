import type { Bar,ReplayCommandInput,ReplayOrder,TradeMarker } from '../types';

export async function endTraining(orders:ReplayOrder[],quantity:number,command:(c:ReplayCommandInput)=>Promise<boolean>):Promise<boolean> {
  for(const order of orders.filter(o=>o.status==='pending')) {
    if(!await command({type:'cancel_order',order_id:order.id}))return false;
  }
  if(quantity) {
    if(!await command({type:'place_order',reduce_only:true,side:quantity>0?'sell':'buy',quantity:Math.abs(quantity)}))return false;
  }
  return command({type:'finish'});
}

export function mapReplayMarkers(markers:TradeMarker[],bars:Bar[]):TradeMarker[] {
  return markers.flatMap(marker=>{
    const time=Date.parse(marker.timestamp);
    let low=0,high=bars.length-1,index=-1;
    while(low<=high){const mid=Math.floor((low+high)/2);if(Date.parse(bars[mid].timestamp)<=time){index=mid;low=mid+1;}else high=mid-1;}
    return index<0?[]:[{...marker,timestamp:bars[index].timestamp}];
  }).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
}
