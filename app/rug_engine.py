"""RuG: official-evidence retrieval + exact allergen rule engine.

No model requests, autonomous diagnosis, or assumed ingredient lists. Public sources
are stored separately from per-account encrypted personal allergies and histories.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SOURCES = {
    "MFDS": {
        "name": "식품의약품안전처: 화장품 알레르기 유발성분 표시 규정",
        "url": "https://www.mfds.go.kr/brd/m_211/view.do?seq=14985",
        "hosts": ("mfds.go.kr", "www.mfds.go.kr"),
        "role": "국내 표시규정·안전기준",
    },
    "KCIA": {
        "name": "대한화장품협회 화장품 성분사전",
        "url": "https://kcia.or.kr/cid/search/ingd_list.php",
        "hosts": ("koreacosmetics.org", "www.koreacosmetics.org", "kcia.or.kr", "www.kcia.or.kr"),
        "role": "국문 표준 성분명·이명 확인",
    },
    "COSING": {
        "name": "유럽연합 집행위원회 CosIng 성분 데이터베이스",
        "url": "https://single-market-economy.ec.europa.eu/sectors/cosmetics/cosmetic-ingredient-database_en",
        "hosts": ("single-market-economy.ec.europa.eu", "ec.europa.eu"),
        "role": "성분 공통명·관련 규정",
    },
    "SCCS": {
        "name": "EU 과학위원회 SCCS 공식 의견",
        "url": "https://health.ec.europa.eu/scientific-committees/scientific-committee-consumer-safety-sccs/sccs-opinions_en",
        "hosts": ("health.ec.europa.eu",),
        "role": "개별 성분에 대한 과학적 의견",
    },
    "FDA": {
        "name": "미국 FDA 화장품 알레르겐 자료",
        "url": "https://www.fda.gov/cosmetics/cosmetic-ingredients/allergens-cosmetics",
        "hosts": ("fda.gov", "www.fda.gov"),
        "role": "일반적인 화장품 알레르겐 정보",
    },
}

NOTICE = "확인된 자료와 고객이 직접 등록한 알레르겐 성분명만 대조합니다. 제품의 안전성을 보장하지 않습니다."
INCOMPLETE_NOTICE = "이 결과는 확인된 성분 {count}개 범위 안에서만 분석한 것입니다. 일부 성분이 확인되지 않아 모든 성분을 검토하지는 못했습니다. 제조사에 전성분을 문의해 받아 오시면 다시 검토해 드리겠습니다."
MANUFACTURER_NOTICE = ""
MAX_INGREDIENT_AGE_DAYS = 365


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(value: str) -> str:
    """Compare normalized full names only; never fuzzy-match risky ingredients."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans({'‐':'-','‑':'-','–':'-'}))
    return re.sub(r"\s+", "", value).strip()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def official_url(url: str, source_id: str) -> bool:
    if source_id not in SOURCES:
        return False
    p = urlsplit(url)
    return (p.scheme == "https" and p.hostname in SOURCES[source_id]["hosts"]
            and not p.username and not p.password and p.port in (None, 443)
            and len(url) <= 2048)


