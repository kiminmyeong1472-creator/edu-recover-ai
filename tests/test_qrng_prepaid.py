"""No live ANU calls: test payment, races, recovery, auth, and transport bounds."""
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform_core import life_auth
from app.services import qrng_prepaid as q
from app.services import qrng_prepaid_api as api
from app.services.owner_basic_auth import require_owner
from app.training.backup_inventory import discover_runtime_assets


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("EDURECOVER_CLASSROOM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ANU_QRNG_ENABLED", "true")
    monkeypatch.setenv("ANU_QRNG_API_KEY", "unit-test-key-never-sent")
    monkeypatch.setenv("ANU_QRNG_DAILY_CALL_LIMIT", "100")
    monkeypatch.setenv("LIFERECOVER_SESSION_SECRET", "test-only-" * 8)
    monkeypatch.setenv("LIFERECOVER_REGISTRATION_INVITE", "test-invite")
    monkeypatch.setattr(q, "_fetch_numbers", lambda length, key: [7] * length)
    return tmp_path


def grant(account="account_alpha", calls=3, reference="receipt-one", **changes):
    values = dict(account_id=account, provider="manual_bank", reference=reference,
                  calls=calls, amount_minor=1000, currency="KRW")
    values.update(changes)
    return q.grant_verified_prepayment(**values)


def call(account="account_alpha", request="request_first", length=1):
    return q.request_numbers(account_id=account, request_id=request, length=length)


def code(expected, fn):
    with pytest.raises(q.QuotaError) as exc:
        fn()
    assert exc.value.code == expected


def test_unpaid_request_never_reaches_provider(setup, monkeypatch):
    attempts = []
    monkeypatch.setattr(q, "_fetch_numbers", lambda *args: attempts.append(args))
    code("QRNG_PREPAYMENT_REQUIRED", call)
    assert attempts == []
    assert q.balance("account_alpha")["remaining_calls"] == 0


def test_verified_grant_one_request_one_unit_even_for_1024_numbers(setup):
    grant(calls=1)
    result = call(length=1024)
    assert result["charged_calls"] == 1
    assert len(result["data"]) == 1024
    assert result["remaining_calls"] == 0
    code("QRNG_PREPAYMENT_REQUIRED", lambda: call(request="request_second"))


def test_duplicate_payment_does_not_refill_spent_units(setup):
    first = grant(calls=2)
    call()
    again = grant(calls=2)
    assert first["receipt"] == again["receipt"]
    assert again["duplicate"] is True
    assert q.balance("account_alpha")["remaining_calls"] == 1


@pytest.mark.parametrize("change", [{"account": "account_bravo"}, {"calls": 4},
                                      {"amount_minor": 2000}, {"currency": "USD"}])
def test_replayed_payment_cannot_change_account_or_amount(setup, change):
    grant()
    code("QRNG_PAYMENT_CONFLICT", lambda: grant(**change))


def test_revoke_removes_unused_units_and_payment_replay_stays_blocked(setup):
    grant(calls=3)
    call()
    assert q.revoke_prepayment(provider="manual_bank", reference="receipt-one")["revoked_unused_calls"] == 2
    code("QRNG_PREPAYMENT_REQUIRED", lambda: call(request="request_second"))
    code("QRNG_PAYMENT_REVOKED", grant)
    assert q.balance("account_alpha")["attempts_by_state"] == {"SUCCEEDED": 1}


def test_revoke_before_dispatch_prevents_supplier_request(setup, monkeypatch):
    grant(calls=1)
    original = q._dispatch
    attempts = []
    def dispatch(account, request):
        q.revoke_prepayment(provider="manual_bank", reference="receipt-one")
        original(account, request)
    monkeypatch.setattr(q, "_dispatch", dispatch)
    monkeypatch.setattr(q, "_fetch_numbers", lambda *args: attempts.append(args))
    code("QRNG_DISPATCH_DENIED", call)
    assert attempts == []


def test_account_isolation_and_multiple_prepayments(setup):
    grant(calls=1)
    grant(calls=2, reference="receipt-two")
    code("QRNG_PREPAYMENT_REQUIRED", lambda: call(account="account_bravo"))
    for i in range(3):
        call(request=f"request_{i:03}")
    assert q.balance("account_alpha")["remaining_calls"] == 0


def test_duplicate_request_is_never_dispatched_twice(setup, monkeypatch):
    grant()
    attempts = []
    monkeypatch.setattr(q, "_fetch_numbers", lambda n, key: attempts.append(n) or [1] * n)
    call()
    code("QRNG_REQUEST_ALREADY_RESERVED", call)
    code("QRNG_REQUEST_CONFLICT", lambda: call(length=2))
    assert attempts == [1]
    assert q.balance("account_alpha")["remaining_calls"] == 2


