"""Bounded source-to-audiovisual drafts; all inference calls are mocked."""
import asyncio
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.services import audiovisual_drafts as service
from app.services.control_plane import ProviderHeld
from app.services.open_model_transport import OpenModelError
from app.services.structured_materials import bundle_digest, example_bundle, publication_issues


def request(**overrides):
    values = dict(model_key="DeepSeek-V3.2", explicit_authorization=True,
                  source_text="f(x)=x이면 |f(x)|는 원점에서 미분불가능하고, f(x)=x³이면 미분가능하다.",
                  target="두 함수의 좌우 미분계수를 비교합니다.", subject="수학", school_level="고등학교",
                  grade="2학년", expected_digest="server-only-current-draft-digest")
    values.update(overrides)
    return service.AudioVisualDraftRequest(**values)


def payload(visual_kind="DIAGRAM"):
    value = example_bundle().model_dump(exclude_none=True)
    visuals = {
        "DIAGRAM": {"kind": "DIAGRAM", "nodes": [{"id": "a", "label": "좌미분계수"},
                                                  {"id": "b", "label": "우미분계수"}],
                    "edges": [{"from_id": "a", "to_id": "b", "label": "값 비교"}]},
        "TABLE": {"kind": "TABLE", "columns": ["확인", "결과"], "rows": [["좌우 미분계수", "비교"]]},
        "STEPS": {"kind": "STEPS", "steps": ["좌미분계수를 구합니다.", "우미분계수와 비교합니다."]},
    }
    for asset in value["representations"]:
        if asset["kind"] == "SCENE":
            asset["visual"] = visuals[visual_kind]
    return value


