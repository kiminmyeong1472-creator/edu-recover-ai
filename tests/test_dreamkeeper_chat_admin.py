"""선택적 정정 후 보류 풀기, 격리 후보 시험·채택, 관리 경로 연결 시험(가짜 응답만)."""
import base64
import os
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import dreamkeeper_chat_api as chat_api
from app.services.dreamkeeper_chat_core import AFFECTED, NOT_DEPENDENT, UNRESOLVED, ChatHold

from dreamkeeper_chat_support import GOOD, L, S1, T, Bed, answer, verifier, worker

SUPPORTED_BUT_FLAGGED = "엽록체에서 일어난다."
IO = {"input_unit": "question+evidence", "output_unit": "sentence",
      "operation": "evidence_grounded_answer"}


def full_turn(bed, spec):
    core = bed.core
    tid, _ = bed.steps(spec)
    assert core.check_goals(L, T, tid)["ok"]
    assert core.verify(L, T, tid, verifier(), spec)["ok"]
    assert core.check_generation(L, T, tid, stage="before_sentences")
    core.filter_sentences(L, T, tid)
    oid = core.record_receipt(L, T, tid)
    assert core.emit_output(L, T, tid) is not None
    return oid


def kinds(core):
    return [e["kind"] for e in core.events(L, T)]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()
        self.core = self.bed.core

    def tearDown(self):
        self.bed.close()

    def state(self, oid):
        return self.core.load_ledger(L, T)["usage_records"][oid]

    def test_affected_output_released_only_after_recheck_and_confirmation(self):
        oid = full_turn(self.bed, worker())
        self.core.correct_source(L, T, source_id="s1", fields={"body": S1 + " 추가 설명."},
                                 expected_revision=1, user_receipt="fix")
        self.assertEqual(self.state(oid)["state"]["classification"], AFFECTED)
        with self.assertRaises(ChatHold):
            self.core.release_output(L, T, oid, user_confirmed=True, user_receipt="u")
        self.assertIn("OUTPUT_RELEASE_REFUSED", kinds(self.core))
        result = self.core.recheck_output(L, T, oid, purpose="explain",
                                          sources={"s1": ["body"]}, user_receipt="re")
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.state(oid)["state"]["hold"])  # 재확인만으로는 풀리지 않음
        for confirmed in (False, "true", 1):
            with self.subTest(confirmed=confirmed), self.assertRaises(ChatHold):
                self.core.release_output(L, T, oid, user_confirmed=confirmed, user_receipt="u")
        released = self.core.release_output(L, T, oid, user_confirmed=True, user_receipt="u")
        self.assertEqual(released["status"], "RELEASED")
        self.assertFalse(self.state(oid)["state"]["hold"])
        self.assertEqual(self.state(oid)["body"]["receipt"]["sources"]["s1"]["fields"]["body"], 1)
        # 재확인 판본이 다시 정정되면 다시 보류된다.
        self.core.correct_source(L, T, source_id="s1", fields={"body": "완전히 다른 본문"},
                                 expected_revision=2, user_receipt="fix2")
        state = self.state(oid)["state"]
        self.assertEqual((state["classification"], state["hold"]), (AFFECTED, True))

    def test_unresolved_cannot_be_released_before_receipt_supplement(self):
        oid = self.core.import_result(L, T, text=GOOD, origin="sdk-draft", user_receipt="u")
        self.assertEqual(self.state(oid)["state"]["classification"], UNRESOLVED)
        with self.assertRaises(ChatHold):
            self.core.release_output(L, T, oid, user_confirmed=True, user_receipt="u")
        weak = self.core.recheck_output(L, T, oid, purpose="explain", sources={"s2": ["body"]},
                                        user_receipt="re")
        self.assertFalse(weak["ok"])
        self.core.set_source_policy(L, T, source_id="s1", allowed_purposes=["fact"], valid=True,
                                    user_receipt="p")
        ineligible = self.core.recheck_output(L, T, oid, purpose="explain",
                                              sources={"s1": ["body"]}, user_receipt="re")
        self.assertIn("s1: purpose not allowed for source", ineligible["problems"])
        with self.assertRaises(ChatHold):
            self.core.release_output(L, T, oid, user_confirmed=True, user_receipt="u")
        self.assertEqual(self.state(oid)["state"]["classification"], UNRESOLVED)
        self.core.set_source_policy(L, T, source_id="s1", allowed_purposes=["explain"],
                                    valid=True, user_receipt="p")
        self.assertTrue(self.core.recheck_output(L, T, oid, purpose="explain",
                                                 sources={"s1": ["body"]}, user_receipt="re")["ok"])
        self.assertEqual(self.core.release_output(L, T, oid, user_confirmed=True,
                                                  user_receipt="u")["status"], "RELEASED")
        record = self.state(oid)
        self.assertIsNone(record["body"]["receipt"])  # 원래 기록은 보존
        self.assertEqual(record["state"]["recheck_receipt"]["basis"], "HOST_RECHECKED")
        self.assertEqual(record["state"]["classification"], NOT_DEPENDENT)

    def test_release_refused_for_unheld_or_missing_output(self):
        oid = full_turn(self.bed, worker())
        with self.assertRaises(ChatHold):
            self.core.release_output(L, T, oid, user_confirmed=True, user_receipt="u")
        with self.assertRaises(ChatHold):
            self.core.release_output(L, T, "missing", user_confirmed=True, user_receipt="u")


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.score = {"value": 0.3}
        self.bed = Bed(scorer=lambda s, e: self.score["value"] if "엽록체" in s else 1.0)
        self.core = self.bed.core
        full_turn(self.bed, worker(reply=answer(GOOD + " " + SUPPORTED_BUT_FLAGGED)))
        self.qid = next(iter(self.core.load_ledger(L, T)["quarantine"]))

    def tearDown(self):
        self.bed.close()

    def q(self):
        return self.core.load_ledger(L, T)["quarantine"][self.qid]["state"]

    def permit_and_trial(self, passed=True):
        self.score["value"] = 0.9
        self.assertEqual(len(self.core.evaluate_quarantine_candidates(
            L, T, domain="education", purpose="explain", io_spec=IO)), 1)
        self.core.run_trial(L, T, self.qid, lambda body: {"passed": passed})

    def test_adoption_needs_passed_trial_and_user_confirmation(self):
        with self.assertRaises(ChatHold):  # 시험 전
            self.core.adopt_candidate(L, T, self.qid, user_confirmed=True, user_receipt="u")
        self.permit_and_trial()
        self.assertEqual(self.q()["adoption_status"], "NOT_ADOPTED")
        for confirmed in (False, None, "yes"):
            with self.subTest(confirmed=confirmed), self.assertRaises(ChatHold):
                self.core.adopt_candidate(L, T, self.qid, user_confirmed=confirmed,
                                          user_receipt="u")
        result = self.core.adopt_candidate(L, T, self.qid, user_confirmed=True, user_receipt="u")
        self.assertEqual(result["adoption_status"], "ADOPTED")
        self.assertEqual(self.q()["trial_status"], "TRIAL_DONE")
        self.assertIn("CANDIDATE_ADOPTED", kinds(self.core))

    def test_failed_trial_cannot_be_adopted(self):
        self.permit_and_trial(passed=False)
        with self.assertRaises(ChatHold):
            self.core.adopt_candidate(L, T, self.qid, user_confirmed=True, user_receipt="u")
        self.assertEqual(self.q()["adoption_status"], "NOT_ADOPTED")

    def test_gates_are_rechecked_at_adoption(self):
        self.permit_and_trial()
        self.core.advance_policy_generation(L, T, reason="new", user_receipt="u")
        with self.assertRaises(ChatHold):
            self.core.adopt_candidate(L, T, self.qid, user_confirmed=True, user_receipt="u")
        self.assertEqual(self.q()["adoption_status"], "NOT_ADOPTED")
        self.assertIn("ADOPTION_REFUSED", kinds(self.core))


