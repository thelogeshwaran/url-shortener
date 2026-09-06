"""Standalone service: generates a thumbnail whenever 'image_uploaded'
is published. Its own process, its own Redis connection, its own
subscription -- completely independent of the main web app and of the
other two services below. Kill this one and uploads still get logged
to analytics and Slack still gets notified; only thumbnails stop.

Run: python3 -m services.thumbnail_service
"""
import logging
import time

from app.redis_pubsub import RedisPubSub
from app.thumbnails import generate_thumbnail_for_user

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(message)s')
logger = logging.getLogger('thumbnail_service')

pubsub = RedisPubSub()
pubsub.subscribe('image_uploaded', generate_thumbnail_for_user)


def main() -> None:
    logger.info('subscribed to image_uploaded, listening...')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info('shutting down')


if __name__ == '__main__':
    main()
