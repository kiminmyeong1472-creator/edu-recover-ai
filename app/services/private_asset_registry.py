"""Private EduRecover asset registry and task-scoped excerpt selector.

This module keeps internal assets separate from provider payloads. Only assets
with provider_policy="excerpt_only" may contribute excerpts. Assets marked
"never_send" are internal archive/evidence only.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

PRIVATE_DIR = Path(__file__).resolve().parents[1] / "private_assets"

_PROVENANCE_PATTERNS = (
    (re.compile(r"(?i)chatgpt|openai|gemini|anthropic|claude"), "외부 AI"),
    (re.compile(r"제미나이|챗지피티|클로드"), "외부 AI"),
    (re.compile(r"(?i)\\b(?:gpt|claude|gemini)-[a-z0-9.\\-]+"), "외부모델"),
)


def _neutralize_provenance(text: str) -> str:
    value = text
    for pattern, replacement in _PROVENANCE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value

PRIVATE_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "file": "csat_authoring_principles_20260920.txt",
        "drive_id": "1Y1WhRdcQlHEHGJpk5xh4A_Mfr3rodHbP",
        "purpose": "수능형 출제원리",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "school_exam_authoring_principles_20260920.txt",
        "drive_id": "1MAQrJnlFCLOL_0gYLD1wecdn_NrT2Pn3",
        "purpose": "내신형 출제원리",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "distractor_36_direct_map_20260920.txt",
        "drive_id": "1jZNfxcNe3q51J11WIpNDXD_EKl11JWy3",
        "purpose": "오답선지 직접적용 원리",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "csat2026_distractor112_full_analysis.txt",
        "drive_id": "1hVlhrTjr4Y8Oq8KL_1rBqa3eRGod6kkd",
        "purpose": "수능 오답 분석",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "csat2026_choices128_full_analysis.txt",
        "drive_id": "11rQyGAjtLocojE691dGgwpgS5KHC1agj",
        "purpose": "수능 선택지 세부유형 분석",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "english_grade_blueprint_300.txt",
        "drive_id": "1L8Xsu26OMpxWj4u4on0rN-uwfPchnoDn",
        "purpose": "고1·고2·고3 300문항 출제 청사진",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "english300_distractor1200_retag.txt",
        "drive_id": "1RxLq8AnNq5mGL47qz8HnU_Rv1bZmwqTo",
        "purpose": "영어 300문항·오답 1200 재태깅 자산",
        "provider_policy": "excerpt_only",
    },
    {
        "file": "edurecover_handoff_20260926.txt",
        "drive_id": "1SrcTWk_gwL_CyBD8hanjofI2z7Go2E94cOg3XQVODeQ",
        "purpose": "현재 완전인계 문서",
        "provider_policy": "never_send",
    },
    {
        "file": "edurecover_source_dump_v3.24.5_20260924.txt",
        "drive_id": "1G8wx2YUhmqxh7mkVonJD6l4mQAbdxQQ0",
        "purpose": "과거 전체 소스 덤프",
        "provider_policy": "never_send",
        "ingest_state": "PENDING_SECRET_SCAN",
    },
    {
        "file": "current_authoring_chat_backup.txt",
        "drive_id": "11CpuE2_YtauAZ-COOm0FnoFfcF8MbZWA",
        "purpose": "출제원리 작업대화 백업",
        "provider_policy": "never_send",
        "ingest_state": "PENDING_SECRET_SCAN",
    },
)


def _path(name: str) -> Path:
    return PRIVATE_DIR / name


def _read(name: str) -> str:
    p = _path(name)
    if not p.exists():
        return ""
    return _neutralize_provenance(p.read_text(encoding="utf-8"))


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...[task excerpt clipped]"


def _keyword_excerpt(text: str, keywords: set[str], *, max_chars: int = 6500) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    selected: list[str] = []
    normalized = {k.lower() for k in keywords if k}
    for idx, line in enumerate(lines):
        lower = line.lower()
        if any(k in lower for k in normalized):
            start = max(0, idx - 2)
            end = min(len(lines), idx + 5)
            selected.extend(lines[start:end])
            selected.append("")
        if sum(len(x) + 1 for x in selected) >= max_chars:
            break
    if not selected:
        return _clip(text, min(max_chars, 2400))
    deduped: list[str] = []
    seen: set[str] = set()
    for line in selected:
        key = line.strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(line)
    return _clip("\n".join(deduped), max_chars)


def _source_terms(source_text: str, limit: int = 80) -> set[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]{3,}|[가-힣]{2,}", source_text or "")
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        low = token.lower()
        if low not in seen:
            seen.add(low)
            result.append(low)
        if len(result) >= limit:
            break
    return set(result)


def build_task_scoped_private_context(
    *,
    grade: str,
    source_text: str,
    question_types: list[str],
) -> dict[str, Any]:
    """Return only task-relevant excerpts for a single provider request."""
    qtext = " ".join(question_types or [])
    keywords = set(re.findall(r"[A-Za-z가-힣0-9]+", qtext.lower()))
    keywords |= _source_terms(source_text)
    keywords |= {grade.lower()}

    csat_markers = {"수능", "빈칸", "주제", "제목", "요지", "순서", "삽입", "무관", "요약", "함축"}
    school_markers = {"내신", "어법", "서술형", "내용", "일치", "불일치", "어휘", "문법"}
    use_csat = not question_types or any(marker in qtext for marker in csat_markers)
    use_school = not question_types or any(marker in qtext for marker in school_markers)

    excerpts: dict[str, str] = {}
    if use_csat:
        excerpts["csat_authoring_principles"] = _keyword_excerpt(
            _read("csat_authoring_principles_20260920.txt"), keywords, max_chars=5200
        )
        excerpts["csat_choice_patterns"] = _keyword_excerpt(
            _read("csat2026_choices128_full_analysis.txt"), keywords, max_chars=4200
        )
    if use_school:
        excerpts["school_exam_authoring_principles"] = _keyword_excerpt(
            _read("school_exam_authoring_principles_20260920.txt"), keywords, max_chars=5200
        )

    excerpts["distractor_direct_map"] = _keyword_excerpt(
        _read("distractor_36_direct_map_20260920.txt"), keywords, max_chars=4200
    )
    excerpts["grade_blueprint"] = _keyword_excerpt(
        _read("english_grade_blueprint_300.txt"), keywords, max_chars=3600
    )
    excerpts["retag_evidence"] = _keyword_excerpt(
        _read("english300_distractor1200_retag.txt"), keywords, max_chars=3600
    )

    return {
        "delivery_scope": "single_request_only",
        "persistent_provider_memory_required": False,
        "provider_may_store_internal_corpus": False,
        "task_relevant_excerpts": {k: v for k, v in excerpts.items() if v.strip()},
    }


def private_asset_integrity_snapshot() -> list[dict[str, Any]]:
    """Internal integrity inventory; never includes file contents."""
    rows: list[dict[str, Any]] = []
    for item in PRIVATE_ASSETS:
        p = _path(str(item["file"]))
        row = dict(item)
        if p.exists():
            data = p.read_bytes()
            row.update(
                {
                    "local_state": "INSTALLED",
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        else:
            row.update({"local_state": item.get("ingest_state", "NOT_INSTALLED")})
        rows.append(row)
    return rows
