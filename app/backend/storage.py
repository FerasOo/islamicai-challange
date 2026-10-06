import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from .config import RUNTIME


class Storage:
    def __init__(self, path=None):
        self.path = path or RUNTIME / 'sessions.sqlite'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, session_id TEXT, type TEXT, data TEXT, created TEXT);
            CREATE INDEX IF NOT EXISTS events_session ON events(session_id,id);
            CREATE TABLE IF NOT EXISTS voices(session_id TEXT PRIMARY KEY, embedding TEXT);
            CREATE TABLE IF NOT EXISTS voice_samples(session_id TEXT PRIMARY KEY, wav BLOB NOT NULL, duration REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS voice_profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, wav BLOB, embedding TEXT, duration REAL, created TEXT, updated TEXT);
            CREATE TABLE IF NOT EXISTS selected_voice_profiles(session_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bookmarks(session_id TEXT, chunk_id TEXT, data TEXT, PRIMARY KEY(session_id,chunk_id));
            ''')

            # Preserve old session references as reusable profiles, deduplicated by WAV.
            existing = {hashlib.sha256(r[1]).hexdigest():r[0] for r in db.execute('SELECT id,wav FROM voice_profiles WHERE wav IS NOT NULL')}
            for sid,wav,duration,embedding in db.execute('SELECT s.session_id,s.wav,s.duration,v.embedding FROM voice_samples s LEFT JOIN voices v ON v.session_id=s.session_id').fetchall():
                if db.execute('SELECT 1 FROM selected_voice_profiles WHERE session_id=?',(sid,)).fetchone():continue
                digest=hashlib.sha256(wav).hexdigest();pid=existing.get(digest)
                if not pid:
                    pid=str(uuid.uuid4());now=datetime.now(timezone.utc).isoformat()
                    db.execute('INSERT INTO voice_profiles VALUES(?,?,?,?,?,?,?)',(pid,'المتحدث المسجّل',wav,embedding,duration,now,now));existing[digest]=pid
                db.execute('INSERT INTO selected_voice_profiles VALUES(?,?)',(sid,pid))

    def connect(self):
        return sqlite3.connect(self.path)

    def create(self, title='جلسة حوار جديدة'):
        session = {'id':str(uuid.uuid4()),'title':title,'created':datetime.now(timezone.utc).isoformat()}
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(:id,:title,:created)',session)
        return session

    def exists(self, sid):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone())

    def list(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('''SELECT s.*,
                (SELECT count(*) FROM bookmarks b WHERE b.session_id=s.id) saved,
                EXISTS(SELECT 1 FROM events e WHERE e.session_id=s.id AND e.type IN ('candidates','search_done')) has_search
                FROM sessions s ORDER BY created DESC''')]

    def event(self, sid, event):
        with self.connect() as db:
            db.execute('INSERT INTO events(session_id,type,data,created) VALUES(?,?,?,?)',
                (sid,event['type'],json.dumps(event,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))

    def detail(self, sid):
        with self.connect() as db:
            return {'events':[json.loads(r[0]) for r in db.execute('SELECT data FROM events WHERE session_id=? ORDER BY id',(sid,))],
                'bookmarks':[json.loads(r[0]) for r in db.execute('SELECT data FROM bookmarks WHERE session_id=?',(sid,))],
                'enrolled':bool(self.voice_sample(sid)),
                'voice_profile':self.selected_profile(sid),
                'legacy_voice':bool(db.execute('SELECT 1 FROM voices WHERE session_id=?',(sid,)).fetchone())}

    def voice_sample(self, sid, wav=None, duration=None):
        profile=self.selected_profile(sid)
        if profile:
            with self.connect() as db:
                if wav is not None:db.execute('UPDATE voice_profiles SET wav=?,duration=?,updated=? WHERE id=?',(wav,duration,datetime.now(timezone.utc).isoformat(),profile['id']))
                row=db.execute('SELECT wav FROM voice_profiles WHERE id=?',(profile['id'],)).fetchone()
                return row[0] if row else None
        with self.connect() as db:
            if wav is not None:
                db.execute('INSERT OR REPLACE INTO voice_samples VALUES(?,?,?)',(sid,wav,duration))
            row = db.execute('SELECT wav FROM voice_samples WHERE session_id=?',(sid,)).fetchone()
            return row[0] if row else None

    def voice(self, sid, vector=None):
        profile=self.selected_profile(sid)
        if profile:
            with self.connect() as db:
                if vector is not None:db.execute('UPDATE voice_profiles SET embedding=? WHERE id=?',(json.dumps(vector),profile['id']))
                row=db.execute('SELECT embedding FROM voice_profiles WHERE id=?',(profile['id'],)).fetchone()
                return json.loads(row[0]) if row and row[0] else None
        with self.connect() as db:
            if vector is not None:
                db.execute('INSERT OR REPLACE INTO voices VALUES(?,?)',(sid,json.dumps(vector)))
            r = db.execute('SELECT embedding FROM voices WHERE session_id=?',(sid,)).fetchone()
            return json.loads(r[0]) if r else None

    def remove_voice(self, sid):
        profile=self.selected_profile(sid)
        if profile:
            with self.connect() as db:db.execute('UPDATE voice_profiles SET wav=NULL,embedding=NULL,duration=NULL WHERE id=?',(profile['id'],))
        with self.connect() as db:
            db.execute('DELETE FROM voices WHERE session_id=?',(sid,))
            db.execute('DELETE FROM voice_samples WHERE session_id=?',(sid,))

    def bookmark(self, sid, chunk, data=None):
        with self.connect() as db:
            if data is None:
                db.execute('DELETE FROM bookmarks WHERE session_id=? AND chunk_id=?',(sid,chunk))
            else:
                db.execute('INSERT OR REPLACE INTO bookmarks VALUES(?,?,?)',(sid,chunk,json.dumps(data,ensure_ascii=False)))

    def delete(self, sid):
        with self.connect() as db:
            for table in ['events','voices','voice_samples','bookmarks','selected_voice_profiles']:
                db.execute(f'DELETE FROM {table} WHERE session_id=?',(sid,))
            db.execute('DELETE FROM sessions WHERE id=?',(sid,))

    def profiles(self):
        with self.connect() as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT id,name,created,updated,(wav IS NOT NULL) has_voice FROM voice_profiles ORDER BY created')]

    def profile(self, pid):
        return next((p for p in self.profiles() if p['id']==pid),None)

    def create_profile(self, name):
        pid=str(uuid.uuid4());now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:db.execute('INSERT INTO voice_profiles VALUES(?,?,NULL,NULL,NULL,?,?)',(pid,name,now,now))
        return self.profile(pid)

    def rename_profile(self, pid, name):
        with self.connect() as db:db.execute('UPDATE voice_profiles SET name=?,updated=? WHERE id=?',(name,datetime.now(timezone.utc).isoformat(),pid))
        return self.profile(pid)

    def selected_profile(self, sid):
        with self.connect() as db:
            row=db.execute('SELECT profile_id FROM selected_voice_profiles WHERE session_id=?',(sid,)).fetchone()
        return self.profile(row[0]) if row else None

    def select_profile(self, sid, pid):
        if not self.profile(pid):raise ValueError('الملف الصوتي غير موجود.')
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO selected_voice_profiles VALUES(?,?)',(sid,pid))

    def bookmarks(self):
        with self.connect() as db:
            hits={}
            for sid,raw in db.execute('SELECT session_id,data FROM bookmarks ORDER BY rowid'):
                hit=json.loads(raw);hit['session_id']=sid;hits[hit['id']]=hit
            return list(hits.values())

    def remove_bookmark(self, chunk):
        with self.connect() as db:db.execute('DELETE FROM bookmarks WHERE chunk_id=?',(chunk,))

    def save_profile_recording(self, sid, name, wav, metadata, duration, pid=None):
        """Publish a complete named recording and select it in one transaction."""
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            if pid:
                if not db.execute('SELECT 1 FROM voice_profiles WHERE id=?',(pid,)).fetchone():
                    raise ValueError('الملف الصوتي غير موجود.')
                db.execute('UPDATE voice_profiles SET name=?,wav=?,embedding=?,duration=?,updated=? WHERE id=?',
                    (name,wav,json.dumps(metadata) if metadata else None,duration,now,pid))
            else:
                pid=str(uuid.uuid4())
                db.execute('INSERT INTO voice_profiles VALUES(?,?,?,?,?,?,?)',
                    (pid,name,wav,json.dumps(metadata) if metadata else None,duration,now,now))
            db.execute('INSERT OR REPLACE INTO selected_voice_profiles VALUES(?,?)',(sid,pid))
        return self.profile(pid)
