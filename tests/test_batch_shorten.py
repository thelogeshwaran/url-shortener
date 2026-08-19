import uuid

from conftest import _get_test_user, _get_url_row, client
from app.database import get_session
from app.models import User
from sqlmodel import select


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
