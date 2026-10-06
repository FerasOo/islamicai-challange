import type {Run,Turn} from './types';

// Restore a read-only session view without changing the active microphone/session.
export function sessionSnapshot(events:any[]):{turns:Turn[];runs:Run[]}{
 const turns:Turn[]=[];const runs=new Map<string,Run>();
 for(const e of events){
  if(e.type==='turn')turns.push({...e});
  if(e.type==='turn_edited'){const t=turns.find(t=>t.id===e.id);if(t)t.text=e.text;}
  if(e.type==='search_started')runs.set(e.search_id,{id:e.search_id,query:e.query,k:e.k,minimum:e.minimum,hits:[],done:true,accepted:0,checked:0,errors:0,total:0,cost:0});
  const r=runs.get(e.search_id);if(!r)continue;
  if(e.type==='candidates'){r.hits=e.hits.map((h:any)=>({...h}));r.total=r.hits.length;r.timing=e.timing;}
  if(e.type==='verdict'){const h=r.hits.find(h=>h.id===e.chunk_id);if(h)Object.assign(h,{status:e.status,relevance:e.relevance,evidence_kind:e.evidence_kind,classification:e.classification});}
  if(e.type==='search_done'||e.type==='progress')Object.assign(r,e,{done:true});
  if(e.type==='search_cancelled')r.cancelled=true;
  if(e.type==='error')r.error_message=e.message;
 }
 return {turns,runs:[...runs.values()]};
}