class AudioVisualDraftTests(unittest.TestCase):
    def generate(self, value=None, req=None):
        req = req or request()
        output = json.dumps(value if value is not None else payload(), ensure_ascii=False)
        runner = AsyncMock(return_value=output)
        with patch.object(service, "assert_provider_allowed", return_value=SimpleNamespace(control_epoch="test-epoch")) as gate, \
                patch.object(service, "call_model", runner):
            bundle, receipt = asyncio.run(service.generate_audiovisual_draft(req))
        return bundle, receipt, runner, gate

    def test_selected_model_one_call_rebinds_all_ids_and_blocks_publication(self):
        original = payload()
        bundle, receipt, runner, gate = self.generate(original)
        runner.assert_awaited_once()
        gate.assert_called_once_with("deepseek", "generate")
        self.assertEqual(runner.call_args.args[0], "DeepSeek-V3.2")
        self.assertEqual(runner.call_args.kwargs["max_tokens"], 8192)
        sent = json.loads(runner.call_args.args[2])
        self.assertNotIn("expected_digest", sent["request"])
        self.assertEqual(sent["request"]["source_text"], request().source_text)
        self.assertEqual(bundle.target, request().target)
        self.assertNotEqual(bundle.revision_id, original["revision_id"])
        self.assertTrue(all(len(x.concept_id) == 32 for x in bundle.concepts))
        self.assertTrue(all(len(x.link_id) == 32 for x in bundle.links))
        self.assertTrue(all(len(x.scenario_id) == 32 and len(x.condition_revision_id) == 32
                            and len(x.generation_id) == 32 for x in bundle.scenarios))
        self.assertTrue(all(len(x.asset_id) == 32 for x in bundle.representations))
        self.assertTrue(all(x.status == "PENDING" and not x.manifest_digest and not x.generation_id
                            for x in bundle.validations))
        self.assertTrue(publication_issues(bundle))
        self.assertEqual(receipt["student_release"], "BLOCKED")
        self.assertFalse(receipt["correctness_verified"])
        self.assertFalse(receipt["fallback_used"])
        self.assertEqual(receipt["source_sha256"], hashlib.sha256(request().source_text.encode()).hexdigest())
        self.assertEqual(receipt["result_sha256"], bundle_digest(bundle))
        for visual in (a.visual for a in bundle.representations if a.kind == "SCENE"):
            self.assertTrue(all(len(n.id) == 32 for n in visual.nodes))
            self.assertEqual({visual.edges[0].from_id, visual.edges[0].to_id}, {n.id for n in visual.nodes})

    def test_all_safe_visual_types_are_accepted(self):
        for kind in ("DIAGRAM", "TABLE", "STEPS"):
            with self.subTest(kind=kind):
                bundle, _, _, _ = self.generate(payload(kind))
                self.assertEqual([a.visual.kind for a in bundle.representations if a.kind == "SCENE"], [kind, kind])

    def test_model_cannot_create_teacher_review_receipts(self):
        value = payload()
        for check in value["validations"]:
            check.update(status="PASSED", manifest_digest="f" * 64, generation_id="invented-review")
        bundle, _, _, _ = self.generate(value)
        self.assertTrue(all(v.status == "PENDING" and v.generation_id == "" and v.manifest_digest == ""
                            for v in bundle.validations))

    def test_missing_judgment_remains_undetermined_for_teacher(self):
        value = payload()
        value["validations"] = []
        bundle, _, _, _ = self.generate(value)
        self.assertEqual(len(bundle.validations), 6)
        self.assertTrue(all(v.proposition_result == "UNDETERMINED" and not v.evidence for v in bundle.validations))

    def test_identifiers_are_fresh_for_each_generation(self):
        first, _, _, _ = self.generate()
        second, _, _, _ = self.generate()
        self.assertNotEqual(first.revision_id, second.revision_id)
        self.assertTrue({a.asset_id for a in first.representations}.isdisjoint(a.asset_id for a in second.representations))

    def test_bad_reference_media_markup_and_answer_are_rejected(self):
        mutations = [
            lambda p: p["links"][0].update(from_concept="unknown"),
            lambda p: p["concepts"][1].update(concept_id=p["concepts"][0]["concept_id"]),
            lambda p: p["representations"][0].update(media_asset_id="a" * 32, media_sha256="b" * 64, media_type="image/png"),
            lambda p: p["representations"][0].update(media_url="https://invented.example/image.png"),
            lambda p: p["representations"][0].update(content="<svg onload='bad()'></svg>"),
            lambda p: p["representations"][0].pop("visual"),
            lambda p: p["representations"][3].update(content="선택지에 없는 정답"),
            lambda p: p["representations"][2].update(choices=["같은 답", "같은 답"]),
            lambda p: p["comparisons"][0].update(changed_keys=["없는 조건"]),
            lambda p: p["representations"][0]["visual"]["edges"][0].update(to_id="absent"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value = payload()
                mutate(value)
                with self.assertRaises(OpenModelError) as failure:
                    self.generate(value)
                self.assertEqual(failure.exception.code, "AUDIOVISUAL_OUTPUT_INVALID")

    def test_authorization_and_request_types_are_strict(self):
        for value in (False, 1, "true", None):
            with self.subTest(authorization=value), self.assertRaises(ValidationError):
                request(explicit_authorization=value)
        for overrides in ({"source_text": " "}, {"source_text": "a" * 20001}, {"subject": 3},
                          {"target": ""}, {"expected_digest": " "}, {"unknown": "extra"}):
            with self.subTest(fields=tuple(overrides)), self.assertRaises(ValidationError):
                request(**overrides)

    def test_provider_hold_and_unknown_model_never_call_transport(self):
        with patch.object(service, "call_model", AsyncMock()) as runner:
            with patch.object(service, "assert_provider_allowed", side_effect=ProviderHeld("internal setting")):
                with self.assertRaises(OpenModelError) as failure:
                    asyncio.run(service.generate_audiovisual_draft(request()))
                self.assertEqual(failure.exception.http_status, 423)
            with self.assertRaises(OpenModelError) as failure:
                asyncio.run(service.generate_audiovisual_draft(request(model_key="not-selected-model")))
            self.assertEqual(failure.exception.http_status, 422)
            runner.assert_not_awaited()

    def test_python_constructed_unauthorized_request_is_revalidated(self):
        forged = request().model_copy(update={"explicit_authorization": 1})
        with patch.object(service, "call_model", AsyncMock()) as runner, self.assertRaises(OpenModelError):
            asyncio.run(service.generate_audiovisual_draft(forged))
        runner.assert_not_awaited()

    def test_transport_error_does_not_fallback_or_expose_raw_error(self):
        for exc in (OpenModelError("기존의 안전한 오류", "MODEL_HELD", 503), RuntimeError("private transport secret")):
            with self.subTest(error=type(exc).__name__), \
                    patch.object(service, "assert_provider_allowed", return_value=SimpleNamespace(control_epoch="t")), \
                    patch.object(service, "call_model", AsyncMock(side_effect=exc)) as runner:
                with self.assertRaises(OpenModelError) as failure:
                    asyncio.run(service.generate_audiovisual_draft(request()))
                self.assertNotIn("private transport secret", str(failure.exception))
                runner.assert_awaited_once()

    def test_duplicate_json_and_truncation_are_rejected_without_retry(self):
        for output in ('{"revision_id":"a","revision_id":"b"}', '{"partial":', 'NaN', "x" * 100001):
            with self.subTest(output_length=len(output)), \
                    patch.object(service, "assert_provider_allowed", return_value=SimpleNamespace(control_epoch="t")), \
                    patch.object(service, "call_model", AsyncMock(return_value=output)) as runner:
                with self.assertRaises(OpenModelError) as failure:
                    asyncio.run(service.generate_audiovisual_draft(request()))
                self.assertEqual(failure.exception.code, "AUDIOVISUAL_OUTPUT_INVALID")
                runner.assert_awaited_once()
