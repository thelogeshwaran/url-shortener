import uuid
from datetime import datetime, timedelta

from conftest import _expire_link, _get_url_row, _shorten, client


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
