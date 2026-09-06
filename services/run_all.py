"""Single entry point that runs all three "image_uploaded" subscribers
in one process -- cheaper to deploy than three separate services (one
Render Background Worker instead of three), at the cost of losing
independent scaling/restarts: a fatal crash in this one process takes
all three subscriptions down together, unlike truly separate services.

Importing each module registers its subscription and starts its own
RedisPubSub/listener thread (same as tests/conftest.py does) -- so this
process ends up with 3 independent threads, each with its own Redis
connection, same as before, just co-located.

Run: python3 -m services.run_all
"""
import logging
import time

import services.analytics_service  # noqa: F401
import services.notification_service  # noqa: F401
import services.thumbnail_service  # noqa: F401

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(message)s')
logger = logging.getLogger('run_all')


def main() -> None:
    logger.info('all 3 subscribers registered (thumbnail, analytics, notification), listening...')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info('shutting down')


if __name__ == '__main__':
    main()
