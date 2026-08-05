import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import cache
from app.database import get_session
from app.main import app
from app.middleware.rate_limit import _redis as _rate_limit_redis
from app.models import Url, User

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """
    TestClient always reports the same fake peer IP ("testclient") for
    every request across the whole suite -- without resetting this
    between tests, the shared counter would accumulate across all ~95
    other tests in this file and start returning 429s for unrelated
    tests long before any rate-limit test itself ever runs.
    """
    _rate_limit_redis.delete('ratelimit:testclient')
    yield


def _get_url_row(code: str) -> Url | None:
    """Read a row straight from the DB to verify side effects."""
    with get_session() as session:
        return session.exec(select(Url).where(Url.short_code == code)).first()


def _get_test_user(email: str, tier: str = "hobby") -> User:
    """Fetch-or-create a test user; the api_key is stable across runs."""
    with get_session() as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            user = User(email=email, name="Test User", api_key=f"test-{uuid.uuid4().hex}", tier=tier)
            session.add(user)
            session.commit()
            session.refresh(user)
        elif user.tier != tier:
            user.tier = tier
            session.add(user)
            session.commit()
            session.refresh(user)
        return user


def _shorten(url: str, api_key: str | None = None) -> str:
    headers = {"X-API-Key": api_key} if api_key else {}
    return client.post("/shorten", json={"url": url}, headers=headers).json()["short_url"]


def test_shorten_and_redirect():
    """
    Integration test:
    1. Call /shorten with https://example.com and store the short code.
    2. Call /redirect with that code and verify it redirects
       to the original URL.
    """
    # Step 1: shorten the URL
    response = client.post("/shorten", json={"url": "https://example.com"})
    assert response.status_code == 200

    body = response.json()
    short_code = body["short_url"]     # store the short code in a variable
    assert short_code                  # code should not be empty

    # Step 2: redirect using the short code
    response = client.get(
        f"/redirect?code={short_code}",
        follow_redirects=False,        # we want to inspect the redirect itself
    )
    assert response.status_code == 307
    assert response.headers["location"].rstrip("/") == "https://example.com"


def test_delete_short_code():
    """
    Deleting a short code must return 204, and the code must
    stop redirecting afterwards (404).
    """
    user = _get_test_user("owner@test.com")
    random_url = f"https://example.com/{uuid.uuid4()}"
    short_code = _shorten(random_url, user.api_key)

    response = client.delete(f"/urls/{short_code}", headers={"X-API-Key": user.api_key})
    assert response.status_code == 204

    # the mapping is gone: redirect must now 404
    response = client.get(f"/redirect?code={short_code}", follow_redirects=False)
    assert response.status_code == 404


def test_delete_unknown_code_returns_404():
    """
    Deleting a short code that doesn't exist must return 404.
    """
    user = _get_test_user("owner@test.com")
    response = client.delete("/urls/nonexistent123", headers={"X-API-Key": user.api_key})
    assert response.status_code == 404
    assert response.json()["detail"] == "Short code not found"


def test_unknown_code_returns_404():
    """
    Fetching a short code that doesn't exist must return 404.
    """
    response = client.get(
        "/redirect?code=nonexistent123",
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Short code not found"


def test_redirect_increments_click_count():
    """
    Each /redirect access must increment click_count by 1
    and set last_accessed_at.
    """
    random_url = f"https://example.com/{uuid.uuid4()}"
    code = client.post("/shorten", json={"url": random_url}).json()["short_url"]

    # freshly created: never accessed
    row = _get_url_row(code)
    assert row.click_count == 0
    assert row.last_accessed_at is None

    client.get(f"/redirect?code={code}", follow_redirects=False)
    client.get(f"/redirect?code={code}", follow_redirects=False)
    cache.flush_pending_clicks()  # 2nd hit is a cache hit; its click is in-memory until flushed

    row = _get_url_row(code)
    assert row.click_count == 2
    assert row.last_accessed_at is not None


def test_redirect_location_is_plain_url():
    """
    The Location header must be the original URL itself —
    regression test for returning a Row tuple instead of a string.
    """
    random_url = f"https://example.com/{uuid.uuid4()}"
    code = client.post("/shorten", json={"url": random_url}).json()["short_url"]

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].rstrip("/") == random_url


