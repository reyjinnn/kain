from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from backend.services import ai
from backend.design import engine
from backend.sessions.session_store import LocalStore, RETENTION_DAYS, RETENTION_S

class SessionManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.alive = True
        self.autosaved_at = None
        self.telemetry_enabled = False
        self.anonymous_id = str(uuid.uuid4())
        self.image_calls = 0
        self.session_id = str(uuid.uuid4())
        self.data = engine.fixture_data()
        self.generation_token = 1
        self.job_token = str(uuid.uuid4())
        self.ai_job_token = str(uuid.uuid4())
        self.candidate = None
        self.active_revision = None
        self.consent = None
        self.previous_revision = None
        self.ai_trace = None
        self.image = None
        self.image_bytes = None
        self.image_job_token = str(uuid.uuid4())
        self.clear_image_model()
        self.failure = None
        self.search = None
        self.stale = False
        self.planning = False
        self.chat_history = []
        self.chat_summary = None
        self.chat_calls = 0
        self.image_cache = {}
        self.image_attempts = {}

    def clear_image_model(self):
        self.image_model = None
        self.image_model_bytes = None
        self.image_model_job_token = str(uuid.uuid4())

    def signature(self):
        with self.lock:
            return engine.signature(self.data, self.consent)

    def remember(self):
        if self.active_revision is not None:
            self.previous_revision = {"data": deepcopy(self.data), "revision": deepcopy(self.active_revision)}

    def invalidate(self, bump=True):
        with self.lock:
            self.remember()
            if bump:
                self.generation_token += 1
            self.job_token = str(uuid.uuid4())
            self.ai_job_token = str(uuid.uuid4())
            self.candidate = None
            self.active_revision = None
            self.consent = None
            self.ai_trace = None
            self.image = None
            self.image_bytes = None
            self.image_job_token = str(uuid.uuid4())
            self.clear_image_model()
            self.failure = None
            self.search = None
            self.stale = True
            self.planning = False

    def refresh(self):
        sig = self.signature()
        for revision in (self.candidate, self.active_revision):
            if revision is not None and (revision.get("input_signature") != sig or revision.get("construction_status") != engine.t.REVIEW_STATUS):
                self.invalidate()
                break

    def phase(self):
        if self.planning:
            return "planning"
        if self.stale:
            return "stale"
        if self.active_revision:
            return "current_checked"
        if self.candidate:
            return "awaiting_change_acceptance" if self.candidate["variant"] == "front_split_center" else "candidate_checked"
        return "confirmed_input" if confirmed(self.data) else "draft_input"

    def payload(self):
        with self.lock:
            self.refresh()
            return deepcopy({
                "session_id": self.session_id,
                "generation_token": self.generation_token,
                "job_token": self.job_token,
                "data": self.data,
                "signature": self.signature(),
                "candidate": self.candidate,
                "active_revision": self.active_revision,
                "consent": self.consent,
                "phase": self.phase(),
                "stale": self.stale,
                "previous_revision": self.previous_revision,
                "ai_trace": self.ai_trace,
                "image": self.image,
                "image_model": self.image_model,
                "failure": self.failure,
                "search": self.search,
                "chat_history": self.chat_history,
                "chat_summary": self.chat_summary,
                "telemetry_enabled": self.telemetry_enabled,
                "persistence": {"autosaved_at": self.autosaved_at, "retention_days": RETENTION_DAYS},
            })


def confirmed(data):
    return data["request"]["confirmed"] and all(m["confirmed"] for m in data["materials"] if m["available"])



def document(session, include_image=True):
    with session.lock:
        session.refresh()
        fields = ('data', 'generation_token', 'candidate', 'active_revision', 'consent',
                  'previous_revision', 'ai_trace', 'image', 'failure', 'search', 'stale',
                  'chat_history', 'chat_summary', 'chat_calls', 'image_attempts', 'image_calls',
                  'telemetry_enabled', 'anonymous_id')
        result = {key: deepcopy(getattr(session, key)) for key in fields}
        result['record_version'] = 1
        # Store only the active illustration, never the unbounded model responses.
        if include_image and session.image_bytes and len(session.image_bytes) <= 12 * 1024 * 1024:
            result['image_base64'] = base64.b64encode(session.image_bytes).decode('ascii')
        return result


