"""Local speaker embeddings plus conservative enrollment matching, not ASR."""
import io
import math
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from .config import ROOT, VOICE_MATCH


def pcm16k(samples, rate):
    samples = np.asarray(samples,dtype='float32')
    if rate != 16000:
        gcd = math.gcd(rate,16000)
        samples = resample_poly(samples,16000//gcd,rate//gcd).astype('float32')
    return samples


def wav_bytes(samples):
    stream = io.BytesIO()
    sf.write(stream,samples,16000,format='WAV',subtype='PCM_16')
    return stream.getvalue()


class VoiceModel:
    def __init__(self):
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
        path = (ROOT/'models/speaker-ecapa').resolve()
        torch.set_num_threads(2)
        self.model = EncoderClassifier.from_hparams(source=str(path),
            savedir=str(ROOT/'runtime/voice-loaded'),overrides={'pretrained_path':str(path)},run_opts={'device':'cpu'})

    def encode(self, samples):
        if len(samples) < 16000:
            raise ValueError('العينة الصوتية قصيرة. تحدث بوضوح لمدة أطول.')
        rms = float(np.sqrt(np.mean(samples**2)))
        if rms < .003:
            raise ValueError('الصوت منخفض جداً أو لا توجد عينة كلام واضحة.')
        import torch
        with torch.inference_mode():
            vector = self.model.encode_batch(torch.from_numpy(samples.copy()).unsqueeze(0)).squeeze().numpy()
        return vector/(np.linalg.norm(vector)+1e-9)

    def analyze(self, samples, enrollment=None):
        vector = self.encode(samples)
        scores = []
        if enrollment is not None and len(samples)>=40000:
            # A whole-utterance average can hide a speaker change. Check short
            # overlapping windows before accepting the enrolled voice.
            width = 24000
            starts = list(range(0,len(samples)-width+1,16000))
            if starts[-1] != len(samples)-width:starts.append(len(samples)-width)
            for start in starts:
                part = samples[start:start+width]
                if float(np.sqrt(np.mean(part**2))) >= .003:
                    scores.append(float(self.encode(part) @ enrollment))
        consistent = not scores or (min(scores)>=VOICE_MATCH-.12 and sum(v>=VOICE_MATCH for v in scores)/len(scores)>=.8)
        return vector, {'window_scores':scores,'consistent':consistent}

    def enroll(self, samples):
        if not 8 <= len(samples)/16000 <= 15:
            raise ValueError('اقرأ عينة واضحة بين ٨ و١٥ ثانية.')
        # Several windows reduce dependence on the exact enrollment words.
        vectors = [self.encode(part) for part in np.array_split(samples,3)]
        vector = np.mean(vectors,axis=0)
        return vector/(np.linalg.norm(vector)+1e-9)


class SpeakerTracker:
    def __init__(self, enrollment=None):
        self.enrollment = np.array(enrollment,dtype='float32') if enrollment is not None else None
        self.others = []

    def match(self, vector):
        similarity = float(vector @ self.enrollment) if self.enrollment is not None else None
        if similarity is not None and similarity >= VOICE_MATCH:
            return {'speaker':'enrolled','label':'المتحدث المسجّل','similarity':similarity}
        if similarity is not None and similarity >= VOICE_MATCH-.12:
            return {'speaker':'uncertain','label':'متحدث غير مؤكد','similarity':similarity}
        for i, centroid in enumerate(self.others):
            if float(vector @ centroid) >= .65:
                return {'speaker':f'other-{i+1}','label':f'المتحدث الآخر {i+1}','similarity':similarity}
        self.others.append(vector.copy())
        return {'speaker':f'other-{len(self.others)}','label':f'المتحدث الآخر {len(self.others)}','similarity':similarity}


class Segmenter:
    """RMS VAD, sentence pauses, six-second cap. No silent cloud requests."""
    def __init__(self, rate=48000, max_seconds=6, pause_seconds=.55):
        self.rate = rate
        self.max_seconds = max_seconds
        self.pause_seconds = pause_seconds
        self.parts = []
        self.duration = 0
        self.silence = 0
        self.voiced = 0
        self.preroll = []

    def push(self, chunk):
        seconds = len(chunk)/self.rate
        rms = float(np.sqrt(np.mean(chunk**2))) if len(chunk) else 0
        talking = rms > .012
        if not self.parts and not talking:
            self.preroll.append(chunk)
            while sum(len(p) for p in self.preroll) > self.rate*.15:
                self.preroll.pop(0)
            return None
        if not self.parts:
            self.parts = self.preroll
            self.preroll = []
        self.parts.append(chunk)
        self.duration += seconds
        self.silence = 0 if talking else self.silence+seconds
        self.voiced += seconds if talking else 0
        if self.silence >= self.pause_seconds or self.duration >= self.max_seconds:
            return self.flush()
        return None

    def flush(self):
        result = np.concatenate(self.parts) if self.parts and self.voiced >= .8 else None
        self.parts = [];self.duration = 0;self.silence = 0;self.voiced = 0
        return result
