"""Cron job: generate 300x300 thumbnails for users who've uploaded a
profile image but don't have one yet.

Meant to be run on a schedule, not imported -- e.g. a crontab entry like:
    * * * * * cd /path/to/url-shortener && venv/bin/python scripts/generate_thumbnails.py >> logs/thumbnails.log 2>&1

Each user is handled independently: a bad/missing/corrupt image for one
user is logged and skipped rather than aborting the whole run, and its
thumbnail_path is simply left NULL so the next run picks it up again
once the underlying file is fixed.
"""
import logging
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repositories import UserRepository  # noqa: E402

THUMBNAIL_SIZE = (300, 300)
THUMBNAIL_DIR = Path(__file__).resolve().parents[1] / 'uploads' / 'thumbnails'

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')
logger = logging.getLogger('generate_thumbnails')


def generate_thumbnail(image_path: str, user_id: int) -> str:
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        image = image.convert('RGB')
        image = image.resize(THUMBNAIL_SIZE)
        thumbnail_path = THUMBNAIL_DIR / f'{user_id}.jpg'
        image.save(thumbnail_path, format='JPEG')
    return str(thumbnail_path)


def run() -> None:
    repository = UserRepository()
    users = repository.get_users_pending_thumbnail()
    logger.info('found %d user(s) needing a thumbnail', len(users))

    succeeded, failed = 0, 0
    for user in users:
        try:
            thumbnail_path = generate_thumbnail(user.image_path, user.id)
            repository.set_thumbnail_path(user.id, thumbnail_path)
            logger.info('user %d: thumbnail saved to %s', user.id, thumbnail_path)
            succeeded += 1
        except Exception:
            logger.exception('user %d: failed to generate thumbnail from %s', user.id, user.image_path)
            failed += 1

    logger.info('done: %d succeeded, %d failed', succeeded, failed)


if __name__ == '__main__':
    run()
