"""Owner-controlled, versioned teaching assets and bounded provider disclosure.

Raw source stays in SQLite on the classroom volume. No learner records, chat
archives or credentials are ingested. New edits require explicit section approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROVIDERS = {"openai", "gemini", "claude"}
PURPOSES = {"generate", "verify", "tutor"}
MAX_SECTION = 1800
MAX_PAYLOAD = 12000
MAX_PER_ASSET = 2


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def state_root() -> Path:
    return Path(os.getenv("EDURECOVER_CLASSROOM_STATE_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".edurecover_classroom_state")


class VaultError(ValueError):
    pass


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_SECTION)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    purposes: list[Literal["generate", "verify", "tutor"]] = Field(default_factory=list)
    providers: list[Literal["openai", "gemini", "claude"]] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=lambda: ["영어"], max_length=10)
    grades: list[str] = Field(default_factory=list, max_length=20)
    question_types: list[str] = Field(default_factory=list, max_length=30)
    approved: bool = False

    @model_validator(mode="after")
    def bounded_tags(self):
        for values in (self.keywords, self.subjects, self.grades, self.question_types):
            if any(not x.strip() or len(x) > 80 for x in values):
                raise ValueError("Tags must contain 1 to 80 characters.")
        if self.approved and (not self.purposes or not self.providers or not self.subjects):
            raise ValueError("Approved sections require a subject, purpose and provider.")
        if self.approved and not (self.keywords or self.question_types):
            raise ValueError("Approved sections require task matching criteria.")
        return self


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    title: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(default=0, ge=0)
    policy: Literal["excerpt_only", "never_send"] = "never_send"
    sections: list[Section] = Field(min_length=1, max_length=5000)


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS vault_revisions(
 asset_id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
 policy TEXT NOT NULL, sections_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, actor TEXT NOT NULL, PRIMARY KEY(asset_id,revision));
CREATE TABLE IF NOT EXISTS vault_imports(source_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vault_disclosures(
 request_id TEXT PRIMARY KEY, provider TEXT NOT NULL, purpose TEXT NOT NULL,
 task_hash TEXT NOT NULL, sections_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
 characters INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class SecretVault:
    def __init__(self, path: Path | None = None):
        self.path = path or state_root() / "secret_vault.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise VaultError("VAULT_SYMLINK_FORBIDDEN")
        with self.db() as db:
            db.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save(self, revision: Revision, *, actor: str = "owner", import_source: str | None = None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT MAX(revision) FROM vault_revisions WHERE asset_id=?",
                                 (revision.asset_id,)).fetchone()[0] or 0
            if current != revision.expected_revision:
                raise VaultError("REVISION_CONFLICT_RELOAD_REQUIRED")
            if import_source and db.execute("SELECT 1 FROM vault_imports WHERE source_id=?", (import_source,)).fetchone():
                return
            sections = [s.model_dump() for s in revision.sections]
            body = json.dumps(sections, ensure_ascii=False, sort_keys=True)
            db.execute("INSERT INTO vault_revisions VALUES(?,?,?,?,?,?,?,?)", (
                revision.asset_id, current + 1, revision.title, revision.policy, body,
                digest(body), datetime.now(timezone.utc).isoformat(), actor))
            if import_source:
                db.execute("INSERT INTO vault_imports VALUES(?,?)", (import_source, revision.asset_id))
        return {"asset_id": revision.asset_id, "revision": current + 1}

    def assets(self):
        with self.db() as db:
            rows = db.execute("""SELECT v.* FROM vault_revisions v JOIN
                (SELECT asset_id,MAX(revision) revision FROM vault_revisions GROUP BY asset_id) c
                USING(asset_id,revision) ORDER BY title""").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            sections = json.loads(item.pop("sections_json"))
            item["section_count"] = len(sections)
            item["approved_sections"] = sum(s["approved"] for s in sections) if item["policy"] == "excerpt_only" else 0
            result.append(item)
        return result

    def get(self, asset_id: str):
        with self.db() as db:
            row = db.execute("SELECT * FROM vault_revisions WHERE asset_id=? ORDER BY revision DESC LIMIT 1", (asset_id,)).fetchone()
        if not row:
            raise VaultError("ASSET_NOT_FOUND")
        item = dict(row)
        item["sections"] = json.loads(item.pop("sections_json"))
        return item

    def select(self, *, provider: str, purpose: str, subject: str, grade: str,
               question_types: list[str], source_text: str, record: bool = True):
        if provider not in PROVIDERS or purpose not in PURPOSES:
            raise VaultError("EXPLICIT_PROVIDER_AND_PURPOSE_REQUIRED")
        # Grade and generic numbers do not by themselves authorize source disclosure.
        terms = set(re.findall(r"[A-Za-z][A-Za-z'-]{3,}|[가-힣]{2,}", source_text.lower()))
        terms |= {x.lower() for x in question_types if x.strip()}
        qtypes = set(question_types)
        candidates = []
        with self.db() as db:
            rows = db.execute("""SELECT v.* FROM vault_revisions v JOIN
                (SELECT asset_id,MAX(revision) revision FROM vault_revisions GROUP BY asset_id) c
                USING(asset_id,revision) WHERE v.policy='excerpt_only' ORDER BY v.asset_id""").fetchall()
            for row in rows:
                for index, raw in enumerate(json.loads(row["sections_json"])):
                    section = Section.model_validate(raw)
                    if not section.approved or provider not in section.providers or purpose not in section.purposes:
                        continue
                    if subject not in section.subjects or (section.grades and grade not in section.grades):
                        continue
                    if section.question_types and not (qtypes & set(section.question_types)):
                        continue
                    score = len(terms & {x.lower() for x in section.keywords}) + 3 * len(qtypes & set(section.question_types))
                    if not score:
                        continue
                    candidates.append((score, row["asset_id"], row["revision"], index, section.text))
            candidates.sort(key=lambda x: (-x[0], x[1], x[3]))
            selected, refs, counts, total = [], [], {}, 0
            for _, asset_id, revision, index, text in candidates:
                if counts.get(asset_id, 0) >= MAX_PER_ASSET or total + len(text) > MAX_PAYLOAD:
                    continue
                refs.append({"asset_id": asset_id, "revision": revision, "section": index,
                             "sha256": digest(text), "characters": len(text)})
                selected.append(text)
                total += len(text)
                counts[asset_id] = counts.get(asset_id, 0) + 1
            request_id = str(uuid.uuid4())
            payload_hash = digest(json.dumps(selected, ensure_ascii=False))
            task_hash = digest(json.dumps([subject, grade, question_types, source_text], ensure_ascii=False))
            if record:
                db.execute("INSERT INTO vault_disclosures VALUES(?,?,?,?,?,?,?,?,?)", (
                    request_id, provider, purpose, task_hash, json.dumps(refs), payload_hash, total,
                    "PREPARED" if selected else "NO_MATCH", datetime.now(timezone.utc).isoformat()))
        return {"request_id": request_id if record else None, "provider": provider, "purpose": purpose,
                "excerpts": selected, "references": refs, "characters": total,
                "selection_state": "MATCHED" if selected else "NO_MATCH",
                "payload_sha256": payload_hash}

    def mark(self, request_id: str, state: str):
        if state not in {"COMPLETED", "FAILED_OR_UNCERTAIN"}:
            raise VaultError("INVALID_DISCLOSURE_STATE")
        with self.db() as db:
            db.execute("UPDATE vault_disclosures SET state=? WHERE request_id=? AND state='PREPARED'", (state, request_id))

    def receipts(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM vault_disclosures ORDER BY created_at DESC LIMIT 100")]


def split_source(text: str) -> list[str]:
    # Never concatenate an unrelated neighbouring paragraph to a matched paragraph.
    result, current = [], ""
    for line in text.splitlines(keepends=True):
        if not line.strip() and current.strip():
            result.append(current.strip()); current = ""
        else:
            while line:
                room = MAX_SECTION - len(current)
                current += line[:room]; line = line[room:]
                if len(current) == MAX_SECTION:
                    if current.strip(): result.append(current.strip())
                    current = ""
    if current.strip(): result.append(current.strip())
    return result


def import_legacy_assets(vault: SecretVault):
    """One-time migration; never overwrite owner's revisions or re-enable revoked material."""
    from app.services.authoring_assets import ASSET_DIR, ASSET_MANIFEST
    from app.services.private_asset_registry import PRIVATE_ASSETS, PRIVATE_DIR
    sources = [(ASSET_DIR / item["file"], "authoring/" + item["file"], "excerpt_only") for item in ASSET_MANIFEST]
    sources += [(PRIVATE_DIR / item["file"], "private/" + item["file"], item["provider_policy"])
                for item in PRIVATE_ASSETS if item.get("ingest_state") != "PENDING_SECRET_SCAN"]
    with vault.db() as db:
        imported = {r[0] for r in db.execute("SELECT source_id FROM vault_imports")}
    count = 0
    for path, source_id, policy in sources:
        if source_id in imported or not path.is_file() or path.is_symlink():
            continue
        # Bounded, explicitly enumerated source files only, never a recursive dump.
        if path.stat().st_size > 5 * 1024 * 1024:
            continue
        sections = []
        for text in split_source(path.read_text(encoding="utf-8")):
            keywords = [k for k in dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z'-]{3,}|[가-힣]{2,}", text.lower())) if len(k) <= 80][:40]
            sections.append(Section(text=text, keywords=keywords, purposes=["generate", "verify"],
                                    providers=sorted(PROVIDERS), approved=policy == "excerpt_only" and bool(keywords)))
        if not sections:
            continue
        vault.save(Revision(asset_id="legacy-" + digest(source_id)[:24], title=path.name,
                            policy=policy, sections=sections), actor="legacy_policy_migration", import_source=source_id)
        count += 1
    return count
