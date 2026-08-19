import uuid

from conftest import _get_test_user, _shorten, client


def test_health_exempt_from_api_key_check():
    response = client.get("/health")
    assert response.status_code == 200


def test_redirect_exempt_from_api_key_check():
    """Redirect links are public — clicking one must never require an API key."""
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307


def test_shorten_exempt_allows_anonymous_request():
    response = client.post("/shorten", json={"url": "https://example.com"})
    assert response.status_code == 200


def test_shorten_with_invalid_key_returns_401():
    response = client.post(
        "/shorten",
        json={"url": "https://example.com"},
        headers={"X-API-Key": "totally-invalid-key"},
    )
    assert response.status_code == 401


def test_protected_route_without_key_returns_401():
    response = client.get("/urls")
    assert response.status_code == 401


def test_protected_route_with_invalid_key_returns_401():
    response = client.get("/urls", headers={"X-API-Key": "totally-invalid-key"})
    assert response.status_code == 401


def test_protected_route_with_valid_key_succeeds():
    user = _get_test_user("auth-mw@test.com")
    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200


def test_invalid_key_short_circuits_before_service_logic():
    """
    A delete on a NONEXISTENT code with an invalid key must still be 401,
    not 404 — proving the middleware rejected the request before the
    service layer ever checked whether the code exists.
    """
    response = client.delete(
        "/urls/this-code-does-not-exist-at-all",
        headers={"X-API-Key": "totally-invalid-key"},
    )
    assert response.status_code == 401
