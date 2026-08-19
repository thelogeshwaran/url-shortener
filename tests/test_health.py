from unittest.mock import patch

from conftest import client


def test_health_check_reports_ok_when_db_reachable():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_check_requires_no_api_key():
    response = client.get("/health")
    assert response.status_code != 401


def test_health_check_reports_503_when_db_unreachable():
    with patch("app.routers.get_session", side_effect=Exception("connection refused")):
        response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["database"] == "unreachable"


def test_health_check_does_not_leak_internal_error_details():
    with patch(
        "app.routers.get_session",
        side_effect=Exception("password authentication failed for user 'postgres' at 10.0.0.5"),
    ):
        response = client.get("/health")
    assert "postgres" not in response.text
    assert "10.0.0.5" not in response.text
    assert "password" not in response.text
