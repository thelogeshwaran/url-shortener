import time
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app import task_queue
from app.database import get_session
from app.main import app
from app.middleware.rate_limit import _redis as _rate_limit_redis
from app.models import Url, User

client = TestClient(app)

# TestClient only runs FastAPI's lifespan startup (which starts the queue's
# background worker) when used as a context manager -- plain module-level
# instantiation, as used everywhere else in this test suite, never fires it.
# Start the worker explicitly so queue-dependent tests don't hang forever
# waiting on a thread that was never running.
task_queue.start_worker()


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """
    TestClient always reports the same fake peer IP ("testclient") for
    every request across the whole suite -- without resetting this
    between tests, the shared counter would accumulate across every
    other test in the suite and start returning 429s for unrelated
    tests long before any rate-limit test itself ever runs.
    """
    _rate_limit_redis.delete('ratelimit:testclient')
    yield


def _get_url_row(code: str) -> Url | None:
    """Read a row straight from the DB to verify side effects."""
    with get_session() as session:
        return session.exec(select(Url).where(Url.short_code == code)).first()


def _get_user_row(user_id: int) -> User | None:
    with get_session() as session:
        return session.exec(select(User).where(User.id == user_id)).first()


def _wait_until(condition, timeout=5, interval=0.05) -> bool:
    """Poll a background task's result instead of asserting on it
    immediately -- the queue worker runs on its own thread, so there's
    no way to know from the request/response alone when it's done."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def _make_test_image_bytes(size=(50, 50), color=(200, 100, 50)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new('RGB', size, color=color).save(buf, format='PNG')
    return buf.getvalue()


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


def _expire_link(code: str) -> None:
    """Force a link into the past -- no sleep() needed."""
    with get_session() as session:
        url = session.exec(select(Url).where(Url.short_code == code)).first()
        url.expires_at = datetime.utcnow() - timedelta(hours=1)
        session.add(url)
        session.commit()
