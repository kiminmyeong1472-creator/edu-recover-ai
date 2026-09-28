import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.secret_vault import SecretVault, Section, Revision, VaultError, split_source, MAX_SECTION
from app.services.secret_vault_api import vault_router


def section(**kwargs):
    return Section(**({'text': 'approved private passage rule', 'keywords': ['concept'],
                       'purposes': ['generate'], 'providers': ['openai'], 'approved': True} | kwargs))


def revision(**kwargs):
    return Revision(**({'asset_id': 'a', 'title': 'rules', 'policy': 'excerpt_only', 'sections': [section()]} | kwargs))


def select(vault, **kwargs):
    return vault.select(**({'provider': 'openai', 'purpose': 'generate', 'subject': '영어',
                            'grade': '고1', 'question_types': [], 'source_text': 'concept'} | kwargs))


@pytest.fixture
def vault(tmp_path):
    return SecretVault(tmp_path / 'vault.sqlite3')


def test_persistent_revision_and_no_stale_approval(vault):
    vault.save(revision())
    assert select(vault)['excerpts']
    vault.save(revision(expected_revision=1, sections=[section(text='changed rule', approved=False)]))
    reopened = SecretVault(vault.path)
    assert reopened.get('a')['revision'] == 2
    assert select(reopened)['excerpts'] == []
    with pytest.raises(VaultError, match='REVISION_CONFLICT'):
        vault.save(revision(expected_revision=1))
    with vault.db() as db:
        assert db.execute('select count(*) from vault_revisions').fetchone()[0] == 2


@pytest.mark.parametrize('changes', [
    {'provider': 'gemini'}, {'purpose': 'verify'}, {'source_text': 'unrelated'},
    {'subject': '수학'}, {'source_text': ''}, {'source_text': '고1 12345'},
])
def test_no_fallback_or_cross_purpose_disclosure(vault, changes):
    vault.save(revision())
    assert select(vault, **changes)['excerpts'] == []


def test_never_send_asset_and_grade_gate(vault):
    vault.save(revision(policy='never_send'))
    assert select(vault)['excerpts'] == []
    vault.save(revision(expected_revision=1, sections=[section(grades=['고2'])]))
    assert select(vault)['excerpts'] == []
    assert select(vault, grade='고2')['excerpts']


def test_provider_and_purpose_must_be_explicit(vault):
    with pytest.raises(VaultError): select(vault, provider='')
    with pytest.raises(VaultError): select(vault, purpose='train')


def test_global_budget_per_asset_and_audit_does_not_store_task(vault):
    for i in range(10):
        vault.save(revision(asset_id=f'a{i}', sections=[section(text='concept ' + 'x'*1700) for _ in range(5)]))
    result=select(vault, source_text='concept STUDENT_PRIVATE_RESPONSE')
    assert result['characters'] <= 12000
    for asset in {r['asset_id'] for r in result['references']}:
        assert sum(r['asset_id']==asset for r in result['references']) <= 2
    assert 'STUDENT_PRIVATE_RESPONSE' not in json.dumps(vault.receipts())
    vault.mark(result['request_id'], 'COMPLETED')
    assert vault.receipts()[0]['state']=='COMPLETED'


def test_preview_does_not_claim_provider_call(vault):
    vault.save(revision())
    assert select(vault, record=False)['request_id'] is None
    assert not vault.receipts()


def test_split_preserves_text_without_adjacent_paragraph_leak():
    text='concept approved\n\nunrelated confidential\n\n'+'x'*4000
    sections=split_source(text)
    assert sections[0]=='concept approved'
    assert all(0<len(x)<=MAX_SECTION for x in sections)
    assert ''.join(sections).endswith('x'*4000)


def test_owner_api_roundtrip_and_auth(tmp_path, monkeypatch):
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR',str(tmp_path))
    monkeypatch.setenv('EDURECOVER_OWNER_USER','owner')
    monkeypatch.setenv('EDURECOVER_OWNER_PASSWORD','test-password')
    app=FastAPI();app.include_router(vault_router)
    with TestClient(app) as client:
        assert client.get('/owner/secrets/assets').status_code==401
        assert client.post('/owner/secrets/assets',json=revision().model_dump()).status_code==401
        r=client.post('/owner/secrets/assets',json=revision().model_dump(),auth=('owner','test-password'))
        assert r.status_code==200
        r=client.get('/owner/secrets/assets/a',auth=('owner','test-password'))
        assert r.json()['sections'][0]['text']=='approved private passage rule'
        assert r.headers['cache-control']=='no-store'
        assert client.post('/owner/secrets/assets',json=revision().model_dump(),auth=('owner','test-password')).status_code==409


def test_legacy_migration_is_one_time_and_preserves_owner_edits(tmp_path,monkeypatch):
    from app.services import authoring_assets, private_asset_registry
    from app.services.secret_vault import import_legacy_assets
    monkeypatch.setattr(authoring_assets,'ASSET_DIR',tmp_path)
    monkeypatch.setattr(authoring_assets,'ASSET_MANIFEST',[{'file':'rules.txt'}])
    monkeypatch.setattr(private_asset_registry,'PRIVATE_ASSETS',())
    (tmp_path/'rules.txt').write_text('concept approved\n\nprivate unrelated')
    vault=SecretVault(tmp_path/'v.sqlite3')
    assert import_legacy_assets(vault)==1
    a=vault.assets()[0]
    vault.save(revision(asset_id=a['asset_id'],expected_revision=1,policy='never_send'))
    assert import_legacy_assets(vault)==0
    assert select(vault)['excerpts']==[]


def test_real_request_builder_uses_saved_revision(tmp_path,monkeypatch):
    from app.services.auto_generator import _build_request_text
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR',str(tmp_path))
    vault=SecretVault();vault.save(revision())
    args=dict(provider='openai',source_text='concept',count=1,subject='영어',school_level='고등',grade='고1',
              exam_type='내신',question_types=[],difficulty='medium',scope_memo='',extra_instructions='')
    first=json.loads(_build_request_text(**args))['edurecover_authoring_context']['selection']
    assert first['excerpts']==['approved private passage rule']
    vault.save(revision(expected_revision=1,sections=[section(text='updated approved rule')]))
    second=json.loads(_build_request_text(**args))['edurecover_authoring_context']['selection']
    assert second['excerpts']==['updated approved rule']
    assert second['references'][0]['revision']==2
    assert len(vault.receipts())==2


def test_verification_preserves_subject_and_grade_scope(tmp_path,monkeypatch):
    from app.services.context_overlay import build_verification_context
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR',str(tmp_path))
    vault=SecretVault();vault.save(revision(sections=[section(subjects=['수학'],grades=['고2'],purposes=['verify'])]))
    p={'subject':'수학','grade':'고2','passage':'concept','question_type':'proof'}
    assert build_verification_context([p],provider='openai')['selection']['excerpts']
    assert not build_verification_context([dict(p,subject='영어')],provider='openai')['selection']['excerpts']
