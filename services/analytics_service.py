"""Standalone service: notifies the third-party analytics service every
time 'image_uploaded' fires, via webhook. Its own process, its own
Redis connection, its own subscription -- no shared code path with the
other two services beyond RedisPubSub itself.

Run: python3 -m services.analytics_service
"""
import logging
import time

from app.redis_pubsub import RedisPubSub
from app.webhooks import send_analytics_webhook

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(message)s')
logger = logging.getLogger('analytics_service')


def log_upload(user_id: int) -> None:
    """Real analytics call: POSTs a webhook to the configured
    third-party service. Delivery failures are logged inside
    send_analytics_webhook itself and never raised here -- analytics
    being unreachable must not look like this service crashed."""
    send_analytics_webhook('image_uploaded', {'user_id': user_id})


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
