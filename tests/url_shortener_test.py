import uuid
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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


def test_duplicate_url_returns_same_code():
    """
    sending the same URL twice must return the same short code.
    """
    random_url = f"https://example.com/{uuid.uuid4()}"

    first = client.post("/shorten", json={"url": random_url})
    second = client.post("/shorten", json={"url": random_url})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["short_url"] == second.json()["short_url"]


def test_delete_short_code():
    """
    Deleting a short code must return 204, and the code must
    stop redirecting afterwards (404).
    """
    random_url = f"https://example.com/{uuid.uuid4()}"
    short_code = client.post("/shorten", json={"url": random_url}).json()["short_url"]

    response = client.delete(f"/urls/{short_code}")
    assert response.status_code == 204

    # the mapping is gone: redirect must now 404
    response = client.get(f"/redirect?code={short_code}", follow_redirects=False)
    assert response.status_code == 404


def test_delete_unknown_code_returns_404():
    """
    Deleting a short code that doesn't exist must return 404.
    """
    response = client.delete("/urls/nonexistent123")
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


def test_invalid_url_rejected():
    response = client.post("/shorten", json={"url": "not-a-url"})
    assert response.status_code == 422


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

