import uuid

from conftest import _get_test_user, _get_url_row, _shorten, client
from app import cache


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
