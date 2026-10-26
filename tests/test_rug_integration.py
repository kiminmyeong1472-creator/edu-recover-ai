"""RuG acceptance checks. Every document/product below is synthetic test data."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import subprocess
import sys
from datetime import date,datetime,timedelta,timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import rug_feature as ui
from app.platform_core import life_auth
from app.rug_engine import SOURCES, INCOMPLETE_NOTICE, norm
from app.rug_formula import compare_formula, parse_formula
from app.rug_full_rag import RuGFullRAG, NO_EVIDENCE, extract_html

ROOT = Path(__file__).resolve().parents[1]


def _utc_today():
    # The store validates against UTC. Local dates run ahead of it in Korea
    # before 09:00, which would make a fixture look like a future date.
    return datetime.now(timezone.utc).date()
HEADERS = {'Origin':'https://testserver','X-Rug-Consent':'1'}
KNOWN = [
    ('fragrance allergens','FDA'),('cosmetic labeling','FDA'),
    ('ingredient names','KCIA'),('standardized nomenclature','KCIA'),
    ('CosIng inventory','COSING'),('INCI glossary','COSING'),
    ('consumer safety','SCCS'),('scientific opinions','SCCS'),
    ('알레르기 표시','MFDS'),('성분 규정','MFDS')]
UNKNOWN = [
    'fragrance cures diabetes','cosmetic labeling predicts earthquakes',
    'ingredient names guarantee immortality','standardized nomenclature reverses cancer',
    'CosIng inventory teleportation','INCI glossary mindreading',
    'consumer safety astrology','scientific opinions perpetual motion',
    '알레르기 표시 완치보장','성분 규정 외계인']


@pytest.fixture
def store(tmp_path):
    return RuGFullRAG(tmp_path/'public.sqlite3')


def ingest(store,sid,text,url=None):
    return store.ingest_text(source_id=sid,url=url or SOURCES[sid]['url'],title='SYNTHETIC '+sid,
        text=text,checked_on=_utc_today().isoformat(),source_mode='official_page')


@pytest.fixture
def corpus(store):
    for sid in SOURCES:
        phrases = [q for q,source in KNOWN if source==sid]
        ingest(store,sid,'\n'.join(q+' is a synthetic fixture sentence for software validation.' for q in phrases))
    return store


def product(name='시험 제품',ingredients=None):
    return {'product_id':'SYNTHETIC_ONLY','name':name,'brand':'가상 시험 전용',
        'oliveyoung_url':'https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=SYNTHETIC_ONLY',
        'oliveyoung_verified':True,'ingredients':ingredients or ['정제수','리모넨'],
        'ingredients_complete':True,'ingredient_source_kind':'oliveyoung',
        'ingredient_source_url':'https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=SYNTHETIC_ONLY',
        'ingredient_checked_at':_utc_today().isoformat()}


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR',str(tmp_path))
    monkeypatch.delenv('RAILWAY_VOLUME_MOUNT_PATH',raising=False)
    monkeypatch.delenv('RUG_EMBEDDING_MODEL_PATH',raising=False)
    monkeypatch.setenv('LIFERECOVER_DATA_KEY',secrets.token_hex(32))
    monkeypatch.setenv('LIFERECOVER_SESSION_SECRET',secrets.token_hex(32))
    monkeypatch.setenv('LIFERECOVER_REGISTRATION_INVITE','synthetic-invite')
    app = FastAPI();app.include_router(life_auth.router);app.include_router(ui.router)
    with TestClient(app,base_url='https://testserver') as c:
        response=c.post('/life/auth/register',json={'username':'rugtest','password':'synthetic-test-password','invite_code':'synthetic-invite'})
        assert response.status_code==200,response.text
        yield c


@pytest.mark.parametrize('query,expected',KNOWN)
def test_expected_source_in_top_five(corpus,query,expected):
    assert expected in [r['source_id'] for r in corpus.retrieve(query,limit=5)]
    answer=corpus.grounded_answer(query)
    assert answer['state']=='EVIDENCE_FOUND'
    assert all(corpus.verify_sentence(s['text'],s['citation']) for s in answer['sentences'])


@pytest.mark.parametrize('query',UNKNOWN)
def test_ten_absent_facts_refuse_answer(corpus,query):
    answer=corpus.grounded_answer(query)
    assert answer['state']=='NO_EVIDENCE'
    assert answer['answer']==NO_EVIDENCE
    assert answer['sentences']==answer['citations']==[]


@pytest.mark.parametrize('raw,expected',[
    ('정제수, 리모넨\nLINALOOL',['정제수','리모넨','LINALOOL']),
    ('"1,2-Hexanediol",Water',['1,2-Hexanediol','Water']),
    ('1,2-헥산다이올, 정제수',['1,2-헥산다이올','정제수']),
    ('\ufeffingredients\nWater\nLimonene',['Water','Limonene']),
    ('',[]),('리모넨,리모넨',['리모넨','리모넨'])])
def test_text_and_single_column_csv_preserve_original(raw,expected):
    result=parse_formula(raw)
    assert result['raw_text']==raw
    assert result['ingredients']==expected


def test_corrected_list_keeps_unmodified_raw():
    parsed=parse_formula('잘못,나눔',corrected=['잘못,나눔'])
    assert parsed['raw_text']=='잘못,나눔'
    assert parsed['ingredients']==['잘못,나눔']


@pytest.mark.parametrize('raw,options,count',[
    ('리모넨',{},1),('',{},0),('정제수,향료',{'complete':True},2),
    ('정제수,리모넨',{'complete':True,'expected_count':5},2),
    ('정제수,미확인',{'complete':True},1)])
def test_partial_scope_notice_exact(store,raw,options,count):
    result=compare_formula(store,product_name='시험 제품',raw_text=raw,allergens=['리모넨'],**options)
    assert result['status']=='HOLD'
    assert result['confirmed_count']==count
    assert result['incomplete_notice']==INCOMPLETE_NOTICE.format(count=count)
    if '리모넨' in raw:
        assert result['matches']==['리모넨']


def test_full_list_positive_uses_rules_without_rag(store):
    store.import_product(product())
    result=compare_formula(store,product_name='시험 제품',raw_text='정제수, 리모넨',allergens=['리모넨'],complete=True)
    assert result['status']==result['presence']=='MATCH'
    assert result['matches']==['리모넨']


def test_no_match_has_exact_limited_wording(store):
    store.import_product(product())
    result=compare_formula(store,product_name='시험 제품',raw_text='정제수, 리모넨',allergens=['니켈'],complete=True)
    assert result['status']=='NO_MATCH'
    assert result['message']=='확인된 범위에서는 등록하신 성분이 발견되지 않았습니다'


def test_alias_conflict_holds_and_retains_exact_match(store):
    ingest(store,'KCIA','Synthetic alias fixture: 리모넨 corresponds to one. This is software test content only.')
    ingest(store,'COSING','Synthetic alias fixture: 리모넨 corresponds to two. This is software test content only.')
    store.add_alias('리모넨','one','KCIA',SOURCES['KCIA']['url'])
    store.add_alias('리모넨','two','COSING',SOURCES['COSING']['url'])
    result=compare_formula(store,product_name='시험 제품',raw_text='리모넨',allergens=['리모넨'],complete=True)
    assert result['status']=='HOLD'
    assert result['matches']==['리모넨']
    assert '성분 동의어 충돌' in result['issues']


def test_verified_alias_and_original_registered_name(store):
    store.import_product(product())
    ingest(store,'KCIA','Synthetic alias fixture: Limonene corresponds to 리모넨. This is software test content only.')
    store.add_alias('Limonene','리모넨','KCIA',SOURCES['KCIA']['url'])
    result=compare_formula(store,product_name='시험 제품',raw_text='정제수,리모넨',allergens=[' Limonene '],complete=True)
    assert result['matches']==[' Limonene ']


def test_no_unverified_catalog_claim(store):
    result=compare_formula(store,product_name='시험 제품',raw_text='리모넨',allergens=['리모넨'],complete=True)
    assert result['presence']=='MATCH' and result['status']=='HOLD'
    assert result['sale_verification']=='unverified'


def test_sources_are_shown_together_without_final_choice(store):
    for sid,claim in [('FDA','yes'),('MFDS','no')]:
        ingest(store,sid,'Synthetic claim applies: '+claim+'. This fixture represents opposing source statements for display testing.')
    answer=store.grounded_answer('Synthetic claim')
    assert answer['assessment']=='HOLD'
    assert len(answer['citations'])==2
    assert answer['multiple_sources']


def test_summary_is_not_fulltext_or_answer(store,tmp_path):
    seed=tmp_path/'summary.json';seed.write_text(json.dumps([{'source_id':'FDA','url':SOURCES['FDA']['url'],
        'title':'SYNTHETIC overview','excerpt':'Fragrance allergens synthetic source overview. '*4,'verified_on':_utc_today().isoformat()}]))
    store.load_preverified_summaries(seed)
    assert store.coverage()['sources']['FDA']['verified_fulltext_docs']==0
    assert store.grounded_answer('fragrance allergens')['state']=='NO_EVIDENCE'


def test_corrupted_chunk_is_not_cited(corpus):
    with corpus.conn() as db:
        db.execute("UPDATE rag_chunk SET passage='Tampered fragrance allergens synthetic claim.'")
    assert corpus.grounded_answer('fragrance allergens')['state']=='NO_EVIDENCE'


def test_forged_citation_rejected(corpus):
    sentence=corpus.grounded_answer('fragrance allergens')['sentences'][0]
    sentence['citation']['url']='https://www.fda.gov/other'
    assert not corpus.verify_sentence(sentence['text'],sentence['citation'])


def test_bm25_fallback_without_fts(corpus):
    corpus.has_rag_fts=False
    assert corpus.grounded_answer('fragrance allergens')['state']=='EVIDENCE_FOUND'
    assert corpus.last_search_mode=='BM25'


class SyntheticEncoder:
    name='synthetic-test-vector-only'
    key='synthetic-vector-model'
    def encode(self,texts):
        return [[1.0,0.1] if 'fragrance' in t.casefold() else [0.1,1.0] for t in texts]


def test_embedding_vectors_persist_and_rrf_executes(tmp_path):
    store=RuGFullRAG(tmp_path/'embedding.sqlite3',encoder=SyntheticEncoder())
    ingest(store,'FDA','Fragrance allergens appear in this synthetic fixture sentence for retrieval validation.')
    assert store.coverage()['embedding_chunks']>0
    assert store.grounded_answer('fragrance allergens')['search_mode']=='BM25+EMBEDDING_RRF'
    reopened=RuGFullRAG(store.path,encoder=SyntheticEncoder())
    assert reopened.coverage()['embedding_chunks']==store.coverage()['embedding_chunks']


def test_embedding_failure_falls_back(corpus):
    class Broken(SyntheticEncoder):
        def encode(self,texts):raise RuntimeError('synthetic failure')
    corpus.encoder=Broken()
    assert corpus.grounded_answer('fragrance allergens')['search_mode']=='BM25'


def test_unavailable_model_falls_back(monkeypatch,tmp_path):
    monkeypatch.setenv('RUG_EMBEDDING_MODEL_PATH',str(tmp_path/'missing'))
    store=RuGFullRAG(tmp_path/'no-model.sqlite3')
    assert store.coverage()['retrieval']=='BM25'
    assert store.coverage()['embedding_state']=='local_model_directory_missing'


@pytest.mark.parametrize('url',[
    'https://fda.gov.attacker.test/x','http://www.fda.gov/x',
    'https://user:pass@www.fda.gov/x','https://www.fda.gov:444/x'])
def test_official_origin_allowlist(store,url):
    with pytest.raises(ValueError):
        ingest(store,'FDA','Synthetic sufficiently long text for official origin validation. '*2,url)


def test_future_date_rejected(store):
    with pytest.raises(ValueError):
        store.ingest_text(source_id='FDA',url=SOURCES['FDA']['url'],title='Synthetic',text='Test '*30,
            checked_on=(_utc_today()+timedelta(days=1)).isoformat(),source_mode='official_page')


def test_no_network_runs_in_search(client,monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**k:pytest.fail('unexpected network'))
    assert client.post('/life/api/rug/rag/search',json={'q':'unknown allergens'},headers=HEADERS).json()['state']=='NO_EVIDENCE'


def test_authenticated_page_and_post_only_search(client):
    assert client.get('/life/rug').status_code==200
    assert client.get('/life/api/rug/rag/search?q=test').status_code==405
    client.cookies.clear()
    assert client.get('/life/rug',follow_redirects=False).status_code==303
    assert client.post('/life/api/rug/rag/search',json={'q':'test'},headers=HEADERS).status_code==401


def test_same_origin_and_explicit_consent(client):
    assert client.post('/life/api/rug/allergens',json={'allergens':['리모넨']}).status_code==403
    assert client.post('/life/api/rug/allergens',json={'allergens':['리모넨']},headers={**HEADERS,'Origin':'https://elsewhere.test'}).status_code==403


def test_profile_history_encrypted_and_reviewed(client,tmp_path):
    assert client.post('/life/api/rug/allergens',json={'allergens':['리모넨']},headers=HEADERS).status_code==200
    payload={'product_name':'PRIVATE_SYNTHETIC_PRODUCT','raw_text':'정제수','explicit_consent':True}
    first=client.post('/life/api/rug/analyze',json=payload,headers=HEADERS).json()
    second=client.post('/life/api/rug/analyze',json={**payload,'raw_text':'정제수,리모넨'},headers=HEADERS).json()
    assert second['previous_review_id']==first['review_id']
    assert second['changes']['matches_added']==['리모넨']
    assert second['matches']==['리모넨']
    assert len(client.get('/life/api/rug/history').json()['history'])==2
    for db in (tmp_path/'liferecover').glob('*.sqlite3'):
        assert b'PRIVATE_SYNTHETIC_PRODUCT' not in db.read_bytes()
    assert len(ui._store().products())==0


def test_customer_accounts_isolated(client):
    first=client.post('/life/api/rug/analyze',json={'product_name':'private','raw_text':'정제수','explicit_consent':True},headers=HEADERS).json()
    client.cookies.clear()
    assert client.post('/life/auth/register',json={'username':'seconduser','password':'synthetic-test-password','invite_code':'synthetic-invite'}).status_code==200
    assert client.get('/life/api/rug/history').json()['history']==[]
    response=client.post('/life/api/rug/analyze',json={'product_name':'private','raw_text':'정제수','explicit_consent':True,'previous_review_id':first['review_id']},headers=HEADERS)
    assert response.status_code==404


def test_persistent_volume_default_and_misconfiguration(monkeypatch,tmp_path):
    from app.services.secret_vault import state_root
    monkeypatch.delenv('EDURECOVER_CLASSROOM_STATE_DIR',raising=False)
    monkeypatch.setenv('RAILWAY_VOLUME_MOUNT_PATH',str(tmp_path/'volume'))
    assert state_root()==tmp_path/'volume'
    store=ui._store();ingest(store,'FDA','Fragrance allergens synthetic persistence fixture text. '*3)
    assert ui._store().coverage()['sources']['FDA']['verified_fulltext_docs']==1
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR',str(tmp_path/'ephemeral'))
    with pytest.raises(Exception) as err:ui._store()
    assert err.value.status_code==503


def test_sync_and_import_scripts_execute_from_root(tmp_path):
    data=tmp_path/'empty.json';data.write_text('[]')
    for args in [ ['scripts/rug_import.py','--database',str(tmp_path/'p.sqlite3'),'--products',str(data)],
                  ['scripts/rug_rag_sync.py','--database',str(tmp_path/'d.sqlite3'),'--status'] ]:
        result=subprocess.run([sys.executable,*args],cwd=ROOT,capture_output=True,text=True)
        assert result.returncode==0,result.stderr


def test_sync_failure_recorded_without_fake_documents(store,monkeypatch):
    from scripts import rug_rag_sync
    def failed(*a,**k):raise OSError('synthetic offline failure')
    monkeypatch.setattr(rug_rag_sync,'fetch_one',failed)
    result=rug_rag_sync.sync(store,['FDA'],delay=0)
    assert not result['all_succeeded']
    assert result['coverage']['sources']['FDA']['documents']==0
    assert result['coverage']['sources']['FDA']['last_sync']['state']=='failed'


def test_ui_takes_label_photo_and_text_but_never_a_receipt():
    html=(ROOT/'app/static/rug.html').read_text()
    assert html.index('id="result"')<html.index('id="scopeNotice"')
    # The ingredient label may be photographed; a purchase receipt may not.
    for forbidden in ('pickReceipt','영수증','receipt'):
        assert forbidden not in html
    assert 'id="formulaFile"' in html and 'id="normalization"' in html
    assert 'id="photoShot"' in html and 'id="photoPick"' in html
    assert 'id="unresolvedList"' in html and 'id="keepAsIs"' in html


def test_main_includes_rug_once():
    text=(ROOT/'app/main.py').read_text()
    assert text.count('app.include_router(rug_router)')==1
    assert (ROOT/'app/liferecover_runtime.py').read_text().count("href='/life/rug'")==1


def test_index_tables_have_no_graph_entities(store):
    with store.conn() as db:
        names=[r[0].lower() for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert not any('graph' in n or 'entity_relation' in n or 'neo4j' in n for n in names)


def test_scripts_and_navigation_have_no_capture_payload():
    assert not (ROOT/'app/life_media_capture.py').exists()
    assert not (ROOT/'app/static/life_media_capture.js').exists()
    source=(ROOT/'app/rug_feature.py').read_text()
    assert '/receipt' not in source and 'receipt' not in source


def test_html_scripts_removed():
    text=extract_html(b'<html><script>invented_secret_claim()</script><article><p>Official synthetic fragrance allergens reference text for parser validation.</p></article></html>')
    assert 'invented_secret_claim' not in text


def test_actual_ui_controls_and_untrusted_text():
    result=subprocess.run(['node',str(ROOT/'tests/test_rug_ui.js')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_alias_without_indexed_source_is_rejected(store):
    with pytest.raises(ValueError):
        store.add_alias('guess','invented','KCIA',SOURCES['KCIA']['url'])


def test_partial_group_variant_cannot_claim_complete(store):
    result=compare_formula(store,product_name='시험 제품',raw_text='정제수,Fragrance (Parfum)',allergens=['리모넨'],complete=True)
    assert result['incomplete_ingredients'] and result['status']=='HOLD'


def test_customer_json_has_no_store_headers(client):
    assert client.get('/life/api/rug/history').headers['cache-control']=='no-store'


def test_unreadable_name_is_kept_and_blocks_a_verdict(store):
    """Recognition can mangle an allergen; a mangled name must not read as absent."""
    ingest(store,'KCIA','표준 성분명 목록입니다. 정제수 글리세린 리모넨. '*12,url=SOURCES['KCIA']['url'])
    for name in ('정제수','글리세린','리모넨'):
        store.add_alias(name,name,'KCIA',SOURCES['KCIA']['url'])
    store.import_product(product(ingredients=['정제수','글리세린','리모넨']))
    link='https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=SYNTHETIC_ONLY'
    mangled=compare_formula(store,product_name='시험 제품',raw_text='정제수, 글리세린, F|GUl',
        allergens=['리모넨'],complete=False,oliveyoung_link=link,reading_source='photo_ocr')
    assert mangled['unresolved_ingredients']==['F|GUl']
    assert mangled['requires_user_review'] is True
    assert mangled['status']=='NEEDS_USER_REVIEW' and mangled['presence']=='HOLD'
    assert '발견되지 않' not in mangled['message']

    corrected=compare_formula(store,product_name='시험 제품',raw_text='정제수, 글리세린, F|GUl',
        allergens=['리모넨'],complete=False,oliveyoung_link=link,reading_source='photo_ocr',
        corrected=['정제수','글리세린','리모넨'])
    assert corrected['matches']==['리모넨'] and corrected['requires_user_review'] is False

    kept=compare_formula(store,product_name='시험 제품',raw_text='정제수, 글리세린, F|GUl',
        allergens=['리모넨'],complete=False,oliveyoung_link=link,reading_source='photo_ocr',
        user_review={'confirmed_at':'2026-10-26T12:00:00Z','reviewed_names':['F|GUl']})
    assert kept['status']!='NEEDS_USER_REVIEW' and kept['user_review']['reviewed_names']==['F|GUl']

    for sham in ({'confirmed_at':'','reviewed_names':[]},
                 {'confirmed_at':'2026-10-26T12:00:00Z','reviewed_names':['다른 이름']}):
        assert compare_formula(store,product_name='시험 제품',raw_text='정제수, 글리세린, F|GUl',
            allergens=['리모넨'],complete=False,oliveyoung_link=link,
            user_review=sham)['status']=='NEEDS_USER_REVIEW'


def test_missing_dictionary_is_named_not_treated_as_unreadable(store):
    store.import_product(product(ingredients=['정제수']))
    link='https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=SYNTHETIC_ONLY'
    result=compare_formula(store,product_name='시험 제품',raw_text='정제수, 글리세린',
        allergens=['리모넨'],complete=False,oliveyoung_link=link)
    assert result['requires_user_review'] is False
    assert result['dictionary_loaded'] is False
    # The missing dictionary must not become a reason to withhold on its own.
    assert '표준 성분명 사전 미반입' not in result['issues']


def test_known_rejects_a_name_the_dictionary_never_carried(store):
    ingest(store,'KCIA','표준 성분명 목록입니다. 정제수 리모넨. '*12,url=SOURCES['KCIA']['url'])
    store.add_alias('리모넨','리모넨','KCIA',SOURCES['KCIA']['url'])
    assert store.known('리모넨') is True
    assert store.known('F|GUl') is False
    # canonical() falls back to the input, so it cannot make this distinction.
    assert store.canonical('F|GUl')[0] is not None


def test_ocr_module_keeps_numeric_locants_and_drops_label_prefix():
    from app.rug_ocr import _split
    assert _split('전성분: 정제수, 글리세린, 1,2-헥산다이올') == ['정제수','글리세린','1,2-헥산다이올']
    assert _split('Ingredients: Water, Limonene') == ['Water','Limonene']


OCR_SAMPLE = '전성분: 정제수, 글리세린, 1,2-헥산\n다이올, 리모넨, 향료'


def test_tidy_guard_keeps_joined_names_and_drops_invented_ones():
    from app.rug_tidy import verify_against_text
    kept, dropped = verify_against_text(
        ['정제수', '글리세린', '1,2-헥산다이올', '리모넨', '향료'], OCR_SAMPLE)
    assert '1,2-헥산다이올' in kept and dropped == []
    kept, dropped = verify_against_text(['정제수', '벤조페논-3', '토코페롤'], OCR_SAMPLE)
    assert kept == ['정제수'] and dropped == ['벤조페논-3', '토코페롤']
    assert verify_against_text('not a list', OCR_SAMPLE) == ([], [])


def test_tidy_falls_back_when_no_model_is_configured():
    from app.rug_tidy import tidy
    result = tidy(OCR_SAMPLE, ['정제수', '글리세린'])
    assert result['ingredients'] == ['정제수', '글리세린'] and result['applied'] is False


def test_stored_photo_is_encrypted_and_bound_to_one_account(tmp_path, monkeypatch):
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR', str(tmp_path))
    monkeypatch.delenv('RAILWAY_VOLUME_MOUNT_PATH', raising=False)
    monkeypatch.setenv('LIFERECOVER_DATA_KEY', secrets.token_hex(32))
    from fastapi import HTTPException
    from app.rug_photo import read_photo, store_photo
    picture = b'\x89PNG\r\n\x1a\n' + secrets.token_bytes(2048)

    record = store_photo('account-A', picture, 'image/png')
    assert record['sha256'] == hashlib.sha256(picture).hexdigest()
    assert read_photo('account-A', record['photo_id']) == (picture, 'image/png')

    blobs = list(tmp_path.rglob('*.enc'))
    assert blobs and blobs[0].read_bytes()[:8] != picture[:8]

    for subject, photo_id in [('account-B', record['photo_id']),
                              ('account-A', '../../etc/passwd'),
                              ('account-A', 'rug_photo_' + 'f' * 32)]:
        with pytest.raises(HTTPException):
            read_photo(subject, photo_id)

    blobs[0].write_bytes(blobs[0].read_bytes()[:-1] + b'\x00')
    with pytest.raises(HTTPException):
        read_photo('account-A', record['photo_id'])