def oliveyoung_url(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == "https" and p.hostname in {"www.oliveyoung.co.kr", "m.oliveyoung.co.kr", "oliveyoung.co.kr"} and not p.username and not p.password


def verify_product(record: dict) -> dict:
    """Import validation: no made-up products or ingredient lists without source."""
    product = dict(record)
    for key in ("product_id", "name", "brand", "oliveyoung_url"):
        if not isinstance(product.get(key), str) or not product[key].strip() or len(product[key]) > 1024:
            raise ValueError("제품 필수정보 누락: " + key)
    if not re.fullmatch(r"[\w\-.]{1,80}", product["product_id"]):
        raise ValueError("제품 식별자 오류")
    if not oliveyoung_url(product["oliveyoung_url"]):
        raise ValueError("올리브영 공식 상품페이지 URL만 허용합니다.")
    if product.get("oliveyoung_verified") is not True:
        raise ValueError("올리브영 판매 확인 여부가 누락됐습니다.")
    ingredients = product.get("ingredients", [])
    if not isinstance(ingredients, list) or len(ingredients) > 400 or any(not isinstance(x, str) or not x.strip() or len(x) > 180 for x in ingredients):
        raise ValueError("제품 전성분 형식 오류")
    product["ingredients"] = [v.strip() for v in ingredients]
    if product.get("ingredients_complete") is True:
        if not ingredients or product.get("ingredient_source_kind") not in ("manufacturer", "oliveyoung"):
            raise ValueError("전성분 완전성·제조사 또는 올리브영 출처가 필요합니다.")
        u = str(product.get("ingredient_source_url", ""))
        p = urlsplit(u)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            raise ValueError("전성분 원출처 HTTPS 주소가 필요합니다.")
        if product["ingredient_source_kind"] == "oliveyoung" and not oliveyoung_url(u):
            raise ValueError("올리브영 출처 주소가 일치하지 않습니다.")
        if product["ingredient_source_kind"] == "manufacturer":
            owner = str(product.get("manufacturer_host", "")).lower().strip()
            if not owner or p.hostname != owner:
                raise ValueError("제조사 출처 호스트 일치 여부를 확인하십시오.")
        date.fromisoformat(str(product.get("ingredient_checked_at", "")))
    return product


class EvidenceStore:
    """Local provenance store. No live scraping or third-party API required."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            # A --database CLI option can operate on its own without requiring
            # the entire authenticated Life Recover web app to be installed.
            from app.services.secret_vault import state_root
            path = state_root() / "liferecover" / "rug_evidence.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema()

    @contextmanager
    def conn(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _schema(self):
        with self.conn() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, brand TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, version TEXT NOT NULL, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, excerpt TEXT NOT NULL, verified_on TEXT NOT NULL, kind TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases (alias TEXT NOT NULL, canonical TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT NOT NULL, PRIMARY KEY(alias,canonical,source_id));
            """)
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(id UNINDEXED, title, excerpt, tokenize='unicode61')")
                self.has_fts = True
            except sqlite3.OperationalError:
                self.has_fts = False

    def add_evidence(self, row: dict) -> None:
        src = row.get("source_id", "")
        if not official_url(str(row.get("url", "")), src):
            raise ValueError("허가된 공식 근거 사이트 주소만 사용합니다.")
        eid = str(row.get("id", ""))
        title = str(row.get("title", ""))
        excerpt = str(row.get("excerpt", ""))
        checked = str(row.get("verified_on", ""))
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{3,100}", eid) or len(title) not in range(1, 250) or not excerpt.strip() or len(excerpt) > 18000:
            raise ValueError("근거자료 식별자·제목·본문 오류")
        date.fromisoformat(checked)
        kind = str(row.get("kind", "reference"))
        if kind not in {"reference", "ingredient", "legal", "scientific"}:
            raise ValueError("근거자료 종류 오류")
        with self.conn() as db:
            db.execute("INSERT OR REPLACE INTO evidence VALUES(?,?,?,?,?,?,?)",
                       (eid, src, row["url"], title, excerpt, checked, kind))
            if self.has_fts:
                db.execute("DELETE FROM evidence_fts WHERE id=?", (eid,))
                db.execute("INSERT INTO evidence_fts(id,title,excerpt) VALUES(?,?,?)", (eid, title, excerpt))

    def add_alias(self, alias: str, canonical: str, source_id: str, source_url: str) -> None:
        if source_id not in {"KCIA", "COSING"} or not official_url(source_url, source_id):
            raise ValueError("표준 성분명 연결은 성분사전/CosIng 출처만 허용합니다.")
        if not (alias.strip() and canonical.strip() and len(alias) <= 160 and len(canonical) <= 160):
            raise ValueError("성분명 오류")
        with self.conn() as db:
            db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?,?,?)", (norm(alias), norm(canonical), source_id, source_url))

    def canonical(self, value: str) -> tuple[str | None, bool]:
        key = norm(value)
        with self.conn() as db:
            rows = db.execute("SELECT DISTINCT canonical FROM aliases WHERE alias=?", (key,)).fetchall()
        options = {r["canonical"] for r in rows}
        if len(options) > 1:
            return None, True  # conflicting aliases: never guess
        return (next(iter(options)) if options else key), False

    def import_product(self, record: dict) -> dict:
        product = verify_product(record)
        checksum = digest(product.get("ingredients", []))
        with self.conn() as db:
            prev = db.execute("SELECT version FROM products WHERE id=?", (product["product_id"],)).fetchone()
            changed = bool(prev and prev["version"] != checksum)
            db.execute("INSERT OR REPLACE INTO products VALUES(?,?,?,?,?,?)",
                       (product["product_id"], product["brand"], product["name"],
                        json.dumps(product, ensure_ascii=False), checksum, now_iso()))
        return {"product_id": product["product_id"], "formula_changed": changed, "formula_digest": checksum}

    def get_product(self, product_id: str) -> dict | None:
        with self.conn() as db:
            row = db.execute("SELECT payload FROM products WHERE id=?", (product_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def products(self) -> list[dict]:
        with self.conn() as db:
            rows = db.execute("SELECT payload FROM products ORDER BY brand,name LIMIT 1500").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def source_counts(self) -> dict:
        with self.conn() as db:
            rows = db.execute("SELECT source_id,COUNT(*) AS n FROM evidence GROUP BY source_id").fetchall()
        counts = {key: 0 for key in SOURCES}
        counts.update({r["source_id"]: r["n"] for r in rows})
        return counts

    def search(self, query: str, limit: int = 8) -> list[dict]:
        tokens = re.findall(r"[\w가-힣]{2,}", query.lower())[:12]
        if not tokens:
            return []
        with self.conn() as db:
            rows = []
            if self.has_fts:
                # Parameterized FTS terms, not raw user search syntax.
                expression = " OR ".join('"' + t.replace('"', '') + '"' for t in tokens)
                try:
                    rows = db.execute("""SELECT e.* FROM evidence_fts f JOIN evidence e ON e.id=f.id
                       WHERE evidence_fts MATCH ? ORDER BY bm25(evidence_fts) LIMIT ?""", (expression, limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                cond = " OR ".join(["(lower(excerpt) LIKE ? OR lower(title) LIKE ?)" for _ in tokens])
                values = [x for t in tokens for x in (f"%{t}%", f"%{t}%")]
                rows = db.execute(f"SELECT * FROM evidence WHERE {cond} ORDER BY verified_on DESC LIMIT ?", (*values, limit)).fetchall()
        # A citation is evidence of the excerpt only; not proof that a specific product contains it.
        return [dict(r) for r in rows]


def _fresh_date(value: str, max_age: int = MAX_INGREDIENT_AGE_DAYS) -> bool:
    try:
        age = (date.today() - date.fromisoformat(value)).days
        return 0 <= age <= max_age
    except (ValueError, TypeError):
        return False


def _presence(store: EvidenceStore, product: dict, allergens: list[str]) -> dict:
    issues: list[str] = []
    if not product.get("oliveyoung_verified") or not oliveyoung_url(str(product.get("oliveyoung_url", ""))):
        issues.append("올리브영 판매 여부 확인 불가")
    if not product.get("ingredients_complete") or not product.get("ingredients"):
        issues.append("제품별 공식 전성분 미확인")
    other_lists = product.get("alternate_official_ingredient_lists", [])
    if other_lists:
        if not isinstance(other_lists, list) or any(not isinstance(x, list) for x in other_lists):
            issues.append("서로 다른 출처의 성분정보 형식 불일치")
        elif any(set(map(norm, x)) != set(map(norm, product.get("ingredients", []))) for x in other_lists):
            issues.append("공식 출처 간 전성분 불일치")
    if not product.get("ingredient_source_url") or not product.get("ingredient_checked_at"):
        issues.append("전성분 출처 또는 확인일 누락")
    elif not _fresh_date(product["ingredient_checked_at"]):
        issues.append("전성분 자료 확인일 경과·변경 가능성")
    if not allergens:
        issues.append("등록된 개인 알레르겐 없음")
    canonical_ingredients = set()
    aliases_ambiguous = False
    for ingredient in product.get("ingredients", []):
        canonical, ambiguous = store.canonical(ingredient)
        aliases_ambiguous |= ambiguous
        if canonical:
            canonical_ingredients.add(canonical)
    matches = []
    for allergen in allergens:
        canonical, ambiguous = store.canonical(allergen)
        aliases_ambiguous |= ambiguous
        if canonical and canonical in canonical_ingredients:
            matches.append(allergen)
    if aliases_ambiguous:
        issues.append("성분명 또는 동의어의 표준명 매칭 충돌")
    # A partial formula may contain a confirmed positive hit (shown in
    # `matches`) but cannot support a final whole-formula assessment.
    if issues:
        state = "HOLD"
        message = INCOMPLETE_NOTICE if "제품별 공식 전성분 미확인" in issues else "근거 부족 또는 조건 미충족·판정 보류"
    elif matches:
        state, message = "MATCH", "등록 알레르겐과 일치하는 성분 확인"
    else:
        state, message = "NO_MATCH", "확인된 자료에서 등록 알레르겐이 발견되지 않음"
    return {"status": state, "message": message,
            "matches": list(dict.fromkeys(matches)), "issues": issues}


def analyze(store: EvidenceStore, product_id: str, allergens: list[str], *, include_alternatives: bool = True) -> dict:
    aliases = list(dict.fromkeys(v.strip() for v in allergens if v.strip()))[:50]
    product = store.get_product(product_id)
    if product is None:
        return {"status": "HOLD", "message": "제품 식별 실패·판정 보류", "issues": ["올리브영 등록 제품을 찾지 못함"],
                "matches": [], "sources": [], "alternatives": [], "notice": NOTICE,
                "registered_allergens": aliases, "incomplete_ingredients": True,
                "incomplete_notice": INCOMPLETE_NOTICE, "manufacturer_notice": MANUFACTURER_NOTICE}
    judgment = _presence(store, product, aliases)
    query = "화장품 성분 알레르기 표준 성분명 " + " ".join(aliases + product.get("ingredients", [])[:6])
    refs = store.search(query, 8)
    if not refs:
        judgment["issues"].append("공식 기준자료 RAG 근거 미확보")
        judgment["status"] = "HOLD"
        judgment["message"] = "공식 근거 부족·판정 보류"
    alternatives = []
    if include_alternatives and aliases and refs and len(store.products()) > 1:
        for other in store.products():
            if other["product_id"] == product_id:
                continue
            if _presence(store, other, aliases)["status"] != "NO_MATCH":
                continue
            alternatives.append({"product_id": other["product_id"], "brand": other["brand"], "name": other["name"],
                                 "source": other["ingredient_source_url"], "checked_at": other["ingredient_checked_at"], "oliveyoung_url": other["oliveyoung_url"]})
            if len(alternatives) >= 5:
                break
    return {"product": {"product_id": product["product_id"], "name": product["name"], "brand": product["brand"],
                         "oliveyoung_url": product["oliveyoung_url"], "ingredients": product["ingredients"],
                         "ingredient_source_url": product.get("ingredient_source_url"), "ingredient_checked_at": product.get("ingredient_checked_at"),
                         "ingredients_complete": product.get("ingredients_complete", False), "formula_digest": digest(product["ingredients"])},
            **judgment, "sources": [{"id": r["id"], "source_id": r["source_id"], "url": r["url"], "title": r["title"],
                                    "excerpt": r["excerpt"][:240], "verified_on": r["verified_on"]} for r in refs],
            "source_counts": store.source_counts(), "alternatives": alternatives, "notice": NOTICE, "checked_at": now_iso(),
            "registered_allergens": aliases,
            "incomplete_ingredients": not (product.get("ingredients_complete") is True and product.get("ingredients")),
            "incomplete_notice": INCOMPLETE_NOTICE if not (product.get("ingredients_complete") is True and product.get("ingredients")) else "",
            "manufacturer_notice": MANUFACTURER_NOTICE if not (product.get("ingredients_complete") is True and product.get("ingredients")) else ""}
