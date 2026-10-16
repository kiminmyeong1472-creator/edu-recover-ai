"""Source-to-display integration with no network inference or real student data."""
import base64
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('EDURECOVER_OWNER_USER', 'owner-test')
os.environ.setdefault('EDURECOVER_OWNER_PASSWORD', 'password-test')
os.environ.setdefault('EDURECOVER_CLASSROOM_STATE_DIR', tempfile.mkdtemp(prefix='edurecover-av-'))
os.environ.setdefault('EDURECOVER_TRAINING_PSEUDONYM_SALT', 'test-only-training-pseudonym-salt')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import classroom_tutor as classroom
from app.services import audiovisual_drafts as drafts
from app.services.open_model_catalog import MODEL_CATALOG
from app.services.structured_materials import example_bundle


class AudioVisualClassroomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(classroom.classroom_tutor_router)
        app.include_router(classroom.teacher_classroom_router)
        cls.client = TestClient(app)
        value = base64.b64encode(b'owner-test:password-test').decode()
        cls.headers = {'Authorization': 'Basic ' + value}

    def session(self):
        r = self.client.post('/classroom/api/sessions', headers=self.headers,
                             json={'title': '시청각 합성 시험'})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def request(self, session):
        base = f"/tutor/api/sessions/{session['session_id']}"
        params = {'teacher_token': session['teacher_token']}
        r = self.client.get(base + '/audiovisual-context', params=params, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        return {'model_key': next(x['model_key'] for x in MODEL_CATALOG if x['family'] == 'deepseek'),
                'explicit_authorization': True, 'source_text': '시험용 교재. 좌우 미분계수가 같을 때 미분가능성을 확인합니다.',
                'target': '조건과 결론을 구별합니다.', 'subject': '수학', 'school_level': 'HIGH', 'grade': '2',
                'expected_digest': r.json()['context_digest']}

    def model_output(self):
        b = example_bundle().model_dump(exclude_none=True)
        for a in b['representations']:
            if a['kind'] == 'SCENE':
                a['visual'] = {'kind': 'STEPS', 'steps': ['좌우에서 조건을 확인합니다.', '차이를 비교합니다.']}
        return json.dumps(b, ensure_ascii=False)

    def generate(self, session, request, **kwargs):
        return self.client.post(f"/tutor/api/sessions/{session['session_id']}/structured-materials/generate",
                                params={'teacher_token': session['teacher_token']},
                                headers=self.headers, json=request, **kwargs)

    def test_source_generation_stays_private_until_exact_review_then_reaches_robot(self):
        session = self.session()
        req = self.request(session)
        with (patch.object(drafts, 'assert_provider_allowed', return_value=SimpleNamespace(control_epoch='test')),
              patch.object(drafts, 'call_model', AsyncMock(return_value=self.model_output())) as runner):
            r = self.generate(session, req)
        self.assertEqual(r.status_code, 200, r.text)
        runner.assert_awaited_once()
        result = r.json()
        self.assertTrue(result['human_review_required'])
        self.assertEqual(result['student_release'], 'BLOCKED')
        with classroom.db() as conn:
            event = conn.execute("SELECT payload_json FROM events WHERE session_id=? AND event_type=? ORDER BY event_id DESC LIMIT 1",
                                 (session['session_id'], 'MATERIAL_DRAFT_SAVED')).fetchone()
        recorded = json.loads(event['payload_json'])
        self.assertEqual(recorded['generation_receipt'], result['receipt'])
        self.assertNotIn(req['source_text'], event['payload_json'])
        self.assertEqual(result['receipt']['source_sha256'], __import__('hashlib').sha256(req['source_text'].encode()).hexdigest())
        base = f"/tutor/api/sessions/{session['session_id']}"
        teacher = {'teacher_token': session['teacher_token']}
        self.assertEqual(self.client.get(base, params={**teacher, 'display_only': True}).status_code, 423)
        b = result['bundle']
        self.assertTrue(all(v['status'] == 'PENDING' and not v['manifest_digest'] for v in b['validations']))
        reviewed = self.client.post(base + '/structured-materials/publish', params=teacher,
            json={'revision_id': b['revision_id'], 'expected_digest': result['digest'], 'teacher_verified_content': True})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        scene = next(a for a in b['representations'] if a['kind'] == 'SCENE')
        self.client.post(base + '/command', params=teacher, json={'action': 'GOTO', 'segment_id': scene['asset_id']})
        displayed = self.client.get(base, params={**teacher, 'display_only': True}).json()
        self.assertEqual(len(displayed['lesson']), 1)
        self.assertEqual(displayed['lesson'][0]['scene_visual']['kind'], 'STEPS')
        self.assertIn('audio_text', displayed['lesson'][0])
        self.assertNotIn('correct_answer', displayed['lesson'][0])
        self.assertEqual(self.client.get(session['robot_url']).status_code, 200)

    def test_owner_token_and_stale_context_checks_happen_before_inference(self):
        session = self.session()
        req = self.request(session)
        url = f"/tutor/api/sessions/{session['session_id']}/structured-materials/generate"
        with patch.object(classroom, 'generate_audiovisual_draft', AsyncMock()) as runner:
            r = self.client.post(url, params={'teacher_token': session['teacher_token']}, json=req)
            self.assertIn(r.status_code, (401, 403))
            r = self.client.post(url, params={'teacher_token': 'wrong'}, headers=self.headers, json=req)
            self.assertEqual(r.status_code, 403)
            req['expected_digest'] = 'stale'
            self.assertEqual(self.generate(session, req).status_code, 409)
            req['explicit_authorization'] = 1
            self.assertEqual(self.generate(session, req).status_code, 422)
            runner.assert_not_awaited()

    def test_edit_during_generation_does_not_overwrite_newer_draft(self):
        session = self.session()
        req = self.request(session)
        later = example_bundle()
        later.revision_id = 'teacher-edited-while-generating'
        async def delayed(_):
            classroom.save_material_draft(session['session_id'], later, session['teacher_token'])
            return example_bundle(), {'verification': 'NOT_PERFORMED'}
        with patch.object(classroom, 'generate_audiovisual_draft', delayed):
            r = self.generate(session, req)
        self.assertEqual(r.status_code, 409)
        saved = self.client.get(f"/tutor/api/sessions/{session['session_id']}/structured-materials",
                                params={'teacher_token': session['teacher_token']}).json()
        self.assertEqual(saved['bundle']['revision_id'], later.revision_id)

    def test_actual_scripts_compile_and_all_scene_types_escape_text(self):
        programs = []
        for html in (classroom.HOME_HTML, classroom.TEACHER_PRODUCT_HOME_HTML,
                     classroom.board_html('synthetic', 'synthetic', display_mode='ROBOT')):
            programs.extend(re.findall(r'<script>(.*?)</script>', html, re.S))
        script = "const vm=require('node:vm');const data=JSON.parse(require('node:fs').readFileSync(0,'utf8'));" \
                 "for(const p of data.programs)new vm.Script(p);" \
                 "const c=vm.createContext({});vm.runInContext(data.common,c);" \
                 "for(const v of data.scenes){const out=vm.runInContext('sceneVisual('+JSON.stringify(v)+')',c);" \
                 "if(out.includes('<script>')||out.includes('<img '))throw Error('unsafe markup');" \
                 "if(!out.includes('&lt;script&gt;'))throw Error('missing escaped content');}"
        bad = '<script>malicious</script>'
        scenes = [{'kind': 'STEPS', 'steps': [bad]},
                  {'kind': 'TABLE', 'columns': [bad, '비교'], 'rows': [['가', '나']]},
                  {'kind': 'DIAGRAM', 'nodes': [{'id': 'a', 'label': bad}, {'id': 'b', 'label': '조건'}],
                   'edges': [{'from_id': 'a', 'to_id': 'b', 'label': bad}]}]
        r = subprocess.run(['node', '-e', script], input=json.dumps({'programs': programs,
                           'common': classroom.COMMON_JS, 'scenes': scenes}), text=True,
                           capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_editor_locks_concurrent_generation_and_restores_state(self):
        harness = Path(__file__).with_name('audiovisual_editor_harness.js')
        result = subprocess.run(['node', str(harness)], input=classroom.MATERIAL_EDITOR_SCRIPT,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
