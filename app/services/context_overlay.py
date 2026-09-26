"""Task-scoped authoring context overlay.

Imported before generator/verifier modules so those modules bind to the
task-scoped context builders rather than the broader legacy builders.
"""

from __future__ import annotations

import re
from typing import Any

from app.services import authoring_assets as base
from app.services.private_asset_registry import build_task_scoped_private_context


def _task_excerpt(text: str, *, source_text: str, question_types: list[str], limit: int) -> str:
    terms = set(re.findall(r"[A-Za-z가-힣0-9]+", " ".join(question_types or []).lower()))
    terms |= {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z'-]{3,}|[가-힣]{2,}", source_text or "")[:80]
    }
    if not terms:
        return base._clip(text, min(limit, 2600))
    lines = text.splitlines()
    selected: list[str] = []
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            selected.extend(lines[max(0, i - 2): min(len(lines), i + 5)])
            selected.append("")
        if sum(len(x) + 1 for x in selected) >= limit:
            break
    return base._clip("\n".join(selected) if selected else text, limit)


def build_authoring_context(
    *,
    subject: str,
    grade: str,
    source_text: str,
    question_types: list[str],
) -> dict[str, Any]:
    if subject.strip() != "영어":
        return {
            "authority": "Use only EduRecover rules applicable to the requested subject.",
            "privacy_boundary": "Use only this task payload.",
            "delivery_scope": "single_request_only",
        }

    syntax = base._read("syntax_master_v1.txt")
    grade_syntax = base._read("grade_syntax_3y.txt")
    marker = next((m for key, m in base._GRADE_MARKERS.items() if key in grade), "SYN-GRADE")

    return {
        "authority": (
            "These are provider-neutral EduRecover authoring constraints. "
            "Use only the excerpts in this request and do not request unrelated internal material."
        ),
        "privacy_boundary": (
            "Do not request or use platform chat history, unrelated customer data, "
            "credentials, internal logs, or assets outside this task payload."
        ),
        "delivery_scope": "single_request_only",
        "persistent_provider_memory_required": False,
        "vocabulary": {
            "matched_source_entries": base._source_vocab_rows(source_text),
            "grade_vocab_verified": _task_excerpt(
                base._read("grade_vocab_blueprint_verified.txt"),
                source_text=source_text,
                question_types=question_types,
                limit=2400,
            ),
        },
        "syntax_grade_profile": base._section(syntax, marker, chars=3200),
        "three_year_grade_syntax": _task_excerpt(
            grade_syntax, source_text=source_text, question_types=question_types, limit=3600
        ),
        "current_policy_excerpt": _task_excerpt(
            base._read("current_policy.txt"), source_text=source_text, question_types=question_types, limit=3200
        ),
        "english_authoring_rules": _task_excerpt(
            base._read("english_authoring_rules.txt"), source_text=source_text, question_types=question_types, limit=4400
        ),
        "authoring_wizard_authority": _task_excerpt(
            base._read("authoring_wizard_canonical.txt"), source_text=source_text, question_types=question_types, limit=4400
        ),
        "distractor_rules": _task_excerpt(
            base._read("distractor_principles_35.txt"), source_text=source_text, question_types=question_types, limit=3800
        ),
        "additional_private_assets": build_task_scoped_private_context(
            grade=grade,
            source_text=source_text,
            question_types=question_types,
        ),
        "requested_question_types": list(question_types),
    }


def build_verification_context(problems: list[dict[str, Any]]) -> dict[str, Any]:
    combined_source = "\n".join(
        str(problem.get("passage") or problem.get("evidence") or "")
        for problem in problems
        if isinstance(problem, dict)
    )
    qtypes = [
        str(problem.get("question_type") or problem.get("question") or "")
        for problem in problems
        if isinstance(problem, dict)
    ]
    return {
        "authority": "Validate using only task-scoped EduRecover verification excerpts.",
        "privacy_boundary": "Do not request data outside this verification payload.",
        "delivery_scope": "single_request_only",
        "persistent_provider_memory_required": False,
        "source_vocab_entries": base._source_vocab_rows(combined_source),
        "english_authoring_rules": _task_excerpt(
            base._read("english_authoring_rules.txt"), source_text=combined_source, question_types=qtypes, limit=4200
        ),
        "authoring_wizard_authority": _task_excerpt(
            base._read("authoring_wizard_canonical.txt"), source_text=combined_source, question_types=qtypes, limit=4200
        ),
        "distractor_rules": _task_excerpt(
            base._read("distractor_principles_35.txt"), source_text=combined_source, question_types=qtypes, limit=3600
        ),
        "syntax_rules": _task_excerpt(
            base._read("syntax_master_v1.txt"), source_text=combined_source, question_types=qtypes, limit=3200
        ),
        "additional_private_assets": build_task_scoped_private_context(
            grade="",
            source_text=combined_source,
            question_types=qtypes,
        ),
    }


# Patch before auto_generator/provider_verifier import their function objects.
base.build_authoring_context = build_authoring_context
base.build_verification_context = build_verification_context
