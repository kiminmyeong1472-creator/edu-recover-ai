import os
from unittest.mock import patch

import pytest

from app.services import code_execution_runtime as rt


@pytest.fixture(autouse=True)
def reset():
    rt._accepted.update(candidate=None, environment=None)
    yield
    rt._accepted.update(candidate=None, environment=None)


def fake_send(accept):
    calls = []

    def send(query, variables, token, header):
        calls.append((token, header, variables))
        result = accept(query, variables, token, header)
        if result is None:
            raise rt.CodeExecutionError("ACCESS_DENIED")
        return result
    return send, calls


def test_account_token_in_project_variable_is_retried_as_bearer():
    send, calls = fake_send(lambda q, v, t, h: {"ok": 1} if h == "Authorization" else None)
    with patch.dict(os.environ, {"RAILWAY_TOKEN": "acct"}, clear=True), \
            patch.object(rt, "_graphql_send", send):
        assert rt._graphql_request("q", {"environmentId": "e1"}) == {"ok": 1}
        assert rt._accepted["candidate"] == 1
        rt._graphql_request("q", {"environmentId": "e1"})
    assert calls[-1][1] == "Authorization"


def test_stale_project_token_does_not_shadow_fresh_account_token():
    send, _ = fake_send(lambda q, v, t, h: {"ok": 1} if t == "fresh" else None)
    with patch.dict(os.environ, {"RAILWAY_TOKEN": "old", "RAILWAY_API_TOKEN": "fresh"}, clear=True), \
            patch.object(rt, "_graphql_send", send):
        assert rt._graphql_request("q", {"environmentId": "e1"}) == {"ok": 1}


def test_project_token_moves_to_its_own_environment():
    def accept(q, v, t, h):
        if q == rt._PROJECT_TOKEN_ENV:
            return {"projectToken": {"environmentId": "e2"}}
        env = v.get("environmentId") or v["input"]["environmentId"]
        return {"ok": env} if env == "e2" and h == "project-access-token" else None
    send, _ = fake_send(accept)
    with patch.dict(os.environ, {"RAILWAY_TOKEN": "proj"}, clear=True), \
            patch.object(rt, "_graphql_send", send):
        assert rt._graphql_request("q", {"input": {"environmentId": "e1"}}) == {"ok": "e2"}
        assert rt._graphql_request("q", {"environmentId": "e1"}) == {"ok": "e2"}


def test_other_errors_are_not_retried():
    def send(q, v, t, h):
        raise rt.CodeExecutionError("PLAN_LIMIT")
    with patch.dict(os.environ, {"RAILWAY_TOKEN": "a", "RAILWAY_API_TOKEN": "b"}, clear=True), \
            patch.object(rt, "_graphql_send", send):
        with pytest.raises(rt.CodeExecutionError, match="PLAN_LIMIT"):
            rt._graphql_request("q", {"environmentId": "e1"})


def test_all_denied_stays_access_denied():
    send, calls = fake_send(lambda q, v, t, h: None)
    with patch.dict(os.environ, {"RAILWAY_TOKEN": "a", "RAILWAY_API_TOKEN": "b"}, clear=True), \
            patch.object(rt, "_graphql_send", send):
        with pytest.raises(rt.CodeExecutionError, match="ACCESS_DENIED"):
            rt._graphql_request("q", {"environmentId": "e1"})
    assert {(t, h) for t, h, _ in calls} >= {("a", "project-access-token"), ("b", "Authorization"),
                                              ("a", "Authorization"), ("b", "project-access-token")}
