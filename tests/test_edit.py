import uuid
from datetime import datetime, timedelta

from conftest import _expire_link, _get_test_user, _get_url_row, _shorten, client


def test_owner_can_edit_destination_url():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    new_url = f"https://example.com/new-{uuid.uuid4()}"

    response = client.put(f"/urls/{code}", json={"url": new_url}, headers={"X-API-Key": user.api_key})
    assert response.status_code == 200

    redirect = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].rstrip("/") == new_url


def test_edit_expiry_only_leaves_url_untouched():
    user = _get_test_user("owner@test.com")
    original_url = f"https://example.com/{uuid.uuid4()}"
    code = _shorten(original_url, user.api_key)
    future = (datetime.utcnow() + timedelta(days=1)).isoformat()

    response = client.put(f"/urls/{code}", json={"expires_at": future}, headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    assert _get_url_row(code).original_url.rstrip("/") == original_url


def test_edit_past_expiry_deactivates_link():
    """The client-requested feature: past expiry via edit == deactivation."""
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    past = (datetime.utcnow() - timedelta(hours=1)).isoformat()

    response = client.put(f"/urls/{code}", json={"expires_at": past}, headers={"X-API-Key": user.api_key})
    assert response.status_code == 200

    redirect = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert redirect.status_code == 410


def test_edit_can_reactivate_expired_link():
    """Clearing expiry (explicit null) brings a deactivated link back."""
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    _expire_link(code)
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 410

    response = client.put(f"/urls/{code}", json={"expires_at": None}, headers={"X-API-Key": user.api_key})
    assert response.status_code == 200

    redirect = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert redirect.status_code == 307


def test_edit_extending_expiry_reactivates_link():
    """Setting a future expiry also brings a deactivated link back."""
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    _expire_link(code)

    future = (datetime.utcnow() + timedelta(days=1)).isoformat()
    response = client.put(f"/urls/{code}", json={"expires_at": future}, headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 307


def test_non_owner_cannot_edit():
    owner = _get_test_user("owner@test.com")
    intruder = _get_test_user("intruder@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", owner.api_key)

    response = client.put(
        f"/urls/{code}",
        json={"url": "https://example.com/hijacked"},
        headers={"X-API-Key": intruder.api_key},
    )
    assert response.status_code == 403


def test_edit_without_api_key_returns_401():
    owner = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", owner.api_key)

    response = client.put(f"/urls/{code}", json={"url": "https://example.com/x"})
    assert response.status_code == 401


def test_edit_unknown_code_returns_404():
    user = _get_test_user("owner@test.com")
    response = client.put(
        "/urls/nonexistent123",
        json={"url": "https://example.com"},
        headers={"X-API-Key": user.api_key},
    )
    assert response.status_code == 404


def test_edit_deleted_code_returns_404():
    """A soft-deleted code is not editable — deletion stays the irreversible path."""
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    client.delete(f"/urls/{code}", headers={"X-API-Key": user.api_key})

    response = client.put(
        f"/urls/{code}",
        json={"url": "https://example.com/x"},
        headers={"X-API-Key": user.api_key},
    )
    assert response.status_code == 404


def test_edit_empty_body_rejected():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.put(f"/urls/{code}", json={}, headers={"X-API-Key": user.api_key})
    assert response.status_code == 422
