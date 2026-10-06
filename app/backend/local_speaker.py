"""Local ECAPA verification, speech-only enrollment, and bounded live audio windows.

All model calls run on one dedicated executor. Gemini supplies text; its current
Live response has no usable timing fields, so mixed/short audio stays uncertain.
No ordinary microphone audio is persisted by this module.
"""
import hashlib
import io
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import numpy as np
import soundfile as sf

from .config import SPEAKER_ACCEPT, SPEAKER_REJECT
from .voice import VoiceModel, pcm16k, wav_bytes

METHOD = 'ecapa-silero-v1'
LABELS = {'enrolled':'المتحدث المسجّل', 'other':'متحدث آخر',
          'uncertain':'صوت غير مؤكد', 'unidentified':'متحدث',
          'waiting':'يتحقق من الصوت…', 'silent':'بانتظار الكلام'}


class LocalSpeakerService:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='speaker')
        self.encoder = None
        self.vad = None
        self.error = None

    @property
    def ready(self):
        return self.encoder is not None and self.vad is not None

    def load(self):
        if self.ready:return
        from silero_vad import load_silero_vad
        import torch
        self.vad = load_silero_vad()
        self.encoder = VoiceModel()
        torch.set_num_threads(2)
        self.error = None

    async def run(self, fn, *args):
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(self.executor, partial(fn, *args))

    def speech_data(self, samples):
        from silero_vad import get_speech_timestamps
        import torch
        self.load()
        if not len(samples):return samples,0.
        with torch.inference_mode():
            spans = get_speech_timestamps(torch.from_numpy(samples.copy()), self.vad,
                sampling_rate=16000, min_silence_duration_ms=150, speech_pad_ms=30)
        speech=np.concatenate([samples[s['start']:s['end']] for s in spans]) if spans else np.zeros(0,dtype='float32')
        age=(len(samples)-spans[-1]['end'])/16000 if spans else len(samples)/16000
        return speech,age

    def clean(self, samples):
        return self.speech_data(samples)[0]

    def enroll(self, samples):
        duration = len(samples)/16000
        if not 8<=duration<=15:
            raise ValueError('تحدث وحدك بين ٨ و١٥ ثانية لتسجيل صوتك.')
        speech = self.clean(samples)
        if len(speech)<6*16000:
            raise ValueError('العينة لا تحتوي على كلام كافٍ. تحدث بوضوح طوال ١٠ ثوانٍ.')
        vector = self.encoder.encode(speech)
        wav = wav_bytes(samples)
        return wav, {'method':METHOD, 'embedding':vector.tolist(), 'duration':duration,
            'speech_seconds':len(speech)/16000, 'sha256':hashlib.sha256(wav).hexdigest()}

    def restore(self, wav, cached):
        if not wav:return None
        if isinstance(cached,dict) and cached.get('method')==METHOD and cached.get('sha256')==hashlib.sha256(wav).hexdigest():
            vector = np.asarray(cached.get('embedding',[]),dtype='float32')
            if vector.shape==(192,) and np.isfinite(vector).all() and .99<float(np.linalg.norm(vector))<1.01:
                return cached
        samples,rate = sf.read(io.BytesIO(wav),dtype='float32')
        if samples.ndim>1:samples=samples.mean(axis=1)
        _,metadata = self.enroll(pcm16k(samples,rate))
        # The cached vector belongs to the original stored WAV, even if its
        # input representation needed resampling or PCM conversion.
        metadata['sha256']=hashlib.sha256(wav).hexdigest()
        return metadata

    def identify(self, samples, enrollment, minimum=3., rolling=False):
        if enrollment is None:return {'speaker':'unidentified','label':LABELS['unidentified'], 'identified':False}
        started = time.perf_counter()
        speech,age = self.speech_data(samples)
        if rolling:speech=speech[-5*16000:]
        seconds = len(speech)/16000
        result={'speaker':'uncertain','identified':False,'speech_seconds':round(seconds,3),
                'method':METHOD,'alignment':'local-audio-window','silence_seconds':round(age,3)}
        if seconds<minimum:
            result['reason']='insufficient_speech'
        else:
            reference = np.asarray(enrollment['embedding'],dtype='float32')
            # Longer speech-only windows replace the old veto on every raw 1.5s
            # window. Multiple supporting windows also expose a speaker change.
            width = int(3*16000)
            starts=list(range(0,max(1,len(speech)-width+1),int(1.5*16000)))
            if len(speech)>width and starts[-1]!=len(speech)-width:starts.append(len(speech)-width)
            windows=[speech[a:a+width] for a in starts]
            if len(windows)==1 and seconds>=3.5:
                windows=[speech[:width],speech[-width:]]
            scores=[float(self.encoder.encode(x)@reference) for x in windows]
            # Two confirmations are required. A 3s phrase can use two overlapping
            # 2.5s speech windows; shorter phrases remain uncertain.
            if len(scores)==1:
                scores=[float(self.encoder.encode(speech[:40000])@reference),
                        float(self.encoder.encode(speech[-40000:])@reference)]
            result.update(similarity=round(float(np.median(scores)),4), window_scores=[round(v,4) for v in scores])
            if all(v>=SPEAKER_ACCEPT for v in scores):
                result.update(speaker='enrolled',identified=True,reason='matched')
            elif all(v<=SPEAKER_REJECT for v in scores):
                result.update(speaker='other',identified=True,reason='different_voice')
            else:result['reason']='ambiguous_or_speaker_change'
        result['label']=LABELS[result['speaker']]
        result['ms']=round((time.perf_counter()-started)*1000,1)
        return result

    def close(self):
        self.executor.shutdown(wait=False,cancel_futures=True)


