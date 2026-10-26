"""Version-bound essay score proposals; no model calls or automatic grading.

The checks establish document identity, quoted spans, score arithmetic and the
recorded teacher decision. They do not establish the semantic truth of a reason.
Offsets are Python Unicode character offsets in the exact, unnormalised answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from typing import Any, Mapping, Protocol, Sequence


def _identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _revision(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("revision must be a positive integer")


def _reading(value: str) -> None:
    if value not in {"confirmed", "uncertain"}:
        raise ValueError("reading_status must be confirmed or uncertain")


def _decimal(value: Decimal | str | int) -> Decimal:
    # Floats are deliberately refused: scoring should retain the supplied decimal.
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ValueError("points must be Decimal, a decimal string or an integer")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid points") from exc
    if not result.is_finite():
        raise ValueError("points must be finite")
    return result


def _sum_points(values: Sequence[Decimal]) -> Decimal:
    """Retain every supplied digit even if other code lowered decimal precision."""
    if not values:
        return Decimal(0)
    places = min(value.as_tuple().exponent for value in values)
    highest = max(value.adjusted() for value in values)
    with localcontext() as context:
        context.prec = max(28, highest - places + len(str(len(values))) + 2)
        return sum(values, Decimal(0))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    """SHA-256 of the exact UTF-8 text, without whitespace normalisation."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EssayProblem:
    problem_id: str
    revision: int
    text: str
    reading_status: str = "uncertain"

    def __post_init__(self) -> None:
        _identity(self.problem_id, "problem_id")
        _revision(self.revision)
        _identity(self.text, "problem text")
        _reading(self.reading_status)

    def document(self) -> dict[str, Any]:
        return {"problem_id": self.problem_id, "revision": self.revision,
                "text": self.text, "text_sha256": text_hash(self.text),
                "reading_status": self.reading_status}


@dataclass(frozen=True)
class TeacherContext:
    context_id: str
    revision: int
    teacher_id: str
    instructions: str
    problem: EssayProblem

    def __post_init__(self) -> None:
        _identity(self.context_id, "context_id")
        _revision(self.revision)
        _identity(self.teacher_id, "teacher_id")
        _identity(self.instructions, "instructions")
        if not isinstance(self.problem, EssayProblem):
            raise ValueError("problem must be EssayProblem")

    def document(self) -> dict[str, Any]:
        return {"context_id": self.context_id, "revision": self.revision,
                "teacher_id": self.teacher_id, "instructions": self.instructions,
                "problem_id": self.problem.problem_id,
                "problem_revision": self.problem.revision,
                "guidance_policy": GUIDANCE_POLICY}


@dataclass(frozen=True)
class EssayAnswer:
    answer_id: str
    revision: int
    student_id: str
    text: str
    reading_status: str = "uncertain"

    def __post_init__(self) -> None:
        _identity(self.answer_id, "answer_id")
        _revision(self.revision)
        _identity(self.student_id, "student_id")
        _identity(self.text, "answer text")
        _reading(self.reading_status)

    def document(self) -> dict[str, Any]:
        return {"answer_id": self.answer_id, "revision": self.revision,
                "student_id": self.student_id, "text": self.text,
                "text_sha256": text_hash(self.text), "reading_status": self.reading_status}


@dataclass(frozen=True)
class RubricCriterion:
    key: str
    description: str
    dimension: str
    maximum: Decimal | str | int
    minimum: Decimal | str | int = 0

    def __post_init__(self) -> None:
        _identity(self.key, "criterion key")
        _identity(self.description, "criterion description")
        if self.dimension not in {"creativity", "argument_validity", "other"}:
            raise ValueError("dimension must be creativity, argument_validity or other")
        object.__setattr__(self, "maximum", _decimal(self.maximum))
        object.__setattr__(self, "minimum", _decimal(self.minimum))
        if self.minimum < 0 or self.maximum < self.minimum:
            raise ValueError("criterion requires 0 <= minimum <= maximum")

    def document(self) -> dict[str, str]:
        return {"key": self.key, "description": self.description,
                "dimension": self.dimension, "minimum": str(self.minimum),
                "maximum": str(self.maximum)}