def test_uncertain_outcome_keeps_unit_and_never_retries(setup, monkeypatch):
    grant()
    attempts = []
    def timeout(*args):
        attempts.append(1)
        raise TimeoutError("secret-provider-response")
    monkeypatch.setattr(q, "_fetch_numbers", timeout)
    code("QRNG_OUTCOME_UNKNOWN_NO_RETRY", call)
    code("QRNG_REQUEST_ALREADY_RESERVED", call)
    assert attempts == [1]
    assert q.balance("account_alpha")["remaining_calls"] == 2
    assert q.balance("account_alpha")["attempts_by_state"] == {"UNKNOWN": 1}


def test_crash_after_reservation_preserves_deduction_across_connections(setup):
    grant(calls=1)
    q.reserve(account_id="account_alpha", request_id="request_first", length=1, daily_limit=100)
    code("QRNG_REQUEST_ALREADY_RESERVED", call)
    assert q.balance("account_alpha")["remaining_calls"] == 0


def test_concurrent_unique_requests_never_exceed_prepaid_units(setup, monkeypatch):
    grant(calls=4)
    attempts = []
    monkeypatch.setattr(q, "_fetch_numbers", lambda n, key: attempts.append(1) or [1])
    def work(i):
        try:
            call(request=f"request_{i:03}")
            return "OK"
        except q.QuotaError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(work, range(20)))
    assert results.count("OK") == 4
    assert results.count("QRNG_PREPAYMENT_REQUIRED") == 16
    assert len(attempts) == 4
    assert q.balance("account_alpha")["remaining_calls"] == 0


def test_concurrent_duplicate_request_uses_one_unit(setup, monkeypatch):
    grant(calls=3)
    attempts = []
    monkeypatch.setattr(q, "_fetch_numbers", lambda n, key: attempts.append(1) or [1])
    def work(_):
        try:
            call()
            return "OK"
        except q.QuotaError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(12)))
    assert results.count("OK") == 1
    assert len(attempts) == 1
    assert q.balance("account_alpha")["remaining_calls"] == 2


def test_global_daily_cap_does_not_consume_another_prepaid_unit(setup, monkeypatch):
    monkeypatch.setenv("ANU_QRNG_DAILY_CALL_LIMIT", "1")
    grant(calls=2)
    grant(account="account_bravo", reference="receipt-two", calls=1)
    call()
    code("QRNG_DAILY_LIMIT_REACHED", lambda: call(account="account_bravo"))
    assert q.balance("account_bravo")["remaining_calls"] == 1


@pytest.mark.parametrize("name,value", [("ANU_QRNG_ENABLED", "false"), ("ANU_QRNG_API_KEY", ""),
    ("ANU_QRNG_API_KEY", "bad\nkey"), ("ANU_QRNG_DAILY_CALL_LIMIT", "0"),
    ("ANU_QRNG_DAILY_CALL_LIMIT", "bad"), ("ANU_QRNG_DAILY_CALL_LIMIT", "100000000000")])
def test_missing_or_invalid_config_does_not_consume_units(setup, monkeypatch, name, value):
    grant()
    monkeypatch.setenv(name, value)
    code("QRNG_NOT_CONFIGURED", call)
    assert q.balance("account_alpha")["remaining_calls"] == 3


@pytest.mark.parametrize("length", [0, 1025, True, "1"])
def test_invalid_length_does_not_consume_units(setup, length):
    grant()
    code("QRNG_INVALID_COUNT", lambda: call(length=length))
    assert q.balance("account_alpha")["remaining_calls"] == 3


def test_db_is_private_and_encrypted_backup_includes_it(setup):
    grant()
    call()
    path = setup / "qrng/prepaid.sqlite3"
    assert path.stat().st_mode & 0o777 == 0o600
    raw = path.read_bytes()
    assert b"unit-test-key-never-sent" not in raw
    assert b"receipt-one" not in raw
    inv = discover_runtime_assets(setup)
    asset = next(x for x in inv.assets if x["source"] == "qrng/prepaid.sqlite3")
    assert asset["sqlite_snapshot"] is True
    assert asset["contains_personal_data"] is True


def test_storage_failure_never_contacts_provider(setup, monkeypatch):
    (setup / "qrng").symlink_to(setup, target_is_directory=True)
    attempts = []
    monkeypatch.setattr(q, "_fetch_numbers", lambda *args: attempts.append(args))
    code("QRNG_STORAGE_UNAVAILABLE", call)
    assert attempts == []


def client():
    app = FastAPI()
    app.include_router(life_auth.router)
    app.include_router(api.router)
    app.include_router(api.owner_router)
    return TestClient(app, base_url="https://testserver")


def register(c, username="alpha"):
    result = c.post("/life/auth/register", json={"username": username,
        "password": "safe-test-password", "invite_code": "test-invite"})
    assert result.status_code == 200, result.text
    return result.json()["account_id"]


