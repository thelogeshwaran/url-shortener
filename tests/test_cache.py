import uuid

from conftest import _get_test_user, _shorten, client
from app import cache


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
