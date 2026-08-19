"""Shared thumbnail-generation logic, used by both the cron job
(scripts/generate_thumbnails.py) and the in-memory task queue -- two
different ways of scheduling the same underlying work.
"""
import logging
from pathlib import Path

from PIL import Image

from app.repositories import UserRepository

THUMBNAIL_SIZE = (300, 300)
THUMBNAIL_DIR = Path(__file__).resolve().parents[1] / 'uploads' / 'thumbnails'

logger = logging.getLogger('thumbnails')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
    logger.addHandler(_handler)


def generate_thumbnail_for_user(user_id: int) -> str:
    """Load the given user's uploaded image, resize it to 300x300, save
    it, and record the new path on the user's row. Raises if the user
    doesn't exist or has no image / the image can't be read -- callers
    decide how to handle that (log-and-skip for the cron job, mark the
    queued task failed for the queue worker)."""
    logger.info('user %d: thumbnail generation started', user_id)
    repository = UserRepository()
    user = repository.get_user_by_id(user_id)
    if user is None or not user.image_path:
        raise ValueError(f'user {user_id} has no uploaded image to generate a thumbnail from')

    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(user.image_path) as image:
        image = image.convert('RGB')
        image = image.resize(THUMBNAIL_SIZE)
        thumbnail_path = THUMBNAIL_DIR / f'{user_id}.jpg'
        image.save(thumbnail_path, format='JPEG')

    repository.set_thumbnail_path(user_id, str(thumbnail_path))
    logger.info('user %d: thumbnail generation finished, saved to %s', user_id, thumbnail_path)
    return str(thumbnail_path)