ENV = {"EDURECOVER_OWNER_USER": "owner", "EDURECOVER_OWNER_USER ": "",
       "EDURECOVER_OWNER_PASSWORD": "pw"}
AUTH = {"Authorization": "Basic " + base64.b64encode(b"owner:pw").decode()}
KEY = {"logical_id": L, "task_id": T}


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed(scorer=lambda s, e: 0.3 if "엽록체" in s else 1.0)
        self.oid = full_turn(self.bed, worker(reply=answer(GOOD + " " + SUPPORTED_BUT_FLAGGED)))
        self.env = mock.patch.dict(os.environ, {**ENV, "DREAMKEEPER_CHAT_ENABLED": "1",
                                                "DREAMKEEPER_CHAT_DB": str(self.bed.path),
                                                "DREAMKEEPER_CHAT_OWNER_ID": "owner-a"})
        self.env.start()
        app = FastAPI()
        self.assertTrue(chat_api.install_dreamkeeper_chat(app))
        self.client = TestClient(app, base_url="https://testserver")

    def tearDown(self):
        chat_api.configure_trial_runner(None)
        chat_api.reset_chat_cores()
        self.env.stop()
        self.bed.close()

    def post(self, path, body, auth=True):
        return self.client.post("/dreamkeeper/chat/" + path, json={**KEY, **body},
                                headers=AUTH if auth else {})

    def test_every_admin_route_requires_owner_and_enable_flag(self):
        for path in ("source/policy", "policy/advance", "source/correct", "output/recheck",
                     "output/release", "quarantine/evaluate", "quarantine/trial",
                     "quarantine/adopt"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, {}, auth=False).status_code, 401)
                with mock.patch.dict(os.environ, {"DREAMKEEPER_CHAT_ENABLED": ""}):
                    self.assertEqual(self.post(path, {}).status_code, 404)

    def test_policy_correction_recheck_release_flow(self):
        r = self.post("source/policy", {"source_id": "s2", "allowed_purposes": ["fact"],
                                        "valid": True, "user_receipt": "p"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["allowed_purposes"], ["fact"])
        self.assertEqual(self.post("source/policy", {"source_id": "s2", "allowed_purposes":
                                   ["fact"], "valid": "true", "user_receipt": "p"}).status_code, 422)
        r = self.post("source/correct", {"source_id": "s1", "fields": {"body": S1 + " 보충."},
                                         "expected_revision": 1, "user_receipt": "fix"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["classification"][AFFECTED], [self.oid])
        self.assertEqual(data["held_outputs"], [self.oid])
        self.assertEqual(data["labels"][UNRESOLVED], "의존 미확정")
        stale = self.post("source/correct", {"source_id": "s1", "fields": {"body": "x"},
                                             "expected_revision": 1, "user_receipt": "fix"})
        self.assertEqual(stale.status_code, 409)
        release = {"output_id": self.oid, "user_confirmed": True, "user_receipt": "u"}
        self.assertEqual(self.post("output/release", release).status_code, 409)
        r = self.post("output/recheck", {"output_id": self.oid, "purpose": "explain",
                                         "sources": {"s1": ["body"]}, "user_receipt": "re"})
        self.assertEqual((r.status_code, r.json()["ok"]), (200, True), r.text)
        self.assertEqual(self.post("output/release", {**release, "user_confirmed": False}
                                   ).status_code, 409)
        r = self.post("output/release", release)
        self.assertEqual((r.status_code, r.json()["status"]), (200, "RELEASED"))
        r = self.post("policy/advance", {"reason": "new policy", "user_receipt": "u"})
        self.assertEqual(r.json(), {"policy_generation": 2})

    def test_quarantine_evaluate_trial_adopt_routes(self):
        qid = next(iter(self.bed.core.load_ledger(L, T)["quarantine"]))
        r = self.post("quarantine/evaluate", {"domain": "education", "purpose": "explain",
                                              "io_spec": IO})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([p["quarantine_id"] for p in r.json()["permitted"]], [qid])
        adopt = {"quarantine_id": qid, "user_confirmed": True, "user_receipt": "u"}
        self.assertEqual(self.post("quarantine/adopt", adopt).status_code, 409)
        trial = self.post("quarantine/trial", {"quarantine_id": qid})
        self.assertEqual(trial.status_code, 503)
        seen = []
        chat_api.configure_trial_runner(lambda body: seen.append(body) or {"passed": True})
        trial = self.post("quarantine/trial", {"quarantine_id": qid})
        self.assertEqual(trial.status_code, 200, trial.text)
        self.assertEqual(trial.json()["adoption_status"], "NOT_ADOPTED")
        self.assertEqual(seen[0]["sentence"], SUPPORTED_BUT_FLAGGED)
        self.assertEqual(self.post("quarantine/adopt", {**adopt, "user_confirmed": "true"}
                                   ).status_code, 422)
        self.assertEqual(self.post("quarantine/adopt", {**adopt, "user_confirmed": False}
                                   ).status_code, 409)
        r = self.post("quarantine/adopt", adopt)
        self.assertEqual((r.status_code, r.json()["adoption_status"]), (200, "ADOPTED"))
        bad_io = self.post("quarantine/evaluate", {"domain": "education", "purpose": "explain",
                                                   "io_spec": {**IO, "extra": "x"}})
        self.assertEqual(bad_io.status_code, 422)


if __name__ == "__main__":
    unittest.main()
