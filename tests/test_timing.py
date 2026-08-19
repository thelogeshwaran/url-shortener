from conftest import _shorten, client
import uuid


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
