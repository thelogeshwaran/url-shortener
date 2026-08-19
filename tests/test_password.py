import uuid

from conftest import _expire_link, _get_test_user, _get_url_row, _shorten, client


def test_shorten_without_password_is_unaffected():
    """Baseline: links with no password behave exactly as before."""
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307


def test_password_too_short_rejected_on_create():
    response = client.post(
        "/shorten", json={"url": "https://example.com", "password": "abc"}
    )
    assert response.status_code == 422


def test_edit_password_too_short_rejected():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.put(
        f"/urls/{code}", json={"password": "abc"}, headers={"X-API-Key": user.api_key}
    )
    assert response.status_code == 422


def test_redirect_without_password_is_rejected_when_one_is_set():
    """No password given on a paywalled code -> 401, not a crash."""
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    client.put(f"/urls/{code}", json={"password": "secret1"}, headers={"X-API-Key": user.api_key})

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 401
    assert response.json()["detail"] == "Password required"


def test_redirect_with_correct_password_succeeds():
    user = _get_test_user("owner@test.com")
    original_url = f"https://example.com/{uuid.uuid4()}"
    response = client.post(
        "/shorten",
        json={"url": original_url, "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]

    redirect = client.get(f"/redirect?code={code}&password=secret1", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].rstrip("/") == original_url


def test_redirect_with_wrong_password_returns_401_and_does_not_increment():
    user = _get_test_user("owner@test.com")
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]

    redirect = client.get(f"/redirect?code={code}&password=wrongpass", follow_redirects=False)
    assert redirect.status_code == 401
    assert _get_url_row(code).click_count == 0


def test_deleted_password_protected_code_returns_404_not_401():
    """Existence must not leak through the paywall: deleted -> 404, never 401."""
    user = _get_test_user("owner@test.com")
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]
    client.delete(f"/urls/{code}", headers={"X-API-Key": user.api_key})

    redirect = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert redirect.status_code == 404


def test_expired_password_protected_code_returns_410_not_401():
    """Liveness must be checked before the paywall: expired -> 410, never 401."""
    user = _get_test_user("owner@test.com")
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]
    _expire_link(code)

    redirect = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert redirect.status_code == 410


def test_edit_can_add_password_to_existing_link():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 307

    response = client.put(
        f"/urls/{code}", json={"password": "secret1"}, headers={"X-API-Key": user.api_key}
    )
    assert response.status_code == 200
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 401


def test_edit_can_clear_password_via_null():
    user = _get_test_user("owner@test.com")
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 401

    edit = client.put(
        f"/urls/{code}", json={"password": None}, headers={"X-API-Key": user.api_key}
    )
    assert edit.status_code == 200

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307
