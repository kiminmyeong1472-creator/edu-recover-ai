from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app, base_url="https://testserver")


def test_life_page_is_wired_into_main_app():
    response = client.get("/life")
    assert response.status_code == 200
    assert "생활의 혁명" in response.text
    assert "LifeRevolve" in response.text
    assert "/life/screen" in response.text
    assert "/life/daily" in response.text


def test_life_private_api_requires_authentication():
    response = client.get("/life/api/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "life_auth_required"


def test_life_routes_do_not_replace_edurecover_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