class SpeakerStream:
    """Per-microphone buffer. Access from the service's serial executor only.

Updates every 0.5s on up to 8s of wall-clock audio, with >=3s useful speech.
Final text is verified against audio received since the last final. Without
provider timestamps, a mixed window is deliberately not split into invented
word/speaker assignments. Sixty seconds of raw audio is the hard memory limit.
"""
    def __init__(self, service, enrollment):
        self.service=service
        self.enrollment=enrollment
        self.samples=np.zeros(0,dtype='float32')
        self.start=0
        self.end=0
        self.last_final=0
        self.last_check=0
        self.last_state='waiting'
        self.streak=0
        self.previous=None

    def push(self, samples):
        self.samples=np.concatenate([self.samples,samples])
        self.end+=len(samples)
        if len(self.samples)>60*16000:
            excess=len(self.samples)-60*16000
            self.samples=self.samples[excess:];self.start+=excess
        if self.enrollment is None or self.end-self.last_check<8000:return None
        self.last_check=self.end
        if self.end<3*16000:return None
        result=self.service.identify(self.samples[-8*16000:],self.enrollment,rolling=True)
        state=result['speaker']
        if result.get('speech_seconds',0)<.3 or result.get('silence_seconds',0)>.8:state='silent'
        elif result.get('reason')=='insufficient_speech':state='waiting'
        self.streak=self.streak+1 if state==self.previous else 1
        self.previous=state
        if state in ['enrolled','other'] and self.streak<2:state='waiting'
        self.last_state=state
        return {'type':'speaker_state',**result,'speaker':state,'label':LABELS[state]}

    def final(self, end=None, force=False):
        end=self.end if end is None else min(end,self.end)
        start=self.last_final
        if self.enrollment is None:return self.service.identify([],None)
        if start<self.start or end<=start:
            self.last_final=max(self.last_final,end)
            return {'speaker':'uncertain','label':LABELS['uncertain'],'identified':False,'reason':'missing_audio_window','method':METHOD}
        audio=self.samples[start-self.start:end-self.start].copy()
        result=self.service.identify(audio,self.enrollment)
        # Keep short finalized text/audio together until enough useful speech
        # accumulates. Stop forces an uncertain decision instead of losing text.
        if force or result.get('reason')!='insufficient_speech':
            self.last_final=max(self.last_final,end)
        result.update(audio_start_s=round(start/16000,3),audio_end_s=round(end/16000,3),
                      timestamp_exact=False)
        return result
