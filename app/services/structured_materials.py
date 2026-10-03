"""Condition-bound teaching bundles; structural checks are not proof of correctness.

An owner may prepare a draft, but publication requires a separate teacher review.
Changing any draft withdraws its previous release until the complete replacement
is reviewed.  False propositions are useful counterexamples, not failed checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MaterialModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Concept(MaterialModel):
    concept_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    stage: Literal["BASIC", "CONDITION", "INFERENCE", "ADVANCED"]


class LogicLink(MaterialModel):
    link_id: str = Field(min_length=1, max_length=100)
    from_concept: str
    to_concept: str
    premise: str = Field(min_length=1, max_length=3000)
    condition_keys: list[str] = Field(min_length=1, max_length=30)
    rule: str = Field(min_length=1, max_length=3000)
    conclusion: str = Field(min_length=1, max_length=3000)


class Scenario(MaterialModel):
    scenario_id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    condition_revision_id: str = Field(min_length=1, max_length=100)
    conditions: dict[str, str] = Field(min_length=1, max_length=30)
    # Optional while reading older drafts; publication requires a bound generation.
    generation_id: str = Field(default="", max_length=100)


class ComparisonPair(MaterialModel):
    baseline: str
    altered: str
    changed_keys: list[str] = Field(min_length=1, max_length=30)


class Representation(MaterialModel):
    asset_id: str = Field(min_length=1, max_length=100)
    kind: Literal["SCENE", "AUDIO", "QUESTION", "ANSWER", "IMAGE", "VIDEO", "UPLOADED_AUDIO", "CAPTION"]
    scenario_id: str
    condition_revision_id: str
    generation_id: str = Field(default="", max_length=100)
    depends_on: list[str] = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=12000)
    choices: list[str] = Field(default_factory=list, max_length=20)
    # References only: the authenticated publication route checks the uploaded
    # file against this identity and supplies the access URL from its own DB.
    media_asset_id: str | None = None
    media_sha256: str | None = None
    media_type: str | None = None


class LinkValidation(MaterialModel):
    link_id: str
    scenario_id: str
    condition_revision_id: str
    status: Literal["PENDING", "PASSED", "FAILED"] = "PENDING"
    proposition_result: Literal["TRUE", "FALSE", "UNDETERMINED"] = "UNDETERMINED"
    evidence: str = Field(default="", max_length=5000)
    generation_id: str = Field(default="", max_length=100)
    manifest_digest: str = Field(default="", max_length=64)
    validation_basis: Literal["TEACHER_REVIEWED"] = "TEACHER_REVIEWED"


class IndependentAssessment(MaterialModel):
    question: str = Field(min_length=1, max_length=5000)
    choices: list[str] = Field(min_length=2, max_length=20)
    correct_answer: str = Field(min_length=1, max_length=3000)
    concept_key: str = Field(min_length=1, max_length=100)
    content_revision_id: str = Field(min_length=1, max_length=100)


class StructuredMaterialBundle(MaterialModel):
    revision_id: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=1000)
    concepts: list[Concept] = Field(min_length=4, max_length=100)
    links: list[LogicLink] = Field(min_length=3, max_length=150)
    scenarios: list[Scenario] = Field(min_length=2, max_length=20)
    comparisons: list[ComparisonPair] = Field(min_length=1, max_length=30)
    representations: list[Representation] = Field(min_length=8, max_length=100)
    validations: list[LinkValidation] = Field(default_factory=list, max_length=3000)
    independent_assessment: IndependentAssessment | None = None


def bundle_digest(bundle: StructuredMaterialBundle) -> str:
    data = json.dumps(bundle.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def scenario_manifest_digest(bundle: StructuredMaterialBundle, scenario_id: str) -> str:
    """Fingerprint the actual output context, including every optional representation.

    This proves byte-level equality to the reviewed snapshot, not that a diagram,
    explanation or mathematical proposition is true.
    """
    scenario = next((x for x in bundle.scenarios if x.scenario_id == scenario_id), None)
    if scenario is None:
        raise ValueError("Unknown scenario")
    manifest = {
        "target": bundle.target,
        "concepts": [x.model_dump() for x in bundle.concepts],
        "links": [x.model_dump() for x in bundle.links],
        "scenario": scenario.model_dump(),
        "representations": [x.model_dump() for x in sorted(
            (a for a in bundle.representations if a.scenario_id == scenario_id),
            key=lambda a: (a.kind, a.asset_id))],
        "comparisons": [x.model_dump() for x in bundle.comparisons],
        "independent_assessment": (bundle.independent_assessment.model_dump()
                                   if bundle.independent_assessment else None),
    }
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issue_teacher_review_receipts(bundle: StructuredMaterialBundle) -> StructuredMaterialBundle:
    """Record teacher attestation against the current manifest, without semantic claims.

    Call only after authentication, an exact draft-digest match, verified files
    for media references and the teacher's explicit review acknowledgement.
    The caller must persist this returned bundle.
    """
    reviewed = bundle.model_copy(deep=True)
    scenarios = {s.scenario_id: s for s in reviewed.scenarios}
    for check in reviewed.validations:
        scenario = scenarios.get(check.scenario_id)
        if scenario is None:
            continue
        if check.status == "FAILED" or check.proposition_result == "UNDETERMINED":
            raise ValueError(f"Failed or undetermined validation must be resolved: {check.scenario_id}:{check.link_id}")
        current_digest = scenario_manifest_digest(reviewed, check.scenario_id)
        if check.status == "PASSED" and (
            (check.manifest_digest and check.manifest_digest != current_digest)
            or (check.generation_id and check.generation_id != scenario.generation_id)
        ):
            raise ValueError(f"Stale passed review must be reset: {check.scenario_id}:{check.link_id}")
        check.status = "PASSED"
        check.generation_id = scenario.generation_id
        check.manifest_digest = current_digest
        check.validation_basis = "TEACHER_REVIEWED"
    issues = publication_issues(reviewed)
    if issues:
        raise ValueError("Review incomplete: " + ", ".join(issues[:20]))
    return reviewed


def publication_issues(bundle: StructuredMaterialBundle) -> list[str]:
    """Check references, condition versions, completeness and recorded review evidence.

    These checks intentionally do not claim to verify mathematics or language
    semantics. A teacher must independently approve the actual materials.
    """
    issues: list[str] = []
    for name, values in (
        ("concept", [x.concept_id for x in bundle.concepts]),
        ("link", [x.link_id for x in bundle.links]),
        ("scenario", [x.scenario_id for x in bundle.scenarios]),
        ("asset", [x.asset_id for x in bundle.representations]),
        ("condition_revision", [x.condition_revision_id for x in bundle.scenarios]),
    ):
        if len(values) != len(set(values)):
            issues.append(f"DUPLICATE_{name.upper()}_ID")
    concepts = {x.concept_id: x for x in bundle.concepts}
    links = {x.link_id: x for x in bundle.links}
    scenarios = {x.scenario_id: x for x in bundle.scenarios}
    manifest_digests = {scenario_id: scenario_manifest_digest(bundle, scenario_id)
                        for scenario_id in scenarios}
    reserved_ids = {f"{bundle.revision_id}:map", f"{bundle.revision_id}:independent"}
    reserved_ids.update(f"{bundle.revision_id}:compare:{i}" for i in range(len(bundle.comparisons)))
    if reserved_ids & {x.asset_id for x in bundle.representations}:
        issues.append("RESERVED_SEGMENT_ID_COLLISION")
    if {x.stage for x in bundle.concepts} != {"BASIC", "CONDITION", "INFERENCE", "ADVANCED"}:
        issues.append("LEARNING_MAP_STAGES_INCOMPLETE")
    for link in bundle.links:
        if link.from_concept not in concepts or link.to_concept not in concepts:
            issues.append(f"UNKNOWN_CONCEPT:{link.link_id}")
    # Require an actual path through the four teaching stages, not just labels.
    stage_order = ["BASIC", "CONDITION", "INFERENCE", "ADVANCED"]
    frontier = {x.concept_id for x in bundle.concepts if x.stage == "BASIC"}
    for stage in stage_order[1:]:
        frontier = {x.to_concept for x in bundle.links if x.from_concept in frontier
                    and x.to_concept in concepts and concepts[x.to_concept].stage == stage}
    if not frontier:
        issues.append("LEARNING_PATH_NOT_CONNECTED")
    for pair in bundle.comparisons:
        first, second = scenarios.get(pair.baseline), scenarios.get(pair.altered)
        if not first or not second or first.scenario_id == second.scenario_id:
            issues.append("INVALID_COMPARISON_PAIR")
            continue
        if set(first.conditions) != set(second.conditions):
            issues.append("COMPARISON_CONDITION_KEYS_DIFFER")
        actual = {key for key in set(first.conditions) | set(second.conditions)
                  if first.conditions.get(key) != second.conditions.get(key)}
        if actual != set(pair.changed_keys):
            issues.append("COMPARISON_CHANGED_KEYS_MISMATCH")
    validations = {}
    for validation in bundle.validations:
        key = (validation.scenario_id, validation.link_id)
        if key in validations:
            issues.append("DUPLICATE_VALIDATION")
        validations[key] = validation
        scenario = scenarios.get(validation.scenario_id)
        if not scenario or validation.link_id not in links:
            issues.append("UNKNOWN_VALIDATION_REFERENCE")
        elif validation.condition_revision_id != scenario.condition_revision_id:
            issues.append("STALE_VALIDATION_CONDITION")
    for scenario in bundle.scenarios:
        assets = [x for x in bundle.representations if x.scenario_id == scenario.scenario_id]
        required = {"ANSWER", "AUDIO", "QUESTION", "SCENE"}
        kinds = [x.kind for x in assets]
        if not required.issubset(kinds) or any(kinds.count(kind) != 1 for kind in required):
            issues.append(f"REPRESENTATIONS_INCOMPLETE_OR_DUPLICATE:{scenario.scenario_id}")
        if len({x.asset_id for x in assets}) != len(assets) or not scenario.generation_id.strip():
            issues.append(f"OUTPUT_GENERATION_INCOMPLETE:{scenario.scenario_id}")
        question = next((x for x in assets if x.kind == "QUESTION"), None)
        answer = next((x for x in assets if x.kind == "ANSWER"), None)
        if question and answer and (len(question.choices) < 2 or answer.content not in question.choices):
            issues.append(f"ANSWER_NOT_IN_CHOICES:{scenario.scenario_id}")
        for link in bundle.links:
            if not set(link.condition_keys).issubset(scenario.conditions):
                issues.append(f"MISSING_APPLICABILITY_CONDITION:{scenario.scenario_id}:{link.link_id}")
            check = validations.get((scenario.scenario_id, link.link_id))
            if (not check or check.status != "PASSED" or not check.evidence.strip()
                    or check.proposition_result == "UNDETERMINED"):
                issues.append(f"LINK_NOT_VERIFIED:{scenario.scenario_id}:{link.link_id}")
            if check and (check.generation_id != scenario.generation_id
                          or check.manifest_digest != manifest_digests[scenario.scenario_id]):
                issues.append(f"STALE_VALIDATION_MANIFEST:{scenario.scenario_id}:{link.link_id}")
    for asset in bundle.representations:
        scenario = scenarios.get(asset.scenario_id)
        if not scenario:
            issues.append(f"UNKNOWN_ASSET_SCENARIO:{asset.asset_id}")
        elif asset.condition_revision_id != scenario.condition_revision_id:
            issues.append(f"STALE_ASSET_CONDITION:{asset.asset_id}")
        if scenario and (not asset.generation_id.strip() or
                         asset.generation_id != scenario.generation_id):
            issues.append(f"STALE_ASSET_GENERATION:{asset.asset_id}")
        if not set(asset.depends_on).issubset(links):
            issues.append(f"UNKNOWN_ASSET_DEPENDENCY:{asset.asset_id}")
        media_kind_types = {
            "IMAGE": {"image/png", "image/jpeg"},
            "VIDEO": {"video/mp4", "video/webm"},
            "UPLOADED_AUDIO": {"audio/mpeg", "audio/wav"},
        }
        if asset.kind in media_kind_types:
            if (not asset.media_asset_id or not re.fullmatch(r"[0-9a-f]{32}", asset.media_asset_id)
                    or not asset.media_sha256 or not re.fullmatch(r"[0-9a-f]{64}", asset.media_sha256)
                    or asset.media_type not in media_kind_types[asset.kind]):
                issues.append(f"MEDIA_REFERENCE_INCOMPLETE:{asset.asset_id}")
        elif asset.media_asset_id or asset.media_sha256 or asset.media_type:
            issues.append(f"MEDIA_REFERENCE_UNEXPECTED:{asset.asset_id}")
    assessment = bundle.independent_assessment
    if assessment:
        if assessment.correct_answer not in assessment.choices:
            issues.append("INDEPENDENT_ANSWER_NOT_IN_CHOICES")
        normalized = " ".join(assessment.question.split()).casefold()
        if any(" ".join(x.content.split()).casefold() == normalized
               for x in bundle.representations if x.kind == "QUESTION"):
            issues.append("INDEPENDENT_ASSESSMENT_REUSES_PRACTICE")
    return sorted(set(issues))


def affected_assets(previous: StructuredMaterialBundle | None, updated: StructuredMaterialBundle) -> list[str]:
    """Identify changed conditions/links and their transitive representation dependants."""
    if previous is None:
        return sorted(x.asset_id for x in updated.representations)
    if (previous.target != updated.target or previous.concepts != updated.concepts
            or previous.comparisons != updated.comparisons
            or previous.independent_assessment != updated.independent_assessment):
        return sorted(x.asset_id for x in updated.representations)
    old_links = {x.link_id: x.model_dump() for x in previous.links}
    changed_links = ({x.link_id for x in updated.links if old_links.get(x.link_id) != x.model_dump()}
                     | (set(old_links) - {x.link_id for x in updated.links}))
    old_scenarios = {x.scenario_id: x.model_dump() for x in previous.scenarios}
    changed_scenarios = {x.scenario_id for x in updated.scenarios
                         if old_scenarios.get(x.scenario_id) != x.model_dump()}
    # A change to a conclusion can invalidate downstream rules, not only direct assets.
    while True:
        changed_concepts = {x.to_concept for x in [*previous.links, *updated.links] if x.link_id in changed_links}
        expanded = changed_links | {x.link_id for x in updated.links if x.from_concept in changed_concepts}
        if expanded == changed_links:
            break
        changed_links = expanded
    old_assets = {x.asset_id: x.model_dump() for x in previous.representations}
    return sorted(x.asset_id for x in updated.representations
                  if x.scenario_id in changed_scenarios or set(x.depends_on) & changed_links
                  or old_assets.get(x.asset_id) != x.model_dump())


def bundle_lesson(bundle: StructuredMaterialBundle) -> list[dict]:
    """Create coordinated board scenes and TTS from the reviewed revision."""
    if publication_issues(bundle):
        raise ValueError("Unreviewed or stale material cannot become a public lesson")
    common = {"structured_bundle_revision": bundle.revision_id,
              "structured_bundle_digest": bundle_digest(bundle),
              "source_integrity_verified": True, "content_trust": "VERIFIED",
              "material_validation_basis": "TEACHER_REVIEWED_STRUCTURAL",
              "concept_key": (bundle.independent_assessment.concept_key if bundle.independent_assessment
                              else f"STRUCTURED:{bundle.concepts[-1].concept_id}")}
    steps = [{**common, "segment_id": f"{bundle.revision_id}:map", "kind": "MAP",
              "title": "전체 학습지도", "text": bundle.target,
              "learning_map": [x.model_dump() for x in bundle.concepts],
              "logic_links": [x.model_dump() for x in bundle.links], "choices": []}]
    for scenario in bundle.scenarios:
        assets = {x.kind: x for x in bundle.representations if x.scenario_id == scenario.scenario_id}
        scene, audio, question, answer = (assets[k] for k in ("SCENE", "AUDIO", "QUESTION", "ANSWER"))
        contextual = {**common, "scenario_id": scenario.scenario_id,
                      "condition_revision_id": scenario.condition_revision_id,
                      "generation_id": scenario.generation_id,
                      "manifest_digest": scenario_manifest_digest(bundle, scenario.scenario_id),
                      "conditions": scenario.conditions}
        steps.append({**contextual, "segment_id": scene.asset_id, "kind": "DEMONSTRATE",
                      "title": scenario.label, "text": scene.content, "audio_text": audio.content,
                      "logic_links": [x.model_dump() for x in bundle.links], "choices": [],
                      "content_revision_id": f"{bundle.revision_id}:{scene.asset_id}", "hint_level": 1})
        for asset in (x for x in bundle.representations if x.scenario_id == scenario.scenario_id
                      and x.kind in {"IMAGE", "VIDEO", "UPLOADED_AUDIO", "CAPTION"}):
            step = {**contextual, "segment_id": asset.asset_id, "kind": "DEMONSTRATE",
                    "title": f"{scenario.label} · 시청각 자료", "text": asset.content,
                    "choices": [], "hint_level": 1,
                    "content_revision_id": f"{bundle.revision_id}:{asset.asset_id}"}
            if asset.kind != "CAPTION":
                step.update({"media_asset_id": asset.media_asset_id,
                             "media_sha256": asset.media_sha256,
                             "media_type": asset.media_type})
            steps.append(step)
        steps.append({**contextual, "segment_id": question.asset_id, "kind": "PRACTICE",
                      "title": f"{scenario.label} · 연습", "text": question.content,
                      "choices": question.choices, "correct_answer": answer.content,
                      "feedback_phase": "POST_FEEDBACK", "independent_followup": False,
                      "hint_level": 1, "content_revision_id": f"{bundle.revision_id}:{question.asset_id}"})
    for index, pair in enumerate(bundle.comparisons):
        a, b = (next(x for x in bundle.scenarios if x.scenario_id == key) for key in (pair.baseline, pair.altered))
        steps.append({**common, "segment_id": f"{bundle.revision_id}:compare:{index}", "kind": "COMPARE",
                      "title": f"{a.label} ↔ {b.label}", "text": "바뀐 조건과 유지된 조건을 비교합니다.",
                      "comparison": {"baseline": a.conditions, "altered": b.conditions,
                                     "changed_keys": pair.changed_keys}, "choices": [], "hint_level": 1})
    if bundle.independent_assessment:
        assessment = bundle.independent_assessment
        steps.append({**common, "segment_id": f"{bundle.revision_id}:independent", "kind": "RETEST",
                      "title": "지원 없는 새로운 문항", "text": assessment.question,
                      "choices": assessment.choices, "correct_answer": assessment.correct_answer,
                      "content_revision_id": assessment.content_revision_id,
                      "concept_key": assessment.concept_key, "independent_followup": True,
                      "hint_level": 0, "answer_disclosed": False, "feedback_phase": "PRE_FEEDBACK"})
    return steps


def example_bundle() -> StructuredMaterialBundle:
    """An editable worked example. Its validations start pending teacher review."""
    concepts = [Concept(concept_id=f"c{i}", label=label, stage=stage) for i, (label, stage) in enumerate([
        ("미분계수와 차분몫", "BASIC"), ("f(0)=0이고 f가 0에서 미분가능", "CONDITION"),
        ("|f|의 좌우 차분몫 비교", "INFERENCE"), ("|x³+ax|의 미분가능 조건", "ADVANCED")])]
    links = [LogicLink(link_id=f"l{i}", from_concept=f"c{i}", to_concept=f"c{i+1}",
        premise=premise, condition_keys=["f(0)", "f′(0)", "함수"], rule=rule, conclusion=conclusion)
        for i, (premise, rule, conclusion) in enumerate([
            ("f는 0에서 미분가능하고 f(0)=0", "f(h)=f′(0)h+o(h)", "좌우 극한을 변화율로 나타낼 수 있다."),
            ("g(x)=|f(x)|, f(0)=0", "우미분계수=|f′(0)|, 좌미분계수=-|f′(0)|", "g는 f′(0)=0일 때만 0에서 미분가능하다."),
            ("조건별 f′(0)를 확인한다.", "좌우 미분계수의 일치 여부를 판정한다.", "이 조건에서 |f|는 0에서 미분가능하다."),
        ])]
    scenarios = [Scenario(scenario_id="linear", label="기준: f(x)=x", condition_revision_id="linear-c1",
                          generation_id="linear-g1",
                          conditions={"함수": "x", "f(0)": "0", "f′(0)": "1"}),
                 Scenario(scenario_id="cubic", label="변경: f(x)=x³", condition_revision_id="cubic-c1",
                          generation_id="cubic-g1",
                          conditions={"함수": "x³", "f(0)": "0", "f′(0)": "0"})]
    representations = []
    validations = []
    for scenario in scenarios:
        linear = scenario.scenario_id == "linear"
        detail = "|x|의 좌미분계수는 -1, 우미분계수는 1이므로 미분불가능합니다." if linear else "|x³|의 좌우 차분몫은 모두 0으로 수렴하므로 미분가능합니다."
        for kind, content in [("SCENE", detail), ("AUDIO", detail),
                              ("QUESTION", f"{scenario.label}일 때 |f(x)|는 원점에서 미분가능한가요?"),
                              ("ANSWER", "미분불가능" if linear else "미분가능")]:
            representations.append(Representation(asset_id=f"{scenario.scenario_id}-{kind.lower()}", kind=kind,
                scenario_id=scenario.scenario_id, condition_revision_id=scenario.condition_revision_id,
                generation_id=scenario.generation_id,
                depends_on=["l0", "l1", "l2"], content=content,
                choices=["미분가능", "미분불가능"] if kind == "QUESTION" else []))
        for link in links:
            validations.append(LinkValidation(link_id=link.link_id, scenario_id=scenario.scenario_id,
                condition_revision_id=scenario.condition_revision_id, status="PENDING",
                proposition_result="FALSE" if linear and link.link_id == "l2" else "TRUE",
                evidence=detail if link.link_id != "l0" else "다항함수이므로 미분가능하며 f(0)=0이다."))
    return StructuredMaterialBundle(revision_id="absolute-differentiability-v1",
        target="기초 변화율에서 절댓값의 성립조건을 거쳐 매개변수 문제를 해결합니다.",
        concepts=concepts, links=links, scenarios=scenarios,
        comparisons=[ComparisonPair(baseline="linear", altered="cubic", changed_keys=["함수", "f′(0)"])],
        representations=representations, validations=validations,
        independent_assessment=IndependentAssessment(question="h(x)=|x⁵+b x|가 0에서 미분가능하도록 하는 실수 b는?",
            choices=["b=0", "b=1", "모든 실수 b"], correct_answer="b=0", concept_key="ABS:DIFFERENTIABILITY",
                content_revision_id="absolute-transfer-x5-v1"))
