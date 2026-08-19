import uuid

from conftest import _get_test_user, client


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


def test_rate_limit_tier_skips_non_free_users():
    """Hobby-tier users are already blocked from /shorten/batch by the
    enterprise gate (authorization.py) on every request -- the free-tier
    rate limiter must not additionally trip a 429 for them, since it isn't
    their limit to enforce."""
    user = _get_test_user("hobby@test.com", tier="hobby")
    for _ in range(7):
        response = client.post(
            "/shorten/batch",
            json={"urls": [{"url": "https://example.com"}]},
            headers={"X-API-Key": user.api_key},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Bulk creation requires the enterprise tier"


def test_free_tier_batch_requests_blocked_after_5_within_window():
    """Pins the actual current behavior: /shorten/batch already returns 403
    for every non-enterprise tier via authorization.py, which runs *after*
    rate_limit_tier -- so the first 5 free-tier requests still surface as
    403 (the enterprise gate), and only request 6+ within the 60s window is
    intercepted earlier as 429 by the tier limiter itself."""
    from app.middleware.rate_limit_tier import _redis as _tier_redis

    user = _get_test_user(f"free-{uuid.uuid4().hex}@test.com", tier="free")
    _tier_redis.delete(f"{user.id}:/shorten/batch")

    responses = [
        client.post(
            "/shorten/batch",
            json={"urls": [{"url": "https://example.com"}]},
            headers={"X-API-Key": user.api_key},
        )
        for _ in range(5)
    ]
    assert all(r.status_code == 403 for r in responses)

    sixth = client.post(
        "/shorten/batch",
        json={"urls": [{"url": "https://example.com"}]},
        headers={"X-API-Key": user.api_key},
    )
    assert sixth.status_code == 429
    assert sixth.json()["detail"] == "Too many requests"


def test_rate_limit_tier_is_scoped_per_user():
    """Two different free-tier users must not share the same budget."""
    from app.middleware.rate_limit_tier import _redis as _tier_redis

    user_a = _get_test_user(f"free-a-{uuid.uuid4().hex}@test.com", tier="free")
    user_b = _get_test_user(f"free-b-{uuid.uuid4().hex}@test.com", tier="free")
    _tier_redis.delete(f"{user_a.id}:/shorten/batch")
    _tier_redis.delete(f"{user_b.id}:/shorten/batch")

    for _ in range(6):
        client.post(
            "/shorten/batch",
            json={"urls": [{"url": "https://example.com"}]},
            headers={"X-API-Key": user_a.api_key},
        )

    # user_a is now over budget; user_b's separate bucket must be untouched
    response = client.post(
        "/shorten/batch",
        json={"urls": [{"url": "https://example.com"}]},
        headers={"X-API-Key": user_b.api_key},
    )
    assert response.status_code == 403  # the enterprise gate, not 429


def test_free_tier_shorten_endpoint_is_not_yet_rate_limited_by_tier_rule():
    """`included_path` in rate_limit_tier.py currently only covers
    /shorten/batch -- which free-tier users are already unconditionally
    blocked from by the enterprise gate, so this rule can never actually
    fire for them there. /shorten and /redirect, the endpoints free users
    can actually call, aren't covered at all yet. This test pins that gap;
    it should start failing (in a good way, prompting an update) once
    /shorten and /redirect are added to included_path."""
    user = _get_test_user(f"free-shorten-{uuid.uuid4().hex}@test.com", tier="free")
    for _ in range(6):
        response = client.post(
            "/shorten",
            json={"url": "https://example.com"},
            headers={"X-API-Key": user.api_key},
        )
        assert response.status_code != 429