@dataclass(frozen=True)
class TeacherRubric:
    rubric_id: str
    revision: int
    problem_id: str
    problem_revision: int
    criteria: tuple[RubricCriterion, ...]
    approved_by: str | None = None
    reading_status: str = "uncertain"

    def __post_init__(self) -> None:
        _identity(self.rubric_id, "rubric_id")
        _identity(self.problem_id, "problem_id")
        _revision(self.revision)
        _revision(self.problem_revision)
        _reading(self.reading_status)
        object.__setattr__(self, "criteria", tuple(self.criteria))
        if not self.criteria or any(not isinstance(c, RubricCriterion) for c in self.criteria):
            raise ValueError("criteria must contain RubricCriterion objects")
        if len({c.key for c in self.criteria}) != len(self.criteria):
            raise ValueError("criterion keys must be unique")
        if self.approved_by is not None:
            _identity(self.approved_by, "approved_by")

    @property
    def maximum_total(self) -> Decimal:
        return _sum_points([c.maximum for c in self.criteria])

    def document(self) -> dict[str, Any]:
        return {"rubric_id": self.rubric_id, "revision": self.revision,
                "problem_id": self.problem_id, "problem_revision": self.problem_revision,
                "criteria": [c.document() for c in self.criteria],
                "approved_by": self.approved_by, "reading_status": self.reading_status}


def source_documents(context: TeacherContext, answer: EssayAnswer,
                     rubric: TeacherRubric) -> dict[str, dict[str, Any]]:
    """Exact snapshots for the host to register as four engine source fields."""
    return {"teacher_context": context.document(), "problem": context.problem.document(),
            "answer": answer.document(), "rubric": rubric.document()}


@dataclass(frozen=True)
class ScoreBinding:
    teacher_context_hash: str
    problem_hash: str
    answer_hash: str
    rubric_hash: str

    @classmethod
    def capture(cls, context: TeacherContext, answer: EssayAnswer,
                rubric: TeacherRubric) -> ScoreBinding:
        documents = source_documents(context, answer, rubric)
        return cls(*(_hash(documents[role]) for role in
                     ("teacher_context", "problem", "answer", "rubric")))

    def document(self) -> dict[str, str]:
        return {"teacher_context": self.teacher_context_hash, "problem": self.problem_hash,
                "answer": self.answer_hash, "rubric": self.rubric_hash}


@dataclass(frozen=True)
class AnswerEvidence:
    answer_id: str
    answer_revision: int
    answer_text_hash: str
    start: int
    end: int
    quote: str

    def __post_init__(self) -> None:
        _identity(self.answer_id, "answer_id")
        _revision(self.answer_revision)
        _identity(self.answer_text_hash, "answer_text_hash")

    @classmethod
    def from_answer(cls, answer: EssayAnswer, start: int, end: int) -> AnswerEvidence:
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, int) or not isinstance(end, int) or
                not 0 <= start < end <= len(answer.text)):
            raise ValueError("evidence must be a non-empty answer span")
        return cls(answer.answer_id, answer.revision, text_hash(answer.text),
                   start, end, answer.text[start:end])

    def document(self) -> dict[str, Any]:
        return dict(answer_id=self.answer_id, answer_revision=self.answer_revision,
                    answer_text_hash=self.answer_text_hash, start=self.start, end=self.end,
                    quote=self.quote)


@dataclass(frozen=True)
class CriterionScore:
    criterion_key: str
    points: Decimal | str | int
    evidence: tuple[AnswerEvidence, ...]
    reason: str

    def __post_init__(self) -> None:
        _identity(self.criterion_key, "criterion_key")
        _identity(self.reason, "reason")
        object.__setattr__(self, "points", _decimal(self.points))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if any(not isinstance(e, AnswerEvidence) for e in self.evidence):
            raise ValueError("evidence must contain AnswerEvidence objects")

    def document(self) -> dict[str, Any]:
        return {"criterion_key": self.criterion_key, "points": str(self.points),
                "evidence": [e.document() for e in self.evidence], "reason": self.reason}