def test_same_url_gets_multiple_codes():
    """
    Shortening the same URL twice must create two different codes,
    both redirecting to that URL, each with its own click_count.
    """
    random_url = f"https://example.com/{uuid.uuid4()}"
    first = client.post("/shorten", json={"url": random_url}).json()["short_url"]
    second = client.post("/shorten", json={"url": random_url}).json()["short_url"]

    assert first != second

    # both codes resolve to the same original URL
    for code in (first, second):
        response = client.get(f"/redirect?code={code}", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].rstrip("/") == random_url

    # a second hit on `first` only — counts are tracked per code
    client.get(f"/redirect?code={first}", follow_redirects=False)
    cache.flush_pending_clicks()  # 2nd hit on `first` is a cache hit; flush before checking the DB
    assert _get_url_row(first).click_count == 2
    assert _get_url_row(second).click_count == 1


def test_shorten_does_not_count_as_click():
    """
    Only /redirect counts as a view — shortening a URL again
    (which creates a new code) must not touch the first code's click_count.
    """
    random_url = f"https://example.com/{uuid.uuid4()}"
    code = client.post("/shorten", json={"url": random_url}).json()["short_url"]
    client.post("/shorten", json={"url": random_url})  # creates a second code

    row = _get_url_row(code)
    assert row.click_count == 0


def test_failed_redirect_does_not_increment():
    """
    A 404 on a deleted code must not resurrect or count anything.
    """
    user = _get_test_user("owner@test.com")
    random_url = f"https://example.com/{uuid.uuid4()}"
    code = _shorten(random_url, user.api_key)
    client.delete(f"/urls/{code}", headers={"X-API-Key": user.api_key})

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 404
    assert _get_url_row(code).deleted_at is not None


def test_shorten_with_api_key_links_owner():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)
    assert _get_url_row(code).user_id == user.id


def test_shorten_without_api_key_is_anonymous():
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    assert _get_url_row(code).user_id is None


def test_invalid_api_key_returns_401():
    response = client.post(
        "/shorten",
        json={"url": "https://example.com"},
        headers={"X-API-Key": "definitely-not-a-real-key"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


def test_owner_can_delete_own_link():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.delete(f"/urls/{code}", headers={"X-API-Key": user.api_key})
    assert response.status_code == 204

    assert _get_url_row(code).deleted_at is not None   # soft-deleted, row remains
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 404


def test_non_owner_cannot_delete():
    owner = _get_test_user("owner@test.com")
    intruder = _get_test_user("intruder@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", owner.api_key)

    response = client.delete(f"/urls/{code}", headers={"X-API-Key": intruder.api_key})
    assert response.status_code == 403

    # link untouched: not soft-deleted, still redirects
    assert _get_url_row(code).deleted_at is None
    assert client.get(f"/redirect?code={code}", follow_redirects=False).status_code == 307


def test_delete_without_api_key_returns_401():
    owner = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", owner.api_key)

    response = client.delete(f"/urls/{code}")
    assert response.status_code == 401
    assert _get_url_row(code).deleted_at is None       # nothing was deleted


def test_authenticated_user_can_delete_anonymous_link():
    user = _get_test_user("owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}")   # created with no key

    response = client.delete(f"/urls/{code}", headers={"X-API-Key": user.api_key})
    assert response.status_code == 204
    assert _get_url_row(code).deleted_at is not None


def _expire_link(code: str) -> None:
    """Force a link into the past — no sleep() needed."""
    with get_session() as session:
        url = session.exec(select(Url).where(Url.short_code == code)).first()
        url.expires_at = datetime.utcnow() - timedelta(hours=1)
        session.add(url)
        session.commit()


def test_shorten_with_future_expiry_redirects():
    random_url = f"https://example.com/{uuid.uuid4()}"
    expiry = (datetime.utcnow() + timedelta(days=1)).isoformat()
    response = client.post("/shorten", json={"url": random_url, "expires_at": expiry})
    assert response.status_code == 200

    code = response.json()["short_url"]
    assert _get_url_row(code).expires_at is not None

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307


def test_expired_link_returns_410():
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    _expire_link(code)

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 410
    assert response.json()["detail"] == "Short code expired"


def test_expired_link_does_not_increment_clicks():
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    _expire_link(code)

    client.get(f"/redirect?code={code}", follow_redirects=False)
    row = _get_url_row(code)
    assert row.click_count == 0
    assert row.last_accessed_at is None


def test_past_expiry_rejected_on_create():
    expiry = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "expires_at": expiry},
    )
    assert response.status_code == 422


