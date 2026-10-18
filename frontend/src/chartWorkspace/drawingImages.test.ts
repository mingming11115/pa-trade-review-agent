import { afterEach, expect, it, vi } from 'vitest';
import { readDrawingImage, saveDrawingImage } from './drawingImages';

afterEach(()=>vi.unstubAllGlobals());
function storage(fail=false){
 const assets=new Map<string,Blob>();const close=vi.fn();
 const db={close,createObjectStore:vi.fn(),transaction:()=>{
  const tx={oncomplete:null as null|(()=>void),onerror:null as null|(()=>void),onabort:null as null|(()=>void),error:new Error('quota'),objectStore:()=>({
   put:(blob:Blob,key:string)=>queueMicrotask(()=>{if(fail){tx.onabort?.();return;}assets.set(key,blob);tx.oncomplete?.();}),
   get:(key:string)=>{const req={result:assets.get(key),onsuccess:null as null|(()=>void),onerror:null};queueMicrotask(()=>req.onsuccess?.());return req;},
  })};return tx;
 }};
 vi.stubGlobal('indexedDB',{open:()=>{const req={result:db,onupgradeneeded:null,onsuccess:null as null|(()=>void),onerror:null,onblocked:null};queueMicrotask(()=>req.onsuccess?.());return req;}});
 return {assets,close};
}
it('reads image bytes through a fresh database connection and isolates scoped names',async()=>{
 const db=storage();const image=new Blob(['image bytes'],{type:'image/png'});
 await saveDrawingImage('user:ES:market:picture',image);
 expect(await readDrawingImage('user:ES:market:picture')).toBe(image);
 await expect(readDrawingImage('other:ES:market:picture')).rejects.toThrow('not found');
 expect(db.close).toHaveBeenCalledTimes(3);
});
it('fails an uncommitted image save instead of reporting durable success',async()=>{
 const db=storage(true);
 await expect(saveDrawingImage('picture',new Blob(['image']))).rejects.toThrow('quota');
 expect(db.assets.size).toBe(0);expect(db.close).toHaveBeenCalled();
});
