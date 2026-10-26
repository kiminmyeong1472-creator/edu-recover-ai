#!/usr/bin/env python3
"""On-demand five-source official evidence sync. Never runs during app requests.

No automated crawler. Downloads one explicitly selected public page per source.
The owner must separately import any product formulations from source-verified records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.rug_engine import SOURCES, official_url
from app.rug_full_rag import RuGFullRAG, extract_html, extract_pdf, utc_date

MAX_BYTES = 12_000_000


class OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, source_id: str):
        super().__init__()
        self.source_id = source_id
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > 3 or not official_url(newurl, self.source_id):
            raise ValueError("external_redirect_blocked")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_one(source_id: str, url: str, timeout: int = 12) -> tuple[str, str, int, str]:
    if not official_url(url, source_id):
        raise ValueError("source_url_not_official")
    handler = OfficialRedirects(source_id)
    opener = build_opener(handler)
    request = Request(url, headers={"User-Agent": "RuG-Reference-Indexer/1.0 (manual single-page verification)",
                                   "Accept": "text/html,application/pdf;q=0.8"})
    with opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        if not official_url(final_url, source_id):
            raise ValueError("unsafe_final_url")
        advertised = response.headers.get("Content-Length")
        if advertised and int(advertised) > MAX_BYTES:
            raise ValueError("source_page_too_large")
        raw = response.read(MAX_BYTES + 1)
        content_type = response.headers.get_content_type()
    if len(raw) > MAX_BYTES:
        raise ValueError("source_page_too_large")
    if content_type == "application/pdf" or raw.startswith(b"%PDF-"):
        return extract_pdf(raw), "official_pdf", len(raw), hashlib.sha256(raw).hexdigest()
    if content_type not in {"text/html", "application/xhtml+xml", "text/plain", "application/octet-stream"}:
        raise ValueError("unsupported_official_content_type")
    if content_type == "text/plain":
        text = raw.decode("utf-8", errors="replace")
    else:
        text = extract_html(raw)
    if any(term in text.casefold() for term in ('access denied', 'request rejected', 'verify you are human', 'captcha challenge')):
        raise ValueError("official_source_block_page")
    return text, "official_page", len(raw), hashlib.sha256(raw).hexdigest()


def sync(store: RuGFullRAG, sources: list[str], delay: float = 0.8) -> dict:
    report = []
    for idx, sid in enumerate(sources):
        entry = SOURCES[sid]
        try:
            content, fmt, size, raw_hash = fetch_one(sid, entry["url"])
            result = store.ingest_text(source_id=sid, url=entry["url"], title=entry["name"],
                                       text=content, checked_on=utc_date(), source_mode=fmt, raw_sha256=raw_hash)
            store.record_sync_result(sid, True)
            report.append({"source": sid, "ok": True, "bytes_read": size, **result})
        except (OSError, ValueError, TimeoutError, ImportError) as exc:
            store.record_sync_result(sid, False, type(exc).__name__ + ": " + str(exc))
            report.append({"source": sid, "ok": False, "reason": str(exc)[:240]})
        if idx < len(sources) - 1:
            time.sleep(max(0, min(delay, 5)))
    return {"results": report, "coverage": store.coverage(), "all_succeeded": all(x["ok"] for x in report)}


def local_import(store: RuGFullRAG, manifest: Path) -> dict:
    """Import reviewed source transcript with URL/date. Never infer review from filename."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) > 500:
        raise ValueError("invalid_import_manifest")
    report = []
    for row in data:
        sid = row["source_id"]
        url = row["url"]
        if not official_url(url, sid):
            raise ValueError("source_url_not_official")
        if row.get("reviewed") is not True:
            raise ValueError("manual_transcript_requires_explicit_review")
        text_file = Path(row["local_path"])
        if not text_file.is_file() or text_file.stat().st_size > MAX_BYTES:
            raise ValueError("local_source_missing_or_too_large")
        extension = text_file.suffix.lower()
        content = text_file.read_bytes()
        if extension == ".pdf":
            text, mode = extract_pdf(content), "official_pdf"
        elif extension in {".htm", ".html"}:
            text, mode = extract_html(content), "human_verified_transcript"
        elif extension in {".txt", ".md"}:
            text, mode = content.decode("utf-8-sig"), "human_verified_transcript"
        else:
            raise ValueError("unsupported_import_extension")
        report.append(store.ingest_text(source_id=sid, url=url, title=row["title"],
                                        text=text, checked_on=row["checked_on"], source_mode=mode, reviewed=True))
    return {"count": len(report), "changes": report, "coverage": store.coverage()}


def main() -> int:
    parser = argparse.ArgumentParser(description="RuG 공식 근거 5개 검색 색인·동기화")
    parser.add_argument("--database", help="로컬 시험/운영 데이터베이스 위치. 생략 시 기존 Life Recover 저장 위치")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--sync-all", action="store_true", help="공식 출처별 기본 페이지 5개를 HTTPS로 각각 읽고 색인")
    choice.add_argument("--sync-source", choices=sorted(SOURCES))
    choice.add_argument("--manifest", type=Path, help="사람이 확인한 공식 원문/PDF 반입 manifest JSON")
    choice.add_argument("--seed-index", action="store_true", help="공식 근거 5곳의 출처 개요만 로컬에 등록; 원문 확보로 표기하지 않음")
    choice.add_argument("--status", action="store_true")
    choice.add_argument("--query", help="근거 검색 시험 문장")
    parser.add_argument("--delay", type=float, default=0.8)
    args = parser.parse_args()
    store = RuGFullRAG(args.database)
    if args.seed_index:
        seed = Path(__file__).resolve().parents[1] / 'data' / 'rug_공식근거_5개.json'
        outcome = {"added_index_only": store.load_preverified_summaries(str(seed)), "coverage": store.coverage()}
    elif args.status:
        outcome = store.coverage()
    elif args.query:
        outcome = store.grounded_answer(args.query)
    elif args.manifest:
        outcome = local_import(store, args.manifest)
    else:
        outcome = sync(store, list(SOURCES) if args.sync_all else [args.sync_source], delay=args.delay)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if not isinstance(outcome, dict) or outcome.get("all_succeeded", True) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print("RuG 공식자료 동기화 실패:", str(exc), file=sys.stderr)
        raise SystemExit(2)
