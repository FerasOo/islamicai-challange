import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import ROOT, ACTIVE_SOURCE_NAMES, OPENROUTER_KEY, STT_MODEL, GEMINI_KEY, LIVE_STT_MODEL, STT_TRANSPORT, CANDIDATE_LIMIT, RETRIEVAL_MINIMUM, JEV_ACCEPTANCE, TRIGGER_MINIMUM
from .providers import Cloud, ProviderError
from .retrieval import Retrieval
from .storage import Storage
from .voice import Segmenter, pcm16k
from .reference_voice import enrollment_wav, prefix_audio, extract_turns
from .live_transcribe import LiveTranscriber
from .local_speaker import LocalSpeakerService, SpeakerStream, METHOD

log = logging.getLogger('daleel')
storage = Storage()
retrieval = Retrieval()
cloud = None
speaker_service = LocalSpeakerService()
provider_semaphore = asyncio.Semaphore(80)
ALLOWED_ORIGINS = {'http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8765','http://localhost:8765','https://vowed-trustless-reason.ngrok-free.dev'}
ALLOWED_ORIGINS.update(o.strip().rstrip('/') for o in os.getenv('DALEEL_ALLOWED_ORIGINS','').split(',') if o.strip())


@asynccontextmanager
async def lifespan(app):
    global cloud
    cloud = Cloud()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(retrieval.executor,retrieval.load)
    if STT_TRANSPORT=='gemini-live':
        try:await speaker_service.run(speaker_service.load)
        except Exception:
            speaker_service.error='تعذر تحميل مطابقة الصوت المحلية. تحقق من تثبيت متطلبات التطبيق.'
            log.exception('Local speaker models could not load')
    yield
    await cloud.close()
    retrieval.close()
    speaker_service.close()


app = FastAPI(title='دليلك',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=list(ALLOWED_ORIGINS),allow_methods=['GET','POST','DELETE'],allow_headers=['Content-Type'])


class Query(BaseModel):
    text: str = Field(min_length=2,max_length=2000)
    k: int = Field(default=CANDIDATE_LIMIT,ge=1,le=CANDIDATE_LIMIT)
    sources: list[str] = Field(default_factory=list,max_length=10)
    minimum: float = Field(default=RETRIEVAL_MINIMUM,ge=0,le=1)
    acceptance: float = Field(default=JEV_ACCEPTANCE,ge=.5,le=1)

    @field_validator('text')
    @classmethod
    def nonblank(cls,value):
        if len(value.strip()) < 2:
            raise ValueError('السؤال فارغ')
        return value.strip()

    @field_validator('sources')
    @classmethod
    def known_sources(cls,values):
        if any(s not in ACTIVE_SOURCE_NAMES for s in values):
            raise ValueError('مصدر غير معروف')
        return list(dict.fromkeys(values))


def check_session(sid):
    if not storage.exists(sid):
        raise HTTPException(404,'الجلسة غير موجودة')


@app.get('/api/health')
async def health():
    return {'ready':retrieval.engine is not None,'cloud_configured':bool(OPENROUTER_KEY),
        'stt_model':LIVE_STT_MODEL if STT_TRANSPORT=='gemini-live' else STT_MODEL,
        'stt_mode':STT_TRANSPORT,'stt_configured':bool(GEMINI_KEY) if STT_TRANSPORT=='gemini-live' else bool(OPENROUTER_KEY),
        'voice_ready':speaker_service.ready if STT_TRANSPORT=='gemini-live' else True,
        'voice_method':METHOD if STT_TRANSPORT=='gemini-live' else 'gemini-reference-prefix','voice_experimental':True,
        'voice_error':speaker_service.error,
        'clip_seconds':5,'corpus':retrieval.info,'sources':ACTIVE_SOURCE_NAMES,
        'retrieval_policy':{'candidate_limit':CANDIDATE_LIMIT,'minimum':RETRIEVAL_MINIMUM,'metric':'cosine'}}


class ProfileName(BaseModel):
    name: str = Field(min_length=1,max_length=60)
    @field_validator('name')
    @classmethod
    def valid_name(cls,value):
        value=value.strip()
        if not value:raise ValueError('أدخل اسم المتحدث.')
        return value

@app.get('/api/voice-profiles')
async def voice_profiles():return storage.profiles()

@app.post('/api/voice-profiles')
async def create_voice_profile(body:ProfileName):return storage.create_profile(body.name)

