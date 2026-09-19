"""Q10: the same pipeline as app/reliable_queue.py + app/dead_letter_notifier.py
-- retry on failure, exponential backoff, dead-letter after too many
failures, email alert -- rebuilt on Celery instead of hand-rolled Redis
primitives. Kept as its own file rather than replacing the original so
the two can be compared side by side.

What Celery gives you for free (all of this was hand-rolled before):
- The queue itself: `.delay()` serializes the call and pushes it onto a
  broker queue; no manual RPUSH/BLPOP/json.dumps.
- Attempt tracking: `self.request.retries` -- no envelope dict to carry
  the count ourselves.
- Backoff: `retry_backoff`/`retry_backoff_max`/`retry_jitter` on the
  decorator replace app/reliable_queue.py's sorted-set-plus-promoter-
  thread entirely. Celery still has no "not visible until time T" queue
  primitive under the hood -- it schedules the retry via the broker's
  own ETA/countdown support -- but none of that plumbing is something
  we have to write or reason about anymore.
- Workers: `celery -A app.celery_queue worker` replaces start_worker()
  and its manual daemon-thread-plus-while-True-loop.

What's still on us, same as before:
- Celery has no built-in "dead-letter queue" concept. After
  max_retries is exhausted, the task just ends up in a FAILURE state --
  where that failure actually goes (a queue, a table, an alert) is
  still something we have to wire up ourselves, via the task_failure
  signal below. Celery's retry is also not automatically idempotency-
  safe -- a message being retried can still double-run a step that
  wasn't safe to repeat, exactly the same problem Q6 covers.
- The actual email-sending is reused as-is from app/dead_letter_notifier
  (notify_developer) rather than reimplemented -- Celery has no opinion
  about what a "dead letter" should do once it happens.

Run a worker:   celery -A app.celery_queue worker --loglevel=info
Enqueue a test: python3 -c "from app.celery_queue import process_message; process_message.delay('test-message')"
"""
import logging
import os

from celery import Celery
from celery.signals import task_failure
from dotenv import load_dotenv

load_dotenv()  # standalone entry point -- see services/reliable_queue_worker.py for why this matters

from app.dead_letter_notifier import notify_developer

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
MAIN_QUEUE = 'celery_tasks'  # deliberately distinct from reliable_queue.py's 'tasks' --
                              # both are plain Redis lists, and Celery's message format
                              # (its own headers/properties envelope) isn't compatible
                              # with the hand-rolled JSON envelope the other pipeline uses
DEAD_LETTER_QUEUE = 'celery_tasks:dead'  # not a real Celery queue -- just a label passed
                                          # through to notify_developer() for the alert email
MAX_RETRIES = 4  # retries AFTER the first attempt -- 5 total tries, same total as reliable_queue.py's MAX_RETRIES=5

logger = logging.getLogger('celery_queue')

celery_app = Celery('reliable_queue', broker=REDIS_URL)
celery_app.conf.task_default_queue = MAIN_QUEUE


def _real_work(message_id: str) -> None:
    """Same always-fails placeholder as services/reliable_queue_worker.py's
    handler -- swap this out for the real task logic. Guarantees every test
    message travels all the way through retry -> backoff -> dead-letter -> email."""
    raise NotImplementedError(f'no real handler wired up yet for {message_id!r}')


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=1,       # base delay in seconds -- doubles each retry: 1, 2, 4, 8
    retry_backoff_max=60,
    retry_jitter=False,     # off to match reliable_queue.py's deterministic doubling;
                             # leave this on (the default) in most real deployments --
                             # jitter is what avoids every failed message retrying in lockstep
    max_retries=MAX_RETRIES,
)
def process_message(self, message_id: str) -> None:
    _real_work(message_id)


@task_failure.connect(sender=process_message)
def _on_permanently_failed(sender=None, task_id=None, args=None, kwargs=None, einfo=None, **_kw):
    """Fires exactly once per message: only on the final failure once
    retries are exhausted, never on the intermediate ones (a `Retry` is
    Celery's own control flow, not a task failure) -- the same one-shot
    guarantee app/reliable_queue.py's attempt-count check gives."""
    message_id = args[0] if args else kwargs.get('message_id')
    attempt = sender.request.retries + 1
    logger.error("giving up on %r after %d attempts -- dead-lettering", message_id, attempt)
    send_dead_letter_alert.delay(message_id, attempt)


@celery_app.task
def send_dead_letter_alert(message_id: str, attempt: int) -> None:
    notify_developer(DEAD_LETTER_QUEUE, message_id, attempt)
