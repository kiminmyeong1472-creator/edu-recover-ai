from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.domains.medical import hospital_os_api as hospital
from app.domains.medical.hospital_access import make_role_grant, make_tenant_membership, to_payload
from app.domains.medical.hospital_access_api import router as access_router
from app.liferecover_runtime import router as runtime_router
from app.platform_core.life_auth import router as auth_router
from app.platform_core.persistent_domain_store import get_resource, list_resources, put_resource, resource_transaction

BASE = '/life/api/medical/hospital-os'


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv('EDURECOVER_CLASSROOM_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('LIFERECOVER_SESSION_SECRET', 's' * 40)
    monkeypatch.setenv('LIFERECOVER_REGISTRATION_INVITE', 'invite-test')
    monkeypatch.setenv('LIFERECOVER_DATA_KEY', '22' * 32)
    monkeypatch.setenv('EDURECOVER_HOSPITAL_RBAC_ENFORCED', 'true')
    monkeypatch.setenv('EDURECOVER_HOSPITAL_TENANT_ENFORCED', 'false')
    app = FastAPI()
    for router in (auth_router, access_router, runtime_router, hospital.router):
        app.include_router(router)
    return app


def user(app, name, patients=None, tenant=None, roles=None, provision=True):
    client = TestClient(app, base_url='https://testserver')
    r = client.post('/life/auth/register', json={'username': name, 'password': 'very-secure-password', 'invite_code': 'invite-test'})
    assert r.status_code == 200, r.text
    account = r.json()['account_id']
    if provision:
        grant = make_role_grant(account_id=account, tenant_id=tenant,
            roles=roles or ['DOCTOR', 'HOSPITAL_ADMIN', 'ER_STAFF'], patient_scope=patients or [])
        put_resource(domain='medical', subject_id=account, kind='hospital_os_role_grant', payload=to_payload(grant), resource_id=grant.grant_id)
    if tenant:
        membership = make_tenant_membership(account_id=account, tenant_id=tenant)
        put_resource(domain='medical', subject_id=account, kind='hospital_os_tenant_membership', payload=to_payload(membership), resource_id=membership.membership_id)
    return client, account


def post(client, route, data):
    r = client.post(BASE + route, json=data)
    assert r.status_code == 200, r.text
    return r.json()


def source(client, name='source-1', patient='patient-1'):
    return post(client, '/source-revisions', {'source_id': name, 'patient_id': patient, 'payload': {'dose': 'old'}, 'verification_state': 'VERIFIED'})['source_revision_id']


def action(client, patient='patient-1'):
    return post(client, '/clinical-actions', {'patient_id': patient, 'action_type': 'review', 'responsible_role': 'DOCTOR', 'risk_tier': 'HIGH'})['clinical_action_id']


def linked(client):
    src = source(client)
    act = action(client)
    post(client, '/actual-use-evidence', {'clinical_action_id': act, 'source_revision_id': src, 'manifest_type': 'review', 'actor_or_system': 'doctor', 'verification_state': 'VERIFIED'})
    post(client, '/event-subscriptions', {'consumer_id': 'consumer', 'event_types': ['source.revision.changed']})
    return src, act


@pytest.mark.parametrize('kind', ['hospital_os_role_grant', 'hospital_os_tenant_membership', 'hospital_os_clinical_action', 'hospital_os_actual_use', 'hospital_os_source_revision'])
def test_generic_resource_api_cannot_forge_internal_records(env, kind):
    client, account = user(env, 'unprivileged', provision=False)
    r = client.post('/life/api/resources', json={'domain': 'medical', 'kind': kind, 'payload': {'account_id': account, 'roles': ['DOCTOR']}})
    assert r.status_code == 403
    assert list_resources(domain='medical', subject_id=account, kind=kind) == []
    assert client.get(BASE + '/clinical-actions').status_code == 403
    ordinary = client.post('/life/api/resources', json={'domain': 'medical', 'kind': 'personal_note', 'payload': {'text': 'mine'}})
    assert ordinary.status_code == 200


def test_generic_read_cannot_bypass_scoped_hospital_api(env):
    client, account = user(env, 'generic-read')
    act = action(client)
    assert client.get('/life/api/resources/medical/' + act).status_code == 404
    assert all(not x['kind'].startswith('hospital_os_') for x in client.get('/life/api/resources/medical').json()['resources'])


def test_tenant_mode_always_requires_role_even_if_rbac_flag_is_false(env, monkeypatch):
    monkeypatch.setenv('EDURECOVER_HOSPITAL_TENANT_ENFORCED', 'true')
    monkeypatch.setenv('EDURECOVER_HOSPITAL_RBAC_ENFORCED', 'false')
    client, _ = user(env, 'member-no-role', tenant='hospital-a', provision=False)
    assert client.get(BASE + '/clinical-actions').status_code == 403


def test_scoped_lists_dashboard_and_page_hide_other_patients(env, monkeypatch):
    monkeypatch.setenv('EDURECOVER_HOSPITAL_TENANT_ENFORCED', 'true')
    writer, _ = user(env, 'writer', tenant='hospital-a')
    reader, _ = user(env, 'reader', patients=['patient-1'], tenant='hospital-a')
    own, other = action(writer), action(writer, 'patient-2')
    for patient in ('patient-1', 'patient-2'):
        post(writer, '/census/patients', {'patient_id': patient, 'care_setting': 'INPATIENT'})
        post(writer, '/events', {'event_type': 'test.resulted', 'patient_id': patient, 'payload': {'marker': patient}})
    visible = reader.get(BASE + '/clinical-actions').json()['actions']
    assert [x['clinical_action_id'] for x in visible] == [own]
    today = reader.get(BASE + '/hospital/today').json()
    assert today['inpatient_patients'] == 1
    assert [x['patient_id'] for x in today['last_hour_changes']] == ['patient-1']
    audits = reader.get(BASE + '/audit-events').json()['audit_events']
    assert audits and all(x['patient_id'] == 'patient-1' for x in audits)
    page = reader.get('/life/medical/hospital-os')
    assert page.status_code == 200
    assert 'patient-2' not in page.text and other not in page.text
    assert reader.post(BASE + f'/clinical-actions/{other}/complete', json={}).status_code == 404


def test_id_routes_enforce_patient_scope_and_event_binding(env, monkeypatch):
    monkeypatch.setenv('EDURECOVER_HOSPITAL_TENANT_ENFORCED', 'true')
    writer, _ = user(env, 'id-writer', tenant='hospital-a')
    reader, _ = user(env, 'id-reader', patients=['patient-1'], tenant='hospital-a')
    src = source(writer, patient='patient-2')
    other = action(writer, 'patient-2')
    med = post(writer, '/medication-objects', {'patient_id': 'patient-2', 'clinical_action_id': other, 'prescription_revision_id': src, 'custody_stage': 'PREPARED'})['medication_object_id']
    handoff = post(writer, '/handoff/snapshot', {'patient_id': 'patient-2'})['handoff_snapshot_id']
    report = post(writer, '/reports/snapshot', {'patient_id': 'patient-2'})['report_snapshot_id']
    assert reader.get(BASE + f'/handoff/{handoff}/delta').status_code == 404
    assert reader.get(BASE + f'/reports/{report}/delta').status_code == 404
    assert reader.post(BASE + f'/medication-objects/{med}/prescription-change', json={'changed_from_revision_id': src, 'identity_status': 'VERIFIED'}).status_code == 404
    assert reader.post(BASE + '/actual-use-evidence', json={'clinical_action_id': other, 'source_revision_id': src, 'manifest_type': 'x', 'actor_or_system': 'x'}).status_code == 404
    mismatch = writer.post(BASE + '/events', json={'event_type': 'test.resulted', 'patient_id': 'patient-1', 'clinical_action_id': other})
    assert mismatch.status_code == 409


def test_patient_scoped_role_cannot_create_global_source_or_bulk_correct(env):
    client, _ = user(env, 'scope-source', patients=['patient-1'])
    assert client.post(BASE + '/source-revisions', json={'source_id': 'global', 'payload': {}}).status_code == 403
    src = source(client)
    assert client.post(BASE + '/source-changes', json={'source_before_revision_id': src, 'payload': {}}).status_code == 403


def test_existing_workflow_cannot_be_reassigned_to_another_patient(env):
    client, _ = user(env, 'workflow')
    workflow = post(client, '/er/workflows', {'patient_id': 'patient-1', 'workflow_state': 'ARRIVED', 'responsible_role': 'ER_STAFF'})
    r = client.post(BASE + '/er/workflows', json={'er_workflow_id': workflow['er_workflow_id'], 'patient_id': 'patient-2', 'workflow_state': 'DISCHARGE', 'responsible_role': 'ER_STAFF'})
    assert r.status_code == 409


def test_resource_id_cannot_move_between_accounts_or_kinds(env):
    put_resource(domain='medical', subject_id='a', kind='note', payload={'value': 1}, resource_id='same-id')
    for account, kind in [('b', 'note'), ('a', 'hospital_os_role_grant')]:
        with pytest.raises(HTTPException) as exc:
            put_resource(domain='medical', subject_id=account, kind=kind, payload={}, resource_id='same-id')
        assert exc.value.status_code == 409
    assert get_resource(domain='medical', subject_id='a', resource_id='same-id')['payload'] == {'value': 1}


def test_nested_writes_rollback_together(env):
    with pytest.raises(RuntimeError):
        with resource_transaction('medical'):
            put_resource(domain='medical', subject_id='a', kind='note', payload={}, resource_id='one')
            put_resource(domain='medical', subject_id='a', kind='note', payload={}, resource_id='two')
            raise RuntimeError('abort')
    assert list_resources(domain='medical', subject_id='a') == []


def test_source_change_rolls_back_state_lineage_receipt_event_and_delivery(env, monkeypatch):
    client, account = user(env, 'rollback')
    src, act = linked(client)
    before = list_resources(domain='medical', subject_id=account)
    real_put = hospital.put_resource
    def fail_delivery(**kwargs):
        if kwargs['kind'] == 'hospital_os_event_delivery':
            raise RuntimeError('simulated disk failure')
        return real_put(**kwargs)
    with monkeypatch.context() as m:
        m.setattr(hospital, 'put_resource', fail_delivery)
        with pytest.raises(RuntimeError, match='disk failure'):
            client.post(BASE + '/source-changes', json={'source_before_revision_id': src, 'payload': {'dose': 'new'}, 'changed_fields': ['dose']})
    assert list_resources(domain='medical', subject_id=account) == before
    changed = post(client, '/source-changes', {'source_before_revision_id': src, 'payload': {'dose': 'new'}, 'changed_fields': ['dose']})
    assert changed['post_states'][act] == 'HOLD'
    outbox = client.get(BASE + '/event-outbox/consumer').json()
    assert outbox['pending_count'] == 1
    assert outbox['items'][0]['event']['source_revision_id'] == changed['source_after_revision_id']
    # A fresh client reads persisted delivery, not an in-memory queue.
    fresh = TestClient(env, base_url='https://testserver')
    fresh.cookies.update(client.cookies)
    assert fresh.get(BASE + '/event-outbox/consumer').json()['pending_count'] == 1


def test_event_fanout_is_all_or_nothing_and_ack_is_scoped(env, monkeypatch):
    monkeypatch.setenv('EDURECOVER_HOSPITAL_TENANT_ENFORCED', 'true')
    client, _ = user(env, 'event-writer', tenant='hospital-a')
    scoped, _ = user(env, 'event-reader', patients=['patient-1'], tenant='hospital-a')
    for consumer in ('first', 'second'):
        post(client, '/event-subscriptions', {'consumer_id': consumer, 'event_types': ['test.resulted']})
    real_put = hospital.put_resource
    def fail_second(**kwargs):
        if kwargs['kind'] == 'hospital_os_event_delivery' and kwargs['payload']['consumer_id'] == 'second':
            raise RuntimeError('delivery failure')
        return real_put(**kwargs)
    before = list_resources(domain='medical', subject_id='hospital_tenant:hospital-a')
    with monkeypatch.context() as m:
        m.setattr(hospital, 'put_resource', fail_second)
        with pytest.raises(RuntimeError):
            client.post(BASE + '/events', json={'event_type': 'test.resulted', 'patient_id': 'patient-2'})
    assert list_resources(domain='medical', subject_id='hospital_tenant:hospital-a') == before
    post(client, '/events', {'event_type': 'test.resulted', 'patient_id': 'patient-2'})
    delivery = client.get(BASE + '/event-outbox/first').json()['items'][0]['delivery']['delivery_id']
    assert scoped.get(BASE + '/event-outbox/first').json() == {'consumer_id': 'first', 'pending_count': 0, 'items': []}
    assert scoped.post(BASE + f'/event-outbox/{delivery}/ack').status_code == 404


def test_concurrent_corrections_cannot_fork_one_source(env):
    client, account = user(env, 'concurrent')
    src, _ = linked(client)
    def correct(value):
        concurrent_client = TestClient(env, base_url='https://testserver')
        concurrent_client.cookies.update(client.cookies)
        return concurrent_client.post(BASE + '/source-changes', json={'source_before_revision_id': src, 'payload': {'dose': value}}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(correct, ['a', 'b']))
    assert sorted(statuses) == [200, 409]
    assert len(list_resources(domain='medical', subject_id=account, kind='hospital_os_source_revision')) == 2
    assert len(list_resources(domain='medical', subject_id=account, kind='hospital_os_event_delivery')) == 1


def test_integration_replacement_revalidates_actual_users_and_queues_event(env):
    client, account = user(env, 'integration', patients=['patient-1'])
    data = {'adapter_type': 'EMR', 'standard': 'VENDOR_JSON', 'source_system': 'emr', 'external_object_id': 'rx', 'patient_id': 'patient-1', 'payload': {'dose': 'old'}, 'verification_state': 'VERIFIED'}
    first = post(client, '/integrations/ingest', data)['source_revision_id']
    act = action(client)
    post(client, '/actual-use-evidence', {'clinical_action_id': act, 'source_revision_id': first, 'manifest_type': 'review', 'actor_or_system': 'doctor'})
    post(client, '/event-subscriptions', {'consumer_id': 'consumer', 'event_types': ['source.revision.changed']})
    second = post(client, '/integrations/ingest', {**data, 'payload': {'dose': 'new'}, 'verification_state': 'UNVERIFIED'})['source_revision_id']
    assert get_resource(domain='medical', subject_id=account, resource_id=act)['payload']['action_state'] == 'HOLD'
    event = client.get(BASE + '/event-outbox/consumer').json()['items'][0]['event']
    assert event['source_revision_id'] == second
    assert event['payload']['affected_action_ids'] == [act]
