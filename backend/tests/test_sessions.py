from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from unittest import mock

from fastapi.testclient import TestClient

from backend import app as service
from backend.design import engine
from backend.services import ai
from backend.sessions.sessions import registry_for, RETENTION_S


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='sisa-session-test-')
        self.addCleanup(self.folder.cleanup)
        self.environment = mock.patch.dict(os.environ, {'SISA_DATA_DIR': self.folder.name, 'NINEROUTER_KEY': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.registry = registry_for({'headers': [(b'host', b'testserver')]})
        self.addCleanup(self.close_store)
        self.a = TestClient(service.app)
        self.b = TestClient(service.app)
        self.a.get('/api/state')
        self.b.get('/api/state')

    def close_store(self):
        from backend.sessions import sessions
        with sessions._REGISTRIES_LOCK:
            sessions._REGISTRIES.pop(str(__import__('pathlib').Path(self.folder.name).resolve()), None)
        self.registry.store.db.close()

    def state(self, client=None):
        response = (client or self.a).get('/api/state')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def entry(self, client=None):
        cookie = (client or self.a).cookies.get('sisa_sid')
        return self.registry.entries[self.registry.owner(cookie)].session

    def save(self, name='Pouch biru'):
        response = self.a.post('/api/projects/save', json={'name': name, 'expected_generation_token': self.state()['generation_token']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['project']

    def reopen_registry(self):
        from backend.sessions import sessions
        key = str(__import__('pathlib').Path(self.folder.name).resolve())
        with sessions._REGISTRIES_LOCK:
            previous = sessions._REGISTRIES.pop(key)
            for entry in previous.entries.values():
                entry.session.alive = False
            previous.store.db.close()
        self.registry = registry_for({'headers': [(b'host', b'testserver')]})

    def test_two_browser_owners_have_independent_locks_data_and_reset(self):
        first, second = self.state(), self.state(self.b)
        self.assertNotEqual(first['session_id'], second['session_id'])
        self.assertIsNot(self.entry().lock, self.entry(self.b).lock)
        self.b.post('/api/plan', json={})
        before_b = self.state(self.b)
        data = deepcopy(first['data']); data['request']['H_mm'] = 100
        self.assertEqual(self.a.post('/api/input', json={'data': data}).status_code, 200)
        self.a.post('/api/reset', json={})
        after_b = self.state(self.b)
        self.assertEqual(before_b['data'], after_b['data'])
        self.assertEqual(before_b['active_revision'], after_b['active_revision'])
        self.assertEqual(before_b['generation_token'], after_b['generation_token'])

    def test_cookie_is_opaque_http_only_strict_and_not_client_selected(self):
        response = TestClient(service.app).get('/api/state')
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=2592000', cookie)
        self.assertRegex(cookie, r'sisa_sid=[0-9a-f]{64};')
        attacker = TestClient(service.app)
        fake = 'a' * 64
        attacker.cookies.set('sisa_sid', fake, domain='testserver.local', path='/')
        response = attacker.get('/api/state')
        self.assertNotIn('sisa_sid=' + fake + ';', response.headers['set-cookie'])
        self.assertNotEqual(self.state()['session_id'], response.json()['session_id'])

    def test_project_ownership_includes_list_open_delete(self):
        project = self.save()
        self.assertEqual(self.b.get('/api/projects').json()['projects'], [])
        for route in ('open', 'delete'):
            response = self.b.post('/api/projects/' + route, json={'project_id': project['id']})
            self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.a.get('/api/projects').json()['projects']), 1)
        self.assertEqual(self.a.post('/api/projects/delete', json={'project_id': project['id']}).status_code, 200)
        self.assertEqual(self.a.get('/api/projects').json()['projects'], [])

    def test_autosave_survives_real_store_reopen_and_revalidates_current_plan(self):
        data = self.state()['data']; data['request'].update(W_mm=140, H_mm=100)
        self.a.post('/api/input', json={'data': data}); self.a.post('/api/confirm', json={})
        self.assertEqual(self.a.post('/api/plan', json={}).status_code, 200)
        before = self.state()
        self.reopen_registry()
        after = self.state()
        self.assertEqual(after['data'], before['data'])
        self.assertEqual(after['signature'], before['signature'])
        self.assertGreater(after['generation_token'], before['generation_token'])
        self.assertNotEqual(after['job_token'], before['job_token'])
        self.assertTrue(after['active_revision']['validation_report']['passed'])
        self.assertEqual(self.a.get('/api/export-pdf', params={'revision_sig': after['signature']}).status_code, 200)

    def test_stored_invalid_plan_is_discarded_without_weakening_validator(self):
        self.a.post('/api/plan', json={})
        owner = self.registry.owner(self.a.cookies.get('sisa_sid'))
        record, _ = self.registry.store.load_autosave(owner)
        record['active_revision']['placements'][0]['translation_mm'] = [10000, 10000]
        record['candidate'] = deepcopy(record['active_revision'])
        self.registry.store.autosave(owner, record)
        self.reopen_registry()
        after = self.state()
        self.assertIsNone(after['active_revision'])
        self.assertTrue(after['stale'])
        self.assertEqual(self.a.get('/api/export-pdf', params={'revision_sig': after['signature']}).status_code, 409)

    def test_save_same_name_updates_and_open_preserves_checked_outputs(self):
        self.a.post('/api/plan', json={})
        before = self.state()
        project = self.save()
        same = self.save()
        self.assertEqual(project['id'], same['id'])
        self.assertEqual(len(self.a.get('/api/projects').json()['projects']), 1)
        self.a.post('/api/reset', json={})
        current = self.state()
        response = self.a.post('/api/projects/open', json={'project_id': project['id'], 'expected_generation_token': current['generation_token']})
        self.assertEqual(response.status_code, 200, response.text)
        opened = response.json()
        self.assertEqual(opened['signature'], before['signature'])
        self.assertGreater(opened['generation_token'], current['generation_token'])
        self.assertTrue(opened['active_revision']['validation_report']['passed'])
        self.assertIsNotNone(opened['persistence']['autosaved_at'])
        stale = self.a.post('/api/projects/save', json={'name': 'lama', 'expected_generation_token': current['generation_token']})
        self.assertEqual(stale.status_code, 409)

    def test_expired_cookie_forgets_autosave_and_saved_projects(self):
        project = self.save()
        old_cookie = self.a.cookies.get('sisa_sid')
        owner = self.registry.owner(old_cookie)
        old = self.entry()
        past = time.time() - RETENTION_S - 1
        self.registry.entries[owner].touched_at = past
        self.registry.store.db.execute('UPDATE autosaves SET updated_at=? WHERE owner=?', (past, owner))
        self.registry.store.db.commit()
        self.state()
        self.assertFalse(old.alive)
        self.assertNotEqual(self.a.cookies.get('sisa_sid'), old_cookie)
        self.assertEqual(self.a.get('/api/projects').json()['projects'], [])
        self.assertEqual(self.a.post('/api/projects/open', json={'project_id': project['id']}).status_code, 404)

    def test_opt_in_telemetry_records_only_allowlisted_events_and_server_time(self):
        before = time.time()
        self.assertFalse(self.a.post('/api/telemetry/event', json={'event': 'start'}).json()['recorded'])
        self.assertEqual(self.registry.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        self.assertEqual(self.a.post('/api/telemetry/consent', json={'enabled': True}).json(), {'enabled': True})
        for event in ('start', 'story_sent', 'design_ready', 'download', 'error'):
            self.assertTrue(self.a.post('/api/telemetry/event', json={'event': event}).json()['recorded'])
        for body in ({'event': 'anything'}, {'event': 'start', 'text': 'private story'}, {'event': 'start', 'timestamp': 1}):
            self.assertEqual(self.a.post('/api/telemetry/event', json=body).status_code, 422)
        rows = self.registry.store.db.execute('SELECT anonymous_id,event,recorded_at FROM events').fetchall()
        self.assertEqual(len(rows), 5)
        self.assertNotIn(self.a.cookies.get('sisa_sid'), str(rows))
        self.assertNotIn(self.registry.owner(self.a.cookies.get('sisa_sid')), str(rows))
        self.assertTrue(all(row[2] >= before for row in rows))
        self.reopen_registry()
        self.assertTrue(self.state()['telemetry_enabled'])
        self.a.post('/api/telemetry/consent', json={'enabled': False})
        self.assertEqual(self.registry.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
        self.assertFalse(self.state()['telemetry_enabled'])

    def test_reset_and_open_do_not_reset_ai_cost_limits_or_telemetry_preference(self):
        project = self.save()
        session = self.entry(); session.chat_calls = 20; session.image_calls = 20
        self.a.post('/api/telemetry/consent', json={'enabled': True})
        self.a.post('/api/reset', json={})
        self.a.post('/api/projects/open', json={'project_id': project['id']})
        self.assertEqual(self.entry().chat_calls, 20); self.assertEqual(self.entry().image_calls, 20)
        self.assertTrue(self.state()['telemetry_enabled'])
        response = self.a.post('/api/ai/chat', json={'text': 'lebih tinggi', 'ai_consent': True})
        self.assertEqual(response.status_code, 429)
        self.a.post('/api/plan', json={})
        self.assertEqual(self.a.post('/api/ai/image', json={'ai_consent': True}).status_code, 429)

    def test_other_browser_reset_cannot_cancel_current_browser_plan(self):
        entered, release = threading.Event(), threading.Event()
        original = service.search
        output = []
        def slow(*args, **kwargs):
            entered.set(); release.wait(5)
            return original(*args, **kwargs)
        with mock.patch.object(service, 'search', side_effect=slow):
            worker = threading.Thread(target=lambda: output.append(self.a.post('/api/plan', json={})))
            worker.start(); self.assertTrue(entered.wait(5))
            self.b.post('/api/reset', json={}); release.set(); worker.join(10)
        self.assertEqual(output[0].status_code, 200, output[0].text)
        self.assertTrue(self.state()['active_revision']['validation_report']['passed'])

    def test_open_cancels_own_late_job_and_never_overwrites_opened_input(self):
        project = self.save()
        data = self.state()['data']; data['request']['H_mm'] = 100
        self.a.post('/api/input', json={'data': data}); self.a.post('/api/confirm', json={})
        entered, release = threading.Event(), threading.Event()
        original = service.search; output = []
        def slow(*args, **kwargs):
            entered.set(); release.wait(5)
            return original(*args, **kwargs)
        with mock.patch.object(service, 'search', side_effect=slow):
            worker = threading.Thread(target=lambda: output.append(self.a.post('/api/plan', json={})))
            worker.start(); self.assertTrue(entered.wait(5))
            opened = self.a.post('/api/projects/open', json={'project_id': project['id']})
            self.assertEqual(opened.status_code, 200)
            release.set(); worker.join(10)
        self.assertEqual(output[0].status_code, 409)
        self.assertEqual(self.state()['data']['request']['H_mm'], 120)
        self.assertIsNone(self.state()['active_revision'])

    def test_chat_registered_explanations_do_not_call_model_or_change_revision(self):
        self.a.post('/api/plan', json={}); before = self.state()
        with mock.patch.object(ai, 'propose_request') as model:
            for text in ('jelaskan langkah 4', 'jelaskan susunan', 'jelaskan langkah 99'):
                response = self.a.post('/api/ai/chat', json={'text': text, 'ai_consent': True})
                self.assertEqual(response.status_code, 200); self.assertIn('event: message', response.text)
            model.assert_not_called()
        after = self.state()
        self.assertEqual(after['signature'], before['signature'])
        self.assertEqual(after['active_revision'], before['active_revision'])

    def test_chat_image_action_uses_existing_image_budget_and_event(self):
        self.a.post('/api/plan', json={})
        with mock.patch.object(ai, 'generate_image', return_value=(b'png', {'media_type':'image/png','source':'live'})) as image, mock.patch.object(ai, 'propose_request') as model:
            response = self.a.post('/api/ai/chat', json={'text': 'buat ulang gambar', 'ai_consent': True})
        self.assertEqual(response.status_code, 200); self.assertIn('event: image', response.text)
        image.assert_called_once(); model.assert_not_called()
        self.assertEqual(self.entry().image_calls, 1)


    def test_pending_split_and_accepted_split_survive_restart_with_gate_intact(self):
        data = self.state()['data']
        data['materials'][0]['available'] = False
        data['materials'][0]['confirmed'] = False
        self.a.post('/api/input', json={'data': data})
        self.a.post('/api/confirm', json={})
        response = self.a.post('/api/plan', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state()['phase'], 'awaiting_change_acceptance')
        self.reopen_registry()
        pending = self.state()
        self.assertEqual(pending['phase'], 'awaiting_change_acceptance')
        self.assertIsNone(pending['active_revision'])
        self.assertEqual(self.a.get('/api/export-pdf', params={'revision_sig': pending['signature']}).status_code, 400)
        response = self.a.post('/api/accept-split', json={'accepted': True, 'additional_seams': 1, 'expected_generation_token': pending['generation_token'], 'candidate_signature': pending['signature']})
        self.assertEqual(response.status_code, 200, response.text)
        self.reopen_registry()
        accepted = self.state()
        self.assertTrue(accepted['consent']['accepted'])
        self.assertEqual(accepted['active_revision']['variant'], 'front_split_center')
        self.assertTrue(accepted['active_revision']['validation_report']['passed'])
        self.assertEqual(self.a.get('/api/export-pdf', params={'revision_sig': accepted['signature']}).status_code, 200)

    def test_image_restart_preserves_current_bytes_cache_and_blocks_old_image_id(self):
        self.a.post('/api/plan', json={})
        with mock.patch.object(ai, 'generate_image', return_value=(b'png', {'media_type':'image/png','source':'live'})):
            info = self.a.post('/api/ai/image', json={'ai_consent': True}).json()
        before = self.state()
        self.reopen_registry()
        after = self.state()
        self.assertNotEqual(info['image_id'], after['image']['image_id'])
        params = {'revision_sig': after['signature'], 'revision_id': after['active_revision']['design_revision_id'], 'image_id': after['image']['image_id']}
        self.assertEqual(self.a.get('/api/ai/image', params=params).content, b'png')
        params['image_id'] = info['image_id']
        self.assertEqual(self.a.get('/api/ai/image', params=params).status_code, 409)
        with mock.patch.object(ai, 'generate_image') as model:
            cached = self.a.post('/api/ai/image', json={'ai_consent': True})
            self.assertEqual(cached.status_code, 200, cached.text)
            self.assertEqual(cached.json()['source'], 'cached')
            model.assert_not_called()
        self.assertEqual(self.entry().image_calls, 1)
        self.assertEqual(before['signature'], after['signature'])

    def test_malformed_record_recovers_draft_instead_of_server_error(self):
        owner = self.registry.owner(self.a.cookies.get('sisa_sid'))
        self.registry.store.db.execute('UPDATE autosaves SET document=? WHERE owner=?', ('{oops', owner))
        self.registry.store.db.commit()
        self.reopen_registry()
        self.assertEqual(self.state()['data'], engine.fixture_data())
        owner = self.registry.owner(self.a.cookies.get('sisa_sid'))
        record, _ = self.registry.store.load_autosave(owner)
        record.update(generation_token='bad', chat_calls=[], image_calls={}, chat_history=12,
                      image_attempts=None, candidate=['bad'], active_revision='bad')
        self.registry.store.autosave(owner, record)
        self.reopen_registry()
        self.assertIsNone(self.state()['active_revision'])

    def test_unchanged_get_does_not_rewrite_image_blob(self):
        self.a.post('/api/plan', json={})
        with mock.patch.object(ai, 'generate_image', return_value=(b'png', {'media_type':'image/png','source':'live'})):
            self.a.post('/api/ai/image', json={'ai_consent': True})
        with mock.patch.object(self.registry.store, 'autosave', wraps=self.registry.store.autosave) as write:
            self.state(); self.state(); self.a.get('/api/projects')
            write.assert_not_called()


    def test_corrupt_saved_project_does_not_replace_current_work(self):
        project = self.save()
        before = self.state()
        self.registry.store.db.execute('UPDATE projects SET document=? WHERE id=?', ('{"record_version":1,"data":{}}', project['id']))
        self.registry.store.db.commit()
        response = self.a.post('/api/projects/open', json={'project_id': project['id']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'PROJECT_INVALID')
        self.assertEqual(self.state()['data'], before['data'])
        self.assertEqual(self.state()['generation_token'], before['generation_token'])


    def test_queued_old_session_requests_reject_after_replacement(self):
        from backend.sessions import sessions
        project = self.save()
        cases = [
            ('post', '/api/input', {'data': self.state()['data']}),
            ('post', '/api/input', {'data': 'invalid'}),
            ('post', '/api/confirm', {}),
            ('post', '/api/cancel', {}),
            ('post', '/api/plan', {}),
            ('post', '/api/projects/save', {'name': 'queued'}),
            ('post', '/api/projects/delete', {'project_id': project['id']}),
            ('post', '/api/ai/request', {'text': 'depan biru', 'ai_consent': True}),
            ('post', '/api/ai/chat', {'text': 'depan biru', 'ai_consent': True}),
            ('post', '/api/ai/image/cancel', {}),
            ('post', '/api/reset', {}),
            ('post', '/api/projects/open', {'project_id': project['id']}),
            ('post', '/api/telemetry/consent', {'enabled': True}),
            ('post', '/api/telemetry/event', {'event': 'start'}),
            ('get', '/api/state', None),
            ('get', '/api/projects', None),
            ('get', '/api/ai/image?revision_sig=old&revision_id=old&image_id=old', None),
        ]
        for method, route, body in cases:
            with self.subTest(route=route):
                old = self.entry()
                owner = self.registry.owner(self.a.cookies.get('sisa_sid'))
                entered = threading.Event(); output = []
                original = self.registry.resolve
                def captured(cookie):
                    result = original(cookie)
                    if result[2] is old:
                        entered.set()
                    return result
                def request():
                    output.append(getattr(self.a, method)(route, **({'json': body} if body is not None else {})))
                with old.lock, mock.patch.object(self.registry, 'resolve', side_effect=captured):
                    worker = threading.Thread(target=request)
                    worker.start(); self.assertTrue(entered.wait(5))
                    replacement = sessions.SessionManager()
                    replacement.generation_token = old.generation_token + 1
                    context = sessions._CURRENT.set(old)
                    try:
                        self.registry.replace(owner, old, replacement)
                    finally:
                        sessions._CURRENT.reset(context)
                worker.join(10)
                self.assertFalse(worker.is_alive())
                self.assertEqual(output[0].status_code, 409, output[0].text)
                self.assertEqual(output[0].json()['detail']['code'], 'STALE_REVISION')
                self.assertFalse(self.entry().telemetry_enabled)
                self.assertEqual(self.state()['generation_token'], replacement.generation_token)

    def test_concurrent_reset_and_open_return_success_then_conflict(self):
        project = self.save()
        old = self.entry(); generation = old.generation_token
        entered = threading.Event(); bound = []; output = []
        original = self.registry.resolve
        count_lock = threading.Lock()
        def captured(cookie):
            result = original(cookie)
            if result[2] is old:
                with count_lock:
                    bound.append(result[2])
                    if len(bound) == 2:
                        entered.set()
            return result
        def request(route, body):
            output.append(self.a.post(route, json=body))
        with old.lock, mock.patch.object(self.registry, 'resolve', side_effect=captured):
            workers = [threading.Thread(target=request, args=('/api/reset', {'expected_generation_token': generation})),
                       threading.Thread(target=request, args=('/api/projects/open', {'project_id': project['id'], 'expected_generation_token': generation}))]
            for worker in workers: worker.start()
            self.assertTrue(entered.wait(5))
        for worker in workers: worker.join(10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(sorted(response.status_code for response in output), [200, 409])
        self.assertEqual(next(response for response in output if response.status_code == 409).json()['detail']['code'], 'STALE_REVISION')
        self.assertTrue(self.entry().alive)
        self.assertGreater(self.state()['generation_token'], generation)