def restore(record):
    session = SessionManager()
    if not isinstance(record, dict) or record.get('record_version') != 1:
        return session
    try:
        session.data = engine.validate_input(record['data'])
    except (KeyError, ValueError, TypeError, engine.EngineError):
        return session
    def bounded_integer(key, minimum, maximum, default=0):
        value = record.get(key, default)
        return max(minimum, min(maximum, value)) if type(value) is int else default
    session.generation_token = bounded_integer('generation_token', 1, 2**53 - 1, 1) + 1
    history = record.get('chat_history', [])
    session.chat_history = [deepcopy(item) for item in history if isinstance(item, dict) and item.get('role') in ('user','assistant') and isinstance(item.get('text'), str) and len(item['text']) <= 2000][-20:] if isinstance(history, list) else []
    session.chat_summary = deepcopy(record.get('chat_summary'))
    session.chat_calls = bounded_integer('chat_calls', 0, 20)
    session.image_calls = bounded_integer('image_calls', 0, 20)
    session.telemetry_enabled = record.get('telemetry_enabled') is True
    identity = record.get('anonymous_id')
    session.anonymous_id = identity if isinstance(identity, str) and re.fullmatch(r'[a-z0-9-]{36}', identity) else str(uuid.uuid4())
    previous = record.get('previous_revision')
    session.previous_revision = deepcopy(previous) if isinstance(previous, dict) and isinstance(previous.get('revision'), dict) and isinstance(previous.get('data'), dict) else None
    session.ai_trace = deepcopy(record.get('ai_trace'))
    session.search = deepcopy(record.get('search'))
    session.failure = deepcopy(record.get('failure'))
    session.consent = deepcopy(record.get('consent'))
    session.stale = record.get('stale') is True
    old_revision_id = None
    for field in ('candidate', 'active_revision'):
        candidate = deepcopy(record.get(field))
        if not candidate:
            continue
        if not isinstance(candidate, dict):
            session.candidate = None
            session.active_revision = None
            session.consent = None
            session.stale = True
            break
        try:
            sig = session.signature()
            report = engine.validate_candidate(session.data, candidate, sig, consent=session.consent,
                                               activation=field == 'active_revision')
            valid = report['passed'] if field == 'active_revision' else report['geometry_passed']
            if not valid or candidate.get('construction_status') != engine.t.REVIEW_STATUS:
                raise ValueError('STORED_PLAN_INVALID')
            old_revision_id = candidate.get('design_revision_id')
            candidate.update(validation_report={**report, 'search': deepcopy(candidate.get('search', {}))},
                             generation_token=session.generation_token, job_token=session.job_token)
            setattr(session, field, candidate)
        except (engine.EngineError, KeyError, TypeError, ValueError):
            session.candidate = None
            session.active_revision = None
            session.consent = None
            session.stale = True
            break
    attempts = record.get('image_attempts', {})
    session.image_attempts = {key: min(4, max(0, value)) for key, value in attempts.items() if isinstance(key, str) and type(value) is int} if isinstance(attempts, dict) else {}
    revision = session.active_revision or session.candidate
    image = record.get('image')
    if revision and isinstance(image, dict) and image.get('input_signature') == session.signature() and image.get('revision_id') == old_revision_id:
        try:
            raw = record.get('image_base64', '')
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('IMAGE_TOO_LARGE')
            decoded = base64.b64decode(raw, validate=True)
            if decoded:
                session.image_bytes = decoded
                session.image = {**deepcopy(image), 'image_id': session.image_job_token}
                cache_key = (session.signature(), hashlib.sha256(ai.image_prompt(session.data, revision).encode()).hexdigest())
                session.image_cache[cache_key] = (decoded, deepcopy(session.image))
        except (ValueError, TypeError):
            pass
    return session


@dataclass
class Entry:
    session: SessionManager
    touched_at: float
    inflight: int = 0
    document_fingerprint: str = ''


_CURRENT = ContextVar('sisa_session', default=None)
_REGISTRY = ContextVar('sisa_registry', default=None)
_OWNER = ContextVar('sisa_owner', default=None)


def current_session():
    session = _CURRENT.get()
    if session is None:
        raise RuntimeError('Session endpoint called outside a browser request')
    return session


def current_registry():
    return _REGISTRY.get()


def current_owner():
    return _OWNER.get()


