export type User={id:string;email:string;name:string;role:'user'|'operator'};
export type Product={id:string;name:string;description:string;sku:string;category:string;price:number;stock:number;art:string;badge:string};
export type Order={id:string;number:number;user_id:string;status:string;version:number;total:number;delivery_fee:number;name:string;phone:string;address:string;delivery:string;comment:string;channel:string;created_at:string;updated_at:string;items:{product_id:string;name:string;price:number;quantity:number}[];history:{id:number;status:string;created_at:string}[]};
export class ApiError extends Error{constructor(public code:string,message:string,public status:number){super(message)}}
let accessToken='';
let refreshPromise:Promise<User|null>|null=null;
export function setToken(token:string){accessToken=token}
export async function refresh():Promise<User|null>{
 if(!refreshPromise)refreshPromise=(async()=>{try{const fetchToken=async()=>{const r=await fetch('/api/v1/auth/refresh',{method:'POST',credentials:'include',signal:AbortSignal.timeout(20000)});if(!r.ok){accessToken='';return null}const d=await r.json();accessToken=d.access_token;return d.user};return navigator.locks?await navigator.locks.request('edin-refresh',fetchToken):await fetchToken()}catch{return null}finally{refreshPromise=null}})();
 return refreshPromise;
}
export async function api<T>(path:string,options:RequestInit={},retry=true):Promise<T>{
 let response:Response;
 try{response=await fetch('/api/v1'+path,{...options,credentials:'include',headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(accessToken?{Authorization:'Bearer '+accessToken}:{}),...options.headers},signal:options.signal||AbortSignal.timeout(20000)})}catch(e){if((e as Error).name==='AbortError')throw e;throw new ApiError('NETWORK_ERROR','Нет связи с сервером. Проверьте интернет и повторите попытку.',0)}
 if(response.status===401&&retry&&!path.startsWith('/auth/')){const user=await refresh();if(user)return api<T>(path,options,false);window.dispatchEvent(new Event('edin:session-expired'))}
 if(!response.ok){let data;try{data=await response.json()}catch{data={}}throw new ApiError(data.error?.code||'HTTP_ERROR',data.error?.message||`Ошибка сервера (${response.status})`,response.status)}
 return response.status===204?undefined as T:response.json();
}
export async function login(path:string,body:unknown){const d=await api<{access_token:string;user:User}>('/auth/'+path,{method:'POST',body:JSON.stringify(body)});accessToken=d.access_token;return d.user}
export async function logout(){try{await api('/auth/logout',{method:'POST'})}finally{accessToken=''}}
export async function stream(signal:AbortSignal,onEvent:()=>void,onState:(state:boolean)=>void){
 let cursor=0;let delay=1500;
 while(!signal.aborted){
  try{
   const r=await fetch('/api/v1/events?after='+cursor,{headers:{Authorization:'Bearer '+accessToken},signal,credentials:'include'});
   if(r.status===401){if(!await refresh()){onState(false);return}continue}
   if(!r.ok||!r.body)throw Error('Stream unavailable');
   onState(true);delay=1500;
   const reader=r.body.getReader();const decoder=new TextDecoder();let buffer='';
   while(!signal.aborted){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const msg=buffer.slice(0,end);buffer=buffer.slice(end+2);if(msg.includes('event: order.updated')){const match=msg.match(/^id: (\d+)/m);if(match)cursor=Number(match[1]);onEvent()}}}
  }catch(e){if(signal.aborted)return;onState(false)}
  if(!signal.aborted)await new Promise<void>(resolve=>{const timer=setTimeout(resolve,delay);signal.addEventListener('abort',()=>{clearTimeout(timer);resolve()},{once:true})});
  delay=Math.min(delay*1.5,15000);
 }
}
export const money=(value:number)=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB',maximumFractionDigits:0}).format(value/100);
export const date=(value:string)=>new Date(value).toLocaleDateString('ru-RU',{day:'numeric',month:'long',timeZone:'Europe/Moscow'});
export const statusNames:Record<string,string>={new:'Новый',confirmed:'Подтверждён',assembling:'Собирается',shipping:'В пути',completed:'Получен',cancelled:'Отменён'};
export const nextStatuses:Record<string,string[]>={new:['confirmed','cancelled'],confirmed:['assembling','cancelled'],assembling:['shipping','cancelled'],shipping:['completed'],completed:[],cancelled:[]};
