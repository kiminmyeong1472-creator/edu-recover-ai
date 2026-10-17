import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services import code_execution_api as api


class FakeSession:
    cleanup_status = "not_created"
    def __init__(self): self.files = {}; self.calls = []
    def __enter__(self): return self
    def __exit__(self, *args): self.cleanup_status = "destroyed"
    def seed(self, files): self.files = {f["path"]: f["content"] for f in files}
    def snapshot(self): return [{"path": p, "content": c} for p,c in self.files.items()]
    def execute(self, name, args):
        self.calls.append((name,args))
        if name == "file_write": self.files[args["path"]] = args["content"]; return {"written": True}
        return {"stdout": "2\n", "stderr": "", "exit_code": 0}


class CodeExecutionAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"EDURECOVER_CLASSROOM_STATE_DIR": self.temp.name,
            "EDURECOVER_OWNER_USER":"owner-test", "EDURECOVER_OWNER_PASSWORD":"test-only"})
        self.env.start(); self.addCleanup(self.env.stop)
        app = FastAPI(); app.include_router(api.router)
        self.client = TestClient(app, base_url="https://testserver"); self.addCleanup(self.client.close)
        self.auth = ("owner-test", "test-only")

    def payload(self, **kw):
        return {"prompt":"", "command":"python3 main.py", "files":[{"path":"main.py", "content":"print(2)"}], "explicit_authorization":True} | kw

    def test_anonymous_never_creates_a_vm(self):
        with patch.object(api.runtime, "RailwayCodeSession") as session:
            self.assertEqual(self.client.post('/owner/code-workspace/runs', json=self.payload()).status_code,401)
            session.assert_not_called()

    def test_missing_runtime_fails_before_worker_submission(self):
        with patch.object(api.runtime, 'runtime_status', return_value={"configured":False,"message":"인증 토큰 설정이 필요합니다."}), patch.object(api._pool, 'submit') as submit:
            r=self.client.post('/owner/code-workspace/runs',auth=self.auth,json=self.payload())
            self.assertEqual(r.status_code,503); submit.assert_not_called()

    def test_rejects_missing_authorization_and_unsafe_project_paths(self):
        for updates in ({"explicit_authorization":1},{"explicit_authorization":False},
                        {"files":[{"path":"../app/main.py","content":"x"}]},
                        {"files":[{"path":"/etc/passwd","content":"x"}]},
                        {"evaluation_case":"unknown"}):
            self.assertEqual(self.client.post('/owner/code-workspace/runs',auth=self.auth,json=self.payload(**updates)).status_code,422)

    def test_manual_worker_preserves_evidence_without_claiming_system_apply(self):
        req=api.CodeRunRequest(**self.payload())
        record={"id":"a"*32,"created_at":1,"status":"queued","steps":[],"files":[]}
        with patch.object(api.runtime,'RailwayCodeSession',FakeSession):
            api._work(req,record,threading.Event())
        saved=api._load(record['id'])
        self.assertEqual(saved['status'],'completed'); self.assertEqual(saved['stdout'],'2\n')
        self.assertEqual(saved['exit_code'],0); self.assertFalse(saved['system_applied'])
        self.assertEqual(saved['cleanup_status'],'destroyed')
        self.assertEqual(saved['files'],[{"path":"main.py","content":"print(2)"}])

    def test_cancellation_stops_before_runtime_creation(self):
        event=threading.Event();event.set()
        record={"id":"b"*32,"created_at":1,"status":"queued","steps":[],"files":[]}
        with patch.object(api.runtime,'RailwayCodeSession') as session:
            api._work(api.CodeRunRequest(**self.payload()),record,event)
            session.assert_not_called()
        self.assertEqual(api._load(record['id'])['status'],'cancelled')

    def test_evaluation_does_not_call_official_scores_or_unrun_zero(self):
        r=self.client.get('/owner/code-workspace/evaluation-cases',auth=self.auth).json()
        self.assertEqual(len(r['categories']),4); self.assertFalse(r['official_benchmark_run'])
        self.assertTrue(all(x['state']=='미실행' for x in r['cases']))

    def test_restart_marks_previous_running_job_interrupted(self):
        record={"id":"c"*32,"created_at":1,"status":"running","steps":[],"files":[]}
        api._save(record); api._recovered.clear()
        self.assertEqual(api._load(record['id'])['status'],'interrupted')
