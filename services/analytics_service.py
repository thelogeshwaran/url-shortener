"""Standalone service: logs every 'image_uploaded' event to analytics.
Its own process, its own Redis connection, its own subscription -- no
shared code path with the other two services beyond RedisPubSub itself.

Run: python3 -m services.analytics_service
"""
import logging
import time

from app.redis_pubsub import RedisPubSub

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(message)s')
logger = logging.getLogger('analytics_service')


def log_upload(user_id: int) -> None:
    """Stand-in for a real analytics call."""
    time.sleep(1)
    logger.info('user %d: upload event logged to analytics', user_id)


pubsub = RedisPubSub()
pubsub.subscribe('image_uploaded', log_upload)


def main() -> None:
    logger.info('subscribed to image_uploaded, listening...')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info('shutting down')


if __name__ == '__main__':
    main()