@app.put('/api/voice-profiles/{pid}')
async def rename_voice_profile(pid:str,body:ProfileName):
    if not storage.profile(pid):raise HTTPException(404,'الملف الصوتي غير موجود')
    return storage.rename_profile(pid,body.name)

@app.get('/api/bookmarks')
async def all_bookmarks():return storage.bookmarks()

@app.delete('/api/bookmarks/{chunk_id:path}')
async def remove_bookmark(chunk_id:str):
    storage.remove_bookmark(chunk_id);return {'ok':True}

@app.get('/api/sessions')
async def sessions():
    return storage.list()


@app.post('/api/sessions')
async def create_session():
    return storage.create()


@app.get('/api/sessions/{sid}')
async def session_detail(sid:str):
    check_session(sid)
    return storage.detail(sid)


@app.delete('/api/sessions/{sid}')
async def delete_session(sid:str):
    check_session(sid);storage.delete(sid)
    return {'ok':True}


@app.delete('/api/sessions/{sid}/voice')
async def delete_voice(sid:str):
    check_session(sid);storage.remove_voice(sid)
    return {'ok':True}


class Bookmark(BaseModel):
    chunk_id: str = Field(max_length=150)
    saved: bool
    search_id: str | None = Field(default=None,max_length=100)


@app.post('/api/sessions/{sid}/bookmarks')
async def bookmark(sid:str,body:Bookmark):
    check_session(sid)
    def fetch():
        return retrieval.chunk(body.chunk_id)
    row = await asyncio.get_running_loop().run_in_executor(retrieval.executor,fetch)
    if not row:raise HTTPException(404,'المقطع غير موجود')
    if body.saved and body.search_id:
        verdict = next((e for e in reversed(storage.detail(sid)['events'])
            if e.get('type')=='verdict' and e.get('search_id')==body.search_id and e.get('chunk_id')==body.chunk_id),None)
        if verdict:
            row['search_id']=body.search_id
            row.update({k:verdict[k] for k in ['relevance','evidence_kind','classification'] if k in verdict})
    storage.bookmark(sid,body.chunk_id,row if body.saved else None)
    return {'ok':True}


@app.get('/api/context')
async def context(parent_id:str):
    result = await asyncio.get_running_loop().run_in_executor(retrieval.executor,partial(retrieval.context,parent_id))
    if not result['parents']:raise HTTPException(404,'المصدر غير موجود')
    return result


async def run_search(sid, query, emit, search_id=None):
    loop = asyncio.get_running_loop()
    search_id = search_id or str(uuid.uuid4());start = time.perf_counter()
    await emit({'type':'search_started','search_id':search_id,'query':query.text,'k':query.k,'minimum':query.minimum})
    docs,timing = await loop.run_in_executor(retrieval.executor,partial(retrieval.search,query.text,query.k,query.sources,query.minimum))
    await emit({'type':'candidates','search_id':search_id,'hits':docs,'timing':timing})
    counters = {'accepted':0,'checked':0,'errors':0,'cost':0.,'first_evidence_ms':None}

    async def inspect(doc):
        async with provider_semaphore:
            try:
                passage = await loop.run_in_executor(retrieval.executor,partial(retrieval.passage,doc))
                if passage.get('required_context_incomplete'):
                    raise ProviderError('راجع المصدر الأصلي؛ السياق المطلوب أطول من حد الفحص.')
                verdict = await cloud.relevant(query.text,passage)
                score = verdict['answers']['relevant'];accepted = score >= query.acceptance
                counters['cost'] += verdict['usage'].get('cost',0) or 0
                counters['accepted'] += int(accepted)
                if accepted and counters['first_evidence_ms'] is None:
                    counters['first_evidence_ms'] = round(1000*(time.perf_counter()-start),1)
                event = {'type':'verdict','search_id':search_id,'chunk_id':doc['id'],
                    'status':'accepted' if accepted else 'rejected','relevance':score,'jev_ms':verdict['ms']}
                kind = verdict['answers'].get('evidence_kind')
                if kind:
                    event['classification']=kind
                    if accepted:event['evidence_kind']=kind['choice']
            except ProviderError as e:
                counters['errors'] += 1
                event = {'type':'verdict','search_id':search_id,'chunk_id':doc['id'],'status':'error','message':str(e)}
            counters['checked'] += 1
            await emit(event)
            await emit({'type':'progress','search_id':search_id,**counters,'total':len(docs)},persist=False)

    tasks = [asyncio.create_task(inspect(doc)) for doc in docs]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks),timeout=30)
    except asyncio.TimeoutError:
        await emit({'type':'notice','message':'انتهت مهلة الفحص. يمكنك مراجعة الأدلة المكتملة والمرشحات المتبقية.'})
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
    await emit({'type':'search_done','search_id':search_id,**counters,'total':len(docs),
        'total_ms':round(1000*(time.perf_counter()-start),1),'timing':timing})


