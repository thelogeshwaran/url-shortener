"""Standalone entry point: starts the full reliable-queue pipeline --
main worker, retry worker, delay promoter, and dead-letter notifier --
as one long-running process. These all run as daemon threads inside
whatever process calls start_worker()/start_delay_promoter()/
start_dead_letter_worker(); this script's only job is to call all
four and then stay alive so those threads don't die with it.

Deploy this as a Render Background Worker (not a Web Service, not a
Cron Job) -- same reasoning as services/thumbnail_service.py etc.:
this needs to run forever, not answer HTTP requests or run on a
schedule.

Run: python3 -m services.reliable_queue_worker
"""
import logging
import time

from dotenv import load_dotenv

# standalone entry point -- unlike app/main.py, nothing else in this process's
# import chain loads .env, so RESEND_API_KEY/REDIS_URL/etc would otherwise
# silently read as None/defaults regardless of .env
load_dotenv()

from app.dead_letter_notifier import start_dead_letter_worker
from app.reliable_queue import start_delay_promoter, start_worker

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(name)s | %(message)s')
logger = logging.getLogger('reliable_queue_worker')

MAIN_QUEUE = 'tasks'
RETRY_QUEUE = 'tasks:retry'
DEAD_LETTER_QUEUE = 'tasks:dead'


def handler(message_id: str) -> None:
    """Replace this with the real task logic -- whatever `message_id`
    actually identifies in your app (an order to fulfil, a report to
    generate, ...). This placeholder exists only so the pipeline below
    is runnable as-is, and always fails, so you can see a message
    travel all the way through retry -> backoff -> dead-letter -> email."""
    raise NotImplementedError(f'no real handler wired up yet for {message_id!r}')


def main() -> None:
    start_worker(MAIN_QUEUE, handler, retry_queue_name=RETRY_QUEUE, dead_letter_queue_name=DEAD_LETTER_QUEUE)
    start_worker(RETRY_QUEUE, handler, dead_letter_queue_name=DEAD_LETTER_QUEUE)
    start_delay_promoter(RETRY_QUEUE)
    start_dead_letter_worker(DEAD_LETTER_QUEUE)
    logger.info('reliable queue pipeline running: %s -> %s -> %s', MAIN_QUEUE, RETRY_QUEUE, DEAD_LETTER_QUEUE)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info('shutting down')


if __name__ == '__main__':
    main()