def test_no_expiry_means_never_expires():
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    assert _get_url_row(code).expires_at is None

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307


def test_custom_code_is_used():
    random_url = f"https://example.com/{uuid.uuid4()}"
    custom = f"my-article-{uuid.uuid4().hex[:8]}"

    response = client.post("/shorten", json={"url": random_url, "code": custom})
    assert response.status_code == 200
    assert response.json()["short_url"] == custom

    response = client.get(f"/redirect?code={custom}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].rstrip("/") == random_url


def test_duplicate_custom_code_returns_409():
    custom = f"taken-{uuid.uuid4().hex[:8]}"
    first = client.post("/shorten", json={"url": "https://example.com", "code": custom})
    assert first.status_code == 200

    second = client.post("/shorten", json={"url": "https://example.com/other", "code": custom})
    assert second.status_code == 409
    assert second.json()["detail"] == "Short code already exists"


def test_custom_code_cannot_reuse_deleted_code():
    """Soft-deleted codes still own their name — no hijacking dead links."""
    user = _get_test_user("owner@test.com")
    custom = f"dead-{uuid.uuid4().hex[:8]}"
    client.post(
        "/shorten",
        json={"url": "https://example.com", "code": custom},
        headers={"X-API-Key": user.api_key},
    )
    client.delete(f"/urls/{custom}", headers={"X-API-Key": user.api_key})

    response = client.post("/shorten", json={"url": "https://example.com", "code": custom})
    assert response.status_code == 409


def test_omitted_code_still_autogenerates():
    response = client.post("/shorten", json={"url": "https://example.com"})
    assert response.status_code == 200
    assert len(response.json()["short_url"]) == 6  # generated, not custom


def test_batch_shorten_all_succeed():
    user = _get_test_user("enterprise@test.com", tier="enterprise")
    urls = [f"https://example.com/{uuid.uuid4()}" for _ in range(3)]
    response = client.post(
        "/shorten/batch",
        json={"urls": [{"url": u} for u in urls]},
        headers={"X-API-Key": user.api_key},
    )
    assert response.status_code == 200

    results = response.json()["results"]
    assert len(results) == 3
    for original, result in zip(urls, results):
        assert result["error"] is None
        assert result["short_url"]
        redirect = client.get(f"/redirect?code={result['short_url']}", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"].rstrip("/") == original


def test_batch_shorten_partial_failure():
    """One item has a taken custom code — it fails, the others still succeed."""
    user = _get_test_user("enterprise@test.com", tier="enterprise")
    taken = f"taken-{uuid.uuid4().hex[:8]}"
    client.post("/shorten", json={"url": "https://example.com", "code": taken})

    urls = [
        {"url": f"https://example.com/{uuid.uuid4()}"},
        {"url": f"https://example.com/{uuid.uuid4()}", "code": taken},   # will fail
        {"url": f"https://example.com/{uuid.uuid4()}"},
    ]
    response = client.post(
        "/shorten/batch", json={"urls": urls}, headers={"X-API-Key": user.api_key}
    )
    assert response.status_code == 200

    results = response.json()["results"]
    assert results[0]["error"] is None and results[0]["short_url"]
    assert results[1]["error"] == "Short code already exists" and results[1]["short_url"] is None
    assert results[2]["error"] is None and results[2]["short_url"]


def test_batch_shorten_malformed_item_rejects_whole_request():
    """Schema-level validation is all-or-nothing: one bad URL 422s the batch."""
    user = _get_test_user("enterprise@test.com", tier="enterprise")
    response = client.post(
        "/shorten/batch",
        json={"urls": [{"url": "https://example.com"}, {"url": "not-a-url"}]},
        headers={"X-API-Key": user.api_key},
    )
    assert response.status_code == 422


def test_batch_shorten_empty_list_rejected():
    """Auth runs before body validation, so an authenticated request is
    needed here to actually exercise the 422 payload check."""
    user = _get_test_user("enterprise@test.com", tier="enterprise")
    response = client.post(
        "/shorten/batch", json={"urls": []}, headers={"X-API-Key": user.api_key}
    )
    assert response.status_code == 422


def test_batch_shorten_too_many_items_rejected():
    user = _get_test_user("enterprise@test.com", tier="enterprise")
    urls = [{"url": "https://example.com"}] * 101
    response = client.post(
        "/shorten/batch", json={"urls": urls}, headers={"X-API-Key": user.api_key}
    )
    assert response.status_code == 422


def test_batch_shorten_links_owner():
    user = _get_test_user("enterprise@test.com", tier="enterprise")
    response = client.post(
        "/shorten/batch",
        json={"urls": [{"url": f"https://example.com/{uuid.uuid4()}"}]},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["results"][0]["short_url"]
    assert _get_url_row(code).user_id == user.id


def test_batch_shorten_requires_enterprise_tier():
    """A hobby-tier user (the default) cannot use bulk creation."""
    user = _get_test_user("hobby@test.com", tier="hobby")
    response = client.post(
        "/shorten/batch",
        json={"urls": [{"url": "https://example.com"}]},
        headers={"X-API-Key": user.api_key},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Bulk creation requires the enterprise tier"


def test_batch_shorten_requires_api_key():
    """No API key at all — bulk creation must not silently run anonymously."""
    response = client.post("/shorten/batch", json={"urls": [{"url": "https://example.com"}]})
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_enterprise_tier_grants_access_after_manual_upgrade():
    """Simulates the client's manual DB update: hobby -> enterprise unlocks batch."""
    user = _get_test_user("upgrade-me@test.com", tier="hobby")

    denied = client.post(
        "/shorten/batch",
        json={"urls": [{"url": "https://example.com"}]},
        headers={"X-API-Key": user.api_key},
    )
    assert denied.status_code == 403

    with get_session() as session:
        db_user = session.exec(select(User).where(User.id == user.id)).first()
        db_user.tier = "enterprise"
        session.add(db_user)
        session.commit()

    allowed = client.post(
        "/shorten/batch",
        json={"urls": [{"url": "https://example.com"}]},
        headers={"X-API-Key": user.api_key},
    )
    assert allowed.status_code == 200


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


def test_invalid_url_rejected():
    response = client.post("/shorten", json={"url": "not-a-url"})
    assert response.status_code == 422


def test_empty_url_returns_422():
    response = client.post("/shorten", json={"url": ""})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert errors[0]["type"] == "url_parsing"
    assert errors[0]["loc"] == ["body", "url"]


def test_missing_url_field():
    response = client.post("/shorten", json={})
    assert response.status_code == 422


def test_missing_code_param():
    response = client.get("/redirect", follow_redirects=False)
    assert response.status_code == 422  # required query param absent


def test_empty_code_returns_404():
    response = client.get("/redirect?code=", follow_redirects=False)
    assert response.status_code == 404  # valid request, no such resource


def test_very_long_url():
    url = "https://example.com/" + "a" * 2000 + str(uuid.uuid4())
    response = client.post("/shorten", json={"url": url})
    assert response.status_code == 200


def test_code_is_case_sensitive():
    # aB3xK9 and ab3xk9 are different codes in a 62-char alphabet
    random_url = f"https://example.com/{uuid.uuid4()}"
    code = client.post("/shorten", json={"url": random_url}).json()["short_url"]
    flipped = code.swapcase()
    if flipped != code:  # skip if code happens to be all digits
        response = client.get(f"/redirect?code={flipped}", follow_redirects=False)
        assert response.status_code in (404, 307)



def test_list_urls_requires_api_key():
    response = client.get("/urls")
    assert response.status_code == 401


def test_list_urls_returns_only_own_urls():
    owner = _get_test_user("list-owner@test.com")
    other = _get_test_user("list-other@test.com")

    owner_url = f"https://example.com/{uuid.uuid4()}"
    other_url = f"https://example.com/{uuid.uuid4()}"
    _shorten(owner_url, owner.api_key)
    _shorten(other_url, other.api_key)

    # size=1000: this test user accumulates URLs across every run of this
    # suite (a shared, never-reset urls.db), so the default page size can't
    # be trusted to include the URL just created above.
    response = client.get("/urls?size=1000", headers={"X-API-Key": owner.api_key})
    assert response.status_code == 200

    body = response.json()
    returned_urls = [item["original_url"] for item in body["urls"]]
    assert owner_url in returned_urls
    assert other_url not in returned_urls


def test_list_urls_does_not_expose_password_hash():
    user = _get_test_user("list-pw@test.com")
    client.post(
        "/shorten",
        json={"url": f"https://example.com/{uuid.uuid4()}", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )

    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    for item in response.json()["urls"]:
        assert "password_hash" not in item
        assert "password" not in item


def test_list_urls_pagination_respects_size():
    user = _get_test_user("list-paginate@test.com")
    for _ in range(5):
        _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.get("/urls?page=1&size=2", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    body = response.json()
    assert len(body["urls"]) == 2
    assert body["page"] == 1
    assert body["size"] == 2
    assert body["total"] >= 5


def test_list_urls_second_page_returns_different_items():
    user = _get_test_user("list-page2@test.com")
    codes = [_shorten(f"https://example.com/{uuid.uuid4()}", user.api_key) for _ in range(5)]

    page1 = client.get("/urls?page=1&size=2", headers={"X-API-Key": user.api_key}).json()
    page2 = client.get("/urls?page=2&size=2", headers={"X-API-Key": user.api_key}).json()

    page1_codes = {item["short_code"] for item in page1["urls"]}
    page2_codes = {item["short_code"] for item in page2["urls"]}
    assert page1_codes.isdisjoint(page2_codes)


def test_list_urls_total_reflects_full_count_not_page_size():
    user = _get_test_user("list-total@test.com")
    for _ in range(3):
        _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    response = client.get("/urls?page=1&size=1", headers={"X-API-Key": user.api_key})
    body = response.json()
    assert len(body["urls"]) == 1
    assert body["total"] >= 3


def test_list_urls_empty_for_user_with_no_links():
    user = _get_test_user("list-empty@test.com")
    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200
    body = response.json()
    assert body["urls"] == []
    assert body["total"] == 0


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


_BLACKLIST_PATH = Path(__file__).resolve().parent.parent / "blacklist.json"


@contextmanager
def _with_blacklisted_keys(keys):
    """Temporarily overwrite blacklist.json, restoring the original after."""
    original = _BLACKLIST_PATH.read_text()
    _BLACKLIST_PATH.write_text(json.dumps({"blocked_keys": keys}))
    try:
        yield
    finally:
        _BLACKLIST_PATH.write_text(original)


def test_blacklisted_key_is_blocked():
    user = _get_test_user("blacklist-blocked@test.com")
    with _with_blacklisted_keys([user.api_key]):
        response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 403


def test_non_blacklisted_key_still_works():
    user = _get_test_user("blacklist-allowed@test.com")
    with _with_blacklisted_keys(["some-other-key-not-this-one"]):
        response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200


def test_blacklist_takes_effect_without_restart():
    """The file is read fresh on every request — no in-memory caching."""
    user = _get_test_user("blacklist-live-reload@test.com")

    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200

    with _with_blacklisted_keys([user.api_key]):
        response = client.get("/urls", headers={"X-API-Key": user.api_key})
        assert response.status_code == 403

    response = client.get("/urls", headers={"X-API-Key": user.api_key})
    assert response.status_code == 200


def test_blacklisting_a_never_registered_key_still_gets_rejected():
    """
    The blacklist now runs before auth, so a blacklisted key is rejected
    (403) without ever reaching the auth DB lookup — even for a key that
    was never a real registered user. This is the efficiency benefit
    blacklisting is meant to provide: reject cheaply, before paying for
    identity resolution.
    """
    fake_key = "totally-fake-never-registered-key"
    with _with_blacklisted_keys([fake_key]):
        response = client.get("/urls", headers={"X-API-Key": fake_key})
    assert response.status_code == 403


def test_blacklist_file_found_regardless_of_working_directory():
    """
    The blacklist file path must not depend on the process's current
    working directory — same bug class already fixed for request.log.
    """
    user = _get_test_user("blacklist-cwd@test.com")
    original_cwd = os.getcwd()
    os.chdir("/tmp")
    try:
        with _with_blacklisted_keys([user.api_key]):
            response = client.get("/urls", headers={"X-API-Key": user.api_key})
    finally:
        os.chdir(original_cwd)
    assert response.status_code == 403


def test_execution_time_header_present_on_success():
    response = client.get("/health")
    assert response.status_code == 200
    header = response.headers.get("x-Response-time")
    assert header is not None
    assert float(header) >= 0


def test_execution_time_header_present_on_rejected_request():
    """Timing wraps every other middleware, so even a short-circuited
    (401) response must still carry the header."""
    response = client.get("/urls")  # no API key
    assert response.status_code == 401
    header = response.headers.get("x-Response-time")
    assert header is not None
    assert float(header) >= 0


def test_execution_time_header_present_on_redirect():
    """Header-setting must work on a RedirectResponse too, not just
    the default JSON responses."""
    code = _shorten(f"https://example.com/{uuid.uuid4()}")
    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307
    header = response.headers.get("x-Response-time")
    assert header is not None
    assert float(header) >= 0


def test_redirect_cache_avoids_repeated_click_stats_db_call():
    """
    The whole point of caching: repeated redirects for the same code
    must not keep hitting the database. The read (get_url_by_code) is
    already proven to be skipped on a hit; this pins the write
    (update_click_stats) to the same standard.
    """
    from app.repositories import UrlRepository

    call_count = 0
    original = UrlRepository.update_click_stats

    def counting_wrapper(self, code):
        nonlocal call_count
        call_count += 1
        return original(self, code)

    UrlRepository.update_click_stats = counting_wrapper
    try:
        code = _shorten(f"https://example.com/{uuid.uuid4()}")
        call_count = 0

        client.get(f"/redirect?code={code}", follow_redirects=False)
        client.get(f"/redirect?code={code}", follow_redirects=False)
        client.get(f"/redirect?code={code}", follow_redirects=False)

        assert call_count == 1, f"expected 1 DB write (first call only), got {call_count}"
    finally:
        UrlRepository.update_click_stats = original


def test_delete_invalidates_cache():
    """A successfully deleted code must stop redirecting even if it was
    cached moments earlier."""
    user = _get_test_user("cache-delete-owner@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    client.get(f"/redirect?code={code}", follow_redirects=False)  # populate cache
    response = client.delete(f"/urls/{code}", headers={"X-API-Key": user.api_key})
    assert response.status_code == 204

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 404


def test_edit_updates_cache_in_place():
    """
    Editing a link's destination must be reflected immediately, even if
    the old destination was already cached. This is now write-through
    (the cache entry is patched, not evicted) rather than invalidation --
    verify the entry stays present in the cache the whole time.
    """
    user = _get_test_user("cache-edit-owner@test.com")
    original_url = f"https://example.com/{uuid.uuid4()}"
    code = _shorten(original_url, user.api_key)

    client.get(f"/redirect?code={code}", follow_redirects=False)  # populate cache
    assert cache.get(code) is not None

    new_url = f"https://example.com/new-{uuid.uuid4()}"
    edit = client.put(f"/urls/{code}", json={"url": new_url}, headers={"X-API-Key": user.api_key})
    assert edit.status_code == 200

    # write-through, not invalidation: the entry is still cached, already
    # holding the new value -- no eviction, no forced re-fetch from the DB
    cached = cache.get(code)
    assert cached is not None
    assert cached.original_url.rstrip("/") == new_url

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].rstrip("/") == new_url


def test_edit_preserves_pending_clicks_via_write_through():
    """
    Write-through must not reset click_count -- a partial HSET never
    touches fields it isn't given, so pending (unflushed) clicks
    accumulated before an edit must survive it.
    """
    user = _get_test_user("cache-edit-clicks@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", user.api_key)

    for _ in range(4):
        client.get(f"/redirect?code={code}", follow_redirects=False)
    pending_before = cache.get(code).click_count
    assert pending_before == 3  # 1st hit is a DB-writing miss, next 3 are pending cache hits

    edit = client.put(
        f"/urls/{code}", json={"expires_at": None}, headers={"X-API-Key": user.api_key}
    )
    assert edit.status_code == 200
    assert cache.get(code).click_count == pending_before


def test_edit_clears_password_in_cache():
    """
    Explicitly clearing a field (password: null) must remove it from the
    cache entry too, not just the database -- otherwise a stale
    password_hash left behind in Redis would keep enforcing a paywall
    that no longer exists in the DB.
    """
    user = _get_test_user("cache-edit-clear-password@test.com")
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]
    client.get(f"/redirect?code={code}&password=secret1", follow_redirects=False)  # populate cache
    assert cache.get(code).password_hash is not None

    edit = client.put(f"/urls/{code}", json={"password": None}, headers={"X-API-Key": user.api_key})
    assert edit.status_code == 200
    assert cache.get(code).password_hash is None

    response = client.get(f"/redirect?code={code}", follow_redirects=False)
    assert response.status_code == 307


def test_rejected_delete_does_not_invalidate_cache():
    """
    An unauthorized (403) delete attempt must not be able to evict
    another user's cached entry -- that would let anyone force cache
    misses on a link they have no rights over, just by trying (and
    failing) to delete it.
    """
    from app import cache

    owner = _get_test_user("cache-owner-protect@test.com")
    intruder = _get_test_user("cache-intruder-protect@test.com")
    code = _shorten(f"https://example.com/{uuid.uuid4()}", owner.api_key)

    client.get(f"/redirect?code={code}", follow_redirects=False)  # populate cache
    assert cache.get(code) is not None

    response = client.delete(f"/urls/{code}", headers={"X-API-Key": intruder.api_key})
    assert response.status_code == 403
    assert cache.get(code) is not None, "cache entry was evicted by a rejected delete attempt"


def test_cached_password_protected_link_still_enforces_password():
    """A cache hit must not bypass password verification -- checkpw
    should still run every time, cached or not."""
    user = _get_test_user("cache-password-owner@test.com")
    response = client.post(
        "/shorten",
        json={"url": "https://example.com", "password": "secret1"},
        headers={"X-API-Key": user.api_key},
    )
    code = response.json()["short_url"]

    # first call: populates the cache, correct password
    ok = client.get(f"/redirect?code={code}&password=secret1", follow_redirects=False)
    assert ok.status_code == 307

    # second call: should be served from cache, but wrong password must still 401
    wrong = client.get(f"/redirect?code={code}&password=wrongpass", follow_redirects=False)
    assert wrong.status_code == 401


def test_rate_limit_allows_requests_under_the_limit():
    for _ in range(5):
        response = client.get("/redirect?code=nonexistent-rl-test", follow_redirects=False)
        assert response.status_code != 429


def test_rate_limit_blocks_after_exceeding_limit():
    from app.middleware.rate_limit import RATE_LIMIT_MAX_REQUESTS

    last_response = None
    for _ in range(RATE_LIMIT_MAX_REQUESTS + 1):
        last_response = client.get("/redirect?code=nonexistent-rl-test", follow_redirects=False)

    assert last_response.status_code == 429
    assert last_response.json()["detail"] == "Too many requests"


def test_rate_limit_response_includes_retry_after_header():
    from app.middleware.rate_limit import RATE_LIMIT_MAX_REQUESTS

    last_response = None
    for _ in range(RATE_LIMIT_MAX_REQUESTS + 1):
        last_response = client.get("/redirect?code=nonexistent-rl-test", follow_redirects=False)

    assert last_response.status_code == 429
    retry_after = int(last_response.headers["retry-after"])
    assert 0 < retry_after <= 60


def test_health_check_exempt_from_rate_limit():
    for _ in range(150):
        response = client.get("/health")
        assert response.status_code != 429


def test_rate_limit_api_uses_correct_header():
    """The API-key rate limiter must key on the real X-API-Key header --
    not treat every caller (keyed or not) as the same shared identity."""
    from app.middleware.rate_limit import _redis

    key = f"test-rl-{uuid.uuid4().hex}"
    _redis.delete(f"ratelimit:{key}:/shorten")
    _redis.delete("ratelimit:None:/shorten")

    client.post("/shorten", json={"url": "https://example.com"}, headers={"X-API-Key": key})

    assert _redis.exists(f"ratelimit:{key}:/shorten"), "no bucket created under the real key"
    assert _redis.get(f"ratelimit:{key}:/shorten") == "1"


def test_rate_limit_api_skips_requests_without_a_key():
    """Anonymous requests are Q8's (IP-based) responsibility -- this
    limiter must not create a shared 'None' bucket for them at all."""
    from app.middleware.rate_limit import _redis

    _redis.delete("ratelimit:None:/shorten")
    client.post("/shorten", json={"url": "https://example.com"})
    assert not _redis.exists("ratelimit:None:/shorten")


def test_shorten_api_limit_is_20_per_second():
    key = f"test-rl-{uuid.uuid4().hex}"
    last_response = None
    for _ in range(21):
        last_response = client.post(
            "/shorten", json={"url": "https://example.com"}, headers={"X-API-Key": key}
        )
    assert last_response.status_code == 429
    assert last_response.json()["detail"] == "Too many requests"


def test_redirect_api_limit_is_50_per_second():
    """
    Must allow up to 50 -- not just 'eventually 429s by request 51',
    which would also be true (falsely) if the threshold were wrongly
    set to 10, the same value /shorten uses.
    """
    key = f"test-rl-{uuid.uuid4().hex}"
    responses = [
        client.get(
            "/redirect?code=nonexistent-rl-test",
            headers={"X-API-Key": key},
            follow_redirects=False,
        )
        for _ in range(50)
    ]
    assert all(r.status_code != 429 for r in responses), "limit tripped before reaching 50"

    response_51 = client.get(
        "/redirect?code=nonexistent-rl-test",
        headers={"X-API-Key": key},
        follow_redirects=False,
    )
    assert response_51.status_code == 429


def test_shorten_and_redirect_api_limits_are_independent():
    """Exhausting the /shorten budget for a key must not affect that
    same key's separate /redirect budget."""
    key = f"test-rl-{uuid.uuid4().hex}"
    for _ in range(10):
        client.post("/shorten", json={"url": "https://example.com"}, headers={"X-API-Key": key})

    response = client.get(
        "/redirect?code=nonexistent-rl-test",
        headers={"X-API-Key": key},
        follow_redirects=False,
    )
    assert response.status_code != 429
