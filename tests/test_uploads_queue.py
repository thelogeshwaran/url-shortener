import uuid
from pathlib import Path

from PIL import Image
from sqlmodel import select

from conftest import _get_test_user, _get_user_row, _make_test_image_bytes, _wait_until, client
from app.database import get_session
from app.models import User


def test_upload_image_requires_api_key():
    response = client.post(
        "/users/me/image",
        files={"file": ("avatar.png", _make_test_image_bytes(), "image/png")},
    )
    assert response.status_code == 401


def test_upload_image_returns_immediately_and_thumbnail_follows_in_background():
    user = _get_test_user(f"upload-{uuid.uuid4().hex}@test.com")

    response = client.post(
        "/users/me/image",
        headers={"X-API-Key": user.api_key},
        files={"file": ("avatar.png", _make_test_image_bytes(), "image/png")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["image_path"].endswith(f"{user.id}.png")

    # The upload response doesn't wait on thumbnail generation -- but the
    # background worker should complete it shortly after.
    assert _wait_until(lambda: _get_user_row(user.id).thumbnail_path is not None), \
        "thumbnail was never generated in the background"

    row = _get_user_row(user.id)
    with Image.open(row.thumbnail_path) as thumbnail:
        assert thumbnail.size == (300, 300)


def test_upload_image_resets_stale_thumbnail():
    """A fresh upload invalidates any thumbnail generated from the
    previous image -- otherwise a re-upload would keep showing the old
    picture's thumbnail until the next cron/queue run happens to touch it."""
    user = _get_test_user(f"reupload-{uuid.uuid4().hex}@test.com")
    with get_session() as session:
        db_user = session.exec(select(User).where(User.id == user.id)).first()
        db_user.image_path = "uploads/originals/stale.png"
        db_user.thumbnail_path = "uploads/thumbnails/stale.jpg"
        session.add(db_user)
        session.commit()

    client.post(
        "/users/me/image",
        headers={"X-API-Key": user.api_key},
        files={"file": ("avatar.png", _make_test_image_bytes(), "image/png")},
    )

    # The stale value must be gone -- either cleared to None synchronously
    # (the common case) or already replaced by a fresh one if the
    # background worker was fast enough to finish first. Asserting
    # strictly None here would race against that legitimate success case.
    assert _get_user_row(user.id).thumbnail_path != "uploads/thumbnails/stale.jpg"


def test_enqueue_requires_api_key():
    response = client.post("/enqueue")
    assert response.status_code == 401


def test_enqueue_generates_thumbnail_for_an_already_uploaded_image():
    user = _get_test_user(f"enqueue-{uuid.uuid4().hex}@test.com")
    image_path = Path(f"/tmp/enqueue-test-{uuid.uuid4().hex}.png")
    image_path.write_bytes(_make_test_image_bytes())

    with get_session() as session:
        db_user = session.exec(select(User).where(User.id == user.id)).first()
        db_user.image_path = str(image_path)
        db_user.thumbnail_path = None
        session.add(db_user)
        session.commit()

    response = client.post("/enqueue", headers={"X-API-Key": user.api_key})
    assert response.status_code == 202
    assert response.json() == {"status": "queued", "user_id": user.id}

    assert _wait_until(lambda: _get_user_row(user.id).thumbnail_path is not None), \
        "thumbnail was never generated in the background"

    image_path.unlink(missing_ok=True)


def test_task_queue_worker_survives_a_failing_task():
    """A single background worker thread processes every queued task --
    if an uncaught exception ever killed that thread, every task queued
    after the failing one would silently never run again. This proves
    the worker isolates failures per-task instead."""
    from app.task_queue import enqueue
    from app.thumbnails import generate_thumbnail_for_user

    broken_user = _get_test_user(f"broken-{uuid.uuid4().hex}@test.com")
    with get_session() as session:
        db_user = session.exec(select(User).where(User.id == broken_user.id)).first()
        db_user.image_path = None  # generate_thumbnail_for_user will raise for this user
        session.add(db_user)
        session.commit()
    enqueue(generate_thumbnail_for_user, broken_user.id)

    good_user = _get_test_user(f"recovers-{uuid.uuid4().hex}@test.com")
    image_path = Path(f"/tmp/worker-recovery-test-{uuid.uuid4().hex}.png")
    image_path.write_bytes(_make_test_image_bytes())
    with get_session() as session:
        db_user = session.exec(select(User).where(User.id == good_user.id)).first()
        db_user.image_path = str(image_path)
        db_user.thumbnail_path = None
        session.add(db_user)
        session.commit()
    enqueue(generate_thumbnail_for_user, good_user.id)

    assert _wait_until(lambda: _get_user_row(good_user.id).thumbnail_path is not None), \
        "worker thread appears to have died after the earlier task's failure"

    image_path.unlink(missing_ok=True)