@dataclass(frozen=True)
class ScoreProposal:
    proposal_id: str
    binding: ScoreBinding
    scores: tuple[CriterionScore, ...]
    reported_total: Decimal | str | int
    status: str = field(default="ai_proposed", init=False)

    def __post_init__(self) -> None:
        _identity(self.proposal_id, "proposal_id")
        if not isinstance(self.binding, ScoreBinding):
            raise ValueError("binding must be ScoreBinding")
        object.__setattr__(self, "scores", tuple(self.scores))
        if any(not isinstance(s, CriterionScore) for s in self.scores):
            raise ValueError("scores must contain CriterionScore objects")
        object.__setattr__(self, "reported_total", _decimal(self.reported_total))

    @property
    def calculated_total(self) -> Decimal:
        return _sum_points([s.points for s in self.scores])

    def document(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "binding": self.binding.document(),
                "scores": [s.document() for s in self.scores],
                "reported_total": str(self.reported_total), "status": self.status}


def propose_score(proposal_id: str, context: TeacherContext, answer: EssayAnswer,
                  rubric: TeacherRubric, scores: Sequence[CriterionScore]) -> ScoreProposal:
    rows = tuple(scores)
    return ScoreProposal(proposal_id, ScoreBinding.capture(context, answer, rubric), rows,
                         _sum_points([row.points for row in rows]))


@dataclass(frozen=True)
class ScoreIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class ScoreReview:
    issues: tuple[ScoreIssue, ...]
    status: str = field(default="ai_proposed", init=False)

    @property
    def valid(self) -> bool:
        return not self.issues


class ScoreHold(ValueError):
    def __init__(self, issues: Sequence[ScoreIssue]):
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{i.code}: {i.detail}" for i in self.issues))


def review_score(proposal: ScoreProposal, context: TeacherContext, answer: EssayAnswer,
                 rubric: TeacherRubric) -> ScoreReview:
    """Check only observable bindings, spans and arithmetic, never reason truth."""
    issues: list[ScoreIssue] = []

    def add(code: str, detail: str) -> None:
        issues.append(ScoreIssue(code, detail))

    current = ScoreBinding.capture(context, answer, rubric).document()
    for role, old_hash in proposal.binding.document().items():
        if old_hash != current[role]:
            add("STALE_" + role.upper(), f"{role} changed since the proposal")
    if rubric.approved_by != context.teacher_id:
        add("RUBRIC_NOT_APPROVED", "current teacher has not approved this rubric version")
    if (rubric.problem_id, rubric.problem_revision) != (
            context.problem.problem_id, context.problem.revision):
        add("RUBRIC_PROBLEM_MISMATCH", "rubric does not bind this problem version")
    for role, document in source_documents(context, answer, rubric).items():
        if document.get("reading_status") == "uncertain":
            add("READING_UNCERTAIN", f"{role} transcription requires teacher verification")
    dimensions = {c.dimension for c in rubric.criteria}
    if not {"creativity", "argument_validity"}.issubset(dimensions):
        add("DIMENSIONS_NOT_SEPARATE", "teacher must supply separate creativity and argument criteria")
    criteria = {c.key: c for c in rubric.criteria}
    keys = [s.criterion_key for s in proposal.scores]
    if len(keys) != len(set(keys)):
        add("DUPLICATE_CRITERION", "a criterion is scored more than once")
    if set(keys) != set(criteria):
        add("CRITERION_SET_MISMATCH", "score keys must exactly match the provided rubric")
    for row in proposal.scores:
        criterion = criteria.get(row.criterion_key)
        if criterion and not criterion.minimum <= row.points <= criterion.maximum:
            add("POINTS_OUT_OF_RANGE", row.criterion_key)
        if not row.evidence:
            add("EVIDENCE_MISSING", row.criterion_key)
        for evidence in row.evidence:
            if (evidence.answer_id, evidence.answer_revision, evidence.answer_text_hash) != (
                    answer.answer_id, answer.revision, text_hash(answer.text)):
                add("EVIDENCE_ANSWER_MISMATCH", row.criterion_key)
            if (isinstance(evidence.start, bool) or isinstance(evidence.end, bool) or
                    not isinstance(evidence.start, int) or not isinstance(evidence.end, int) or
                    not 0 <= evidence.start < evidence.end <= len(answer.text)):
                add("EVIDENCE_SPAN_INVALID", row.criterion_key)
            elif (not isinstance(evidence.quote, str) or not evidence.quote or
                  answer.text[evidence.start:evidence.end] != evidence.quote):
                add("EVIDENCE_QUOTE_MISMATCH", row.criterion_key)
    if proposal.reported_total != proposal.calculated_total:
        add("TOTAL_MISMATCH", "reported total differs from the sum of criterion points")
    if not 0 <= proposal.calculated_total <= rubric.maximum_total:
        add("TOTAL_OUT_OF_RANGE", "total exceeds the supplied rubric bounds")
    return ScoreReview(tuple(issues))


