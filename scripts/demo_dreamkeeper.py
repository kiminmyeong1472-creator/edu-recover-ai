"""Run a synthetic, offline workflow against the real SQLite engine."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.dreamkeeper_engine import DreamkeeperEngine, EngineHold


def main() -> int:
    # These checks describe a deliberately simple, synthetic fixture. A real
    # host must perform each review; copying True values is not verification.
    logical, task = "synthetic-dreamkeeper", "synthetic-work"
    with TemporaryDirectory() as temporary:
        database = Path(temporary) / "engine.sqlite3"
        engine = DreamkeeperEngine(database, "synthetic-owner")
        engine.create_task(logical, task,
            goals={"g1": "원래 목표와 정정을 유지한다"}, priorities=["g1"],
            rules=["검토 전 후보를 사실 결과로 쓰지 않는다"],
            obligations={"o1": "현재 자료 변경본을 확인한다"},
            user_receipt="synthetic-user-instruction")
        engine.put_source(logical, task, source_id="source",
            fields={"answer": "가상 답안 원문", "unrelated": "정정 전 메모"},
            expected_revision=0, user_receipt="synthetic-source-review")
        old_run = engine.begin_run(logical, task, model_id="synthetic-model-a", session_id="window-a")
        engine.model_port(old_run).read_contract({"source": ["answer"]})
        engine.close()

        engine = DreamkeeperEngine(database, "synthetic-owner")
        restored = engine.snapshot(logical, task)
        run = engine.begin_run(logical, task, model_id="synthetic-model-b", session_id="window-b")
        old_run_rejected = False
        try:
            engine.model_port(old_run).read_contract({})
        except EngineHold:
            old_run_rejected = True
        port = engine.model_port(run)
        port.read_contract({"source": ["answer"]})
        candidate_id = port.submit_candidate(content={"answer": "가상 자료를 확인한 후보"},
            purpose="synthetic_example", source_refs={}, consumption_complete=True,
            goal_ids=["g1"])
        approval_id = engine.review_candidate(logical, task, candidate_id=candidate_id,
            checks={name: True for name in engine.REVIEW_CHECKS},
            obligation_checks={"o1": True}, reviewer="synthetic-host",
            receipt="synthetic-fixture-review")
        # A correction to a field not delivered to this run preserves the
        # reviewed candidate. A correction to its actual input then holds it.
        engine.put_source(logical, task, source_id="source",
            fields={"unrelated": "정정된 메모"}, expected_revision=1,
            user_receipt="synthetic-unrelated-correction")
        unrelated_retained = engine.candidate(logical, task, candidate_id)["status"] == "APPROVED"
        engine.put_source(logical, task, source_id="source",
            fields={"answer": "정정된 가상 답안"}, expected_revision=2,
            user_receipt="synthetic-dependent-correction")
        stale_result_rejected = False
        try:
            engine.consume(logical, task, candidate_id=candidate_id,
                approval_id=approval_id, purpose="synthetic_example")
        except EngineHold:
            stale_result_rejected = True
        observations = {
            "synthetic_data_only": True,
            "goals_restored_after_restart": restored["goals"] == {"g1": "원래 목표와 정정을 유지한다"},
            "obligations_retained": restored["obligations"] == {"o1": "현재 자료 변경본을 확인한다"},
            "old_run_rejected": old_run_rejected,
            "unrelated_field_correction_retained_candidate": unrelated_retained,
            "dependent_field_correction_rejected_old_approval": stale_result_rejected,
            "external_model_called": False,
            "weights_trained": False,
        }
        engine.close()
        print(json.dumps(observations, ensure_ascii=False, indent=2))
        expected = [value for key, value in observations.items()
                    if key not in {"external_model_called", "weights_trained"}]
        if not all(expected):
            raise RuntimeError("Offline workflow observation failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
