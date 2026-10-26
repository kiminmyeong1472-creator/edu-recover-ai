"""RuG official text retrieval using BM25 and optional offline embeddings.

No remote language-model calls. Only verified verbatim spans become answers.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit
from typing import Any

from app.rug_engine import EvidenceStore, SOURCES, official_url, norm, now_iso

RAG_VERSION = "2026-10-26.rug-only"
NO_EVIDENCE = "확인 가능한 공식 근거를 찾지 못해 답변하지 않았습니다."
AUTHORITY_NOTICE = (
    "이 자료는 성분·기준에 대한 참고 근거이며, 제품별 전성분을 입증하지 않습니다. "
    "개인별 안전성 또는 알레르기 무발생을 보장하지 않습니다."
)
MAX_DOC_CHARS = 200_000
MAX_DOCS_PER_SOURCE = 300
RAG_SOURCE_LABELS = {k: {"name": v["name"], "url": v["url"], "role": v["role"]} for k, v in SOURCES.items()}


def utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def clean_text(value: str) -> str:
    # Preserve word spacing, Korean, Latin and CAS IDs. No interpolated SQL.
    value = html.unescape(unicodedata.normalize("NFKC", value))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\f\v]+", " ", value.replace("\r", "\n"))).strip()


def terms(value: str) -> list[str]:
    return re.findall(r"[\w가-힣]{2,}", unicodedata.normalize("NFKC", value).casefold())[:40]


def split_passages(text: str, max_chars: int = 780, overlap: int = 115) -> list[str]:
    """Chunk by lines/paragraphs and overlap long paragraphs for traceability."""
    raw = clean_text(text)
    if not raw:
        return []
    chunks, buffer = [], ""
    for para in re.split(r"\n\s*\n|(?<=[.!?。])\s+(?=[A-Z가-힣])", raw):
        para = para.strip()
        if not para:
            continue
        parts = [para[i:i + max_chars] for i in range(0, len(para), max_chars - overlap)] if len(para) > max_chars else [para]
        for part in parts:
            if len(buffer) + len(part) + 1 > max_chars and buffer:
                chunks.append(buffer)
                buffer = buffer[-overlap:] + " " + part if overlap and len(buffer) > overlap else part
            else:
                buffer = (buffer + "\n" + part).strip()
    if buffer:
        chunks.append(buffer)
    # Deduplicate repeated site navigation and almost-empty fragments.
    seen = set()
    answer = []
    for row in chunks:
        canon = clean_text(row)
        if len(canon) < 25 or canon in seen:
            continue
        seen.add(canon)
        answer.append(canon)
    return answer[:400]


class _OfficialHTML(HTMLParser):
    """Lossy readable text extractor. Script/menu/footer text is ignored."""

    SKIP = {"script", "style", "svg", "nav", "footer", "form", "noscript", "header", "aside"}
    BREAK = {"h1", "h2", "h3", "h4", "p", "li", "tr", "div", "br", "article", "section"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.pieces = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.stack.append(tag)
        if tag in self.SKIP:
            self.skipping += 1
        if self.skipping == 0 and tag in self.BREAK:
            self.pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.BREAK and not self.skipping:
            self.pieces.append("\n")
        if tag in self.stack:
            # Tolerate malformed HTML while tracking skipped containers.
            while self.stack:
                item = self.stack.pop()
                if item in self.SKIP:
                    self.skipping = max(0, self.skipping - 1)
                if item == tag:
                    break

    def handle_data(self, data: str) -> None:
        if not self.skipping and data.strip():
            self.pieces.append(data.strip() + " ")

    def result(self) -> str:
        return clean_text("".join(self.pieces))


def extract_html(payload: bytes) -> str:
    if len(payload) > 3_000_000:
        raise ValueError("official_html_too_large")
    decoder = payload.decode("utf-8", errors="replace")
    if decoder.count("\ufffd") > max(30, len(decoder) // 50):
        try:
            decoder = payload.decode("euc-kr", errors="strict")
        except UnicodeError:
            pass
    parser = _OfficialHTML()
    parser.feed(decoder)
    output = parser.result()
    if len(output) < 50:
        raise ValueError("official_text_unreadable")
    if len(output)>MAX_DOC_CHARS:
        raise ValueError("official_text_too_large")
    return output


def extract_pdf(payload: bytes) -> str:
    from pypdf import PdfReader
    if len(payload) > 12_000_000 or not payload.startswith(b"%PDF-"):
        raise ValueError("official_pdf_invalid_or_too_large")
    pdf = PdfReader(io.BytesIO(payload), strict=False)
    if len(pdf.pages) > 100:
        raise ValueError("official_pdf_page_limit")
    result = clean_text("\n\n".join(page.extract_text() or "" for page in pdf.pages))
    if len(result) < 50:
        raise ValueError("official_pdf_text_unavailable_use_reviewed_text")
    if len(result)>MAX_DOC_CHARS:
        raise ValueError("official_text_too_large")
    return result


def query_terms(query: str) -> list[str]:
    stop = {'what','is','are','the','a','an','of','in','do','does','how','can','tell','me','about',
            '무엇','무엇인가요','알려주세요','알려주십시오','설명','해주세요','합니까','인가요','있나요'}
    result = []
    for term in terms(query):
        if term in stop:
            continue
        if re.fullmatch(r'[가-힣]+',term):
            term = re.sub(r'(에서는|으로는|에서|에게|에는|이란|은|는|을|를|의|이|가)$','',term)
        if len(term) >= 2:
            result.append(term)
    return result


class RuGFullRAG(EvidenceStore):
    """Public documents only. Personal formula inputs never enter this database."""

    def __init__(self, path=None, *, encoder=None):
        from app.rug_embeddings import configured_encoder
        self.encoder, self.embedding_reason = (encoder, '') if encoder is not None else configured_encoder()
        self.last_search_mode = 'BM25'
        self.last_embedding_error = ''
        super().__init__(path)

    def _schema(self):
        super()._schema()
        with self.conn() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS rag_document(
              doc_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL,
              title TEXT NOT NULL, text_sha256 TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
              retrieved_at TEXT NOT NULL, checked_on TEXT NOT NULL, source_mode TEXT NOT NULL,
              text_length INTEGER NOT NULL, chunk_count INTEGER NOT NULL, reviewed INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rag_body(doc_id TEXT PRIMARY KEY, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rag_chunk(
              chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
              passage TEXT NOT NULL, passage_sha256 TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_rug_chunk_doc ON rag_chunk(doc_id);
            CREATE TABLE IF NOT EXISTS rag_embedding(
              chunk_id TEXT NOT NULL, model_key TEXT NOT NULL, passage_sha256 TEXT NOT NULL,
              vector TEXT NOT NULL, PRIMARY KEY(chunk_id,model_key));
            CREATE TABLE IF NOT EXISTS rag_job(
              job_id TEXT PRIMARY KEY, run_at TEXT NOT NULL, source_id TEXT NOT NULL,
              state TEXT NOT NULL, reason TEXT NOT NULL);
            ''')
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(chunk_id UNINDEXED,passage,tokenize='unicode61')")
                self.has_rag_fts = True
            except sqlite3.OperationalError:
                self.has_rag_fts = False

    def ingest_text(self, *, source_id, url, title, text, checked_on, source_mode, reviewed=False, raw_sha256=''):
        if not official_url(url,source_id):
            raise ValueError('official_source_url_invalid')
        if source_mode not in {'official_page','official_pdf','human_verified_transcript','official_site_summary'}:
            raise ValueError('source_mode_invalid')
        if not 3 <= len(title.strip()) <= 300:
            raise ValueError('title_invalid')
        if date.fromisoformat(checked_on) > datetime.now(timezone.utc).date():
            raise ValueError('future_verification_date_invalid')
        text = clean_text(text)
        if not 50 <= len(text) <= MAX_DOC_CHARS:
            raise ValueError('official_text_size_invalid')
        if raw_sha256 and not re.fullmatch('[a-f0-9]{64}',raw_sha256):
            raise ValueError('raw_sha256_invalid')
        passages = split_passages(text)
        if not passages:
            raise ValueError('no_indexable_passages')
        doc_id = 'doc_'+hashlib.sha256((source_id+'|'+url).encode()).hexdigest()[:32]
        checksum = hashlib.sha256(text.encode()).hexdigest()
        with self.conn() as db:
            existing = db.execute('SELECT text_sha256 FROM rag_document WHERE doc_id=?',(doc_id,)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM rag_document WHERE source_id=?',(source_id,)).fetchone()[0]
            if existing is None and count >= MAX_DOCS_PER_SOURCE:
                raise ValueError('official_source_document_quota_exceeded')
            old = db.execute('SELECT chunk_id FROM rag_chunk WHERE doc_id=?',(doc_id,)).fetchall()
            for row in old:
                db.execute('DELETE FROM rag_embedding WHERE chunk_id=?',(row[0],))
                if self.has_rag_fts:
                    db.execute('DELETE FROM rag_fts WHERE chunk_id=?',(row[0],))
            db.execute('DELETE FROM rag_chunk WHERE doc_id=?',(doc_id,))
            db.execute('INSERT OR REPLACE INTO rag_document VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (doc_id,source_id,url,title,checksum,raw_sha256,now_iso(),checked_on,source_mode,len(text),len(passages),int(reviewed)))
            db.execute('INSERT OR REPLACE INTO rag_body VALUES(?,?)',(doc_id,text))
            for index,passage in enumerate(passages):
                cid = f'{doc_id}_{index:04d}'
                digest = hashlib.sha256(passage.encode()).hexdigest()
                db.execute('INSERT INTO rag_chunk VALUES(?,?,?,?,?)',(cid,doc_id,index,passage,digest))
                if self.has_rag_fts:
                    db.execute('INSERT INTO rag_fts VALUES(?,?)',(cid,passage))
        self.index_embeddings()
        return {'doc_id':doc_id,'state':'unchanged' if existing and existing[0]==checksum else 'updated',
                'chunk_count':len(passages),'sha256':checksum,'raw_sha256':raw_sha256}

    def index_embeddings(self):
        if self.encoder is None:
            return 0
        from app.rug_embeddings import vector
        try:
            with self.conn() as db:
                rows = db.execute('''SELECT c.* FROM rag_chunk c JOIN rag_document d ON d.doc_id=c.doc_id
                    LEFT JOIN rag_embedding e ON e.chunk_id=c.chunk_id AND e.model_key=?
                    WHERE d.source_mode!='official_site_summary' AND
                    (e.chunk_id IS NULL OR e.passage_sha256!=c.passage_sha256)''',(self.encoder.key,)).fetchall()
                for start in range(0,len(rows),32):
                    batch = rows[start:start+32]
                    vectors = self.encoder.encode([r['passage'] for r in batch])
                    if len(vectors) != len(batch):
                        raise ValueError('embedding_batch_size_mismatch')
                    for row,encoded in zip(batch,vectors):
                        db.execute('INSERT OR REPLACE INTO rag_embedding VALUES(?,?,?,?)',
                            (row['chunk_id'],self.encoder.key,row['passage_sha256'],json.dumps(vector(encoded))))
            self.last_embedding_error = ''
            return len(rows)
        except (OSError,ValueError,RuntimeError,TypeError):
            self.last_embedding_error = 'embedding_index_failed'
            return 0

    def coverage(self):
        detail = {sid:{'name':entry['name'],'url':entry['url'],'documents':0,'chunks':0,
            'verified_fulltext_docs':0,'index_only_docs':0,'latest_checked_on':None,'documents_detail':[]}
            for sid,entry in SOURCES.items()}
        with self.conn() as db:
            for row in db.execute('SELECT * FROM rag_document ORDER BY source_id,url'):
                item = detail[row['source_id']]
                item['documents'] += 1
                item['chunks'] += row['chunk_count']
                item['index_only_docs' if row['source_mode']=='official_site_summary' else 'verified_fulltext_docs'] += 1
                item['latest_checked_on'] = max(item['latest_checked_on'] or '',row['checked_on'])
                item['documents_detail'].append({key:row[key] for key in ('doc_id','title','url','checked_on','text_sha256','raw_sha256','source_mode','chunk_count')})
            jobs = db.execute('SELECT * FROM rag_job ORDER BY run_at,job_id').fetchall()
            for row in jobs:
                detail[row['source_id']]['last_sync'] = {'at':row['run_at'],'state':row['state'],'reason':row['reason']}
            embeddings = db.execute('SELECT COUNT(*) FROM rag_embedding WHERE model_key=?',
                (self.encoder.key if self.encoder else '',)).fetchone()[0]
            eligible = db.execute("SELECT COUNT(*) FROM rag_chunk c JOIN rag_document d ON c.doc_id=d.doc_id WHERE d.source_mode!='official_site_summary'").fetchone()[0]
        ready = bool(self.encoder and embeddings and embeddings==eligible and not self.last_embedding_error)
        return {'sources':detail,'official_fulltext_five_source_coverage':all(x['verified_fulltext_docs']>0 for x in detail.values()),
            'product_count':len(self.products()),'retrieval':'BM25+EMBEDDING_RRF' if ready else 'BM25',
            'last_search_mode':self.last_search_mode,'embedding_model':self.encoder.name if self.encoder else None,
            'embedding_state':self.last_embedding_error or self.embedding_reason or ('ready' if ready else 'not_indexed'),
            'embedding_chunks':embeddings,'last_sync_at':max((x['run_at'] for x in jobs),default=None),
            'llm_connected':False,'timestamp':now_iso(),'version':RAG_VERSION}

    @staticmethod
    def _bm25(rows, tokens):
        docs = [Counter(terms(r['passage'])) for r in rows]
        average = sum(sum(d.values()) for d in docs)/max(1,len(docs))
        scores = []
        for row,doc in zip(rows,docs):
            score = 0.0
            for term in tokens:
                frequency = doc[term]
                df = sum(term in d for d in docs)
                idf = math.log(1+(len(docs)-df+0.5)/(df+0.5))
                score += idf*frequency*2.2/(frequency+1.2*(0.25+0.75*sum(doc.values())/max(1,average)))
            if score:
                scores.append((row['chunk_id'],score))
        return [cid for cid,_ in sorted(scores,key=lambda x:(-x[1],x[0]))]

    def retrieve(self,query,*,source_ids=None,limit=8,include_index_only=False):
        if not 2 <= len(query.strip()) <= 240:
            return []
        if source_ids is not None and any(x not in SOURCES for x in source_ids):
            raise ValueError('unknown_official_source')
        tokens = query_terms(query)
        if not tokens:
            return []
        allowed = set(SOURCES if source_ids is None else source_ids)
        self.last_search_mode = 'BM25'
        with self.conn() as db:
            rows = [dict(r) for r in db.execute('''SELECT c.*,d.source_id,d.url,d.title,d.checked_on,d.source_mode,d.text_sha256,
                b.text AS document_text FROM rag_chunk c JOIN rag_document d ON c.doc_id=d.doc_id
                JOIN rag_body b ON b.doc_id=d.doc_id''')
                if r['source_id'] in allowed and (include_index_only or r['source_mode']!='official_site_summary')]
            if self.has_rag_fts:
                expression = ' OR '.join('"'+t.replace('"','')+'"' for t in tokens)
                lexical = [r[0] for r in db.execute('SELECT chunk_id FROM rag_fts WHERE rag_fts MATCH ? ORDER BY bm25(rag_fts),chunk_id LIMIT 200',(expression,))]
            else:
                lexical = self._bm25(rows,tokens)
            eligible_ids = {r['chunk_id'] for r in rows}
            lexical = [cid for cid in lexical if cid in eligible_ids]
            semantic = []
            if self.encoder and rows:
                try:
                    from app.rug_embeddings import vector,cosine
                    query_vector = vector(self.encoder.encode([query])[0])
                    stored = {r['chunk_id']:r for r in db.execute('SELECT * FROM rag_embedding WHERE model_key=?',(self.encoder.key,))}
                    if all(r['chunk_id'] in stored and stored[r['chunk_id']]['passage_sha256']==r['passage_sha256'] for r in rows):
                        pairs = [(r['chunk_id'],cosine(query_vector,json.loads(stored[r['chunk_id']]['vector']))) for r in rows]
                        semantic = [cid for cid,score in sorted(pairs,key=lambda p:(-p[1],p[0]))[:200] if score>0]
                        self.last_search_mode = 'BM25+EMBEDDING_RRF'
                except (OSError,ValueError,RuntimeError,TypeError,IndexError):
                    self.last_embedding_error = 'embedding_query_failed'
            ranks = defaultdict(float)
            for ranking in (lexical,semantic):
                for rank,cid in enumerate(ranking,1):
                    ranks[cid] += 1/(60+rank)
            mapping = {r['chunk_id']:r for r in rows}
            results = []
            for cid in sorted(ranks,key=lambda c:(-ranks[c],c)):
                row = mapping[cid]
                # Corrupt rows never become displayed evidence.
                if hashlib.sha256(row['passage'].encode()).hexdigest()!=row['passage_sha256']:
                    continue
                if hashlib.sha256(row['document_text'].encode()).hexdigest()!=row['text_sha256']:
                    continue
                row.update(id=cid,excerpt=row['passage'],verified_on=row['checked_on'],retrieval_score=ranks[cid])
                results.append(row)
                if len(results)>=max(1,min(limit,20)):
                    break
        return results

    def verify_sentence(self,text,citation):
        with self.conn() as db:
            row = db.execute('''SELECT c.passage,c.passage_sha256,d.text_sha256,d.source_mode,d.url,d.source_id,
                d.title,d.checked_on,b.text FROM rag_chunk c JOIN rag_document d ON c.doc_id=d.doc_id
                JOIN rag_body b ON b.doc_id=d.doc_id WHERE chunk_id=?''',(citation.get('chunk_id',''),)).fetchone()
        return bool(row and row['source_mode']!='official_site_summary' and text in row['passage']
            and text in row['text'] and official_url(row['url'],row['source_id'])
            and hashlib.sha256(row['passage'].encode()).hexdigest()==row['passage_sha256']==citation.get('chunk_sha256')
            and hashlib.sha256(row['text'].encode()).hexdigest()==row['text_sha256']==citation.get('content_sha256')
            and all(citation.get(key)==row[key] for key in ('url','source_id','title','checked_on')))

    def grounded_answer(self,query,*,limit=6,sources=None):
        found = self.retrieve(query,source_ids=sources,limit=limit,include_index_only=False)
        required = query_terms(query)
        sentences = []
        seen = set()
        for row in found:
            citation = {k:row[k] for k in ('chunk_id','source_id','url','title','checked_on','source_mode')}
            citation.update(source_name=SOURCES[row['source_id']]['name'],retrieved_on=row['checked_on'],
                content_sha256=row['text_sha256'],chunk_sha256=row['passage_sha256'],fulltext=True)
            for sentence in re.split(r'(?<=[.!?。])\s+|\n+',row['passage']):
                sentence = sentence.strip()
                # Conservative extractive gate. No entailment or truth guarantee is claimed.
                if not sentence or not required or not all(norm(t) in norm(sentence) for t in required):
                    continue
                marker = (sentence,row['source_id'],row['url'])
                if marker in seen or not self.verify_sentence(sentence,citation):
                    continue
                seen.add(marker)
                sentences.append({'text':sentence,'citation':{**citation,'excerpt':sentence}})
                break
        if not sentences:
            return {'state':'NO_EVIDENCE','answer':NO_EVIDENCE,'sentences':[],'citations':[],
                    'search_mode':self.last_search_mode,'assessment':'HOLD'}
        citations = [s['citation'] for s in sentences]
        answer = '\n'.join(s['text']+' ['+s['citation']['source_name']+'·'+s['citation']['title']+'·'+
            s['citation']['checked_on']+'·'+s['citation']['url']+']' for s in sentences)
        # Multiple sources are shown together; no source is chosen as a final judgment.
        return {'state':'EVIDENCE_FOUND','answer':answer,'sentences':sentences,'citations':citations,
                'search_mode':self.last_search_mode,'assessment':'HOLD',
                'multiple_sources':len({c['source_id'] for c in citations})>1}

    def search(self,query,limit=8):
        return self.retrieve(query,limit=limit)

    def record_sync_result(self,source_id,success,reason=''):
        if source_id not in SOURCES:
            raise ValueError('unknown_official_source')
        with self.conn() as db:
            stamp = now_iso()
            job = hashlib.sha256((source_id+stamp+reason).encode()).hexdigest()
            db.execute('INSERT INTO rag_job VALUES(?,?,?,?,?)',(job,stamp,source_id,'ok' if success else 'failed',reason[:500]))

    def load_preverified_summaries(self,path):
        rows = json.loads(open(path,encoding='utf8').read())
        for row in rows:
            self.ingest_text(source_id=row['source_id'],url=row['url'],title=row['title'],text=row['excerpt'],
                checked_on=row['verified_on'],source_mode='official_site_summary')
        return len(rows)

    def import_product(self,record):
        data = dict(record)
        snapshot = data.pop('reviewed_ingredient_snapshot',None)
        reviewed = data.pop('official_panel_reviewed',False) is True
        listed = Counter(norm(x) for x in data.get('ingredients',[]))
        snapshot_list = Counter(norm(x) for x in re.split(r'[,\n;]+',snapshot or '') if x.strip())
        data['rag_formula_reviewed'] = bool(reviewed and listed and listed==snapshot_list and data.get('ingredients_complete'))
        return super().import_product(data)

    def add_alias(self,alias,canonical,source_id,source_url):
        # A source URL alone does not prove that the two ingredient names are synonyms.
        with self.conn() as db:
            rows = db.execute('''SELECT b.text FROM rag_document d JOIN rag_body b ON d.doc_id=b.doc_id
                WHERE d.source_id=? AND d.url=? AND d.source_mode!='official_site_summary' ''',
                (source_id,source_url)).fetchall()
        if not any(norm(alias) in norm(r['text']) and norm(canonical) in norm(r['text']) for r in rows):
            raise ValueError('alias_names_missing_from_indexed_official_document')
        return super().add_alias(alias,canonical,source_id,source_url)
