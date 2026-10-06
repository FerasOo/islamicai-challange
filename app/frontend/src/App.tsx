import {useEffect,useRef,useState} from 'react';
import {BookOpen,Bookmark,Check,ChevronDown,ChevronLeft,Clock,Copy,Download,FileText,Headphones,History,Layers,Library,LoaderCircle,Mic,MoreHorizontal,Plus,Search,Send,Settings2,ShieldCheck,Sparkles,Square,Trash2,UserRound,Users,Volume2,X,ArrowUpRight,Radio,PenLine} from 'lucide-react';
import {AudioCapture} from './audio';
import {orderedEvidence} from './evidence';
import {sessionSnapshot} from './history';
import type {Context,Health,Hit,Parent,Run,Session,Turn,VoiceProfile} from './types';

const number=(n:number)=>new Intl.NumberFormat('ar-SA').format(n);
const time=(n?:number)=>n===undefined?'—':n<1000?`${number(Math.round(n))} مللي ثانية`:`${number(Math.round(n/100)/10)} ثانية`;
const sourceGroups=[{id:'all',name:'الكل',icon:Layers,sources:[]},{id:'quran',name:'القرآن',icon:BookOpen,sources:['quran','tafsir-mujahid']},{id:'hadith',name:'السنة',icon:FileText,sources:['bukhari','muslim','hadeethenc']},{id:'books',name:'الكتب',icon:Library,sources:['ibn-baz','ibn-uthaymeen','kuwait-fiqh']},{id:'terminology',name:'المصطلحات',icon:BookOpen,sources:['terminology']}];
async function api<T>(path:string,options?:RequestInit):Promise<T>{
 const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options?.headers}});
 if(!response.ok){let message='تعذر إكمال الطلب.';try{const data=await response.json();if(typeof data.detail==='string')message=data.detail;}catch{}throw new Error(message);}
 return response.json();
}