def test_api_auth_identity_scope_and_manual_owner_grant(setup):
    c = client()
    assert c.post("/life/api/qrng/numbers", json={"request_id": "request_test"}).status_code == 401
    aid = register(c)
    payload = dict(provider="manual_bank", reference="receipt-api", account_id=aid,
                   calls=1, amount_minor=1000, currency="KRW", settlement_checked=True)
    assert c.post("/owner/qrng/prepayments", json=payload).status_code in {401, 503}
    c.app.dependency_overrides[require_owner] = lambda: None
    assert c.post("/owner/qrng/prepayments", json=payload).status_code == 200
    body = {"request_id": "request_test", "length": 1}
    assert c.post("/life/api/qrng/numbers", json=body).status_code == 403
    assert c.post("/life/api/qrng/numbers", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/life/api/qrng/numbers", json={**body,"account_id": "account_someone"},
                  headers={"Origin": "https://testserver"}).status_code == 422
    r = c.post("/life/api/qrng/numbers", json=body, headers={"Origin": "https://testserver"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["remaining_calls"] == 0
    assert c.get("/life/api/qrng/quota").json()["remaining_calls"] == 0


@pytest.mark.parametrize("change", [{"settlement_checked": False}, {"settlement_checked": 1},
    {"calls": True}, {"calls": "1"}, {"amount_minor": 0}, {"currency": "krw"}, {"paid": True}])
def test_owner_api_rejects_unverified_or_malformed_payment(setup, change):
    c = client()
    aid = register(c)
    c.app.dependency_overrides[require_owner] = lambda: None
    payload = dict(provider="manual_bank", reference="receipt-api", account_id=aid,
                   calls=1, amount_minor=1000, currency="KRW", settlement_checked=True)
    payload.update(change)
    assert c.post("/owner/qrng/prepayments", json=payload).status_code == 422
    assert q.balance(aid)["remaining_calls"] == 0


def test_grants_cannot_create_nonexistent_accounts_and_inputs_are_bounded(setup):
    c = client()
    register(c)
    c.app.dependency_overrides[require_owner] = lambda: None
    payload = dict(provider="manual_bank", reference="receipt-api", account_id="account_absent",
                   calls=1, amount_minor=1000, currency="KRW", settlement_checked=True)
    assert c.post("/owner/qrng/prepayments", json=payload).status_code == 404
    assert c.post("/owner/qrng/prepayments", content=b"x" * 4097).status_code == 413


@pytest.mark.parametrize("body", [{"data": [1], "success": False},
    {"data": [True], "success": True, "length": 1, "type": "uint16"},
    {"data": [65536], "success": True, "length": 1, "type": "uint16"},
    {"data": [], "success": True, "length": 1, "type": "uint16"},
    {"data": [1], "success": True, "length": 1, "type": "uint8"}])
def test_transport_rejects_bad_supplier_values(monkeypatch, body):
    class Fake(io.BytesIO):
        status = 200
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.quantumnumbers.anu.edu.au/?length=1&type=uint16"
            assert request.get_header("X-api-key") == "fake-key"
            assert timeout == 20
            return Fake(json.dumps(body).encode())
    monkeypatch.setattr(q, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError):
        q._fetch_numbers(1, "fake-key")


def test_transport_bounds_response_and_rejects_redirects(monkeypatch):
    class Fake(io.BytesIO):
        status = 200
    class Opener:
        def open(self, request, timeout):
            return Fake(b"x" * 20000)
    monkeypatch.setattr(q, "build_opener", lambda *args: Opener())
    with pytest.raises(ValueError, match="response_too_large"):
        q._fetch_numbers(1, "fake-key")
    assert q._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example") is None


def test_transport_accepts_valid_data_without_live_network(monkeypatch):
    class Fake(io.BytesIO):
        status = 200
    class Opener:
        def open(self, request, timeout):
            return Fake(b'{"success":true,"type":"uint16","length":2,"data":[0,65535]}')
    monkeypatch.setattr(q, "build_opener", lambda *args: Opener())
    assert q._fetch_numbers(2, "fake-key") == [0, 65535]


def test_api_exhaustion_and_uncertainty_are_private(setup, monkeypatch):
    c = client()
    aid = register(c)
    headers = {"Origin": "https://testserver"}
    assert c.post("/life/api/qrng/numbers", json={"request_id": "request_test"},
                  headers=headers).status_code == 402
    grant(account=aid, calls=1)
    def fail(*args):
        raise TimeoutError("unit-test-key-never-sent-and-private-error")
    monkeypatch.setattr(q, "_fetch_numbers", fail)
    r = c.post("/life/api/qrng/numbers", json={"request_id": "request_test"}, headers=headers)
    assert r.status_code == 502
    assert r.json() == {"detail": "QRNG_OUTCOME_UNKNOWN_NO_RETRY"}
    assert c.post("/life/api/qrng/numbers", json={"request_id": "request_next"},
                  headers=headers).status_code == 402


def test_corrupt_database_fails_closed_before_supplier(setup, monkeypatch):
    folder = setup / "qrng"
    folder.mkdir()
    (folder / "prepaid.sqlite3").write_bytes(b"broken sqlite file")
    attempts = []
    monkeypatch.setattr(q, "_fetch_numbers", lambda *args: attempts.append(args))
    code("QRNG_STORAGE_UNAVAILABLE", call)
    assert attempts == []
