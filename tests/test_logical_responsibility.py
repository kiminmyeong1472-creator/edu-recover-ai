import tempfile
import unittest
from pathlib import Path

from app.services.logical_responsibility import (
    LogicalResponsibilityError,
    LogicalResponsibilityStore,
    dataset_digest,
)


class LogicalResponsibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LogicalResponsibilityStore(Path(self.tmp.name) / "state.sqlite3")
        self.store.create_subject(
            logical_id="DREAMKEEPER-001",
            task_id="TASK-001",
            goals=[
                {"goal_id": "G1", "text": "특허 명세서 완성", "priority": 1},
                {"goal_id": "G2", "text": "실행 코드 완성", "priority": 2},
            ],
            constraints=["원문 보존"],
            authority_ref="OWNER:test",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_session_model_can_change_while_logical_subject_remains(self):
        first = self.store.bind_run(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            agent_id="agent-a", session_id="session-a", model_id="model-a", run_id="run-a",
        )
        second = self.store.handoff(
            previous_run_id="run-a", agent_id="agent-b",
            session_id="session-b", model_id="model-b", run_id="run-b",
        )
        self.assertEqual(first.logical_id, second.logical_id)
        self.assertEqual([g["goal_id"] for g in second.goals], ["G1", "G2"])

    def test_owner_revision_makes_old_run_stale_and_new_run_gets_latest_state(self):
        self.store.bind_run(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            agent_id="agent-a", session_id="session-a", model_id="model-a", run_id="run-a",
        )
        state = self.store.owner_revise(
            logical_id="DREAMKEEPER-001", task_id="TASK-001", expected_revision=1,
            authority_ref="OWNER:priority-change", priority_updates={"G2": 0},
            corrections=[{"correction_id": "C1", "text": "G2를 먼저 처리"}],
        )
        self.assertEqual(state["revision"], 2)
        stale = self.store.authorize_execution(run_id="run-a", goal_id="G1")
        self.assertFalse(stale.allowed)
        self.assertEqual(stale.state, "STALE_RUN")
        packet = self.store.handoff(
            previous_run_id="run-a", agent_id="agent-b",
            session_id="session-b", model_id="model-b", run_id="run-b",
        )
        self.assertEqual(packet.revision, 2)
        self.assertEqual(packet.goals[0]["goal_id"], "G2")
        self.assertEqual(packet.corrections[-1]["correction_id"], "C1")

    def test_execution_instance_cannot_change_user_goal(self):
        self.store.bind_run(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            agent_id="agent-a", session_id="session-a", model_id="model-a", run_id="run-a",
        )
        decision = self.store.authorize_execution(
            run_id="run-a", goal_id="G1", proposes_goal_change=True,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "GOAL_CHANGE_REQUIRES_OWNER")

    def test_completion_evidence_selectively_removes_completed_goal_from_handoff(self):
        self.store.bind_run(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            agent_id="agent-a", session_id="session-a", model_id="model-a", run_id="run-a",
        )
        state = self.store.record_completion(
            run_id="run-a", goal_id="G1", evidence_ref="sha256:proof",
        )
        self.assertEqual(state["revision"], 2)
        packet = self.store.handoff(
            previous_run_id="run-a", agent_id="agent-b",
            session_id="session-b", model_id="model-b", run_id="run-b",
        )
        self.assertEqual([g["goal_id"] for g in packet.goals], ["G2"])

    def test_model_revision_activates_only_for_matching_control_revision(self):
        digest = dataset_digest([{"case": "owner-reviewed"}])
        self.store.register_model_revision(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            model_revision="model-r1", trained_control_revision=1,
            dataset_sha256=digest, verification_receipt="verify:pass",
            verification_passed=True,
        )
        active = self.store.activate_model_revision(
            logical_id="DREAMKEEPER-001", task_id="TASK-001", model_revision="model-r1",
        )
        self.assertEqual(active["active_model_revision"], "model-r1")
        self.store.owner_revise(
            logical_id="DREAMKEEPER-001", task_id="TASK-001", expected_revision=1,
            authority_ref="OWNER:new-correction",
            corrections=[{"correction_id": "C2", "text": "새 정정"}],
        )
        self.store.register_model_revision(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            model_revision="model-old-control", trained_control_revision=1,
            dataset_sha256=digest, verification_receipt="verify:pass2",
            verification_passed=True,
        )
        with self.assertRaisesRegex(LogicalResponsibilityError, "MODEL_CONTROL_REVISION_MISMATCH"):
            self.store.activate_model_revision(
                logical_id="DREAMKEEPER-001", task_id="TASK-001",
                model_revision="model-old-control",
            )

    def test_failed_model_verification_cannot_activate(self):
        digest = dataset_digest([{"case": "owner-reviewed"}])
        self.store.register_model_revision(
            logical_id="DREAMKEEPER-001", task_id="TASK-001",
            model_revision="model-r-fail", trained_control_revision=1,
            dataset_sha256=digest, verification_receipt="verify:fail",
            verification_passed=False,
        )
        with self.assertRaisesRegex(LogicalResponsibilityError, "MODEL_VERIFICATION_REQUIRED"):
            self.store.activate_model_revision(
                logical_id="DREAMKEEPER-001", task_id="TASK-001",
                model_revision="model-r-fail",
            )


if __name__ == "__main__":
    unittest.main()