function App(){
 const [health,setHealth]=useState<Health>();const [sessions,setSessions]=useState<Session[]>([]);const [sid,setSid]=useState('');
 const [connected,setConnected]=useState(false);const [turns,setTurns]=useState<Turn[]>([]);const [runs,setRuns]=useState<Record<string,Run>>({});const [runOrder,setRunOrder]=useState<string[]>([]);const [selected,setSelected]=useState('');
 const [saved,setSaved]=useState<Set<string>>(new Set());const [savedHits,setSavedHits]=useState<Hit[]>([]);const [enrolled,setEnrolled]=useState(false);
 const [view,setView]=useState<'live'|'history'|'library'|'saved'>('live');const [settingsOpen,setSettingsOpen]=useState(false);const [voiceOpen,setVoiceOpen]=useState(false);
 const auto=true;const [mode,setMode]=useState<'all'|'enrolled'>('all');const [sourceGroup,setSourceGroup]=useState('all');const k=health?.retrieval_policy.candidate_limit??200;
 const [profiles,setProfiles]=useState<VoiceProfile[]>([]);const [profileId,setProfileId]=useState('');const [profileName,setProfileName]=useState('');const [editingProfile,setEditingProfile]=useState('');const [profileEditor,setProfileEditor]=useState(false);
 const [voiceDraftMode,setVoiceDraftMode]=useState<'all'|'enrolled'>('all');const [voiceDraftProfileId,setVoiceDraftProfileId]=useState('');const [voiceApplying,setVoiceApplying]=useState(false);
 const [historyDetail,setHistoryDetail]=useState<{session:Session;turns:Turn[];runs:Run[]}>();const [historyLoading,setHistoryLoading]=useState(false);
 const [historyRunId,setHistoryRunId]=useState('');const [historySourceGroup,setHistorySourceGroup]=useState('all');const [historyShowCandidates,setHistoryShowCandidates]=useState(false);
 const [contextTarget,setContextTarget]=useState<{hit:Hit;owner:string;searchId?:string}>();
 const [notice,setNotice]=useState('');const [recording,setRecording]=useState(false);const [enrolling,setEnrolling]=useState(false);const [countdown,setCountdown]=useState(10);const [level,setLevel]=useState(0);const [audioState,setAudioState]=useState('stopped');
 const [partialTranscript,setPartialTranscript]=useState('');
 const [speakerState,setSpeakerState]=useState<{speaker:string;label:string;similarity?:number}>();
 const liveStt=health?.stt_mode==='gemini-live';
 const [clipSeconds,setClipSeconds]=useState(5);const [audioReport,setAudioReport]=useState<{stt_ms:number;verification:{speaker_labels_returned:boolean;timestamp_trim_failed:boolean}}>();
 const [showCandidates,setShowCandidates]=useState(false);const [context,setContext]=useState<Context>();const [contextMain,setContextMain]=useState('');const [contextLoading,setContextLoading]=useState(false);
 const [deleteTarget,setDeleteTarget]=useState<string>();
 const [copyId,setCopyId]=useState('');const [sessionBusy,setSessionBusy]=useState(false);
 const profilesRef=useRef<VoiceProfile[]>([]);profilesRef.current=profiles;
 const pendingVoiceProfile=useRef('');
 const ws=useRef<WebSocket|undefined>(undefined);const capture=useRef<AudioCapture|undefined>(undefined);const enrollmentTimer=useRef<ReturnType<typeof setInterval>|undefined>(undefined);const toastTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);const transcriptEnd=useRef<HTMLDivElement>(null);
 const settings=useRef({auto,mode,sources:[] as string[],clip_seconds:clipSeconds});
 settings.current={auto,mode,sources:sourceGroups.find(g=>g.id===sourceGroup)!.sources,clip_seconds:clipSeconds};
 const notify=(text:string)=>{setNotice(text);clearTimeout(toastTimer.current);toastTimer.current=setTimeout(()=>setNotice(''),6500);};
 const send=(data:unknown)=>{if(ws.current?.readyState===WebSocket.OPEN)ws.current.send(JSON.stringify(data));else notify('الجلسة غير متصلة. انتظر إعادة الاتصال.');};

 function applyEvent(e:any,restoring=false){
  if(e.type==='connected'){setEnrolled(e.enrolled);if(!e.enrolled)setMode('all');const last=localStorage.getItem('daleel-profile');const preferred=e.voice_profile?.has_voice?e.voice_profile.id:profilesRef.current.some(p=>p.id===last&&p.has_voice)?last:undefined;if(e.voice_profile?.has_voice)setProfileId(e.voice_profile.id);else if(preferred)send({type:'select_profile',profile_id:preferred});else setProfileId('');}
  if(e.type==='profile_selected'){setProfileId(e.profile.id);localStorage.setItem('daleel-profile',e.profile.id);setEnrolled(e.enrolled);setSpeakerState(undefined);if(!e.enrolled)setMode('all');if(pendingVoiceProfile.current===e.profile.id){pendingVoiceProfile.current='';setVoiceApplying(false);if(e.enrolled){setMode('enrolled');setVoiceOpen(false);}else notify('هذا الصوت لا يحتوي على تسجيل صالح. سجّل عينة جديدة.');}void loadProfiles();}
  if(e.type==='transcript_partial'&&!restoring)setPartialTranscript(e.text);
  if(e.type==='speaker_state'&&!restoring)setSpeakerState(e);
  if(e.type==='audio_error'&&!restoring){notify(e.message);setRecording(false);setPartialTranscript('');void capture.current?.stop(true);}
  if(e.type==='turn')setTurns(old=>old.some(t=>t.id===e.id)?old:[...old,e]);
  if(e.type==='turn_edited')setTurns(old=>old.map(t=>t.id===e.id?{...t,text:e.text}:t));
  if(e.type==='trigger')setTurns(old=>old.map(t=>t.id===e.turn_id?{...t,trigger:e.run,trigger_ms:e.ms}:t));
  if(e.type==='search_started'){
   setRuns(old=>({...old,[e.search_id]:{id:e.search_id,query:e.query,k:e.k,minimum:e.minimum,hits:[],done:false,accepted:0,checked:0,errors:0,total:0,cost:0}}));
   setRunOrder(old=>[e.search_id,...old.filter(id=>id!==e.search_id)]);setSelected(e.search_id);setShowCandidates(false);
  }
  if(e.type==='candidates')setRuns(old=>({...old,[e.search_id]:{...old[e.search_id],hits:e.hits,total:e.hits.length,timing:e.timing}}));
  if(e.type==='verdict')setRuns(old=>old[e.search_id]?({...old,[e.search_id]:{...old[e.search_id],hits:old[e.search_id].hits.map(h=>h.id===e.chunk_id?{...h,status:e.status,relevance:e.relevance,evidence_kind:e.evidence_kind,classification:e.classification}:h)}}):old);
  if(e.type==='progress'||e.type==='search_done')setRuns(old=>old[e.search_id]?({...old,[e.search_id]:{...old[e.search_id],...e,done:e.type==='search_done'}}):old);
  if(e.type==='search_done'&&!restoring)void loadSessions();
  if(e.type==='search_cancelled')setRuns(old=>Object.fromEntries(Object.entries(old).map(([id,r])=>[id,id===e.search_id?{...r,done:true,cancelled:true}:r])));
  if(e.type==='error'&&e.search_id)setRuns(old=>old[e.search_id]?({...old,[e.search_id]:{...old[e.search_id],done:true,error_message:e.message}}):old);
  if(e.type==='enrolled'){setEnrolled(true);setEnrolling(false);setProfileEditor(false);setEditingProfile('');setProfileName('');if(e.profile){setProfileId(e.profile.id);setVoiceDraftProfileId(e.profile.id);localStorage.setItem('daleel-profile',e.profile.id);}if(!restoring){void loadProfiles();notify(e.message);}}
  if(e.type==='voice_removed'){setEnrolled(false);setMode('all');setSpeakerState(undefined);}
  if(e.type==='audio_state'&&!restoring){setAudioState(e.state);if(e.state==='stopped'){setSpeakerState(undefined);setRecording(false);void capture.current?.stop(true);}}
  if(e.type==='audio_report'&&!restoring)setAudioReport(e);
  if(['error','notice'].includes(e.type)&&!restoring){notify(e.message);if(e.type==='error'){if(pendingVoiceProfile.current){pendingVoiceProfile.current='';setVoiceApplying(false);}setEnrolling(false);if(e.search_id)setRuns(old=>Object.fromEntries(Object.entries(old).map(([id,r])=>[id,id===e.search_id?{...r,done:true,cancelled:true}:r])));}}
 }

 async function loadProfiles(){const rows=await api<VoiceProfile[]>('/voice-profiles');profilesRef.current=rows;setProfiles(rows);return rows;}
 async function loadSaved(){const rows=await api<Hit[]>('/bookmarks');setSavedHits(rows);setSaved(new Set(rows.map(h=>h.id)));}
 async function saveProfile(){try{if(!editingProfile||!profileName.trim())return;await api<VoiceProfile>(`/voice-profiles/${editingProfile}`,{method:'PUT',body:JSON.stringify({name:profileName.trim()})});await loadProfiles();setProfileEditor(false);setProfileName('');setEditingProfile('');notify('حُفظ الاسم.');}catch(e){notify((e as Error).message);}}
 function openVoiceSettings(){setVoiceDraftMode(mode==='enrolled'&&enrolled?'enrolled':'all');setVoiceDraftProfileId(mode==='enrolled'&&enrolled?profileId:'');setVoiceOpen(true);}
 function applyVoiceSettings(){
  if(voiceDraftMode==='all'){setMode('all');setVoiceOpen(false);return;}
  if(!voiceDraftProfileId){notify('اختر صوتاً مسجلاً قبل الضغط على تطبيق.');return;}
  if(!profiles.some(p=>p.id===voiceDraftProfileId&&p.has_voice)){notify('الصوت المختار غير مسجّل. سجّل عينة أولاً.');return;}
  if(!health?.voice_ready){notify(health?.voice_error||'مطابقة الصوت غير متاحة حالياً.');return;}
  if(!connected||ws.current?.readyState!==WebSocket.OPEN){notify('انتظر اتصال الجلسة لتطبيق اختيار الصوت.');return;}
  if(voiceDraftProfileId===profileId&&enrolled){setMode('enrolled');setVoiceOpen(false);return;}
  pendingVoiceProfile.current=voiceDraftProfileId;setVoiceApplying(true);ws.current.send(JSON.stringify({type:'select_profile',profile_id:voiceDraftProfileId}));
 }
 async function openSession(session:Session){setHistoryLoading(true);setHistoryDetail({session,turns:[],runs:[]});setHistorySourceGroup('all');setHistoryShowCandidates(false);try{const detail=await api<{events:any[]}>(`/sessions/${session.id}`);const snapshot=sessionSnapshot(detail.events);setHistoryRunId(snapshot.runs.at(-1)?.id||'');setHistoryDetail({session,...snapshot});}catch(e){notify((e as Error).message);setHistoryDetail(undefined);}finally{setHistoryLoading(false);}}
 async function loadSessions(){const list=await api<Session[]>('/sessions');setSessions(list);return list;}
 async function newSession(){setSessionBusy(true);try{clearInterval(enrollmentTimer.current);await capture.current?.stop();setRecording(false);setEnrolling(false);const session=await api<Session>('/sessions',{method:'POST'});setSid(session.id);localStorage.setItem('daleel-session',session.id);await loadSessions();setView('live');}catch(e){notify((e as Error).message);}finally{setSessionBusy(false);}}
 useEffect(()=>{let alive=true;(async()=>{try{const h=await api<Health>('/health');if(!alive)return;setHealth(h);await loadProfiles();await loadSaved();const list=await loadSessions();if(!alive)return;const last=localStorage.getItem('daleel-session');if(last&&list.some(s=>s.id===last))setSid(last);else{const s=await api<Session>('/sessions',{method:'POST'});if(alive){setSid(s.id);localStorage.setItem('daleel-session',s.id);await loadSessions();}}}catch(e){if(alive)notify((e as Error).message);}})();return()=>{alive=false;};},[]);
 useEffect(()=>{if(!sid)return;let disposed=false;let retry:ReturnType<typeof setTimeout>;let socket:WebSocket;let attempts=0;
  setTurns([]);setRuns({});setRunOrder([]);setSelected('');setConnected(false);setRecording(false);setEnrolling(false);setAudioState('stopped');setSpeakerState(undefined);setPartialTranscript('');setAudioReport(undefined);localStorage.setItem('daleel-session',sid);
  async function connect(){try{const detail=await api<{events:any[];bookmarks:Hit[];enrolled:boolean}>(`/sessions/${sid}`);if(disposed)return;setTurns([]);setRuns({});setRunOrder([]);detail.events.forEach(e=>applyEvent(e,true));setRuns(old=>Object.fromEntries(Object.entries(old).map(([id,r])=>[id,{...r,done:true}])));setEnrolled(detail.enrolled);void loadSaved();
   socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/live/${sid}`);ws.current=socket;
   socket.onopen=()=>{if(disposed){socket.close();return;}attempts=0;setConnected(true);socket.send(JSON.stringify({type:'settings',...settings.current,mode:detail.enrolled?settings.current.mode:'all'}));};
   socket.onmessage=event=>{if(!disposed)try{applyEvent(JSON.parse(event.data));}catch{notify('تعذر قراءة تحديث الجلسة.');}};
   socket.onclose=()=>{if(disposed)return;pendingVoiceProfile.current='';setVoiceApplying(false);setConnected(false);setRecording(false);setPartialTranscript('');void capture.current?.stop();retry=setTimeout(()=>void connect(),Math.min(10000,1000*2**attempts++));};
   socket.onerror=()=>socket.close();
  }catch(e){if(!disposed){notify((e as Error).message);retry=setTimeout(()=>void connect(),3000);}}}
  void connect();return()=>{disposed=true;clearTimeout(retry);clearInterval(enrollmentTimer.current);void capture.current?.stop(true);socket?.close();};
 },[sid]);
 useEffect(()=>{if(connected)send({type:'settings',...settings.current});},[auto,mode,sourceGroup,connected,clipSeconds]);
 useEffect(()=>{transcriptEnd.current?.scrollIntoView({behavior:'smooth',block:'nearest'});},[turns.length,partialTranscript]);
 useEffect(()=>()=>{clearTimeout(toastTimer.current);clearInterval(enrollmentTimer.current);},[]);

 useEffect(()=>{
  if(!settingsOpen&&!voiceOpen&&!contextMain&&!deleteTarget&&!historyDetail)return;
  const previous=document.activeElement as HTMLElement|null;
  const dialog=Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"]')).at(-1);
  const focusables=()=>Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],select,input,textarea')||[]);
  focusables()[0]?.focus();
  const key=(e:KeyboardEvent)=>{
   if(e.key==='Escape'){if(contextMain)setContextMain('');else if(historyDetail)setHistoryDetail(undefined);else{setSettingsOpen(false);setDeleteTarget(undefined);if(voiceOpen)void cancelEnrollment();}}
   if(e.key==='Tab'){const items=focusables();const first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  };document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus();};
 },[settingsOpen,voiceOpen,!!contextMain,deleteTarget,!!historyDetail]);
 const run=runs[selected];const busy=!!run&&!run.done;
 const retrieving=busy&&!run?.timing;const searchProgress=run?.total?Math.min(100,100*run.checked/run.total):0;
 const rawHits=view==='saved'?savedHits.map(h=>({...h,source_name:health?.sources[h.source]||h.source_name,status:'accepted' as const,reference:h.reference||'موضع محفوظ في المصدر',similarity:h.similarity||0})):run?.hits||[];
 const visibleHits=rawHits.filter(h=>health?.sources[h.source]&&(view==='saved'||sourceGroup==='all'||settings.current.sources.includes(h.source)));
 const accepted=visibleHits.filter(h=>h.status==='accepted');const cards=orderedEvidence(showCandidates?visibleHits:accepted);
 const historyRun=historyDetail?.runs.find(r=>r.id===historyRunId)||historyDetail?.runs.at(-1);
 const historyLastTurn=historyDetail?.turns.at(-1);
 const historySources=sourceGroups.find(g=>g.id===historySourceGroup)?.sources||[];
 const historyHits=(historyRun?.hits||[]).filter(h=>health?.sources[h.source]&&(historySourceGroup==='all'||historySources.includes(h.source)));
 const historyCards=orderedEvidence(historyShowCandidates?historyHits:historyHits.filter(h=>h.status==='accepted'));
 const evidenceSessions=sessions.filter(s=>s.has_search);
 const statusLabel=audioState==='connecting'?'يتصل بالتفريغ…':audioState==='processing'?'تفريغ العبارة…':recording?'يستمع الآن':'الميكروفون';

 async function microphone(enroll=false){try{
  if(recording&&!enroll){await capture.current?.stop();setRecording(false);return;}
  if(!ws.current || !connected)throw new Error('انتظر اتصال الجلسة أولاً.');
  await capture.current?.stop(true);capture.current=new AudioCapture(ws.current,setLevel,message=>{notify(message);setRecording(false);});if(enroll&&!profileName.trim())throw new Error('أدخل اسم المتحدث أولاً.');await capture.current.start(enroll,enroll?{profile_name:profileName.trim(),profile_id:editingProfile||undefined}:undefined);
  setRecording(!enroll);
  if(enroll){setEnrolling(true);setCountdown(10);let remaining=10;enrollmentTimer.current=setInterval(()=>{remaining--;setCountdown(remaining);if(remaining<=0){clearInterval(enrollmentTimer.current);void capture.current?.stop();}},1000);}
 }catch(e){notify((e as Error).name==='NotAllowedError'?'اسمح باستخدام الميكروفون من إعدادات المتصفح.':(e as Error).message);setRecording(false);setEnrolling(false);}}
 async function cancelEnrollment(close=true){if(voiceApplying)return;clearInterval(enrollmentTimer.current);await capture.current?.stop(true);setEnrolling(false);setProfileEditor(false);setProfileName('');setEditingProfile('');if(close){if(voiceDraftMode==='enrolled'&&!voiceDraftProfileId)setMode('all');setVoiceOpen(false);}}
 async function bookmark(hit:Hit,owner=hit.session_id||sid,searchId=hit.search_id||run?.id){try{const next=!saved.has(hit.id);if(next)await api(`/sessions/${owner}/bookmarks`,{method:'POST',body:JSON.stringify({chunk_id:hit.id,saved:true,search_id:searchId})});else await api(`/bookmarks/${encodeURIComponent(hit.id)}`,{method:'DELETE'});await loadSaved();void loadSessions();notify(next?'حُفظ الدليل.':'أُلغي حفظ الدليل.');}catch(e){notify((e as Error).message);}}
 async function openContext(pid:string,target?:{hit:Hit;owner:string;searchId?:string}){setContextTarget(target);setContextMain(pid);setContextLoading(true);setContext(undefined);try{setContext(await api<Context>(`/context?parent_id=${encodeURIComponent(pid)}`));}catch(e){notify((e as Error).message);setContextMain('');}finally{setContextLoading(false);}}
 async function copy(hit:Hit){try{await navigator.clipboard.writeText(`${hit.text}\n\n${hit.source_name} — ${hit.reference}`);setCopyId(hit.id);setTimeout(()=>setCopyId(''),1600);}catch{notify('تعذر النسخ. يمكنك تحديد النص ونسخه يدوياً.');}}
 async function removeSession(id:string){try{await api(`/sessions/${id}`,{method:'DELETE'});const list=await loadSessions();if(id===sid){if(list.length)setSid(list[0].id);else await newSession();}}catch(e){notify((e as Error).message);}}

 function evidenceCard(hit:Hit,owner=hit.session_id||sid,searchId=hit.search_id||run?.id){const isQuran=['quran','tafsir-mujahid'].includes(hit.source);const isHadith=['bukhari','muslim','hadeethenc'].includes(hit.source);const Icon=isQuran?BookOpen:isHadith?FileText:Library;return <article className={`evidence-card ${isQuran?'quran':isHadith?'hadith':'scholar'}`} key={hit.id}>
  <div className="card-top"><span className="source-icon"><Icon size={19}/></span><div><span className="source-name">{hit.source_name}</span><p className="reference">{hit.reference}</p></div><button className={`icon-button bookmark ${saved.has(hit.id)?'active':''}`} aria-label={saved.has(hit.id)?'إلغاء حفظ الدليل':'حفظ الدليل'} onClick={()=>void bookmark(hit,owner,searchId)}><Bookmark size={18} fill={saved.has(hit.id)?'currentColor':'none'}/></button></div>
  <h3>{hit.title.replace(/^(?:صحيح البخاري|صحيح مسلم|مجموع فتاوى ومقالات ابن باز|مجموع فتاوى ورسائل ابن عثيمين) - /,'')}</h3><blockquote>{hit.text.replace(/\n{3,}/g,'\n\n')}</blockquote>
  {hit.review_required&&<div className="review-note">راجع المصدر الأصلي لهذا الموضع.</div>}
  <div className="evidence-judgment">{hit.relevance!==undefined?<span className="relevance"><Check size={13}/> صلة {number(Math.round(hit.relevance*100))}٪</span>:view!=='saved'&&<span className="candidate-label">{hit.status==='error'?'تعذر فحص الصلة':'بانتظار فحص الصلة'}</span>}{hit.evidence_kind&&hit.relevance!==undefined&&hit.relevance>=.75&&['direct','supporting','counter_position'].includes(hit.evidence_kind)&&<span className="evidence-kind" title="تصنيف آلي لعلاقة النص بالحوار">{{direct:'شاهد مباشر',supporting:'سياق مساعد',counter_position:'قول مقابل'}[hit.evidence_kind]}</span>}</div>
  <div className="card-actions"><button onClick={()=>void openContext(hit.parent_id,{hit,owner,searchId})}>النص والسياق <ChevronLeft size={14}/></button><div><button aria-label="نسخ النص مع المصدر" onClick={()=>void copy(hit)}>{copyId===hit.id?<Check size={15}/>:<Copy size={15}/>}</button></div></div>
 </article>;}

 return <div className="app-shell">
  <aside className="rail"><a className="brand" href="#" onClick={e=>{e.preventDefault();setView('live');}} aria-label="دليلك">د<span>•</span></a><div className="rail-divider"/>
   {[{id:'live',icon:Radio,label:'الحوار المباشر'},{id:'history',icon:History,label:'الأدلة السابقة'},{id:'library',icon:Library,label:'المصادر'},{id:'saved',icon:Bookmark,label:'الأدلة المحفوظة'}].map(item=><button key={item.id} className={`rail-button ${view===item.id?'active':''}`} title={item.label} aria-label={item.label} onClick={()=>setView(item.id as typeof view)}><item.icon size={21}/><span>{item.label}</span></button>)}
   <div className="rail-bottom"><button className="rail-button" aria-label="الإعدادات" onClick={()=>setSettingsOpen(true)}><Settings2 size={21}/><span>الإعدادات</span></button></div>
  </aside>
  <main className="workspace">
   <header className="topbar"><div className="identity"><span className="wordmark">دليلك</span><span className="topbar-line"/><span>{view==='live'?'جلسة مباشرة':view==='history'?'الأدلة السابقة':view==='library'?'المصادر':'المحفوظات'}</span></div><div className="topbar-actions"><span className={`connection ${connected?'online':''}`}><i/>{connected?'متصل':'يتصل…'}</span>{<button className="secondary small" onClick={()=>void newSession()} disabled={sessionBusy}><Plus size={15}/> حوار جديد</button>}</div></header>
   {view!=='live'&&<section className="page-heading"><h1>{view==='history'?'الأدلة السابقة':view==='library'?'المصادر':'المحفوظات'}</h1></section>}
   {view==='history'?<section className="history-page"><div className="section-heading"><h2>عمليات البحث السابقة</h2><span>{number(evidenceSessions.length)} حوار</span></div>{evidenceSessions.map(s=><div className="session-row" key={s.id}><button onClick={()=>void openSession(s)}><span className="session-icon"><History size={20}/></span><div><h3>{s.title}</h3><p>{new Date(s.created).toLocaleString('ar-SA')} · {number(s.saved)} دليل محفوظ</p></div><ChevronLeft size={20}/></button><button className="icon-button" aria-label="حذف الجلسة" onClick={()=>setDeleteTarget(s.id)}><Trash2 size={16}/></button></div>)}{!evidenceSessions.length&&<div className="history-empty"><BookOpen size={28}/><h3>لا أدلة سابقة بعد</h3><p>تظهر الحوارات هنا بعد إجراء بحث.</p></div>}</section>:
   view==='library'?<section className="library-page"><div className="library-intro"><BookOpen size={32}/><div><h2>المكتبة العربية</h2><p>تسعة مصادر · {health?number(health.corpus.indexed):'—'} مقطع</p></div></div>{[
    {name:'القرآن وتفسيره',ids:['quran','tafsir-mujahid']},{name:'السنة وشروحها',ids:['bukhari','muslim','hadeethenc']},{name:'الموسوعة الفقهية',ids:['kuwait-fiqh']},{name:'الفتاوى',ids:['ibn-baz','ibn-uthaymeen']},{name:'المصطلحات',ids:['terminology']}
   ].map(group=><section className="catalog-group" key={group.name}><h2>{group.name}</h2><div className="source-grid">{group.ids.filter(id=>health?.sources[id]).map(id=><article className="library-card" key={id}><span className="source-icon"><BookOpen size={23}/></span><div><h3>{health?.sources[id]}</h3><p>{{quran:'٦٬٢٣٦ آية · مصحف حفص','tafsir-mujahid':'روايات تفسيرية مرتبطة بالآيات',bukhari:'نص الأحاديث وأبواب الكتاب',muslim:'نص الأحاديث ورواياتها',hadeethenc:'الشرح والفوائد ومعاني الكلمات','kuwait-fiqh':'٤٥ مجلداً · أقوال المذاهب الفقهية','ibn-baz':'مجموع الفتاوى والمقالات · ٣٠ مجلداً','ibn-uthaymeen':'مجموع الفتاوى والرسائل · ٢٦ مجلداً',terminology:'تعريفات المصطلحات الشرعية'}[id]}</p></div></article>)}</div></section>)}</section>:
   <div className={`stage ${view==='saved'?'saved-view':''}`}>
    {view==='live'&&<section className="conversation panel"><div className="panel-heading"><div className="heading-label"><span className="status-dot"/><h2>الحوار</h2></div></div>
     <div className="audio-strip"><button className={`microphone ${recording?'recording':''}`} onClick={()=>void microphone()} disabled={!connected||enrolling||health?.stt_configured===false} aria-label={recording?'إيقاف الميكروفون':'تشغيل الميكروفون'}>{recording?<Square size={20} fill="currentColor"/>:<Mic size={23}/>}</button><div className="audio-info"><b>{statusLabel}</b><span>{liveStt?`${mode==='all'?'الجميع':'صوتي فقط'} · ${recording&&speakerState?speakerState.label:'مباشر'}`:`${mode==='all'?'الجميع':'صوتي فقط'} · ${number(clipSeconds)} ث${audioReport?` · استجابة ${time(audioReport.stt_ms)}`:''}`}</span></div><div className={`waveform ${recording?'live':''}`} aria-hidden="true">{Array.from({length:27},(_,i)=><i key={i} style={{height:`${6+(recording?level:0)*42*(.4+Math.sin(i*1.7)**2)}px`,animationDelay:`${i*25}ms`}}/>)}</div><button className="audio-options" disabled={!recording} title="إرسال المقطع الآن" aria-label="إرسال المقطع الآن" onClick={()=>send({type:'audio_flush'})}><Send size={16}/></button><button className="audio-options" aria-label="إعداد الصوت" onClick={openVoiceSettings}><Settings2 size={17}/></button></div>
     <div className="transcript">{turns.length===0&&!partialTranscript?<div className="conversation-empty"><button className="empty-orbit" type="button" onClick={()=>void microphone()} disabled={!connected||enrolling||health?.stt_configured===false} aria-label="تشغيل الميكروفون"><Mic size={28}/></button><h3>ابدأ الحديث</h3><p>اضغط الميكروفون لبدء الحوار.</p></div>:turns.map(t=><article className={`turn ${t.speaker==='enrolled'?'specialist':''}`} key={t.id}><div className="speaker-avatar"><UserRound size={15}/></div><div className="turn-content"><div className="turn-heading"><b>{t.label}</b><span>{t.input==='microphone'?'صوت':'نص'}</span></div><p>{t.text}</p><div className="turn-actions"><button onClick={()=>send({type:'search',text:t.text})}><Search size={12}/> بحث</button>{t.excluded_from_filter&&<span className="voice-uncertain">بانتظار تأكيد الصوت</span>}{t.trigger!==undefined&&<span className={t.trigger?'triggered':''}>{t.trigger?'بدأ البحث':'لا بحث جديد'}</span>}</div></div></article>)}{partialTranscript&&<article className="turn interim-turn"><div className="speaker-avatar"><Mic size={15}/></div><div className="turn-content"><div className="turn-heading"><b>تفريغ مباشر</b><span>جارٍ الاستماع</span></div><p>{partialTranscript}</p></div></article>}<div ref={transcriptEnd}/></div>

    </section>}
    <section className="evidence panel"><div className="panel-heading"><div className="heading-label"><BookOpen size={18}/><h2>{view==='saved'?'المحفوظات':'الأدلة'}</h2><span className="count-badge">{number(cards.length)}</span></div><button className="icon-button" aria-label="ضبط البحث" onClick={()=>setSettingsOpen(true)}><Settings2 size={17}/></button></div>
     {view!=='saved'&&<div className="source-tabs">{sourceGroups.map(g=><button key={g.id} className={sourceGroup===g.id?'active':''} onClick={()=>setSourceGroup(g.id)}><g.icon size={13}/>{g.name}</button>)}</div>}
     {run&&view!=='saved'&&<div className="query-strip"><div><Search size={14}/><p>{run.query}</p></div><span>{busy?<><LoaderCircle size={12} className="spin"/> {retrieving?'إحضار النصوص…':`فحص ${number(run.checked)} / ${number(run.total)}`}</>:<><Check size={12}/> {run.error_message?'تعذر البحث':run.cancelled?'توقف البحث':'اكتمل البحث'}</>}</span>{busy&&<button aria-label="إيقاف البحث" onClick={()=>send({type:'cancel'})}><X size={13}/></button>}</div>}
     <div className="evidence-scroll">{busy&&view!=='saved'&&<div className="search-progress" role="status" aria-live="polite"><div><span className="search-pulse"/><b>{retrieving?'إحضار الأدلة':'التحقق من الصلة'}</b><span>{!retrieving&&`${number(run.checked)} / ${number(run.total)}`}</span></div><div className={`search-track ${retrieving?'indeterminate':''}`} role="progressbar" aria-label="تقدم البحث" aria-valuemin={0} aria-valuemax={100} aria-valuenow={retrieving?undefined:Math.round(searchProgress)}><i style={retrieving?undefined:{width:`${searchProgress}%`}}/></div></div>}{busy&&!cards.length?<div className="evidence-skeletons" aria-hidden="true">{[0,1,2].map(i=><div className="evidence-skeleton" key={i}><div><i/><span/></div><b/><b/><b/></div>)}</div>:cards.length?cards.slice(0,showCandidates?k:30).map(h=>evidenceCard(h)):<div className="evidence-empty"><div className="evidence-mark"><BookOpen size={37}/><span><Sparkles size={15}/></span></div><h3>{view==='saved'?'لا أدلة محفوظة':busy?'جارٍ البحث…':run?.error_message?'تعذر البحث':run?.cancelled?'توقف البحث':run?(run.hits.length?'لا نتائج مقبولة':'لا مقاطع فوق عتبة التشابه'):'الأدلة هنا'}</h3><p>{view==='saved'?'احفظ دليلاً للرجوع إليه.':busy?'تظهر النتائج تباعاً.':run?(run.hits.length?'راجع المرشحات أو عدّل البحث.':'جرّب صياغة العبارة بصورة أوضح.'):'تظهر النصوص المرتبطة بالحوار هنا.'}</p></div>}
     {run&&view!=='saved'&&<button className="candidate-toggle" onClick={()=>setShowCandidates(!showCandidates)}>{showCandidates?'الأدلة المقبولة':`المرشحات (${number(run.hits.length)})`}<ChevronDown size={15}/></button>}
     {cards.length>(showCandidates?k:30)&&<p className="muted">تُعرض المقاطع الأولى مع دمج النتائج التي تشترك في المصدر والسياق.</p>}</div>
     
    </section>
   </div>}
   {view==='live'&&runOrder.length>0&&<section className="bottom-strip"><div className="recent-searches"><Clock size={14}/><span>بحث الجلسة</span>{runOrder.slice(0,3).map(id=><button key={id} className={selected===id?'active':''} onClick={()=>setSelected(id)}>{runs[id]?.query.slice(0,32)}</button>)}</div></section>}
  </main>
  {deleteTarget&&<div className="overlay" onClick={()=>setDeleteTarget(undefined)}><section className="modal" role="dialog" aria-modal="true" aria-label="حذف الجلسة" onClick={e=>e.stopPropagation()}><div className="modal-heading"><h2>حذف الجلسة؟</h2><button className="icon-button" aria-label="إغلاق" onClick={()=>setDeleteTarget(undefined)}><X size={20}/></button></div><p className="muted">سيُحذف الحوار وأدلة هذه الجلسة. تبقى الملفات الصوتية محفوظة.</p><div className="delete-actions"><button className="secondary" onClick={()=>setDeleteTarget(undefined)}>إلغاء</button><button className="primary" onClick={()=>{void removeSession(deleteTarget);setDeleteTarget(undefined);}}>حذف</button></div></section></div>}
  {notice&&<div role="status" className="toast"><span>{notice}</span><button aria-label="إغلاق التنبيه" onClick={()=>setNotice('')}><X size={16}/></button></div>}
  {settingsOpen&&<div className="overlay" onClick={()=>setSettingsOpen(false)}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-label="تفاصيل البحث" onClick={e=>e.stopPropagation()}><div className="modal-heading"><div><h2>تفاصيل البحث</h2></div><button className="icon-button" aria-label="إغلاق" onClick={()=>setSettingsOpen(false)}><X size={20}/></button></div>{!run&&<p className="muted">تظهر تفاصيل الأداء بعد أول بحث.</p>}{run&&<div className="run-metrics"><h3>قياس آخر بحث</h3>{[['المرشحات',`${number(run.hits.length)} / ${number(run.k)}`],['تضمين السؤال',time(run.timing?.embedding_ms)],['البحث في المتجهات',time(run.timing?.search_ms)],['أول شاهد مقبول',time(run.first_evidence_ms)],['اكتمال الفحص',time(run.total_ms)],['تعذر فحص',number(run.errors)]].map(([label,value])=><div key={label}><span>{label}</span><b>{value}</b></div>)}</div>}<button className="primary full" onClick={()=>setSettingsOpen(false)}>تم</button></section></div>}
  {voiceOpen&&<div className="overlay" onClick={()=>{if(!enrolling&&!voiceApplying)void cancelEnrollment();}}><section className="modal voice-modal" role="dialog" aria-modal="true" aria-label={profileEditor?(editingProfile?'تعديل الصوت':'إضافة صوت'):'إعداد الصوت'} onClick={e=>e.stopPropagation()}>
   <div className="modal-heading"><h2>{profileEditor?(editingProfile?'تعديل الصوت':'إضافة صوت'):'إعداد الصوت'}</h2><button className="icon-button" disabled={voiceApplying||(enrolling&&countdown===0)} aria-label="إغلاق" onClick={()=>void cancelEnrollment()}><X size={20}/></button></div>
   {profileEditor?<div className="voice-editor"><label>اسم المتحدث<input aria-label="اسم المتحدث" value={profileName} maxLength={60} onChange={e=>setProfileName(e.target.value)} placeholder="مثلاً: فراس" disabled={enrolling}/></label><div className={`enrollment-ring ${enrolling?'active':''}`}><Mic size={30}/>{enrolling&&<b>{number(countdown)}</b>}</div><h3>{enrolling?(countdown?'تحدث وحدك بصوت واضح':'جارٍ حفظ الصوت…'):(editingProfile?'تسجيل صوت جديد':'سجّل صوت المتحدث')}</h3><p>{editingProfile?'يمكنك حفظ الاسم فقط أو استبدال التسجيل.':'تحدث ١٠ ثوانٍ. يُضاف الملف بعد حفظ التسجيل.'}</p>{!enrolling?<><button className="primary full" disabled={!profileName.trim()||!connected||!health?.voice_ready} onClick={()=>void microphone(true)}><Mic size={17}/>{editingProfile?'إعادة تسجيل الصوت':'بدء التسجيل'}</button>{editingProfile&&<button className="secondary full rename-profile" disabled={!profileName.trim()} onClick={()=>void saveProfile()}>حفظ الاسم</button>}<button className="voice-editor-back" onClick={()=>void cancelEnrollment(false)}>رجوع</button></>:<button className="secondary full" disabled={countdown===0} onClick={()=>void cancelEnrollment(false)}>إلغاء التسجيل</button>}</div>:<>
   <p className="voice-section-label">من يبدأ البحث؟</p>
   <div className="mode-options"><button className={voiceDraftMode==='all'?'selected':''} onClick={()=>{setVoiceDraftMode('all');setVoiceDraftProfileId('');setMode('all');}}><span className="mode-icon"><Users size={22}/></span><div><b>الجميع</b><span>يبدأ البحث من كلام أي متحدث.</span></div>{voiceDraftMode==='all'&&<Check size={17}/>}</button><button className={voiceDraftMode==='enrolled'?'selected':''} onClick={()=>setVoiceDraftMode('enrolled')}><span className="mode-icon"><UserRound size={22}/></span><div><b>صوتي فقط</b><span>اختر صوتاً مسجلاً ثم اضغط تطبيق.</span></div>{voiceDraftMode==='enrolled'&&<Check size={17}/>}</button></div>
   {voiceDraftMode==='enrolled'&&<div className="profiles-section"><div className="profiles-section-heading"><div><h3>الأصوات المسجلة</h3><p>اختر صوتاً واحداً لبدء البحث من كلامه فقط.</p></div><button className="add-voice-button" aria-label="إضافة صوت" disabled={recording} onClick={()=>{setProfileEditor(true);setEditingProfile('');setProfileName('');}}><Plus size={19}/></button></div>
    <div className="profiles-list">{profiles.filter(p=>p.has_voice).map(p=><div className={`profile-row ${voiceDraftProfileId===p.id?'selected':''}`} key={p.id}><button className="profile-select" onClick={()=>setVoiceDraftProfileId(p.id)}><span className="profile-avatar"><UserRound size={19}/></span><div><b>{p.name}</b><span>صوت محفوظ</span></div>{voiceDraftProfileId===p.id&&<Check size={17}/>}</button><button className="icon-button" disabled={recording} aria-label={`تعديل صوت ${p.name}`} onClick={()=>{setProfileEditor(true);setEditingProfile(p.id);setProfileName(p.name);}}><PenLine size={16}/></button></div>)}</div>
    {!profiles.some(p=>p.has_voice)&&<div className="profiles-empty"><UserRound size={25}/><p>لم تسجّل صوتاً بعد</p><button className="secondary" onClick={()=>{setProfileEditor(true);setEditingProfile('');setProfileName('');}}>تسجيل صوت جديد</button></div>}
   </div>}
   {!health?.voice_ready&&voiceDraftMode==='enrolled'&&<p className="voice-note">{health?.voice_error||'مطابقة الصوت غير متاحة حالياً.'}</p>}
   <button className="primary full voice-apply" disabled={voiceApplying} onClick={applyVoiceSettings}>{voiceApplying?<><LoaderCircle className="spin" size={16}/> جارٍ التطبيق…</>:'تطبيق'}</button></>}
  </section></div>}
  {historyDetail&&<div className="overlay session-overlay" onClick={()=>setHistoryDetail(undefined)}>
   <section className="modal session-modal" role="dialog" aria-modal="true" aria-label="تفاصيل الجلسة" onClick={e=>e.stopPropagation()}>
    <div className="modal-heading"><div><h2>{historyDetail.session.title}</h2><p>{new Date(historyDetail.session.created).toLocaleString('ar-SA')}</p></div><button className="icon-button" aria-label="إغلاق الجلسة" onClick={()=>setHistoryDetail(undefined)}><X size={20}/></button></div>
    <div className="session-body">{historyLoading?<div className="loading-context"><LoaderCircle className="spin"/> جارٍ إحضار الجلسة…</div>:
     <div className="session-dashboard">
      <aside className="session-sidebar">
       <div className="session-latest"><span>آخر عبارة</span>{historyLastTurn?<><b>{historyLastTurn.label}</b><p>{historyLastTurn.text}</p></>:<p>لا عبارات مسجّلة.</p>}</div>
       <div className="session-run-list"><div className="session-run-heading"><h3>البحوث</h3><span>{number(historyDetail.runs.length)}</span></div>
        {historyDetail.runs.slice().reverse().map(r=><button key={r.id} className={historyRun?.id===r.id?'selected':''} onClick={()=>{setHistoryRunId(r.id);setHistoryShowCandidates(false);}}><Search size={15}/><span>{r.query}</span><small>{number(r.accepted)} دليل</small></button>)}
        {!historyDetail.runs.length&&<p>لم تُجرَ بحوث في هذه الجلسة.</p>}
       </div>
      </aside>
      <section className="session-results panel"><div className="panel-heading"><div className="heading-label"><BookOpen size={18}/><h2>الأدلة</h2><span className="count-badge">{number(historyCards.length)}</span></div></div>
       <div className="source-tabs">{sourceGroups.map(g=><button key={g.id} className={historySourceGroup===g.id?'active':''} onClick={()=>setHistorySourceGroup(g.id)}><g.icon size={13}/>{g.name}</button>)}</div>
       {historyRun&&<div className="query-strip"><div><Search size={14}/><p>{historyRun.query}</p></div><span>{historyRun.cancelled?'توقف البحث':historyRun.error_message?'تعذر البحث':'بحث مكتمل'}</span></div>}
       <div className="session-results-scroll">{historyCards.length?historyCards.slice(0,historyShowCandidates?k:30).map(h=>evidenceCard(h,historyDetail.session.id,historyRun?.id)):<div className="evidence-empty"><div className="evidence-mark"><BookOpen size={34}/></div><h3>{historyRun?'لا أدلة في هذا التصنيف':'لا بحوث بعد'}</h3><p>{historyRun?'اختر مصدراً آخر أو اعرض المقاطع المرشحة.':'ستظهر أدلة الجلسة بعد أول بحث.'}</p></div>}
        {historyRun&&<button className="candidate-toggle" onClick={()=>setHistoryShowCandidates(!historyShowCandidates)}>{historyShowCandidates?'الأدلة المقبولة':`المرشحات (${number(historyHits.length)})`}<ChevronDown size={15}/></button>}
       </div>
      </section>
     </div>}
    </div>
   </section>
  </div>}
  {contextMain&&<div className="overlay context-overlay" onClick={()=>setContextMain('')}><section className="modal context-modal" role="dialog" aria-modal="true" aria-label="النص الأصلي والسياق" onClick={e=>e.stopPropagation()}><div className="modal-heading"><div><h2>النص الأصلي والسياق</h2></div><button className="icon-button" aria-label="إغلاق السياق" onClick={()=>setContextMain('')}><X size={20}/></button></div>{contextLoading?<div className="loading-context"><LoaderCircle className="spin"/> جارٍ إحضار النص…</div>:context&&<><div className="context-actions">{contextTarget&&<button className={`secondary save-evidence ${saved.has(contextTarget.hit.id)?'saved':''}`} onClick={()=>void bookmark(contextTarget.hit,contextTarget.owner,contextTarget.searchId)}><Bookmark size={16} fill={saved.has(contextTarget.hit.id)?'currentColor':'none'}/>{saved.has(contextTarget.hit.id)?'إلغاء حفظ الدليل':'حفظ الدليل'}</button>}{context.parents[contextMain]?.metadata.url&&/^https?:\/\//.test(context.parents[contextMain].metadata.url!)&&<a href={context.parents[contextMain].metadata.url} target="_blank" rel="noreferrer">المصدر على الويب <ArrowUpRight size={14}/></a>}</div><div className="context-body">{Object.values(context.parents).map((p:Parent)=><article className={p.id===contextMain?'main-parent':''} key={p.id}><span className="context-source">{health?.sources[p.source]||p.source} · {p.id===contextMain?'النص الكامل':'سياق مرتبط'}</span><h3>{p.title}</h3>{p.metadata.requires_source_review&&<div className="review-note">راجع الملف الأصلي للتحقق من المواضع التي تحتاج إلى مراجعة.</div>}<p>{p.text}</p></article>)}<div className="context-navigation">{context.links.filter(l=>l.load_on_expansion&&l.from===contextMain&&!['quran','tafsir-mujahid'].includes(context.parents[contextMain]?.source)).slice(0,4).map(l=><button key={l.to} onClick={()=>void openContext(l.to)}>{l.relation==='previous_section'?'الموضع السابق':l.relation==='next_section'?'الموضع التالي':'الآية المجاورة'} <ChevronLeft size={14}/></button>)}</div></div></>}</section></div>}
 </div>;
}
export default App;
