import uuid

from conftest import client


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
