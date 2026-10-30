"""LangGraph 흐름 시험과 주 프로그램 연결 시험. 가짜 모델 응답만 사용."""
import base64
import os
import subprocess
import sys
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import dreamkeeper_chat_api as chat_api

try:  # langgraph는 선택 의존성(requirements-dreamkeeper-graph.txt)
    from app.services.dreamkeeper_chat_graph import ChatRuntime, build_chat_graph, run_chat_turn
    HAVE_GRAPH = True
except ImportError:  # pragma: no cover - 선택 의존성이 없는 환경
    HAVE_GRAPH = False
needs_graph = unittest.skipUnless(HAVE_GRAPH, "langgraph==1.2.18 not installed")

from dreamkeeper_chat_support import BAD, GOOD, L, T, Bed, answer, verifier, worker


def run(bed, runtime, **kw):
    args = dict(logical_id=L, task_id=T, question="광합성이 무엇인가요?", domain="education",
                purpose="explain")
    args.update(kw)
    return run_chat_turn(bed.core, runtime, **args)


@needs_graph
class GraphShapeTests(unittest.TestCase):
    def test_nodes_and_order(self):
        bed = Bed()
        try:
            graph = build_chat_graph(bed.core, ChatRuntime((worker(),), verifier())).get_graph()
            nodes = set(graph.nodes)
            expected = ["load_ledger", "judge_purpose", "select_evidence", "generate", "goal_check",
                        "verify", "generation_check", "sentence_filter", "record_receipt", "output"]
            self.assertTrue(set(expected) <= nodes)
            edges = {(e.source, e.target) for e in graph.edges}
            for a, b in zip(expected, expected[1:]):
                self.assertIn((a, b), edges)
            for loop in ("generate", "goal_check", "verify"):
                self.assertIn((loop, "generate"), edges)
        finally:
            bed.close()

    def test_runtime_rejects_same_family_and_duplicate_names(self):
        with self.assertRaises(ValueError):
            ChatRuntime((worker(family="fam"),), verifier(family="FAM"))
        with self.assertRaises(ValueError):
            ChatRuntime((worker(name="a"), worker(name="a", family="b")), verifier())
        with self.assertRaises(ValueError):
            ChatRuntime((), verifier())


