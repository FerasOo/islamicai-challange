"""Backend-only Gemini Live connection; no speaker identification is inferred."""
import asyncio
import base64
import json
import time

import numpy as np
from websockets.asyncio.client import connect

from .config import GEMINI_KEY, LIVE_STT_MODEL
from .providers import ProviderError
from .voice import pcm16k

LIVE_URL = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent'


def setup_message(model=LIVE_STT_MODEL):
    return {'setup': {
        'model': 'models/' + model.removeprefix('models/'),
        'generationConfig': {'responseModalities': ['TEXT']},
        # Arabic is a supported language hint, not post-processing of the text.
        'inputAudioTranscription': {'languageCodes': ['ar-EG'], 'mode': 'VERBATIM'},
    }}


def audio_message(samples, rate):
    mono = pcm16k(samples, rate)
    pcm = (np.clip(mono, -1, 1) * 32767).astype('<i2').tobytes()
    return {'realtimeInput': {'audio': {
        'data': base64.b64encode(pcm).decode('ascii'),
        'mimeType': 'audio/pcm;rate=16000',
    }}}


class LiveTranscriber:
    def __init__(self, on_event, key=GEMINI_KEY, connector=connect):
        self.on_event = on_event
        self.key = key
        self.connector = connector
        self.socket = None
        self.receiver = None
        self.lock = asyncio.Lock()
        self.stopping = False
        self.drained = asyncio.Event()
        self.started = 0.
        self.pending = False
        self.renew = False
        self.idle_task = None
        self.last_update = 0.
        self.revision = 0
        self.flushed_revision = -1
        self.last_text = ''

    async def start(self):
        if not self.key:
            raise ProviderError('أضف GEMINI_API_KEY إلى ملف .env ثم أعد تشغيل التطبيق.')
        try:
            # Keep the key out of the URL, browser, stored events and error logs.
            self.socket = await self.connector(LIVE_URL,
                additional_headers={'x-goog-api-key': self.key},
                open_timeout=12, close_timeout=3, max_size=2_000_000)
            await self.socket.send(json.dumps(setup_message()))
            response = json.loads(await asyncio.wait_for(self.socket.recv(), 12))
            if 'setupComplete' not in response:
                raise ProviderError('تعذر تهيئة Gemini Live. تحقق من صلاحية المفتاح وإتاحة النموذج.')
            self.started = time.monotonic()
            self.renew = False
            self.receiver = asyncio.create_task(self._receive())
            self.idle_task = asyncio.create_task(self._flush_idle())
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise ProviderError('تعذر الاتصال بـ Gemini Live. تحقق من المفتاح وإتاحة النموذج وحصة الاستخدام.') from None

    async def _receive(self):
        try:
            async for raw in self.socket:
                response = json.loads(raw)
                if response.get('error'):
                    raise ProviderError('رفضت خدمة Gemini Live الطلب. تحقق من حصة الاستخدام.')
                content = response.get('serverContent', {})
                interim = content.get('interimInputTranscription')
                final = content.get('inputTranscription')
                if interim and interim.get('text'):
                    if not self.pending or interim['text'] != self.last_text:
                        self.last_update = time.monotonic()
                        self.revision += 1
                    self.last_text = interim['text']
                    self.pending = True
                    await self.on_event({'type': 'transcript_partial', 'text': interim['text']})
                if final and final.get('text'):
                    self.pending = False
                    await self.on_event({'type': 'transcript_final', 'text': final['text']})
                    if self.stopping:self.drained.set()
                if content.get('turnComplete'):
                    self.drained.set()
                if response.get('goAway'):
                    self.renew = True
                    await self.on_event({'type': 'live_notice', 'message': 'ستُجدّد جلسة التفريغ الصوتي قريباً.'})
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.stopping:
                await self.on_event({'type': 'live_error', 'message': 'انقطع اتصال Gemini Live. شغّل الميكروفون لإعادة الاتصال.'})
        finally:
            if not self.stopping:
                await self.on_event({'type': 'live_closed'})

    async def _flush_idle(self):
        # A brief phrase can remain an interim indefinitely while the microphone
        # streams silence. Ask Google to finalize after two seconds without new
        # transcript text. Never manufacture a final from an interim ourselves.
        while True:
            await asyncio.sleep(.25)
            if (not self.stopping and self.pending and self.revision != self.flushed_revision
                    and time.monotonic() - self.last_update >= 2):
                self.flushed_revision = self.revision
                try:
                    async with self.lock:
                        if not self.stopping and self.socket:await self.flush()
                except Exception:
                    await self.on_event({'type':'live_error','message':'تعذر تثبيت العبارة. أعد تشغيل الميكروفون.'})
                    return

    async def send_audio(self, samples, rate):
        async with self.lock:
            if not self.socket or not self.receiver or self.receiver.done():
                raise ProviderError('اتصال التفريغ الصوتي مغلق. شغّل الميكروفون مجدداً.')
            # Reopen before Google's ten-minute session limit. The boundary is
            # explicit to the user rather than silently pretending continuity.
            if self.renew or time.monotonic() - self.started >= 540:
                await self.on_event({'type': 'live_notice', 'message': 'جارٍ تجديد اتصال التفريغ الصوتي…'})
                await self.finish()
                await self.start()
            try:
                await self.socket.send(json.dumps(audio_message(samples, rate)))
            except Exception:
                raise ProviderError('تعذر إرسال الصوت إلى Gemini Live. أعد تشغيل الميكروفون.') from None

    async def flush(self):
        if self.socket:
            self.drained.clear()
            await self.socket.send(json.dumps({'realtimeInput': {'audioStreamEnd': True}}))

    async def finish(self):
        if not self.socket:
            return
        self.stopping = True
        try:
            await self.flush()
            # Allow the service to commit the last spoken phrase after Stop.
            await asyncio.wait_for(self.drained.wait(), 3)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.pending:
                await self.on_event({'type':'live_notice','message':'لم يكتمل تثبيت آخر عبارة. أعد قولها أو اكتبها يدوياً.'})
        finally:
            await self.close()
            await self.on_event({'type': 'transcript_partial', 'text': ''})

    async def close(self):
        self.stopping = True
        if self.idle_task and self.idle_task is not asyncio.current_task():
            self.idle_task.cancel()
            await asyncio.gather(self.idle_task, return_exceptions=True)
        self.idle_task = None
        if self.socket:
            await self.socket.close()
        if self.receiver and self.receiver is not asyncio.current_task():
            self.receiver.cancel()
            await asyncio.gather(self.receiver, return_exceptions=True)
        self.socket = None
        self.receiver = None
        self.stopping = False
        self.pending = False
