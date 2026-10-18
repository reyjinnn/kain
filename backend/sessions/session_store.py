from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

RETENTION_DAYS = 30
RETENTION_S = RETENTION_DAYS * 86400
PROJECT_LIMIT = 20
EVENT_LIMIT = 500
EVENTS = frozenset({'start', 'story_sent', 'design_ready', 'download', 'error'})


class LocalStore:
    """Private local storage; project IDs never substitute for cookie ownership."""
    def __init__(self, directory=None):
        self.lock = threading.RLock()
        self.memory = directory is None
        if self.memory:
            self.path = ':memory:'
        else:
            folder = Path(directory).expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            if os.name != 'nt':
                folder.chmod(0o700)
            self.path = str(folder / 'sisa.sqlite3')
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        if not self.memory and os.name != 'nt':
            Path(self.path).chmod(0o600)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS autosaves (
                owner TEXT PRIMARY KEY, document TEXT NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                document TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, anonymous_id TEXT NOT NULL,
                event TEXT NOT NULL, recorded_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_anon ON events(anonymous_id);
        ''')
        self.db.commit()

    def cleanup(self, now=None):
        cutoff = (now if now is not None else time.time()) - RETENTION_S
        with self.lock:
            self.db.execute('DELETE FROM autosaves WHERE updated_at < ?', (cutoff,))
            self.db.execute('DELETE FROM events WHERE recorded_at < ?', (cutoff,))
            # Saved projects last for this browser identity's inactivity window.
            self.db.execute('DELETE FROM projects WHERE owner NOT IN (SELECT owner FROM autosaves)')
            self.db.commit()

    def load_autosave(self, owner):
        with self.lock:
            row = self.db.execute('SELECT document, updated_at FROM autosaves WHERE owner=?', (owner,)).fetchone()
            if not row or row[1] < time.time() - RETENTION_S:
                return None
            try:
                document = json.loads(row[0])
                return (document, row[1]) if isinstance(document, dict) else None
            except (ValueError, TypeError):
                return None

    def autosave(self, owner, document):
        now = time.time()
        encoded = json.dumps(document, ensure_ascii=False, separators=(',', ':'))
        with self.lock:
            self.db.execute('INSERT INTO autosaves(owner, document, updated_at) VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET document=excluded.document, updated_at=excluded.updated_at', (owner, encoded, now))
            self.db.commit()
        return now

    def touch_autosave(self, owner):
        now = time.time()
        with self.lock:
            self.db.execute('UPDATE autosaves SET updated_at=? WHERE owner=?', (now, owner))
            self.db.commit()
        return now

    def list_projects(self, owner):
        with self.lock:
            rows = self.db.execute('SELECT id,name,created_at,updated_at FROM projects WHERE owner=? ORDER BY updated_at DESC', (owner,)).fetchall()
        return [dict(zip(('id','name','created_at','updated_at'), row)) for row in rows]

    def save_project(self, owner, name, document):
        now = time.time()
        with self.lock:
            # Reusing a name replaces only this owner's saved project.
            existing = self.db.execute('SELECT id,created_at FROM projects WHERE owner=? AND name=?', (owner,name)).fetchone()
            if not existing and len(self.list_projects(owner)) >= PROJECT_LIMIT:
                raise ValueError('PROJECT_LIMIT')
            project_id, created_at = existing if existing else (str(uuid4()), now)
            self.db.execute('INSERT INTO projects(id,owner,name,document,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document, updated_at=excluded.updated_at', (project_id,owner,name,json.dumps(document,ensure_ascii=False),created_at,now))
            self.db.commit()
        return {'id':project_id,'name':name,'created_at':created_at,'updated_at':now}

    def open_project(self, owner, project_id):
        with self.lock:
            row = self.db.execute('SELECT document FROM projects WHERE owner=? AND id=?', (owner,project_id)).fetchone()
        try:
            document = json.loads(row[0]) if row else None
            return document if isinstance(document, dict) else None
        except (ValueError, TypeError):
            return None

    def delete_project(self, owner, project_id):
        with self.lock:
            cursor = self.db.execute('DELETE FROM projects WHERE owner=? AND id=?', (owner,project_id))
            self.db.commit()
        return cursor.rowcount == 1

    def record_event(self, anonymous_id, event):
        if event not in EVENTS:
            raise ValueError('INVALID_EVENT')
        with self.lock:
            count = self.db.execute('SELECT COUNT(*) FROM events WHERE anonymous_id=?', (anonymous_id,)).fetchone()[0]
            if count >= EVENT_LIMIT:
                return False
            self.db.execute('INSERT INTO events(anonymous_id,event,recorded_at) VALUES(?,?,?)', (anonymous_id,event,time.time()))
            self.db.commit()
        return True

    def forget_events(self, anonymous_id):
        with self.lock:
            self.db.execute('DELETE FROM events WHERE anonymous_id=?', (anonymous_id,))
            self.db.commit()
