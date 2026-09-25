"""Prototype Socratic Trap service for reasoning-risk detection.

The service examines a teacher-selected English reading-comprehension explanation batch
for common hallucination and logic-risk signals. It never rewrites or reveals a
replacement answer. Instead, it returns Socratic counter-questions that force
the worker to re-check source evidence, scope, causality, and inference.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

ABSOLUTE_TERMS = {
    "always",
    "never",
    "completely",
    "entirely",
    "only",
    "all",
    "none",
    "must",
    "certainly",
}

EXTERNAL_KNOWLEDGE_CUES = (
    "common knowledge",
    "we know that",
    "it is well known",
    "in real life",
    "generally speaking",
    "obviously",
    "clearly",
)

SPECULATION_CUES = (
    "probably",
    "perhaps",
    "maybe",
    "likely",
    "seems to",
    "appears to",
)

CAUSAL_CUES = (
    "because",
    "therefore",
    "thus",
    "hence",
    "as a result",
    "causes",
    "caused",
    "leads to",
    "led to",
)


@dataclass(frozen=True)
class TrapFinding:
    problem_id: int
    trap_type: str
    signal: str
    socratic_prompt: str


@dataclass(frozen=True)
class SocraticTrapResult:
    batch_size: int
    trap_count: int
    findings: tuple[TrapFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_size": self.batch_size,
            "trap_count": self.trap_count,
            "findings": [asdict(finding) for finding in self.findings],
        }


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.lower())
        if len(token) >= 3
    }


def _append_finding(
    findings: list[TrapFinding],
    *,
    problem_id: int,
    trap_type: str,
    signal: str,
    socratic_prompt: str,
) -> None:
    findings.append(
        TrapFinding(
            problem_id=problem_id,
            trap_type=trap_type,
            signal=signal,
            socratic_prompt=socratic_prompt,
        )
    )


def detect_socratic_traps(
    problems: list[dict[str, Any]],
) -> SocraticTrapResult:
    """Detect reasoning risks and return Socratic prompts only.

    This prototype treats each detection as a risk signal, not a corrected
    answer. The worker must revisit the source and reason again.
    """

    if not problems:
        raise ValueError("Socratic Trap requires at least one problem.")

    findings: list[TrapFinding] = []

    for index, problem in enumerate(problems, start=1):
        if not isinstance(problem, dict):
            _append_finding(
                findings,
                problem_id=index,
                trap_type="INVALID_ITEM_SHAPE",
                signal="The item is not a JSON object.",
                socratic_prompt=(
                    "Before judging the answer, can you restate this item as a "
                    "structured question, answer, explanation, and source so "
                    "each claim can be checked independently?"
                ),
            )
            continue

        raw_problem_id = problem.get("problem_id", index)
        try:
            problem_id = int(raw_problem_id)
        except (TypeError, ValueError):
            problem_id = index

        if problem_id < 1:
            problem_id = index

        question = _normalize_text(problem.get("question"))
        answer = _normalize_text(problem.get("answer"))
        explanation = _normalize_text(problem.get("explanation"))
        passage = _normalize_text(
            problem.get("passage")
            or problem.get("source_text")
            or problem.get("evidence")
        )

        if not question:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="MISSING_QUESTION",
                signal="No question text was supplied.",
                socratic_prompt=(
                    "What exact claim are you trying to validate, and what "
                    "would count as evidence for or against that claim?"
                ),
            )

        if not answer:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="MISSING_ANSWER",
                signal="No candidate answer was supplied.",
                socratic_prompt=(
                    "What candidate conclusion are you evaluating, and which "
                    "textual evidence would have to support it?"
                ),
            )

        if not explanation:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="MISSING_EXPLANATION",
                signal="No explanation was supplied.",
                socratic_prompt=(
                    "Which exact sentence or relation in the passage supports "
                    "your conclusion, and which competing interpretation does "
                    "that evidence rule out?"
                ),
            )
            continue

        lower_explanation = explanation.lower()

        absolutes = sorted(
            term
            for term in ABSOLUTE_TERMS
            if re.search(rf"\b{re.escape(term)}\b", lower_explanation)
        )
        if absolutes:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="OVERGENERALIZATION_RISK",
                signal="Absolute language detected: " + ", ".join(absolutes),
                socratic_prompt=(
                    "Does the passage justify that absolute scope, or only a "
                    "narrower claim? Which exact words determine the scope?"
                ),
            )

        external_cues = [
            cue for cue in EXTERNAL_KNOWLEDGE_CUES if cue in lower_explanation
        ]
        if external_cues:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="EXTERNAL_KNOWLEDGE_RISK",
                signal=(
                    "Outside-knowledge language detected: "
                    + ", ".join(external_cues)
                ),
                socratic_prompt=(
                    "If all outside knowledge were removed, which sentence in "
                    "the supplied text alone would still justify this inference?"
                ),
            )

        speculation_cues = [
            cue for cue in SPECULATION_CUES if cue in lower_explanation
        ]
        if speculation_cues:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="SPECULATION_RISK",
                signal=(
                    "Speculative language detected: "
                    + ", ".join(speculation_cues)
                ),
                socratic_prompt=(
                    "What textual evidence turns this possibility into a "
                    "supported inference? Which assumption are you adding if "
                    "that evidence is absent?"
                ),
            )

        causal_cues = [cue for cue in CAUSAL_CUES if cue in lower_explanation]
        if causal_cues and passage:
            passage_lower = passage.lower()
            passage_has_causal_cue = any(
                cue in passage_lower for cue in CAUSAL_CUES
            )
            if not passage_has_causal_cue:
                _append_finding(
                    findings,
                    problem_id=problem_id,
                    trap_type="CAUSALITY_INVENTION_RISK",
                    signal=(
                        "Explanation asserts causality, but the source text "
                        "does not contain an obvious causal marker."
                    ),
                    socratic_prompt=(
                        "Are you reading a causal relation that the passage "
                        "actually states, or converting correlation/sequence "
                        "into causation? Point to the wording that licenses the "
                        "causal step."
                    ),
                )

        if passage:
            explanation_tokens = _tokenize(explanation)
            passage_tokens = _tokenize(passage)

            if explanation_tokens:
                overlap = len(explanation_tokens & passage_tokens)
                overlap_ratio = overlap / len(explanation_tokens)
                if overlap_ratio < 0.20:
                    _append_finding(
                        findings,
                        problem_id=problem_id,
                        trap_type="SOURCE_GROUNDING_RISK",
                        signal=(
                            "Low lexical grounding between explanation and "
                            f"source text ({overlap_ratio:.2f})."
                        ),
                        socratic_prompt=(
                            "Which exact words in the passage support each key "
                            "claim in your explanation? Re-check every claim "
                            "that cannot be anchored to the source."
                        ),
                    )

        if answer and answer.lower() not in lower_explanation:
            _append_finding(
                findings,
                problem_id=problem_id,
                trap_type="ANSWER_RATIONALE_DISCONNECT_RISK",
                signal=(
                    "The candidate answer is not explicitly connected to the "
                    "explanation text."
                ),
                socratic_prompt=(
                    "Without changing the answer, can you explain exactly how "
                    "your cited evidence leads to that candidate rather than "
                    "to a competing option?"
                ),
            )

    return SocraticTrapResult(
        batch_size=len(problems),
        trap_count=len(findings),
        findings=tuple(findings),
    )