@dataclass(frozen=True)
class TeacherConfirmedScore:
    proposal: ScoreProposal
    confirmed_by: str
    confirmation_receipt: str
    status: str = field(default="teacher_confirmed", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, ScoreProposal):
            raise ValueError("proposal must be ScoreProposal")
        _identity(self.confirmed_by, "confirmed_by")
        _identity(self.confirmation_receipt, "confirmation_receipt")


def confirm_score(proposal: ScoreProposal, context: TeacherContext, answer: EssayAnswer,
                  rubric: TeacherRubric, *, teacher_id: str,
                  confirmation_receipt: str) -> TeacherConfirmedScore:
    """Host-only decision record. Caller must authenticate the teacher separately."""
    review = review_score(proposal, context, answer, rubric)
    issues = list(review.issues)
    if teacher_id != context.teacher_id:
        issues.append(ScoreIssue("TEACHER_MISMATCH", "confirmation requires the bound teacher"))
    if not isinstance(confirmation_receipt, str) or not confirmation_receipt.strip():
        issues.append(ScoreIssue("TEACHER_RECEIPT_MISSING", "explicit teacher decision is required"))
    if issues:
        raise ScoreHold(issues)
    return TeacherConfirmedScore(proposal, teacher_id, confirmation_receipt)


def review_confirmation(confirmed: TeacherConfirmedScore, context: TeacherContext,
                        answer: EssayAnswer, rubric: TeacherRubric) -> ScoreReview:
    """Recheck before display/export: an old confirmation cannot approve new text."""
    issues = list(review_score(confirmed.proposal, context, answer, rubric).issues)
    if confirmed.confirmed_by != context.teacher_id:
        issues.append(ScoreIssue("TEACHER_MISMATCH", "teacher context changed"))
    if not confirmed.confirmation_receipt.strip():
        issues.append(ScoreIssue("TEACHER_RECEIPT_MISSING", "teacher decision is missing"))
    return ScoreReview(tuple(issues))


# Fixed teaching order. These prompts themselves provide no answer, formula or equation.
GUIDANCE_POLICY = "student_interprets_then_conditions_then_diagram_then_student_equation_v1"
GUIDANCE_STAGES = ("korean_interpretation", "conditions_and_target", "diagram_meaning",
                   "student_equation")
_GUIDANCE = {
    "korean_interpretation": "문제의 문장을 한국어로 어떻게 이해했는지 학생의 말로 설명해 주십시오.",
    "conditions_and_target": "문제에서 주어진 조건과 구해야 할 양을 각각 문장으로 적어 주십시오.",
    "diagram_meaning": "그림이나 도표가 나타내는 대상과 관계를 문장으로 설명해 주십시오. 그림이 없으면 없다고 밝혀 주십시오.",
    "student_equation": "앞서 해석한 조건과 그림의 의미를 바탕으로 학생이 직접 식 또는 논증을 작성해 주십시오.",
}


def guidance_prompt(stage: str) -> str:
    if stage not in _GUIDANCE:
        raise ValueError("unknown guidance stage")
    return _GUIDANCE[stage]