@needs_graph
class GraphFlowTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def test_full_flow_outputs_only_supported_sentences(self):
        result = run(self.bed, ChatRuntime((worker(reply=answer(GOOD + " " + BAD)),), verifier()))
        self.assertEqual(result["status"], "OUTPUT")
        self.assertEqual(result["output"]["text"], GOOD)
        kinds = [e["kind"] for e in self.bed.core.events(L, T)]
        for kind in ("TURN_STARTED", "SENTENCES_FILTERED", "RECEIPT_RECORDED", "OUTPUT_EMITTED"):
            self.assertIn(kind, kinds)
        ledger = self.bed.core.load_ledger(L, T)
        self.assertEqual(len(ledger["quarantine"]), 1)

    def test_failure_swaps_engine_and_keeps_subject(self):
        drift = worker(name="a", family="fam-a", reply=answer(goal_ids=["g1"]))
        good = worker(name="b", family="fam-b")
        result = run(self.bed, ChatRuntime((drift, good), verifier()))
        self.assertEqual(result["status"], "OUTPUT")
        self.assertEqual(result["engine"], "b")
        limit = self.bed.config.max_failures_per_engine
        self.assertEqual(len(drift.generate.calls), limit)
        self.assertEqual(len(good.generate.calls), 1)
        self.assertTrue(self.bed.core.engine_status(L, T)["a"]["quarantined"])
        turn = self.bed.core.turn(L, T, result["turn_id"])["state"]
        self.assertEqual([a["binding"]["logical_id"] for a in turn["attempts"]], [L] * (limit + 1))
        self.assertEqual([a["engine"] for a in turn["attempts"]], ["a"] * limit + ["b"])
        # 다음 차례에는 격리된 엔진을 다시 쓰지 않는다.
        again = run(self.bed, ChatRuntime((drift, good), verifier()))
        self.assertEqual(again["engine"], "b")
        self.assertEqual(len(drift.generate.calls), limit)

    def test_verification_failure_also_swaps(self):
        unsupported = verifier()
        unsupported.verify.reply = lambda p: {"supported": "Seoul" not in p["response"]}
        bad = worker(name="a", family="fam-a", reply=answer("Seoul " + GOOD))
        good = worker(name="b", family="fam-b")
        result = run(self.bed, ChatRuntime((bad, good), unsupported))
        self.assertEqual((result["status"], result["engine"]), ("OUTPUT", "b"))

    def test_all_engines_quarantined_ends_in_hold(self):
        def down(_):
            raise RuntimeError("down")
        engines = (worker(name="a", family="fa", reply=down),
                   worker(name="b", family="fb", reply=answer(goal_ids=[])))
        result = run(self.bed, ChatRuntime(engines, verifier()))
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["hold_reason"], "all engines quarantined")
        self.assertIsNone(result["output"])
        limit = self.bed.config.max_failures_per_engine
        self.assertEqual(len(result["engine_failures"]), 2 * limit)
        ledger = self.bed.core.load_ledger(L, T)
        self.assertIn(result["turn_id"], ledger["holds"]["turns"])
        self.assertEqual(ledger["usage_records"], {})
        again = run(self.bed, ChatRuntime(engines, verifier()))
        self.assertEqual(again["status"], "HOLD")

    def test_late_response_ends_stale_and_is_recorded(self):
        def reply(_):
            self.bed.core.correct_source(L, T, source_id="s1", fields={"body": "정정"},
                                         expected_revision=1, user_receipt="fix")
            return answer()
        result = run(self.bed, ChatRuntime((worker(reply=reply),), verifier()))
        self.assertEqual(result["status"], "STALE")
        self.assertIsNone(result["output"])
        kinds = [e["kind"] for e in self.bed.reopen().events(L, T)]
        self.assertIn("LATE_RESPONSE_REJECTED", kinds)
        self.assertNotIn("OUTPUT_EMITTED", kinds)

    def test_hold_paths(self):
        cases = [
            (dict(domain="medicine"), "domain not allowed"),
            (dict(purpose="sell"), "purpose unknown"),
        ]
        for kw, reason in cases:
            with self.subTest(reason=reason):
                r = run(self.bed, ChatRuntime((worker(),), verifier()), **kw)
                self.assertEqual((r["status"], r["hold_reason"]), ("HOLD", reason))
        rt = ChatRuntime((worker(),), verifier(), purpose_classifier=lambda q: "fact")
        r = run(self.bed, rt)
        self.assertEqual((r["status"], r["hold_reason"]), ("HOLD", "purpose ambiguous"))
        r = run(self.bed, rt, purpose=None)
        self.assertEqual(r["status"], "OUTPUT")
        self.bed.core.advance_policy_generation(L, T, reason="new", user_receipt="u")
        r = run(self.bed, ChatRuntime((worker(),), verifier()))
        self.assertEqual((r["status"], r["hold_reason"]), ("HOLD", "no eligible evidence"))
        self.bed.engine.pause(L, T, reason="check", receipt="r")
        r = run(self.bed, ChatRuntime((worker(),), verifier()))
        self.assertEqual((r["status"], r["hold_reason"]), ("HOLD", "task not active"))
        r = run(self.bed, ChatRuntime((worker(),), verifier()), task_id="missing")
        self.assertEqual(r["status"], "HOLD")

    def test_no_supported_sentence_holds(self):
        r = run(self.bed, ChatRuntime((worker(reply=answer(BAD)),), verifier()))
        self.assertEqual((r["status"], r["hold_reason"]),
                         ("HOLD", "no sentence met support threshold"))


ENV = {"EDURECOVER_OWNER_USER": "owner", "EDURECOVER_OWNER_USER ": "",
       "EDURECOVER_OWNER_PASSWORD": "pw"}
AUTH = {"Authorization": "Basic " + base64.b64encode(b"owner:pw").decode()}
BODY = {"logical_id": L, "task_id": T, "question": "광합성이 무엇인가요?",
        "domain": "education", "purpose": "explain"}


class MainWiringTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()
        self.env = mock.patch.dict(os.environ, {**ENV, "DREAMKEEPER_CHAT_ENABLED": "1",
                                                "DREAMKEEPER_CHAT_DB": str(self.bed.path),
                                                "DREAMKEEPER_CHAT_OWNER_ID": "owner-a"})
        self.env.start()
        self.app = FastAPI()
        self.assertTrue(chat_api.install_dreamkeeper_chat(self.app))
        self.client = TestClient(self.app, base_url="https://testserver")

    def tearDown(self):
        chat_api.configure_chat_runtime(None)
        chat_api.reset_chat_cores()
        self.env.stop()
        self.bed.close()

    def test_main_app_registers_route_only_when_enabled_at_start(self):
        """주 프로그램을 새 프로세스로 불러와, 시작 시 꺼져 있으면 경로 자체가 없음을 확인."""
        probe = (
            "import os, base64\n"
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "os.environ['DREAMKEEPER_CHAT_ENABLED'] = '1'\n"
            "c = TestClient(app, base_url='https://testserver')\n"
            "print(c.post('/dreamkeeper/chat/turn', json={}).status_code)\n")
        codes = {}
        for flag in ("", "1"):
            env = {k: v for k, v in os.environ.items() if not k.startswith("DREAMKEEPER_CHAT")}
            env.update(ENV, DREAMKEEPER_CHAT_ENABLED=flag, PYTHONPATH=os.getcwd())
            done = subprocess.run([sys.executable, "-c", probe], env=env, cwd=os.getcwd(),
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            codes[flag] = done.stdout.strip().splitlines()[-1]
        # 꺼진 채 시작: 요청 때 켜도 경로가 없어 404. 켠 채 시작: 경로가 있고 인증 요구 401.
        self.assertEqual(codes, {"": "404", "1": "401"})
        with mock.patch.dict(os.environ, {"DREAMKEEPER_CHAT_ENABLED": ""}):
            plain = FastAPI()
            self.assertFalse(chat_api.install_dreamkeeper_chat(plain))
        self.assertEqual(TestClient(plain).post(chat_api.ROUTE, json=BODY,
                                                headers=AUTH).status_code, 404)

    def test_requires_owner(self):
        chat_api.configure_chat_runtime(lambda: object())
        self.assertEqual(self.client.post(chat_api.ROUTE, json=BODY).status_code, 401)

    def test_no_injected_models_is_503(self):
        response = self.client.post(chat_api.ROUTE, json=BODY, headers=AUTH)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "dreamkeeper_chat_models_not_configured")

    @needs_graph
    def test_missing_database_is_503(self):
        chat_api.configure_chat_runtime(lambda: object())
        with mock.patch.dict(os.environ, {"DREAMKEEPER_CHAT_DB": ""}):
            response = self.client.post(chat_api.ROUTE, json=BODY, headers=AUTH)
        self.assertEqual(response.status_code, 503)

    def test_turned_off_after_install_is_404(self):
        with mock.patch.dict(os.environ, {"DREAMKEEPER_CHAT_ENABLED": "0"}):
            response = self.client.post(chat_api.ROUTE, json=BODY)
        self.assertEqual(response.status_code, 404)

    def test_missing_graph_dependency_is_503(self):
        chat_api.configure_chat_runtime(lambda: object())
        with mock.patch.dict(sys.modules, {"app.services.dreamkeeper_chat_graph": None}):
            response = self.client.post(chat_api.ROUTE, json=BODY, headers=AUTH)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "dreamkeeper_chat_graph_dependency_missing")

    @needs_graph
    def test_turn_with_injected_fakes(self):
        spec = worker(reply=answer(GOOD + " " + BAD))
        chat_api.configure_chat_runtime(lambda: ChatRuntime((spec,), verifier()))
        response = self.client.post(chat_api.ROUTE, json=BODY, headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data["status"], data["text"]), ("OUTPUT", GOOD))
        self.assertEqual(set(data["receipt_sources"]), {"s1", "s2"})
        held = self.client.post(chat_api.ROUTE, json={**BODY, "domain": "medicine"}, headers=AUTH)
        self.assertEqual(held.status_code, 409)
        self.assertEqual(held.json()["hold_reason"], "domain not allowed")
        extra = self.client.post(chat_api.ROUTE, json={**BODY, "goal_override": 1}, headers=AUTH)
        self.assertEqual(extra.status_code, 422)


if __name__ == "__main__":
    unittest.main()
