import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
load_dotenv(PROJECT / '.env', override=False)
load_dotenv(ROOT / '.env', override=False)
RUNTIME = ROOT / 'runtime'
RUNTIME.mkdir(exist_ok=True)
os.environ.setdefault('HF_HOME', str(RUNTIME / 'hf-cache'))
os.environ.setdefault('TORCH_HOME', str(RUNTIME / 'torch-cache'))
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY', '')
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '') or os.getenv('GOOGLE_API_KEY', '')
LIVE_STT_MODEL = os.getenv('DALEEL_LIVE_STT_MODEL', 'gemini-3.5-transcribe-live')
STT_TRANSPORT = os.getenv('DALEEL_STT_TRANSPORT', 'gemini-live' if GEMINI_KEY else 'interval-clips')
STT_MODEL = os.getenv('DALEEL_STT_MODEL', 'google/gemini-3.5-transcribe')
JEV_MODEL = os.getenv('DALEEL_JEV_MODEL', 'typesafe/jev-1.13')
CANDIDATE_LIMIT = 200
# Calibrated against the frozen hard-question benchmark; cosine, not confidence.
RETRIEVAL_MINIMUM = .50
JEV_ACCEPTANCE = .75
TRIGGER_MINIMUM = .50
VOICE_MATCH = float(os.getenv('DALEEL_VOICE_MATCH', '.55'))
SPEAKER_ACCEPT = float(os.getenv('DALEEL_SPEAKER_ACCEPT', '.43'))
SPEAKER_REJECT = float(os.getenv('DALEEL_SPEAKER_REJECT', '.30'))
if not -1 <= SPEAKER_REJECT < SPEAKER_ACCEPT <= 1:
    raise ValueError('Speaker thresholds must satisfy -1 <= reject < accept <= 1')
SOURCE_NAMES = {
 'quran':'القرآن الكريم', 'tafsir-mujahid':'تفسير مجاهد', 'bukhari':'صحيح البخاري',
 'muslim':'صحيح مسلم', 'hadeethenc':'شرح الأحاديث', 'ibn-baz':'فتاوى ابن باز',
 'ibn-uthaymeen':'فتاوى ابن عثيمين', 'kuwait-fiqh':'الموسوعة الفقهية',
 'bayinat':'بينات', 'terminology':'المصطلحات الشرعية',
}
# Keep archived names for saved provenance; exclude these sources from the app.
ACTIVE_SOURCE_NAMES = {s:name for s,name in SOURCE_NAMES.items() if s!='bayinat'}
