"""Standalone service: notifies the admin on Slack for every
'image_uploaded' event. Its own process, its own Redis connection, its
own subscription -- independent of the other two services.

Run: python3 -m services.notification_service
"""
import logging
import time

from app.redis_pubsub import RedisPubSub

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(message)s')
logger = logging.getLogger('notification_service')


def notify_admin(user_id: int) -> None:
    """Stand-in for a real Slack webhook call."""
    time.sleep(2)
    logger.info('user %d: admin notified via Slack', user_id)


pubsub = RedisPubSub()
pubsub.subscribe('image_uploaded', notify_admin)


def main() -> None:
    logger.info('subscribed to image_uploaded, listening...')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info('shutting down')


if __name__ == '__main__':
    main()