class SessionRegistry:
    def __init__(self, store, ttl_s=RETENTION_S):
        self.store = store
        self.ttl_s = ttl_s
        self.lock = threading.RLock()
        self.entries = {}

    @staticmethod
    def owner(cookie):
        return hashlib.sha256(cookie.encode('ascii')).hexdigest()

    def resolve(self, cookie):
        now = time.time()
        with self.lock:
            for owner, entry in list(self.entries.items()):
                if now - entry.touched_at >= self.ttl_s:
                    entry.session.alive = False
                    del self.entries[owner]
            # Evict idle memory entries only; their autosave can be restored.
            if len(self.entries) >= 256:
                idle = sorted(((owner, entry) for owner, entry in self.entries.items() if not entry.inflight), key=lambda item: item[1].touched_at)
                for expired_owner, entry in idle[:max(1, len(self.entries) - 255)]:
                    entry.session.alive = False
                    del self.entries[expired_owner]
            self.store.cleanup(now)
            valid = isinstance(cookie, str) and re.fullmatch(r'[0-9a-f]{64}', cookie)
            owner = self.owner(cookie) if valid else None
            if owner in self.entries:
                self.entries[owner].touched_at = now
                self.entries[owner].inflight += 1
                return cookie, owner, self.entries[owner].session
            saved = self.store.load_autosave(owner) if owner else None
            if saved:
                session = restore(saved[0])
                session.autosaved_at = saved[1]
            else:
                cookie = secrets.token_hex(32)
                owner = self.owner(cookie)
                session = SessionManager()
            self.entries[owner] = Entry(session, now, 1)
            return cookie, owner, session

    def is_current(self, owner, session):
        with self.lock:
            entry = self.entries.get(owner)
            return session.alive and entry is not None and entry.session is session

    def replace(self, owner, old, replacement):
        from fastapi import HTTPException
        with self.lock:
            if not old.alive or self.entries.get(owner) is None or self.entries[owner].session is not old:
                raise HTTPException(status_code=409, detail={"code": "STALE_REVISION", "message": "Sesi berubah; permintaan lama dibatalkan."})
            old.alive = False
            self.entries[owner] = Entry(replacement, time.time(), self.entries[owner].inflight)
        _CURRENT.set(replacement)
        self.save(owner, replacement)

    def release(self, owner):
        with self.lock:
            entry = self.entries.get(owner)
            if entry:
                entry.inflight = max(0, entry.inflight - 1)

    def save(self, owner, session):
        # Serialize edits from the same browser; another browser has its own lock.
        with session.lock:
            with self.lock:
                if not session.alive or self.entries.get(owner) is None or self.entries[owner].session is not session:
                    return
                entry = self.entries[owner]
                record = document(session, include_image=False)
                # Image bytes are immutable and replaced as a unit; avoid re-encoding them on reads.
                fingerprint = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest() + ':' + str(id(session.image_bytes))
                if fingerprint != entry.document_fingerprint:
                    if session.image_bytes and len(session.image_bytes) <= 12 * 1024 * 1024:
                        record['image_base64'] = base64.b64encode(session.image_bytes).decode('ascii')
                    session.autosaved_at = self.store.autosave(owner, record)
                    entry.document_fingerprint = fingerprint
                elif session.autosaved_at is None or time.time() - session.autosaved_at >= 60:
                    session.autosaved_at = self.store.touch_autosave(owner)


_REGISTRIES = {}
_REGISTRIES_LOCK = threading.RLock()


def registry_for(scope):
    directory = os.getenv('SISA_DATA_DIR', '').strip()
    headers = dict(scope.get('headers', []))
    testing = (scope.get('client') or ('',))[0] == 'testclient'
    if not directory and not testing:
        directory = str(Path(os.getenv('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'SISA' / 'data')
    key = str(Path(directory).expanduser().resolve()) if directory else ':test-memory:'
    with _REGISTRIES_LOCK:
        if key not in _REGISTRIES:
            _REGISTRIES[key] = SessionRegistry(LocalStore(directory or None))
        return _REGISTRIES[key]


class SessionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        request = Request(scope)
        registry = registry_for(scope)
        cookie, owner, session = registry.resolve(request.cookies.get('sisa_sid'))
        session_token = _CURRENT.set(session)
        registry_token = _REGISTRY.set(registry)
        owner_token = _OWNER.set(owner)
        scope.setdefault('state', {})['sisa_owner'] = owner
        scope['state']['sisa_registry'] = registry
        scope['state']['sisa_session'] = session
        try:
            async def session_send(message):
                if message['type'] == 'http.response.start':
                    active = current_session()
                    registry.save(owner, active)
                    secure = '; Secure' if scope.get('scheme') == 'https' else ''
                    value = f'sisa_sid={cookie}; HttpOnly; Max-Age={RETENTION_S}; Path=/; SameSite=Strict{secure}'
                    message = {**message, 'headers': list(message.get('headers', [])) + [(b'set-cookie', value.encode('ascii'))]}
                await send(message)
            await self.app(scope, receive, session_send)
        finally:
            # Streaming chat changes its history after headers have been sent.
            registry.save(owner, current_session())
            registry.release(owner)
            _CURRENT.reset(session_token)
            _REGISTRY.reset(registry_token)
            _OWNER.reset(owner_token)
