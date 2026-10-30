"""챗봇 핵심부 시험: 실제 SQLite 파일, 가짜 모델 응답만 사용."""
import math
import sqlite3
import unittest
from unittest import mock

from app.services.dreamkeeper_chat_core import (
    AFFECTED, NOT_DEPENDENT, UNRESOLVED, ChatHold, DreamkeeperChatCore, default_goal_checker,
    default_support_score, load_config, parse_config, split_sentences,
)
from app.services.dreamkeeper_engine import EngineHold

from dreamkeeper_chat_support import (
    BAD, GOALS, GOOD, L, S1, T, Bed, Recorder, answer, raw_config, verifier, worker,
)


def full_turn(bed, spec, ver=None):
    """그래프 없이 성공 경로 전체를 핵심부 단계로 실행."""
    core = bed.core
    tid, binding = bed.steps(spec)
    assert core.check_goals(L, T, tid)["ok"]
    assert core.verify(L, T, tid, ver or verifier(), spec)["ok"]
    assert core.check_generation(L, T, tid, stage="before_sentences")
    core.filter_sentences(L, T, tid)
    oid = core.record_receipt(L, T, tid)
    return tid, oid, core.emit_output(L, T, tid)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def test_ledger_bundle_survives_restart(self):
        spec = worker(reply=answer(GOOD + " " + BAD))
        tid, oid, out = full_turn(self.bed, spec)
        self.assertIsNotNone(out)
        before = self.bed.core.load_ledger(L, T)
        core = self.bed.reopen()
        after = core.load_ledger(L, T)
        for key in ("goals", "obligations", "priorities", "generation", "sources",
                    "source_policies", "usage_records", "holds", "quarantine", "engines", "turns"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(after["goals"], GOALS)
        self.assertEqual(after["generation"]["policy_generation"], 1)
        self.assertIn(oid, after["usage_records"])
        self.assertEqual(len(after["quarantine"]), 1)
        self.assertEqual(after["logical_id"], L)

    def test_multi_row_write_is_all_or_nothing(self):
        core = self.bed.core
        before = core.load_ledger(L, T)["source_policies"]["s1"]
        with mock.patch.object(DreamkeeperChatCore, "_event", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                core.set_source_policy(L, T, source_id="s1", allowed_purposes=["fact"],
                                       valid=False, user_receipt="x")
            with self.assertRaises(RuntimeError):
                core.advance_policy_generation(L, T, reason="r", user_receipt="u")
        ledger = core.load_ledger(L, T)
        self.assertEqual(ledger["source_policies"]["s1"], before)
        self.assertEqual(ledger["generation"]["policy_generation"], 1)

    def test_ledger_rows_and_events_cannot_be_deleted_or_rewritten(self):
        full_turn(self.bed, worker(reply=answer(GOOD + " " + BAD)))
        db = sqlite3.connect(self.bed.path)
        try:
            for sql in ("DELETE FROM dk_chat_items WHERE kind='quarantine'",
                        "DELETE FROM dk_chat_items",
                        "UPDATE dk_chat_items SET body='{}' WHERE kind='quarantine'",
                        "UPDATE dk_chat_items SET body='{}' WHERE kind='output'",
                        "DELETE FROM dk_chat_events",
                        "UPDATE dk_chat_events SET payload='{}'"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    db.execute(sql)
        finally:
            db.close()
        self.assertEqual(len(self.bed.core.load_ledger(L, T)["quarantine"]), 1)
        self.assertFalse(hasattr(self.bed.core, "delete_quarantine"))


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def evidence_given(self, purpose="explain"):
        spec = worker()
        core = self.bed.core
        tid = core.start_turn(L, T, question="q", purpose=purpose, domain="education")
        core.judge_purpose(L, T, tid)
        selection = core.select_evidence(L, T, tid)
        if selection is None:
            return tid, None
        binding = core.begin_attempt(L, T, tid, spec)
        core.generate(L, T, tid, binding, spec)
        return tid, spec.generate.calls[0]["evidence"]

    def test_only_valid_purpose_allowed_current_generation_sources(self):
        core = self.bed.core
        core.set_source_policy(L, T, source_id="s2", allowed_purposes=["fact"], valid=True,
                               user_receipt="p")
        _, evidence = self.evidence_given("explain")
        self.assertEqual(set(evidence), {"s1"})
        core.set_source_policy(L, T, source_id="s1", allowed_purposes=["explain"], valid=False,
                               user_receipt="p")
        tid, evidence = self.evidence_given("fact")
        self.assertEqual(set(evidence), {"s2"})
        turn = core.turn(L, T, tid)["state"]
        self.assertIn("source not valid", turn["excluded"]["s1"])

    def test_policy_generation_change_disqualifies_until_reapproved(self):
        core = self.bed.core
        self.assertEqual(core.advance_policy_generation(L, T, reason="new policy",
                                                        user_receipt="u"), 2)
        tid, evidence = self.evidence_given()
        self.assertIsNone(evidence)
        turn = core.turn(L, T, tid)["state"]
        self.assertEqual(turn["status"], "HOLD")
        self.assertEqual(turn["hold_reason"], "no eligible evidence")
        self.assertIn("source policy generation is not current", turn["excluded"]["s1"])
        core.set_source_policy(L, T, source_id="s1", allowed_purposes=["explain"], valid=True,
                               user_receipt="again")
        _, evidence = self.evidence_given()
        self.assertEqual(set(evidence), {"s1"})

    def test_run_epoch_is_not_policy_generation(self):
        core = self.bed.core
        self.bed.engine.begin_run(L, T, model_id="x", session_id="y")
        self.assertEqual(core.load_ledger(L, T)["generation"]["policy_generation"], 1)

    def test_policy_rejects_unknown_purpose_and_missing_source(self):
        with self.assertRaises(ValueError):
            self.bed.core.set_source_policy(L, T, source_id="s1", allowed_purposes=["sell"],
                                            valid=True, user_receipt="u")
        with self.assertRaises(ChatHold):
            self.bed.core.set_source_policy(L, T, source_id="nope", allowed_purposes=["fact"],
                                            valid=True, user_receipt="u")


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def test_receipt_is_host_delivery_not_model_claim(self):
        spec = worker(reply=answer(claimed_sources={}))
        _, oid, out = full_turn(self.bed, spec)
        sources = out["receipt"]["sources"]
        self.assertEqual(set(sources), {"s1", "s2"})
        self.assertEqual(sources["s1"]["fields"], {"body": 1, "note": 1})
        self.assertTrue(out["receipt"]["complete"])
        self.assertEqual(out["receipt"]["basis"], "HOST_DELIVERED")

    def test_claim_of_undelivered_source_blocks_output(self):
        spec = worker(reply=answer(claimed_sources={"secret": {"x": 1}}))
        tid, binding = self.bed.steps(spec)
        core = self.bed.core
        core.filter_sentences(L, T, tid)
        self.assertIsNone(core.record_receipt(L, T, tid))
        turn = core.turn(L, T, tid)["state"]
        self.assertEqual((turn["status"], turn["hold_reason"]), ("HOLD", "receipt incomplete"))
        self.assertEqual(core.load_ledger(L, T)["usage_records"], {})

    def test_receipt_problem_rules(self):
        sel = {"s1": ["a", "b"]}
        delivered = {"s1": {"a": 1, "b": 1}}
        P = DreamkeeperChatCore.receipt_problems
        self.assertEqual(P(sel, delivered, delivered, {}), [])
        self.assertTrue(P(sel, {"s1": {"a": 1}}, {"s1": {"a": 1}}, {}))
        self.assertTrue(P(sel, delivered, {"s1": {"a": 1}}, {}))
        self.assertTrue(P(sel, delivered, delivered, {"s1": {"c": 1}}))
        self.assertTrue(P(sel, delivered, delivered, ["s1"]))
        self.assertTrue(P({}, {}, {}, {}))


class SelectiveCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def make_output(self, purpose_sources):
        core = self.bed.core
        for sid in ("s1", "s2"):
            core.set_source_policy(L, T, source_id=sid, allowed_purposes=["explain"],
                                   valid=sid in purpose_sources, user_receipt="p")
        text = GOOD if "s1" in purpose_sources else "호흡은 포도당을 분해해 에너지를 얻는 과정이다."
        _, oid, out = full_turn(self.bed, worker(reply=answer(text)))
        self.assertIsNotNone(out)
        return oid

    def test_three_way_classification_and_hold_list(self):
        core = self.bed.core
        used_s1 = self.make_output({"s1"})
        used_s2 = self.make_output({"s2"})
        no_receipt = core.import_result(L, T, text="SDK 초안", origin="sdk-draft",
                                        user_receipt="u")
        result = core.correct_source(L, T, source_id="s1", fields={"body": "정정된 본문"},
                                     expected_revision=1, user_receipt="fix")
        summary = result["classification"]
        self.assertEqual(summary[AFFECTED], [used_s1])
        self.assertEqual(summary[NOT_DEPENDENT], [used_s2])
        self.assertEqual(summary[UNRESOLVED], [no_receipt])
        ledger = core.load_ledger(L, T)
        self.assertEqual(set(ledger["holds"]["outputs"]), {used_s1, no_receipt})
        self.assertEqual(ledger["usage_records"][no_receipt]["state"]["classification_ko"],
                         "의존 미확정")

    def test_unresolved_is_never_treated_as_not_dependent(self):
        core = self.bed.core
        oid = core.import_result(L, T, text="outside", origin="sdk", user_receipt="u")
        incomplete = core.import_result(L, T, text="o2", origin="sdk", user_receipt="u",
                                        receipt={"complete": False, "sources": {"s2": {
                                            "fields": {"body": 1}}}})
        claimed_complete_empty = core.import_result(L, T, text="o3", origin="sdk",
                                                    user_receipt="u",
                                                    receipt={"complete": True, "sources": {}})
        core.correct_source(L, T, source_id="s2", fields={"body": "새 본문"},
                            expected_revision=1, user_receipt="fix")
        records = core.load_ledger(L, T)["usage_records"]
        for item in (oid, incomplete, claimed_complete_empty):
            self.assertEqual(records[item]["state"]["classification"], UNRESOLVED)
            self.assertTrue(records[item]["state"]["hold"])

    def test_field_level_selectivity(self):
        core = self.bed.core
        oid = core.import_result(L, T, text="x", origin="manual", user_receipt="u",
                                 receipt={"complete": True, "sources": {"s1": {"fields": {"body": 1}}}})
        core.correct_source(L, T, source_id="s1", fields={"note": "교과서 4장"},
                            expected_revision=1, user_receipt="fix")
        state = core.load_ledger(L, T)["usage_records"][oid]["state"]
        self.assertEqual(state["classification"], NOT_DEPENDENT)
        self.assertFalse(state["hold"])

    def test_crash_between_engine_write_and_classification_recovers(self):
        oid = self.make_output({"s1"})
        self.bed.engine.put_source(L, T, source_id="s1", fields={"body": "직접 정정"},
                                   expected_revision=1, user_receipt="bypass")
        core = self.bed.reopen()
        state = core.load_ledger(L, T)["usage_records"][oid]["state"]
        self.assertEqual(state["classification"], AFFECTED)
        self.assertTrue(state["hold"])


class GoalDriftTests(unittest.TestCase):
    def check(self, response, question="q"):
        return default_goal_checker({"goals": GOALS, "priorities": ["g1", "g2"],
                                     "goal_revision": 1, "question": question,
                                     "response": {"text": GOOD, "goal_ids": list(GOALS),
                                                  **response}})

    def test_default_checker(self):
        self.assertTrue(self.check({})["ok"])
        self.assertTrue(self.check({"declared_goals": {"g1": " 광합성 과정을  설명한다 "}})["ok"])
        failing = [
            {"goal_ids": ["g1"]},
            {"goal_ids": ["g1", "g2", "g3"]},
            {"declared_goals": {"g1": "광합성 대신 시를 쓴다"}},
            {"declared_goals": {"g9": "x"}},
            {"goal_changes": {"remove": ["g2"]}},
            {"text": "우선순위에 따라 나머지 목표는 삭제합니다."},
            {"text": "다른 목표는 무시하고 진행합니다."},
            {"text": "I will drop the other goals now."},
        ]
        for response in failing:
            with self.subTest(response=response):
                self.assertFalse(self.check(response)["ok"])

    def test_priority_instruction_is_not_deletion_authority(self):
        result = self.check({"goal_ids": ["g1"], "goal_changes": {"remove": ["g2"]}},
                            question="g1을 최우선으로 하고 나머지는 빼 주세요")
        self.assertFalse(result["ok"])

    def test_engine_goal_check_uses_injected_checker_and_ledger_goals(self):
        seen = []

        def checker(check):
            seen.append(check)
            return {"ok": False, "reasons": ["custom"]}

        bed = Bed(goal_checker=checker)
        try:
            tid, _ = bed.steps(worker())
            result = bed.core.check_goals(L, T, tid)
            self.assertEqual(result, {"ok": False, "reasons": ["custom"]})
            self.assertEqual(seen[0]["goals"], GOALS)
            bed2 = Bed(goal_checker=lambda c: {"ok": "yes"})
            try:
                tid, _ = bed2.steps(worker())
                self.assertFalse(bed2.core.check_goals(L, T, tid)["ok"])
            finally:
                bed2.close()
        finally:
            bed.close()


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def test_same_family_is_refused_and_recorded(self):
        spec = worker(family="Family-A")
        tid, _ = self.bed.steps(spec)
        same = verifier(family="family-a ")
        with self.assertRaises(ChatHold):
            self.bed.core.verify(L, T, tid, same, spec)
        self.assertEqual(same.verify.calls, [])
        kinds = [e["kind"] for e in self.bed.core.events(L, T)]
        self.assertIn("VERIFIER_REFUSED", kinds)

    def test_verifier_sees_only_evidence_and_response(self):
        spec = worker()
        tid, _ = self.bed.steps(spec, question="비밀 질문 문장")
        ver = verifier()
        self.bed.core.verify(L, T, tid, ver, spec)
        payload = ver.verify.calls[0]
        self.assertEqual(set(payload), {"evidence", "response"})
        self.assertNotIn("비밀 질문 문장", str(payload))
        self.assertEqual(payload["response"], GOOD)
        self.assertEqual(payload["evidence"]["s1"]["body"], S1)

    def test_non_boolean_or_error_verdict_fails(self):
        spec = worker()
        for reply in ({"supported": "true"}, {"supported": 1}, None):
            tid, _ = self.bed.steps(spec)
            ver = verifier()
            ver.verify.reply = reply
            self.assertFalse(self.bed.core.verify(L, T, tid, ver, spec)["ok"])


class EngineHandoverTests(unittest.TestCase):
    def test_failure_limit_quarantines_engine_and_keeps_logical_id(self):
        bed = Bed()
        try:
            core = bed.core
            spec = worker(name="a", reply=answer(goal_ids=["g1"]))
            tid = core.start_turn(L, T, question="q", purpose="explain", domain="education")
            core.judge_purpose(L, T, tid); core.select_evidence(L, T, tid)
            bindings = []
            for n in range(bed.config.max_failures_per_engine):
                self.assertIs(core.next_engine(L, T, [spec]), spec)
                b = core.begin_attempt(L, T, tid, spec); bindings.append(b)
                core.generate(L, T, tid, b, spec)
                self.assertFalse(core.check_goals(L, T, tid)["ok"])
                entry = core.record_failure(L, T, tid, engine_name="a", reason="goal drift")
            self.assertTrue(entry["quarantined"])
            self.assertIsNone(core.next_engine(L, T, [spec]))
            with self.assertRaises(ChatHold):
                core.begin_attempt(L, T, tid, spec)
            nxt = worker(name="b", family="family-b")
            b = core.begin_attempt(L, T, tid, core.next_engine(L, T, [spec, nxt]))
            bindings.append(b)
            self.assertEqual({x.logical_id for x in bindings}, {L})
            self.assertEqual({x.task_id for x in bindings}, {T})
            self.assertEqual(b.model_id, "b")
            core = bed.reopen()
            self.assertTrue(core.engine_status(L, T)["a"]["quarantined"])
            failed = [q for q in core.load_ledger(L, T)["quarantine"].values()
                      if q["body"]["type"] == "failed_response"]
            self.assertEqual(len(failed), bed.config.max_failures_per_engine)
        finally:
            bed.close()


class LateResponseTests(unittest.TestCase):
    def setUp(self):
        self.bed = Bed()

    def tearDown(self):
        self.bed.close()

    def assert_late_recorded(self, stage):
        core = self.bed.reopen()
        events = [e for e in core.events(L, T) if e["kind"] == "LATE_RESPONSE_REJECTED"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["stage"], stage)
        ledger = core.load_ledger(L, T)
        self.assertFalse(any(r["state"]["status"] == "OUTPUT"
                             for r in ledger["usage_records"].values()))
        rejected = [q for q in ledger["quarantine"].values()
                    if q["body"]["type"] == "rejected_response"]
        self.assertEqual(len(rejected), 1)
        return events[0]

    def test_source_correction_during_generation_discards_response(self):
        def reply(_):
            self.bed.core.correct_source(L, T, source_id="s1", fields={"body": "중간 정정"},
                                         expected_revision=1, user_receipt="fix")
            return answer()
        spec = worker(reply=reply)
        tid, _ = self.bed.steps(spec)
        self.assertFalse(self.bed.core.check_generation(L, T, tid, stage="before_sentences"))
        event = self.assert_late_recorded("before_sentences")
        self.assertIn("sources changed", event["payload"]["reasons"])

    def test_replaced_run_discards_response(self):
        def reply(_):
            self.bed.engine.begin_run(L, T, model_id="other", session_id="other-window")
            return answer()
        tid, _ = self.bed.steps(worker(reply=reply))
        self.assertFalse(self.bed.core.check_generation(L, T, tid, stage="before_sentences"))
        event = self.assert_late_recorded("before_sentences")
        self.assertIn("engine run replaced", event["payload"]["reasons"])

    def test_final_check_right_before_output(self):
        core = self.bed.core
        spec = worker()
        tid, _ = self.bed.steps(spec)
        core.check_goals(L, T, tid); core.verify(L, T, tid, verifier(), spec)
        self.assertTrue(core.check_generation(L, T, tid, stage="before_sentences"))
        core.filter_sentences(L, T, tid)
        oid = core.record_receipt(L, T, tid)
        self.assertIsNotNone(oid)
        core.advance_policy_generation(L, T, reason="late change", user_receipt="u")
        self.assertIsNone(core.emit_output(L, T, tid))
        self.assert_late_recorded("output")
        self.assertEqual(self.bed.core.load_ledger(L, T)["usage_records"][oid]["state"]["status"],
                         "WITHHELD")


class HallucinationControlTests(unittest.TestCase):
    def test_below_threshold_sentences_are_quarantined_not_output(self):
        bed = Bed()
        try:
            _, oid, out = full_turn(bed, worker(reply=answer(GOOD + " " + BAD)))
            self.assertEqual(out["text"], GOOD)
            self.assertNotIn("치즈", out["text"])
            q = list(bed.core.load_ledger(L, T)["quarantine"].values())
            self.assertEqual(len(q), 1)
            self.assertEqual(q[0]["body"]["sentence"], BAD)
            self.assertLess(q[0]["state"]["score"], bed.config.sentence_support_threshold)
            self.assertEqual(q[0]["state"]["threshold"], 0.6)
        finally:
            bed.close()

    def test_threshold_is_read_from_config(self):
        bed = Bed(config=raw_config(sentence_support_threshold=1.0),
                  scorer=lambda s, e: 0.99)
        try:
            tid, _ = bed.steps(worker())
            self.assertEqual(bed.core.filter_sentences(L, T, tid), [])
        finally:
            bed.close()
        self.assertEqual(load_config().sentence_support_threshold, 0.6)

    def test_injected_scorer_and_invalid_scores_fail_closed(self):
        for value in (True, math.nan, 1.5, -0.1, "0.9", RuntimeError):
            def scorer(s, e, value=value):
                if value is RuntimeError:
                    raise RuntimeError("x")
                return value
            bed = Bed(scorer=scorer)
            try:
                tid, _ = bed.steps(worker())
                with self.subTest(value=value):
                    self.assertEqual(bed.core.filter_sentences(L, T, tid), [])
                    q = list(bed.core.load_ledger(L, T)["quarantine"].values())
                    self.assertEqual(q[0]["state"]["reason"], "scorer invalid")
            finally:
                bed.close()

    def test_default_scorer_and_splitter(self):
        self.assertEqual(split_sentences("가. 나!  다?\n라"), ["가.", "나!", "다?", "라"])
        self.assertGreaterEqual(default_support_score(GOOD, [S1]), 0.99)
        self.assertLess(default_support_score(BAD, [S1]), 0.6)
        self.assertEqual(default_support_score("2024년에 발표되었다.", ["2023년에 발표되었다."]), 0.0)

    def test_config_validation(self):
        bad = [
            raw_config(sentence_support_threshold=0),
            raw_config(sentence_support_threshold=1.5),
            raw_config(sentence_support_threshold=True),
            raw_config(max_failures_per_engine=0),
            raw_config(max_failures_per_engine=True),
            raw_config(known_purposes=["fact", "fact"]),
            raw_config(domain_priority_weight={"education": 1.0}),
            raw_config(schema="other"),
            {**raw_config(), "extra": 1},
        ]
        missing = raw_config(); del missing["allowed_domains"]; bad.append(missing)
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_config(raw)


class QuarantineGateTests(unittest.TestCase):
    IO = {"input_unit": "question+evidence", "output_unit": "sentence",
          "operation": "evidence_grounded_answer"}

    def setUp(self):
        self.score = {"value": 0.3}
        self.bed = Bed(scorer=lambda s, e: self.score["value"] if "치즈" in s else 1.0)
        full_turn(self.bed, worker(reply=answer(GOOD + " " + BAD)))
        self.qid = next(iter(self.bed.core.load_ledger(L, T)["quarantine"]))

    def tearDown(self):
        self.bed.close()

    def evaluate(self, domain="education", purpose="explain", io=None):
        return self.bed.core.evaluate_quarantine_candidates(L, T, domain=domain, purpose=purpose,
                                                           io_spec=io or self.IO)

    def gates(self):
        return self.bed.core.load_ledger(L, T)["quarantine"][self.qid]["state"]

    def test_all_five_gates_then_priority_then_trial_without_adoption(self):
        self.assertEqual(self.evaluate(), [])
        self.assertFalse(self.gates()["gate_history"][-1]["gates"]["resume_condition"])
        self.score["value"] = 0.8
        permitted = self.evaluate()
        self.assertEqual([p["quarantine_id"] for p in permitted], [self.qid])
        self.assertAlmostEqual(permitted[0]["priority"], 0.8)
        state = self.gates()
        self.assertEqual(state["trial_status"], "TRIAL_PERMITTED")
        self.assertEqual(state["adoption_status"], "NOT_ADOPTED")
        result = self.bed.core.run_trial(L, T, self.qid, lambda body: {"passed": True})
        self.assertEqual(result["adoption_status"], "NOT_ADOPTED")
        state = self.gates()
        self.assertEqual((state["trial_status"], state["adoption_status"]),
                         ("TRIAL_DONE", "NOT_ADOPTED"))
        self.assertTrue(state["trials"][0]["passed"])

    def test_each_gate_blocks_independently(self):
        self.score["value"] = 0.8
        cases = {
            "domain_allowed": lambda: self.evaluate(domain="patent"),
            "io_units_and_operation": lambda: self.evaluate(io={**self.IO, "output_unit": "word"}),
        }
        for gate, run in cases.items():
            with self.subTest(gate=gate):
                self.assertEqual(run(), [])
                self.assertFalse(self.gates()["gate_history"][-1]["gates"][gate])
                with self.assertRaises(ChatHold):
                    self.bed.core.run_trial(L, T, self.qid, lambda b: {"passed": True})
        self.bed.core.advance_policy_generation(L, T, reason="new", user_receipt="u")
        self.assertEqual(self.evaluate(), [])
        self.assertFalse(self.gates()["gate_history"][-1]["gates"]["eligibility_and_policy_generation"])
        for sid in ("s1", "s2"):
            self.bed.core.set_source_policy(L, T, source_id=sid, allowed_purposes=["explain"],
                                            valid=True, user_receipt="re")
        self.bed.core.correct_source(L, T, source_id="s2", fields={"body": "정정"},
                                     expected_revision=1, user_receipt="fix")
        self.assertEqual(self.evaluate(), [])
        last = self.gates()["gate_history"][-1]["gates"]
        self.assertFalse(last["required_receipts"])
        self.assertFalse(last["resume_condition"])

    def test_quarantine_is_never_removed(self):
        before = len(self.bed.core.load_ledger(L, T)["quarantine"])
        self.evaluate(); self.score["value"] = 0.9; self.evaluate()
        self.bed.core.run_trial(L, T, self.qid, lambda b: {"passed": False})
        self.assertEqual(len(self.bed.reopen().load_ledger(L, T)["quarantine"]), before)


if __name__ == "__main__":
    unittest.main()
