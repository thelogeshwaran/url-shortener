import uuid

from conftest import _get_test_user, client


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
