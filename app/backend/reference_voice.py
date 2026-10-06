"""Experimental reference-prefix diarization; never infer identity from ID order."""
import io
import math
from collections import defaultdict

import numpy as np
import soundfile as sf

from .voice import wav_bytes

SEPARATOR_SECONDS = 1


def enrollment_wav(samples):
    samples = np.asarray(samples, dtype='float32')
    duration = len(samples) / 16000
    if not 8 <= duration <= 15:
        raise ValueError('تحدث وحدك لمدة ٨ إلى ١٥ ثانية، ثم أعد التسجيل.')
    if not np.isfinite(samples).all() or np.sqrt(np.mean(samples**2)) < .003:
        raise ValueError('العينة منخفضة الصوت. تحدث بوضوح وأعد التسجيل.')
    # Reject clips dominated by silence, rather than saving an empty reference.
    frames = [samples[i:i+3200] for i in range(0, len(samples), 3200)]
    if sum(np.sqrt(np.mean(f**2)) >= .003 for f in frames) < len(frames) * .5:
        raise ValueError('العينة تحتوي على صمت طويل. تحدث طوال مدة التسجيل.')
    return wav_bytes(samples), duration


def prefix_audio(samples, reference=None):
    if reference is None:
        return wav_bytes(samples), 0., 0.
    recorded, rate = sf.read(io.BytesIO(reference), dtype='float32')
    if rate != 16000 or recorded.ndim != 1:
        raise ValueError('العينة المسجلة غير صالحة. أعد تسجيل صوتك.')
    duration = len(recorded) / rate
    if not 8 <= duration <= 15:
        raise ValueError('العينة المسجلة غير صالحة. أعد تسجيل صوتك.')
    offset = duration + SEPARATOR_SECONDS
    combined = np.concatenate([recorded, np.zeros(16000 * SEPARATOR_SECONDS, dtype='float32'), samples])
    return wav_bytes(combined), duration, offset


def _span(item):
    try:
        start, end = float(item['start']), float(item['end'])
    except (TypeError, ValueError, KeyError):
        return None
    return (start, end) if math.isfinite(start) and math.isfinite(end) and 0 <= start < end else None


def _speaker(item):
    value = item.get('speaker')
    if isinstance(value, bool) or value is None:
        return None
    return str(value) if isinstance(value, (str, int)) and str(value) else None


def extract_turns(data, reference_duration=0., offset=0.):
    """Remove reference words by time; missing labels stay explicitly uncertain.

    Provider responses can put speaker IDs on words or segments. Do not use
    the full combined text as a fallback: that would leak enrollment into search.
    """
    segments = data.get('segments', [])
    segments = segments if isinstance(segments, list) else []
    items = []
    for word in data.get('words', []) or []:
        if not isinstance(word, dict) or not _span(word) or not isinstance(word.get('word'), str):
            continue
        start, end = _span(word)
        speaker = _speaker(word)
        if speaker is None:
            labels = set()
            for segment in segments:
                if not isinstance(segment, dict) or not _span(segment):
                    continue
                a, b = _span(segment)
                if a <= (start+end)/2 < b and _speaker(segment) is not None:
                    labels.add(_speaker(segment))
            if len(labels) == 1:
                speaker = labels.pop()
        items.append({'start': start, 'end': end, 'text': word['word'].strip(), 'speaker': speaker})
    if not items:
        for segment in segments:
            if isinstance(segment, dict) and _span(segment) and isinstance(segment.get('text'), str):
                start, end = _span(segment)
                # A segment spanning the reference boundary cannot be safely cut.
                if offset and start < offset < end:
                    continue
                items.append({'start': start, 'end': end, 'text': segment['text'].strip(), 'speaker': _speaker(segment)})
    items.sort(key=lambda item: (item['start'], item['end']))
    reference_scores = defaultdict(float)
    for item in items:
        if item['speaker'] is not None and item['end'] <= reference_duration + .03:
            reference_scores[item['speaker']] += item['end'] - item['start']
    reference_label = None
    if reference_scores:
        label = max(reference_scores, key=reference_scores.get)
        total_reference = sum(i['end']-i['start'] for i in items if i['end'] <= reference_duration+.03)
        if total_reference and reference_scores[label] / total_reference >= .8:
            reference_label = label
    turns, others = [], {}
    for item in items:
        if item['start'] < offset - .03 or not item['text']:
            continue
        label = item['speaker']
        if reference_label is not None and label == reference_label:
            speaker, display = 'enrolled', 'صوتي · تجريبي'
        elif label is not None and (reference_label is not None or not reference_duration):
            # Other IDs are local to this clip; never claim cross-clip identity.
            others.setdefault(label, len(others) + 1)
            speaker, display = f'other-{others[label]}', f'متحدث آخر · {others[label]}'
        else:
            speaker, display = 'uncertain', 'الصوت غير مؤكد'
        if turns and turns[-1]['speaker'] == speaker and turns[-1]['provider_speaker'] == label:
            turns[-1]['text'] += ' ' + item['text']
            turns[-1]['end'] = item['end'] - offset
        else:
            turns.append({'text': item['text'], 'speaker': speaker, 'label': display,
                          'provider_speaker': label, 'start': max(0., item['start']-offset), 'end': item['end']-offset})
    # Without a reference, plain transcription is safe to display as uncertain.
    if not turns and not offset and isinstance(data.get('text'), str) and data['text'].strip():
        turns = [{'text': data['text'].strip(), 'speaker': 'uncertain', 'label': 'الصوت غير مؤكد',
                  'provider_speaker': None, 'start': None, 'end': None}]
    return turns, {'method': 'gemini-reference-prefix', 'experimental': True,
                   'reference_seconds': reference_duration, 'reference_label': reference_label,
                   'speaker_labels_returned': any(i['speaker'] is not None for i in items),
                   'reference_removed': bool(offset), 'timestamp_trim_failed': bool(offset and not turns)}
