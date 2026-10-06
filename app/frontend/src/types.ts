export type Hit={session_id?:string;search_id?:string;id:string;parent_id:string;source:string;source_name:string;title:string;text:string;reference:string;similarity:number;status:'pending'|'accepted'|'rejected'|'error';relevance?:number;evidence_kind?:'direct'|'supporting'|'counter_position';classification?:{choice:string;confidence:number;probabilities:Record<string,number>};review_required?:boolean;source_url?:string;start_char?:number;end_char?:number};
export type Turn={id:string;text:string;speaker:string;label:string;input?:string;stt_ms?:number;voice_similarity?:number;trigger?:boolean;trigger_ms?:number;excluded_from_filter?:boolean};
export type Timing={embedding_ms:number;search_ms:number;fetch_ms:number;retrieval_ms:number;minimum?:number;candidate_limit?:number;ranked?:number;candidates?:number;cap_reached?:boolean};
export type Run={id:string;query:string;k:number;minimum?:number;hits:Hit[];done:boolean;accepted:number;checked:number;errors:number;total:number;cost:number;first_evidence_ms?:number;total_ms?:number;cancelled?:boolean;error_message?:string;timing?:Timing};
export type Session={id:string;title:string;created:string;saved:number;has_search?:boolean};
export type Health={ready:boolean;cloud_configured:boolean;stt_model:string;stt_mode:string;stt_configured:boolean;voice_ready:boolean;voice_error?:string;voice_method?:string;corpus:{partial:boolean;indexed:number;total:number};sources:Record<string,string>;retrieval_policy:{candidate_limit:number;minimum:number;metric:'cosine'}};
export type Parent={id:string;source:string;title:string;text:string;metadata:{[key:string]:unknown;url?:string;requires_source_review?:boolean;pdf_pages?:number[];edition_reference?:string;pages?:{printed:string}[]}};
export type Context={parents:Record<string,Parent>;links:{from:string;to:string;relation:string;load_on_expansion?:boolean}[]};

export type VoiceProfile={id:string;name:string;has_voice:boolean;created:string;updated:string};
