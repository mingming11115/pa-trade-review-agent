import { setImageReader } from 'lightweight-charts-drawing';

// The upstream Image tool keeps asset names in drawings; PA supplies durable
// browser-local bytes through the library's public image-reader API.
const databaseName = 'pa.chart-drawing-images.v1';
async function database(): Promise<IDBDatabase> {
 return new Promise((resolve,reject)=>{
  const request=indexedDB.open(databaseName,1);
  request.onupgradeneeded=()=>request.result.createObjectStore('images');
  request.onsuccess=()=>resolve(request.result);
  request.onerror=()=>reject(request.error??new Error('Image storage unavailable'));
  request.onblocked=()=>reject(new Error('Image storage upgrade blocked'));
 });
}
export async function saveDrawingImage(name:string,blob:Blob):Promise<void> {
 const db=await database();
 try {
  await new Promise<void>((resolve,reject)=>{
   const tx=db.transaction('images','readwrite');
   tx.objectStore('images').put(blob,name);
   tx.oncomplete=()=>resolve();
   tx.onabort=tx.onerror=()=>reject(tx.error??new Error('Could not save image'));
  });
 } finally {db.close();}
}
export async function readDrawingImage(name:string):Promise<Blob> {
 const db=await database();
 try {
  return await new Promise<Blob>((resolve,reject)=>{
   const tx=db.transaction('images','readonly');
   const request=tx.objectStore('images').get(name);
   request.onsuccess=()=>request.result instanceof Blob?resolve(request.result):reject(new Error('Saved image not found'));
   request.onerror=()=>reject(request.error??new Error('Could not read image'));
   tx.onabort=()=>reject(tx.error??new Error('Could not read image'));
  });
 } finally {db.close();}
}
export function enableDrawingImages(){setImageReader(readDrawingImage);}
