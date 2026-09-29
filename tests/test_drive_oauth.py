import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.training import drive_oauth as oauth
from app.training.backup_adapters import DriveBackupAdapter, BackupStorageError
from app.training.trade_secret_engine import BackupTarget
from app.training.backup_service import _retain_verified_local_copies


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR',str(tmp_path))
    monkeypatch.setenv('EDURECOVER_PUBLIC_ORIGIN','https://example.test')
    monkeypatch.setenv('EDURECOVER_DRIVE_CLIENT_ID','test-client')
    monkeypatch.setenv('EDURECOVER_DRIVE_CLIENT_SECRET','test-only-client-secret')
    monkeypatch.setenv('EDURECOVER_BACKUP_MASTER_KEY','hex:'+'13'*32)
    monkeypatch.delenv('EDURECOVER_BACKUP_OWNER_PASSPHRASE',raising=False)
    monkeypatch.setenv('EDURECOVER_OWNER_USER','owner')
    monkeypatch.setenv('EDURECOVER_OWNER_PASSWORD','test-password')
    (tmp_path/'oauth_private').mkdir(mode=0o700)
    oauth._pending.clear()
    return tmp_path


def test_oauth_credential_encrypted_roundtrip_and_not_plaintext(config):
    data={'type':'authorized_user','refresh_token':'private-refresh-token','client_id':'test-client',
          'scopes':oauth.SCOPES,'folders':{'DRIVE_TRADE_SECRET':'folder'}}
    oauth._write_credentials(data)
    assert b'private-refresh-token' not in oauth.credential_path().read_bytes()
    assert oauth.read_credentials()==data
    assert list((config/'oauth_private').iterdir())==[oauth.credential_path()]


def test_owner_only_connect_csrf_and_expiry(config, monkeypatch):
    app=FastAPI();app.include_router(oauth.drive_router)
    flow=Mock(code_verifier='test-pkce')
    flow.authorization_url.return_value=('https://accounts.google.com/test','state')
    monkeypatch.setattr(oauth,'_flow',lambda **kwargs:flow)
    monkeypatch.setattr(oauth,'_complete',Mock())
    with TestClient(app,base_url='https://example.test') as client:
        assert client.post('/owner/drive/connect',json={}).status_code==401
        r=client.post('/owner/drive/connect',json={},auth=('owner','test-password'))
        assert r.status_code==200
        state=list(oauth._pending)[0]
        assert flow.authorization_url.call_args.kwargs['access_type']=='offline'
        assert flow.authorization_url.call_args.kwargs['state']==state
        client.cookies.clear()
        r=client.get('/owner/drive/callback',params={'state':state,'code':'test-code'},auth=('owner','test-password'))
        assert r.status_code==403
        oauth._complete.assert_not_called()
        assert state not in oauth._pending


def test_missing_client_does_not_create_oauth_request(config,monkeypatch):
    monkeypatch.delenv('EDURECOVER_DRIVE_CLIENT_ID')
    app=FastAPI();app.include_router(oauth.drive_router)
    with TestClient(app,base_url='https://example.test') as client:
        assert client.post('/owner/drive/connect',json={},auth=('owner','test-password')).status_code==503
        assert not oauth._pending


def test_my_drive_requires_owner_only_permissions():
    class Resp:
        status_code=200
        def __init__(self,data):self.data=data
        def json(self):return self.data
    class Session:
        shared=False
        def request(self,method,url,**kwargs):
            if url.endswith('permissions'):
                return Resp({'permissions':[{'type':'user','role':'writer' if self.shared else 'owner'}]})
            return Resp({'id':'folder','mimeType':'application/vnd.google-apps.folder','trashed':False,
                         'ownedByMe':True,'capabilities':{'canAddChildren':True}})
    session=Session()
    adapter=DriveBackupAdapter(session=session,folders={BackupTarget.DRIVE_TRADE_SECRET:'folder'},payloads=None,user_oauth=True)
    adapter.preflight(target=BackupTarget.DRIVE_TRADE_SECRET)
    session.shared=True
    with pytest.raises(BackupStorageError,match='OWNER_ONLY'):
        adapter.preflight(target=BackupTarget.DRIVE_TRADE_SECRET)


def test_verified_rotation_does_not_delete_outside_registered_root(tmp_path):
    root=tmp_path/'backups';root.mkdir()
    outside=tmp_path/'keep.edurbk';outside.write_bytes(b'keep')
    receipt=Mock(writes=[])
    prev={'retained_local_copies':[{'path':str(outside),'sha256':'bad'}]*4}
    assert len(_retain_verified_local_copies(prev,receipt,root))==3
    assert outside.read_bytes()==b'keep'
