"""Real SQLite tests, including cross-connection admission/correction races."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from app.services.dreamkeeper_engine import DreamkeeperEngine, EngineHold


class DreamkeeperEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.path=Path(self.tmp.name)/"engine.sqlite3"
        self.engine=DreamkeeperEngine(self.path,"owner-a")
        self.create(self.engine)
        self.source(self.engine,"source",{"x":"original x","y":"original y"})
        self.binding=self.engine.begin_run("logical","task",model_id="model-a",session_id="window-a")

    def tearDown(self):
        self.engine.close();self.tmp.cleanup()

    @staticmethod
    def create(engine,logical="logical",task="task"):
        return engine.create_task(logical,task,goals={"g1":"write","g2":"verify"},
            priorities=["g1","g2"],rules=["preserve corrections"],
            obligations={"o1":"verify actual answer","o2":"retain unfinished work"},
            user_receipt="authenticated-user-1")

    @staticmethod
    def source(engine,sid,fields,expected=0,logical="logical",task="task"):
        return engine.put_source(logical,task,source_id=sid,fields=fields,
                                 expected_revision=expected,user_receipt="source-review")

    def candidate(self,refs=None,*,complete=True,purpose="fact",content=None,port=None):
        return (port or self.engine.model_port(self.binding)).submit_candidate(
            content={"answer":"candidate"} if content is None else content,purpose=purpose,
            source_refs={"source":{"x":1}} if refs is None else refs,
            consumption_complete=complete,goal_ids=["g1","g2"])

    def approve(self,cid,engine=None,**kw):
        eng=engine or self.engine
        args=dict(candidate_id=cid,checks={k:True for k in eng.REVIEW_CHECKS},
                  reviewer="trusted-host",receipt="review-1",obligation_checks={"o1":True,"o2":True})
        args.update(kw)
        return eng.review_candidate("logical","task",**args)

    def consume(self,cid,aid,engine=None,purpose="fact"):
        return (engine or self.engine).consume("logical","task",candidate_id=cid,
                                               approval_id=aid,purpose=purpose)

    def test_restart_preserves_goals_corrections_and_obligations(self):
        self.engine.revise_task("logical","task",expected_revision=1,user_authorized=True,
            user_receipt="user-correction",instruction="continue original purpose",rules=["new rule"])
        other=DreamkeeperEngine(self.path,"owner-a")
        try:
            state=other.snapshot("logical","task")
            self.assertEqual(state["goal_revision"],2)
            self.assertEqual(state["priorities"],["g1","g2"])
            self.assertEqual(set(state["obligations"]),{"o1","o2"})
            self.assertEqual(state["corrections"][-1]["instruction"],"continue original purpose")
        finally:other.close()

    def test_window_replacement_keeps_identity_but_fences_old_response(self):
        cid=self.candidate();aid=self.approve(cid)
        new=self.engine.begin_run("logical","task",model_id="model-b",session_id="window-b")
        self.assertEqual(new.logical_id,self.binding.logical_id)
        self.assertNotEqual(new.run_id,self.binding.run_id)
        self.assertGreater(new.run_epoch,self.binding.run_epoch)
        with self.assertRaises(EngineHold):self.candidate()
        with self.assertRaises(EngineHold):self.consume(cid,aid)
        self.assertEqual(len(self.engine.model_port(new).read_contract({})["obligations"]),2)

    def test_private_memory_database_survives_multiple_method_connections(self):
        memory=DreamkeeperEngine(":memory:","owner")
        try:
            self.create(memory)
            self.source(memory,"source",{"x":"value"})
            run=memory.begin_run("logical","task",model_id="m",session_id="s")
            cid=self.candidate(port=memory.model_port(run))
            aid=self.approve(cid,engine=memory)
            self.assertEqual(self.consume(cid,aid,engine=memory)["status"],"CONSUMED")
            self.assertGreater(len(memory.history("logical","task")),3)
        finally:memory.close()

    def test_owner_and_task_isolation(self):
        cid=self.candidate()
        other=DreamkeeperEngine(self.path,"owner-b")
        try:
            with self.assertRaises(EngineHold):other.snapshot("logical","task")
            self.create(other)
            with self.assertRaises(EngineHold):other.candidate("logical","task",cid)
            with self.assertRaises(EngineHold):other.model_port(self.binding).read_contract()
            self.create(self.engine,task="other-task")
            with self.assertRaises(EngineHold):self.engine.candidate("logical","other-task",cid)
            self.create(self.engine,logical="other-logical")
            with self.assertRaises(EngineHold):self.engine.candidate("other-logical","task",cid)
        finally:other.close()

    def test_existing_task_cannot_be_overwritten(self):
        with self.assertRaises(EngineHold):self.create(self.engine)

    def test_model_surface_cannot_change_goals_or_approve(self):
        port=self.engine.model_port(self.binding)
        for method in ("revise_task","review_candidate","consume","resume_after_check","create_task"):
            self.assertFalse(hasattr(port,method))
        with self.assertRaises(EngineHold):
            self.engine.revise_task("logical","task",expected_revision=1,
                user_authorized=False,user_receipt="model says user approved",goals={"g1":"only"},priorities=["g1"])
        self.assertEqual(set(self.engine.snapshot("logical","task")["goals"]),{"g1","g2"})

    def test_revision_bool_zero_and_stale_user_change_rejected(self):
        for revision in (True,0):
            with self.subTest(revision=revision),self.assertRaises(ValueError):
                self.engine.revise_task("logical","task",expected_revision=revision,
                    user_authorized=True,user_receipt="user")
        with self.assertRaises(EngineHold):
            self.engine.revise_task("logical","task",expected_revision=2,
                user_authorized=True,user_receipt="user")

    def test_goal_revision_change_invalidates_old_approval(self):
        cid=self.candidate();aid=self.approve(cid)
        self.engine.revise_task("logical","task",expected_revision=1,user_authorized=True,
            user_receipt="authenticated reprioritization",priorities=["g2","g1"])
        with self.assertRaises(EngineHold):self.consume(cid,aid)
        state=self.engine.snapshot("logical","task")
        self.assertEqual(state["priorities"],["g2","g1"])
        self.assertEqual(set(state["goals"]),{"g1","g2"})

    def test_candidate_preserves_raw_content_hash_and_purpose(self):
        value={"thought":"odd idea, not established fact","symbols":["가",1]}
        cid=self.candidate(purpose="creative",content=value)
        saved=self.engine.candidate("logical","task",cid)
        self.assertEqual(saved["content"],value)
        self.assertEqual(saved["content_hash"],sha256(saved["content_raw"].encode()).hexdigest())
        aid=self.approve(cid)
        with self.assertRaises(EngineHold):self.consume(cid,aid,purpose="fact")
        self.assertEqual(self.consume(cid,aid,purpose="creative")["fact_status"],"NOT_ASSERTED")

    def test_candidate_must_account_for_every_goal(self):
        with self.assertRaises(EngineHold):
            self.engine.model_port(self.binding).submit_candidate(content={},purpose="draft",
                source_refs={},consumption_complete=True,goal_ids=["g1"])

    def test_delivered_fields_are_host_bound_even_when_model_omits_them(self):
        port=self.engine.model_port(self.binding)
        contract=port.read_contract({"source":["x"]})
        self.assertEqual(set(contract["sources"]["source"]["fields"]),{"x"})
        cid=self.candidate(refs={},port=port)
        saved=self.engine.candidate("logical","task",cid)
        self.assertEqual(saved["source_refs"],{"source":{"x":1}})
        self.source(self.engine,"source",{"x":"changed"},expected=1)
        with self.assertRaises(EngineHold):self.approve(cid)

    def test_host_guard_does_not_claim_fields_were_delivered(self):
        port=self.engine.model_port(self.binding)
        port.read_contract(record_delivery=False)
        cid=self.candidate(refs={"source":{"y":1}},port=port)
        self.assertEqual(self.engine.candidate("logical","task",cid)["source_refs"],{"source":{"y":1}})

    def test_model_claim_is_not_complete_trace_proof(self):
        cid=self.candidate(refs={"source":{"y":1}},complete=True)
        self.source(self.engine,"source",{"x":"changed"},expected=1)
        self.assertEqual(self.engine.candidate("logical","task",cid)["status"],"HOLD")
        with self.assertRaises(EngineHold):self.approve(cid)

    def test_exact_dependent_field_held_verified_unrelated_candidate_kept(self):
        affected=self.candidate(refs={"source":{"x":1}})
        preserved=self.candidate(refs={"source":{"y":1}})
        affected_approval=self.approve(affected);preserved_approval=self.approve(preserved)
        self.source(self.engine,"source",{"x":"corrected x"},expected=1)
        self.assertEqual(self.engine.candidate("logical","task",affected)["status"],"HOLD")
        self.assertEqual(self.engine.candidate("logical","task",preserved)["status"],"APPROVED")
        with self.assertRaises(EngineHold):self.consume(affected,affected_approval)
        self.assertEqual(self.consume(preserved,preserved_approval)["status"],"CONSUMED")

    def test_incomplete_use_record_not_admitted(self):
        cid=self.candidate(complete=False)
        with self.assertRaises(EngineHold):self.approve(cid)
        self.source(self.engine,"new-source",{"unused":"new"})
        self.assertEqual(self.engine.candidate("logical","task",cid)["status"],"HOLD")

    def test_same_source_value_does_not_invalidate_current_approval(self):
        cid=self.candidate();aid=self.approve(cid)
        result=self.source(self.engine,"source",{"x":"original x"},expected=1)
        self.assertEqual(result["revision"],1)
        self.assertEqual(self.consume(cid,aid)["status"],"CONSUMED")

    def test_source_revision_checked_at_submission(self):
        self.source(self.engine,"source",{"x":"changed"},expected=1)
        with self.assertRaises(EngineHold):self.candidate()
        with self.assertRaises(ValueError):self.candidate(refs={"source":{"x":True}})

    def test_every_obligation_needs_current_host_check(self):
        cid=self.candidate()
        for checks in ({},{"o1":True},{"o1":True,"o2":False},{"o1":True,"o2":True,"extra":True}):
            with self.subTest(checks=checks),self.assertRaises(EngineHold):
                self.approve(cid,obligation_checks=checks)
        aid=self.approve(cid)
        self.assertEqual(self.consume(cid,aid)["status"],"CONSUMED")

    def test_source_trace_and_all_review_checks_required(self):
        cid=self.candidate()
        checks={key:True for key in self.engine.REVIEW_CHECKS}
        del checks["source_trace_verified"]
        with self.assertRaises(EngineHold):self.approve(cid,checks=checks)
        checks["source_trace_verified"]=True;checks["evidence_verified"]=False
        with self.assertRaises(EngineHold):self.approve(cid,checks=checks)

    def test_approval_cannot_be_used_for_another_content_or_twice(self):
        one=self.candidate(content="one");two=self.candidate(content="two")
        aid=self.approve(one)
        with self.assertRaises(EngineHold):self.consume(two,aid)
        self.consume(one,aid)
        with self.assertRaises(EngineHold):self.consume(one,aid)

    def test_exact_reviewed_hash_required_when_provided(self):
        cid=self.candidate()
        with self.assertRaises(EngineHold):self.approve(cid,expected_hash="some-other-content")

    def test_quarantine_and_pause_need_current_resume_checks(self):
        cid=self.candidate();aid=self.approve(cid)
        for method in (self.engine.pause,self.engine.quarantine):
            method("logical","task",reason="inspection",receipt="hold-receipt")
            reviewed=self.engine.snapshot("logical","task")
            with self.assertRaises(EngineHold):
                self.engine.begin_run("logical","task",model_id="new",session_id="new")
            with self.assertRaises(EngineHold):
                self.engine.resume_after_check("logical","task",
                    review_state_hash=reviewed["review_state_hash"],checks={"anything":True},
                    reviewer="host",receipt="not-enough")
            state=self.engine.resume_after_check("logical","task",
                review_state_hash=reviewed["review_state_hash"],
                checks={k:True for k in self.engine.RESUME_CHECKS},reviewer="host",receipt="rechecked")
            self.assertEqual(len(state["obligations"]),2)
        with self.assertRaises(EngineHold):self.consume(cid,aid)
        with self.assertRaises(EngineHold):self.candidate()

    def test_resume_requires_exact_review_state_hash(self):
        self.engine.quarantine("logical","task",reason="inspection",receipt="hold")
        args=dict(checks={key:True for key in self.engine.RESUME_CHECKS},
                  reviewer="host",receipt="review")
        with self.assertRaises(TypeError):
            self.engine.resume_after_check("logical","task",**args)
        with self.assertRaises(EngineHold):
            self.engine.resume_after_check("logical","task",review_state_hash="wrong",**args)
        self.assertEqual(self.engine.snapshot("logical","task")["phase"],"QUARANTINED")

    def test_goal_or_obligation_revision_invalidates_resume_review(self):
        self.engine.quarantine("logical","task",reason="inspection",receipt="hold")
        reviewed=self.engine.snapshot("logical","task")
        self.engine.revise_task("logical","task",expected_revision=1,user_authorized=True,
            user_receipt="user-correction",goals={"g1":"new purpose","g2":"verify"},
            obligations={"o1":"check new answer","o2":"retain unfinished work"})
        args=dict(checks={key:True for key in self.engine.RESUME_CHECKS},
                  reviewer="host",receipt="review")
        with self.assertRaises(EngineHold):
            self.engine.resume_after_check("logical","task",
                review_state_hash=reviewed["review_state_hash"],**args)
        current=self.engine.snapshot("logical","task")
        self.assertNotEqual(current["review_state_hash"],reviewed["review_state_hash"])
        resumed=self.engine.resume_after_check("logical","task",
            review_state_hash=current["review_state_hash"],**args)
        self.assertEqual(resumed["goal_revision"],2)
        self.assertEqual(resumed["phase"],"ACTIVE")

    def test_source_correction_invalidates_resume_review_across_connections(self):
        self.engine.quarantine("logical","task",reason="inspection",receipt="hold")
        reviewed=self.engine.snapshot("logical","task")
        other=DreamkeeperEngine(self.path,"owner-a")
        try:
            self.source(other,"source",{"y":"corrected y"},expected=1)
            with self.assertRaises(EngineHold):
                self.engine.resume_after_check("logical","task",
                    review_state_hash=reviewed["review_state_hash"],
                    checks={key:True for key in self.engine.RESUME_CHECKS},
                    reviewer="host",receipt="old-review")
            self.assertEqual(self.engine.snapshot("logical","task")["phase"],"QUARANTINED")
        finally:other.close()

    def test_repeated_suspension_changes_review_hash_but_restart_does_not(self):
        self.engine.pause("logical","task",reason="inspection",receipt="hold")
        reviewed=self.engine.snapshot("logical","task")
        other=DreamkeeperEngine(self.path,"owner-a")
        try:
            self.assertEqual(other.snapshot("logical","task")["review_state_hash"],
                             reviewed["review_state_hash"])
            other.quarantine("logical","task",reason="new inspection",receipt="new hold")
            with self.assertRaises(EngineHold):
                self.engine.resume_after_check("logical","task",
                    review_state_hash=reviewed["review_state_hash"],
                    checks={key:True for key in self.engine.RESUME_CHECKS},
                    reviewer="host",receipt="old-review")
        finally:other.close()

    def test_original_goal_and_source_revisions_are_recoverable_from_history(self):
        self.engine.revise_task("logical","task",expected_revision=1,user_authorized=True,
            user_receipt="new-goal",goals={"g1":"write corrected","g2":"verify"})
        self.source(self.engine,"source",{"x":"corrected x"},expected=1)
        reopened=DreamkeeperEngine(self.path,"owner-a")
        try:
            history=reopened.history("logical","task")
            created=next(x["payload"] for x in history if x["kind"]=="TASK_CREATED")
            changed=next(x["payload"] for x in history if x["kind"]=="USER_REVISED")
            sources=[x["payload"] for x in history if x["kind"]=="SOURCE_CORRECTED"]
            self.assertEqual(created["after"]["goals"]["g1"],"write")
            self.assertEqual(changed["before"]["goals"]["g1"],"write")
            self.assertEqual(changed["after"]["goals"]["g1"],"write corrected")
            self.assertEqual(sources[-1]["before"]["fields"]["x"]["value"],"original x")
            self.assertEqual(sources[-1]["after"]["fields"]["x"]["value"],"corrected x")
        finally:reopened.close()

    def test_correction_winning_before_consume_invalidates_review(self):
        cid=self.candidate();aid=self.approve(cid)
        other=DreamkeeperEngine(self.path,"owner-a")
        try:
            self.source(other,"source",{"x":"corrected"},expected=1)
            with self.assertRaises(EngineHold):self.consume(cid,aid)
        finally:other.close()

    def test_consume_check_and_write_are_one_serialized_transaction(self):
        cid=self.candidate();aid=self.approve(cid)
        checked=threading.Event();release=threading.Event();writer_started=threading.Event();writer_done=threading.Event()
        original=self.engine._current_candidate
        def gated(db,*args):
            result=original(db,*args);checked.set()
            if not release.wait(5):raise RuntimeError("test gate timed out")
            return result
        self.engine._current_candidate=gated
        other=DreamkeeperEngine(self.path,"owner-a")
        def correct():
            writer_started.set()
            self.source(other,"source",{"x":"later correction"},expected=1)
            writer_done.set()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                consuming=pool.submit(self.consume,cid,aid)
                self.assertTrue(checked.wait(3))
                correcting=pool.submit(correct)
                self.assertTrue(writer_started.wait(3))
                self.assertFalse(writer_done.wait(0.05))
                release.set()
                self.assertEqual(consuming.result(timeout=5)["status"],"CONSUMED")
                correcting.result(timeout=5)
            self.assertEqual(self.engine.candidate("logical","task",cid)["status"],"HOLD")
        finally:
            release.set();other.close();self.engine._current_candidate=original


if __name__=="__main__":unittest.main()
