"""Private EduRecover authoring asset loader.

Full source assets are kept in the private EduRecover server/repository.
External AI providers receive only task-relevant excerpts built here.
Platform chat history is never included in an AI provider request.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).resolve().parents[1] / "authoring_assets"

ASSET_MANIFEST: list[dict[str, Any]] = [
    {
        "file": "vocab_norm_v1.txt",
        "source_title": "01_수능영단어_정규화정리본_VOC-ID_20260923.txt",
        "description": (
            "정규화 구체 어휘 자산(VOC-ID). 복구된 원본에는 개별 단어의 "
            "고1/고2/고3 확정 태그가 없으므로 학년 태그를 임의 생성하지 않는다."
        ),
        "server_only": True,
    },
    {
        "file": "grade_vocab_blueprint_verified.txt",
        "source_title": "영어_출제블루프린트_고1고2고3_300문항_통합본.txt에서 직접 회수",
        "description": "블루프린트 원본에서 직접 회수한 학년별 구체 어휘.",
        "server_only": True,
    },
    {
        "file": "syntax_master_v1.txt",
        "source_title": "02_수능내신_구문집_SYNTAX-ID_20260923.txt",
        "description": "수능·내신 구체 구문 자산과 고1·고2·고3 구문 범위.",
        "server_only": True,
    },
    {
        "file": "grade_syntax_3y.txt",
        "source_title": "영어_학년별_구문난도_3개년45회전수조사_내신서술형청사진_20260920.txt",
        "description": "3개년 45회 실측 기반 학년별 구문난도와 내신 서술형 청사진.",
        "server_only": True,
    },
    {
        "file": "english_authoring_rules.txt",
        "source_title": "영어_문항제작_영업비밀_최신규칙.txt",
        "description": "영어 문항 제작 최신 내부 출제·검수 규칙.",
        "server_only": True,
    },
    {
        "file": "authoring_wizard_canonical.txt",
        "source_title": "01_AUTHORING_WIZARD_CANONICAL_v4AUTH_20260922-4.txt",
        "description": "출제마법사 통합 단일본 / PATENT-V4 AUTHORITY.",
        "server_only": True,
    },
    {
        "file": "distractor_principles_35.txt",
        "source_title": "영어_오답선지_구성원리_35개_유형별_내부본_20260920.txt",
        "description": "영어 오답선지 35개 제작·검수 원리.",
        "server_only": True,
    },
    {
        "file": "current_policy.txt",
        "source_title": "01_CURRENT_POLICY_v9_v7_2_CURRENT_20260921.txt",
        "description": "현재 운영정책: 학년·어휘·구문 게이트 포함.",
        "server_only": True,
    },
]

_GRADE_MARKERS = {
    "1학년": "SYN-GRADE-H1",
    "고1": "SYN-GRADE-H1",
    "2학년": "SYN-GRADE-H2",
    "고2": "SYN-GRADE-H2",
    "3학년": "SYN-GRADE-H3",
    "고3": "SYN-GRADE-H3",
}


_PROVENANCE_PATTERNS = (
    (re.compile(r"(?i)chatgpt|openai|gemini|anthropic|claude"), "외부 AI"),
    (re.compile(r"제미나이|챗지피티|클로드"), "외부 AI"),
    (re.compile(r"(?i)\\b(?:gpt|claude|gemini)-[a-z0-9.\\-]+"), "외부모델"),
)


def _neutralize_provenance(text: str) -> str:
    """Remove provider/model-origin clues from runtime authoring material."""
    value = text
    for pattern, replacement in _PROVENANCE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def get_asset_manifest() -> list[dict[str, Any]]:
    public_names = {
        "vocab_norm_v1.txt": "EduRecover 정규화 구체 어휘 자산",
        "grade_vocab_blueprint_verified.txt": "EduRecover 학년별 구체 어휘 자산",
        "syntax_master_v1.txt": "EduRecover 수능·내신 구체 구문 자산",
        "grade_syntax_3y.txt": "EduRecover 학년별 구문난도·서술형 기준",
        "english_authoring_rules.txt": "EduRecover 영어 문항 출제·검수 지침",
        "authoring_wizard_canonical.txt": "EduRecover 출제마법사 통합 기준",
        "distractor_principles_35.txt": "EduRecover 오답선지 제작·검수 원리",
        "current_policy.txt": "EduRecover 현재 운영정책",
    }
    result = []
    for item in ASSET_MANIFEST:
        clean = dict(item)
        clean["source_title"] = public_names.get(clean["file"], "EduRecover 내부 출제자산")
        clean["provider_origin_removed"] = True
        result.append(clean)
    return result


def _read(name: str) -> str:
    return _neutralize_provenance((ASSET_DIR / name).read_text(encoding="utf-8"))


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...[server excerpt clipped]"


def _section(text: str, marker: str, *, chars: int = 4000) -> str:
    pos = text.find(marker)
    if pos < 0:
        return ""
    return _clip(text[pos:], chars)


def _source_vocab_rows(source_text: str, *, max_rows: int = 120) -> str:
    """Return concrete VOC-ID entries that occur in the supplied source.

    The normalized vocabulary source does not contain verified H1/H2/H3 tags.
    Grade labels are therefore never invented for individual headwords.
    """
    vocab = _read("vocab_norm_v1.txt")
    tokens = set(re.findall(r"[A-Za-z][A-Za-z'-]+", source_text.lower()))
    rows: list[str] = []
    for line in vocab.splitlines():
        if not line.startswith("VOC-"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            continue
        if parts[1].lower() in tokens:
            rows.append(line)
            if len(rows) >= max_rows:
                break
    return "\n".join(rows)


def build_authoring_context(
    *,
    subject: str,
    grade: str,
    source_text: str,
    question_types: list[str],
) -> dict[str, Any]:
    """Build the minimum task-relevant EduRecover authoring package."""
    if subject.strip() != "영어":
        return {
            "authority": "Use only EduRecover rules applicable to the requested subject.",
            "asset_manifest": get_asset_manifest(),
            "privacy_boundary": "Do not request or use platform chat history.",
        }

    syntax = _read("syntax_master_v1.txt")
    grade_syntax = _read("grade_syntax_3y.txt")
    marker = next((m for key, m in _GRADE_MARKERS.items() if key in grade), "SYN-GRADE")

    return {
        "authority": (
            "These are private, provider-neutral EduRecover authoring materials and instructions. "
            "External provider/model provenance labels are removed from runtime material. "
            "Apply the same EduRecover constraints for generation and verification. "
            "Do not represent an internal asset as an official MOE/KICE rule unless the source itself says so."
        ),
        "privacy_boundary": (
            "Never ask for, infer, or include EduRecover platform chat history, unrelated customer data, "
            "API keys, internal logs, or other customers' materials. Use only this task payload."
        ),
        "vocabulary": {
            "matched_source_entries": _source_vocab_rows(source_text),
            "grade_vocab_verified": _read("grade_vocab_blueprint_verified.txt"),
            "grade_tag_status": (
                "The normalized VOC-ID corpus is installed on the server, but its recovered source does "
                "not contain verified per-word H1/H2/H3 tags. Do not fabricate grade labels. Apply the "
                "separate grade/curriculum suitability gate and keep vocabulary close to the supplied source."
            ),
        },
        "syntax_grade_profile": _section(syntax, marker, chars=5200),
        "three_year_grade_syntax": _clip(grade_syntax, 9000),
        "current_policy_excerpt": _clip(_read("current_policy.txt"), 8000),
        "english_authoring_rules": _clip(_read("english_authoring_rules.txt"), 15000),
        "authoring_wizard_authority": _clip(_read("authoring_wizard_canonical.txt"), 15000),
        "distractor_rules": _clip(_read("distractor_principles_35.txt"), 12000),
        "requested_question_types": list(question_types),
    }


def build_verification_context(problems: list[dict[str, Any]]) -> dict[str, Any]:
    combined_source = "\n".join(
        str(problem.get("passage") or problem.get("evidence") or "")
        for problem in problems
        if isinstance(problem, dict)
    )
    return {
        "authority": (
            "Validate against the same provider-neutral EduRecover private authoring and verification rules "
            "used for generation."
        ),
        "privacy_boundary": (
            "Do not request or use platform chat history, unrelated customer data, internal logs, "
            "or data outside this verification payload."
        ),
        "source_vocab_entries": _source_vocab_rows(combined_source),
        "english_authoring_rules": _clip(_read("english_authoring_rules.txt"), 12000),
        "authoring_wizard_authority": _clip(_read("authoring_wizard_canonical.txt"), 12000),
        "distractor_rules": _clip(_read("distractor_principles_35.txt"), 10000),
        "syntax_rules": _clip(_read("syntax_master_v1.txt"), 7000),
    }