@app.websocket('/api/live/{sid}')
async def live(ws:WebSocket,sid:str):
    if ws.headers.get('origin') and ws.headers['origin'] not in ALLOWED_ORIGINS:
        await ws.close(code=1008);return
    if not storage.exists(sid):await ws.close(code=1008);return
    await ws.accept()
    loop = asyncio.get_running_loop()
    send_lock = asyncio.Lock()
    reference = storage.voice_sample(sid)
    reference_revision = 0
    settings = {'auto':True,'mode':'all','sources':[],'clip_seconds':5,'evidence_pinned':False}
    history = storage.detail(sid)['events']
    settings['evidence_pinned'] = next((bool(e['pinned']) for e in reversed(history)
        if e['type']=='evidence_pin'),False)
    turns = [dict(e) for e in history if e['type']=='turn']
    for event in history:
        if event['type']=='turn_edited':
            for turn in turns:
                if turn['id']==event['id']:turn['text']=event['text']
    turns = turns[-10:]
    # An attempted search already covers that conversation topic for auto-triggering,
    # even when it is still running or returns no accepted evidence.
    topics = [e['query'] for e in history if e['type']=='search_started']
    segmenter = Segmenter(max_seconds=settings['clip_seconds'],pause_seconds=float('inf'))
    enrolling = False;enroll_parts = [];rate = 48000; audio_active = False
    enrollment_target = None
    audio_queue = asyncio.Queue(maxsize=4)
    search_task = None;trigger_task = None
    live_client = None
    using_live = STT_TRANSPORT=='gemini-live'
    enrollment = None
    speaker_stream = None
    pending_transcripts=[]
    pending_at=0.
    commit_lock=asyncio.Lock()
    if using_live and reference:
        try:
            enrollment = await speaker_service.run(speaker_service.restore,reference,storage.voice(sid))
            storage.voice(sid,enrollment)
        except Exception:
            log.exception('Could not restore local enrollment')
            enrollment=None

    async def emit(event,persist=True):
        if persist and event['type'] not in ['audio_state','level'] and storage.exists(sid):storage.event(sid,event)
        async with send_lock:await ws.send_json(event)

    async def guarded_search(query):
        search_id = str(uuid.uuid4())
        try:
            await run_search(sid,query,emit,search_id)
        except asyncio.CancelledError:
            await emit({'type':'search_cancelled','search_id':search_id})
            raise
        except Exception:
            log.exception('Search failed')
            await emit({'type':'error','search_id':search_id,'message':'تعذر إكمال البحث. أعد المحاولة.'})

    async def start_search(text):
        nonlocal search_task
        if search_task and not search_task.done():
            search_task.cancel()
            await asyncio.gather(search_task,return_exceptions=True)
        query = Query(text=text,sources=settings['sources'])
        topics.append(query.text)
        search_task = asyncio.create_task(guarded_search(query))

    async def automatic(turn):
        try:
            recent = [dict(t) for t in turns if settings['mode']!='enrolled' or t['speaker']=='enrolled' or t['id']==turn['id']][-10:]
            decision = await cloud.trigger(recent,topics.copy())
            scores = decision['answers']
            run = (scores['needs_evidence'] >= TRIGGER_MINIMUM
                and scores['new_need'] >= TRIGGER_MINIMUM
                and scores['reply_to_searched_topic'] < TRIGGER_MINIMUM)
            await emit({'type':'trigger','turn_id':turn['id'],'run':run,'scores':scores,'ms':decision['ms']})
            if run:
                await start_search(turn['text'][-2000:])
        except ProviderError as e:await emit({'type':'notice','message':str(e)+' يمكنك تشغيل البحث يدوياً.'})

    async def add_turn(text,speaker,label,extra=None,trigger_allowed=True):
        nonlocal trigger_task
        if not text.strip():return
        profile=storage.selected_profile(sid)
        if speaker=='enrolled' and profile:
            label=profile['name'];extra={**(extra or {}),'voice_profile_id':profile['id']}
        turn = {'type':'turn','id':str(uuid.uuid4()),'text':text.strip(),'speaker':speaker,'label':label,**(extra or {})}
        turns.append(turn);del turns[:-10]
        await emit(turn)
        if settings['auto'] and not settings['evidence_pinned'] and trigger_allowed:
            if trigger_task and not trigger_task.done():trigger_task.cancel()
            trigger_task = asyncio.create_task(automatic(turn))

    async def commit_live_text(force=False,text=None):
        nonlocal pending_at
        async with commit_lock:
            if text:
                pending_transcripts.append(text)
                if not pending_at:pending_at=time.monotonic()
            await commit_live_text_locked(force)

    async def commit_live_text_locked(force=False):
        nonlocal pending_at
        if pending_transcripts:
            revision = reference_revision
            identity = {'speaker':'unidentified','label':'متحدث','identified':False}
            if speaker_stream:
                # Freeze the audio cursor before awaiting model work. Incoming
                # frames must not be included just because inference took time.
                end=speaker_stream.end
                identity=await speaker_service.run(speaker_stream.final,end,force or settings['mode']=='all')
            if revision != reference_revision:return
            if identity.get('reason')=='insufficient_speech' and not force and settings['mode']=='enrolled':
                return
            text=' '.join(pending_transcripts);pending_transcripts.clear()
            pending_at=0.
            await emit({'type':'transcript_partial','text':''},persist=False)
            if settings['mode']=='enrolled' and identity['speaker']!='enrolled':
                await emit({'type':'audio_skipped','speaker':identity['speaker'],
                    'message':'لم يُؤكد تطابق الصوت. لم تُضف العبارة.','verification':identity},persist=False)
                return
            await add_turn(text,identity['speaker'],identity['label'],
                {'input':'microphone','stt_model':LIVE_STT_MODEL,'speaker_identified':identity['identified'],
                 'voice_similarity':identity.get('similarity'),'verification':identity})

    async def expire_short_finals():
        # Do not require additional words to release a finalized short phrase.
        # In voice-only mode an unverified phrase expires safely as skipped.
        while True:
            await asyncio.sleep(.25)
            if pending_at and time.monotonic()-pending_at>=2:
                await commit_live_text(force=True)

    async def live_event(event):
        nonlocal audio_active
        kind = event['type']
        if kind=='transcript_final':
            await commit_live_text(force=not audio_active or sum(len(t) for t in pending_transcripts)>2000,text=event['text'])
        elif kind=='transcript_partial':
            # A current speaker estimate cannot verify every word in a cloud
            # interim. Enrolled-only mode displays committed, verified text.
            if settings['mode']=='all':
                await emit({**event,'text':' '.join([*pending_transcripts,event['text']])},persist=False)
        elif kind=='live_notice':
            await emit({'type':'notice','message':event['message']},persist=False)
        elif kind=='live_error':
            await emit({'type':'audio_error','message':event['message']},persist=False)
        elif kind=='live_closed':
            audio_active=False
            await commit_live_text(force=True)
            await emit({'type':'transcript_partial','text':''},persist=False)
            await emit({'type':'audio_state','state':'stopped'},persist=False)

    async def audio_worker():
        while True:
            samples, sample_rate, sample_reference, revision, queued_at = await audio_queue.get()
            try:
                await emit({'type':'audio_state','state':'processing'},persist=False)
                mono = pcm16k(samples,sample_rate)
                clip_id = str(uuid.uuid4())
                wav, reference_duration, offset = prefix_audio(mono,sample_reference)
                result = await cloud.transcribe(wav)
                # Discard obsolete results after reference replacement/removal.
                if revision != reference_revision:
                    continue
                utterances, verification = extract_turns(result,reference_duration,offset)
                await emit({'type':'audio_report','stt_ms':result['ms'],
                    'clip_ms':len(mono)/16,'elapsed_ms':1000*(time.perf_counter()-queued_at),
                    'usage':result['usage'],'verification':verification})
                if verification['timestamp_trim_failed']:
                    await emit({'type':'notice','message':'لم تُرجع الخدمة توقيتاً يفصل العينة عن الكلام الجديد. لم يُضف النص؛ أعد المحاولة.'},persist=False)
                for utterance in utterances:
                    identity = utterance['speaker']
                    if settings['mode']=='enrolled' and identity.startswith('other-'):
                        await emit({'type':'audio_skipped','speaker':identity,'message':'تجاوز كلام المتحدث الآخر.'},persist=False)
                        continue
                    await add_turn(utterance['text'],identity,utterance['label'],
                        {'verification':verification,'stt_ms':result['ms'],'input':'microphone',
                         'clip_id':clip_id,'provider_speaker':utterance['provider_speaker'],
                         'start':utterance['start'],'end':utterance['end'],
                         'excluded_from_filter':settings['mode']=='enrolled' and identity!='enrolled'},
                        trigger_allowed=settings['mode']=='all' or identity=='enrolled')
            except (ValueError,ProviderError) as e:await emit({'type':'notice','message':str(e)})
            except Exception:
                log.exception('Audio processing failed');await emit({'type':'error','message':'تعذر معالجة العبارة الصوتية.'})
            finally:
                audio_queue.task_done()
                await emit({'type':'audio_state','state':'listening' if audio_active else 'stopped'},persist=False)

    audio_task = asyncio.create_task(audio_worker())
    expiry_task = asyncio.create_task(expire_short_finals())

    async def enqueue(samples):
        if samples is not None:
            try:audio_queue.put_nowait((samples,rate,reference,reference_revision,time.perf_counter()))
            except asyncio.QueueFull:await emit({'type':'notice','message':'المعالجة الصوتية متأخرة. توقف قليلاً حتى تكتمل العبارات السابقة.'})

    try:
        await emit({'type':'connected','enrolled':enrollment is not None if using_live else reference is not None,'stt_mode':STT_TRANSPORT,
            'voice_method':METHOD if using_live else 'gemini-reference-prefix','voice_profile':storage.selected_profile(sid),
            'evidence_pinned':settings['evidence_pinned']},persist=False)
        if using_live and reference and enrollment is None:
            await emit({'type':'notice','message':'أعد تسجيل عينة صوتك لتفعيل المطابقة المحلية.'},persist=False)
        while True:
            message = await ws.receive()
            if message['type']=='websocket.disconnect':break
            if message.get('bytes') is not None:
                if not audio_active:continue
                data = message['bytes']
                if len(data)>262144 or len(data)%4:continue
                chunk = np.frombuffer(data,dtype='<f4').copy()
                if not np.isfinite(chunk).all():continue
                if enrolling:
                    enroll_parts.append(chunk)
                    if sum(len(p) for p in enroll_parts)>rate*15:
                        enrolling=False;enroll_parts=[];audio_active=False
                        await emit({'type':'error','message':'انتهت مهلة تسجيل الصوت. أعد تسجيل عينة مدتها ١٠ ثوانٍ.'})
                        await emit({'type':'audio_state','state':'stopped'},persist=False)
                elif using_live:
                    try:
                        if speaker_stream:
                            state=await speaker_service.run(speaker_stream.push,pcm16k(chunk,rate))
                            if state:await emit(state,persist=False)
                        await live_client.send_audio(chunk,rate)
                    except ProviderError as e:
                        audio_active=False
                        await emit({'type':'audio_error','message':str(e)},persist=False)
                        await live_client.close()
                else:await enqueue(segmenter.push(chunk))
                continue
            try:
                data = json.loads(message.get('text','{}'))
                if not isinstance(data,dict):raise ValueError('Invalid event')
                kind = data.get('type')
                if kind=='settings':
                    # Retrieval and Jev thresholds are server-owned; ignore old clients' tuning fields.
                    candidate = Query(text='إعداد البحث',sources=data.get('sources',settings['sources']))
                    mode = data.get('mode',settings['mode'])
                    if mode not in ['all','enrolled']:raise ValueError('وضع الاستماع غير صالح.')
                    if using_live and mode=='enrolled' and enrollment is None:raise ValueError('سجّل عينة صوتك أولاً لتفعيل المطابقة المحلية.')
                    if mode=='enrolled' and reference is None:raise ValueError('سجّل عينة صوتك أولاً.')
                    clip_seconds = data.get('clip_seconds',settings['clip_seconds'])
                    if isinstance(clip_seconds,bool) or clip_seconds not in [3,5,8,12]:raise ValueError('مدة المقطع غير صالحة.')
                    if clip_seconds != settings['clip_seconds']:
                        await enqueue(segmenter.flush())
                        segmenter = Segmenter(rate,max_seconds=clip_seconds,pause_seconds=float('inf'))
                    settings.update({'sources':candidate.sources,'auto':bool(data.get('auto',settings['auto'])),'mode':mode})
                    settings['clip_seconds'] = clip_seconds
                    if using_live and mode=='enrolled':
                        await emit({'type':'transcript_partial','text':''},persist=False)
                elif kind=='evidence_pin':
                    pinned=data.get('pinned')
                    if not isinstance(pinned,bool):raise ValueError('حالة تثبيت الأدلة غير صالحة.')
                    if pinned and not any(e['type']=='search_started' for e in storage.detail(sid)['events']):
                        raise ValueError('ابحث عن أدلة أولاً قبل تثبيتها.')
                    settings['evidence_pinned']=pinned
                    if pinned and trigger_task and not trigger_task.done():
                        trigger_task.cancel()
                    await emit({'type':'evidence_pin','pinned':pinned})
                elif kind=='select_profile':
                    if audio_active or enrolling:raise ValueError('أوقف التسجيل قبل تغيير الملف الصوتي.')
                    pid=data.get('profile_id');profile=storage.profile(pid)
                    if not profile:raise ValueError('الملف الصوتي غير موجود.')
                    storage.select_profile(sid,pid)
                    reference=storage.voice_sample(sid);enrollment=None;reference_revision+=1
                    if using_live and reference:
                        enrollment=await speaker_service.run(speaker_service.restore,reference,storage.voice(sid))
                        storage.voice(sid,enrollment)
                    speaker_stream=None;pending_transcripts.clear();pending_at=0.
                    if not reference:settings['mode']='all'
                    await emit({'type':'profile_selected','profile':profile,'enrolled':bool(enrollment) if using_live else bool(reference)},persist=False)
                elif kind=='audio_start':
                    enrollment_target=None
                    if data.get('enroll') and data.get('profile_name') is not None:
                        name=ProfileName(name=data['profile_name']).name
                        pid=data.get('profile_id')
                        if pid and not storage.profile(pid):raise ValueError('الملف الصوتي غير موجود.')
                        enrollment_target={'name':name,'id':pid}
                    new_rate = int(data.get('sample_rate',48000))
                    if new_rate not in [16000,22050,24000,44100,48000,96000]:raise ValueError('معدل الصوت غير مدعوم.')
                    if enrolling:raise ValueError('أكمل تسجيل العينة أولاً.')
                    if using_live:
                        audio_active=False
                        if live_client:await live_client.finish()
                        await commit_live_text(force=True)
                        rate=new_rate
                        enrolling=bool(data.get('enroll'));enroll_parts=[]
                        if enrolling:
                            if not speaker_service.ready:
                                try:await speaker_service.run(speaker_service.load)
                                except Exception:
                                    enrolling=False
                                    raise ProviderError('تعذر تحميل مطابقة الصوت المحلية. تحقق من تثبيت متطلبات التطبيق.') from None
                            audio_active=True
                            await emit({'type':'audio_state','state':'enrolling'},persist=False)
                            continue
                        speaker_stream=SpeakerStream(speaker_service,enrollment) if enrollment else None
                        await emit({'type':'speaker_state','speaker':'waiting' if enrollment else 'unidentified',
                            'label':'يتحقق من الصوت…' if enrollment else 'متحدث'},persist=False)
                        await emit({'type':'audio_state','state':'connecting'},persist=False)
                        live_client=LiveTranscriber(live_event)
                        try:
                            await live_client.start()
                            audio_active=True
                            await emit({'type':'audio_state','state':'listening'},persist=False)
                        except ProviderError as e:
                            await emit({'type':'audio_error','message':str(e)},persist=False)
                        continue
                    await enqueue(segmenter.flush())
                    audio_active = True;rate = new_rate
                    segmenter = Segmenter(rate,max_seconds=settings['clip_seconds'],pause_seconds=float('inf'))
                    enrolling = bool(data.get('enroll'));enroll_parts=[]
                    await emit({'type':'audio_state','state':'enrolling' if enrolling else 'listening'},persist=False)
                elif kind=='audio_stop':
                    audio_active = False
                    if using_live and enrolling:
                        enrolling=False
                        await emit({'type':'audio_state','state':'processing'},persist=False)
                        try:
                            samples=pcm16k(np.concatenate(enroll_parts) if enroll_parts else np.array([],dtype='float32'),rate)
                            wav,metadata=await speaker_service.run(speaker_service.enroll,samples)
                            if enrollment_target:
                                storage.save_profile_recording(sid,enrollment_target['name'],wav,metadata,metadata['duration'],enrollment_target['id'])
                            else:
                                storage.voice_sample(sid,wav,metadata['duration']);storage.voice(sid,metadata)
                            reference=wav;enrollment=metadata;reference_revision+=1
                            await emit({'type':'enrolled','duration':metadata['duration'],
                                'speech_seconds':metadata['speech_seconds'],'method':METHOD,
                                'profile':storage.selected_profile(sid),'message':'حُفظ الصوت في ملف المتحدث.'})
                        finally:
                            enroll_parts=[]
                            await emit({'type':'audio_state','state':'stopped'},persist=False)
                        continue
                    if using_live:
                        if live_client:
                            await emit({'type':'audio_state','state':'processing'},persist=False)
                            await live_client.finish()
                        await commit_live_text(force=True)
                        await emit({'type':'audio_state','state':'stopped'},persist=False)
                        continue
                    if enrolling:
                        enrolling=False
                        samples = pcm16k(np.concatenate(enroll_parts) if enroll_parts else np.array([],dtype='float32'),rate)
                        wav, duration = enrollment_wav(samples)
                        if enrollment_target:storage.save_profile_recording(sid,enrollment_target['name'],wav,None,duration,enrollment_target['id'])
                        else:storage.voice_sample(sid,wav,duration)
                        reference=wav;reference_revision+=1
                        await emit({'type':'enrolled','duration':duration,'method':'gemini-reference-prefix',
                            'profile':storage.selected_profile(sid),'message':'حُفظت العينة. ستُرسل مع كل مقطع للتجربة.'})
                        enroll_parts=[]
                    else:await enqueue(segmenter.flush())
                    await emit({'type':'audio_state','state':'stopped'},persist=False)
                elif kind=='cancel_enroll':
                    audio_active = False
                    enrolling=False;enroll_parts=[]
                    await emit({'type':'audio_state','state':'stopped'},persist=False)
                elif kind=='forget_voice':
                    storage.remove_voice(sid);reference=None;reference_revision+=1;settings['mode']='all'
                    enrollment=None
                    if speaker_stream:speaker_stream.enrollment=None
                    pending_transcripts.clear()
                    pending_at=0.
                    await emit({'type':'transcript_partial','text':''},persist=False)
                    await emit({'type':'voice_removed'})
                elif kind=='audio_flush':
                    if using_live:
                        if live_client and audio_active:await live_client.flush()
                    elif not enrolling:await enqueue(segmenter.flush())
                elif kind=='search':
                    await start_search(Query(text=data.get('text','')).text)
                elif kind=='turn':
                    text = Query(text=data.get('text','')).text
                    speaker = data.get('speaker','enrolled')
                    if speaker not in ['enrolled','other-1']:raise ValueError('المتحدث غير صالح.')
                    await add_turn(text,speaker,'المتحدث المسجّل' if speaker=='enrolled' else 'المتحدث الآخر 1',{'input':'typed'})
                elif kind=='edit_turn':
                    text = Query(text=data.get('text','')).text
                    turn = next((t for t in turns if t['id']==data.get('id')),None)
                    if turn is None:raise ValueError('العبارة غير موجودة.')
                    turn['text']=text
                    await emit({'type':'turn_edited','id':turn['id'],'text':text})
                elif kind=='cancel':
                    if search_task:search_task.cancel()
                elif kind=='ping':await emit({'type':'pong'},persist=False)
            except (ValueError,TypeError,ProviderError) as e:
                await emit({'type':'error','message':str(e) if isinstance(e,ProviderError) or isinstance(e,ValueError) and not isinstance(e,json.JSONDecodeError) else 'تحقق من المدخلات وأعد المحاولة.'})
    except WebSocketDisconnect:pass
    finally:
        if live_client:await live_client.close()
        for task in [audio_task,expiry_task,search_task,trigger_task]:
            if task:task.cancel()
        await asyncio.gather(*[t for t in [audio_task,expiry_task,search_task,trigger_task] if t],return_exceptions=True)


dist = ROOT/'frontend/dist'
if dist.exists():app.mount('/',StaticFiles(directory=dist,html=True),name='frontend')