@dataclass(frozen=True)
class GuidanceProgress:
    """Records student input order, not proof that the student understood it."""
    responses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.responses, str):
            raise ValueError("responses must be a sequence of complete student responses")
        object.__setattr__(self, "responses", tuple(self.responses))
        if len(self.responses) > len(GUIDANCE_STAGES):
            raise ValueError("too many guidance responses")
        for response in self.responses:
            _identity(response, "student response")

    @property
    def stage(self) -> str:
        return (GUIDANCE_STAGES[len(self.responses)] if len(self.responses) < len(GUIDANCE_STAGES)
                else "teacher_review")

    @property
    def prompt(self) -> str:
        if self.stage == "teacher_review":
            return "학생의 해석과 식 또는 논증을 교사가 확인해 주십시오."
        return guidance_prompt(self.stage)

    def record_student_response(self, stage: str, text: str) -> GuidanceProgress:
        if stage != self.stage or self.stage == "teacher_review":
            raise ValueError("guidance stages must follow the fixed order")
        _identity(text, "student response")
        return GuidanceProgress(self.responses + (text,))


@dataclass(frozen=True)
class EngineSourceLink:
    source_id: str
    field_name: str
    revision: int
    snapshot_hash: str

    def __post_init__(self) -> None:
        _identity(self.source_id, "source_id")
        _identity(self.field_name, "field_name")
        _revision(self.revision)
        _identity(self.snapshot_hash, "snapshot_hash")


class CandidatePort(Protocol):
    def read_contract(self) -> dict[str, Any]: ...

    def submit_candidate(self, *, content: Any, purpose: str,
                         source_refs: dict[str, dict[str, int]],
                         consumption_complete: bool, goal_ids: list[str]) -> str: ...


def submit_score_proposal(port: CandidatePort, proposal: ScoreProposal,
                          context: TeacherContext, answer: EssayAnswer,
                          rubric: TeacherRubric, *,
                          source_links: Mapping[str, EngineSourceLink],
                          goal_ids: Sequence[str]) -> str:
    """Thin model-port adapter; never reviews, consumes or teacher-confirms itself.

    The trusted host must register source_documents() as exact source field values,
    construct links from returned field revisions and authenticate the teacher.
    Registered source values are checked through the port; the engine rechecks
    revisions at submission to reject changes after this read.
    """
    review = review_score(proposal, context, answer, rubric)
    issues = list(review.issues)
    expected = proposal.binding.document()
    if set(source_links) != set(expected):
        issues.append(ScoreIssue("SOURCE_LINKS_INCOMPLETE", "all four snapshots must be linked"))
    refs: dict[str, dict[str, int]] = {}
    seen: set[tuple[str, str]] = set()
    for role, link in source_links.items():
        if not isinstance(link, EngineSourceLink):
            issues.append(ScoreIssue("SOURCE_LINK_INVALID", role))
            continue
        if role not in expected or link.snapshot_hash != expected[role]:
            issues.append(ScoreIssue("SOURCE_SNAPSHOT_MISMATCH", role))
        pair = (link.source_id, link.field_name)
        if pair in seen:
            issues.append(ScoreIssue("SOURCE_LINK_ALIAS", role))
        seen.add(pair)
        refs.setdefault(link.source_id, {})[link.field_name] = link.revision
    if isinstance(goal_ids, str) or not goal_ids or any(
            not isinstance(goal, str) or not goal.strip() for goal in goal_ids):
        issues.append(ScoreIssue("GOALS_MISSING", "explicit task goal identifiers are required"))
    if issues:
        raise ScoreHold(issues)
    contract = port.read_contract()
    sources = contract.get("sources", {})
    for role, link in source_links.items():
        try:
            record = sources[link.source_id]["fields"][link.field_name]
            matches = (record["revision"] == link.revision and
                       _hash(record["value"]) == expected[role])
        except (KeyError, TypeError, ValueError):
            matches = False
        if not matches:
            issues.append(ScoreIssue("ENGINE_SOURCE_MISMATCH", role))
    if issues:
        raise ScoreHold(issues)
    return port.submit_candidate(content=proposal.document(), purpose="essay_score_proposal",
                                 source_refs=refs, consumption_complete=True,
                                 goal_ids=list(goal_ids))
