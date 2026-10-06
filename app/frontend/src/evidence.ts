import type {Hit} from './types';

// Choose the strongest Jev-judged chunk before grouping sibling source chunks.
// Unchecked passages follow scored passages; ties retain the original order.
export function orderedEvidence(hits:Hit[]):Hit[]{
 const parents=new Set<string>();
 return [...hits].sort((a,b)=>(b.relevance??-1)-(a.relevance??-1)).filter(hit=>{
  if(parents.has(hit.parent_id))return false;
  parents.add(hit.parent_id);return true;
 });
}
