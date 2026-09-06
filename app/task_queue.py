"""A minimal in-memory Pub/Sub simulation: one shared queue carries
events tagged with a name (`{"event": ..., "data": ...}`) instead of
being tied to a single flow. Two background worker threads both pull
from the same queue and dispatch each event to whichever handlers are
registered for it in `_SUBSCRIBERS`.

This replaces two earlier, worse versions of the same idea:
- One queue, one hardcoded fan-out of 3 calls per event (fine for one
  flow, but every concern had to know about the other two).
- Three separate queues, one per concern (works, but a new flow means
  a new queue *and* a new worker thread -- doesn't scale to 10 flows).

Adding a new flow now means adding an entry to `_SUBSCRIBERS` -- no new
queue, no new thread. That's the actual point of naming events instead
of routing by queue: publishers don't need to know who (or how many
handlers) are listening, and listeners don't need their own queue.

Still just a simulation, not real Pub/Sub -- same limits as before:
- In-memory, single-process: events are lost on restart, and this
  doesn't scale across multiple server instances.
- Queue.get() is thread-safe, so even with two workers sharing one
  queue, each event still goes to exactly one of them, never both.
- Fixed at two workers; a burst of slow events still queues up behind
  whichever worker frees first.

A real system would reach for Redis Pub/Sub or Streams, RabbitMQ, or
Kafka -- durable, shared across processes, with proper consumer groups.
"""
import logging
import queue
import threading
import time

from app.thumbnails import generate_thumbnail_for_user

logger = logging.getLogger('task_queue')

event_queue: "queue.Queue" = queue.Queue()


def publish(event: str, data) -> None:
    """Announce that `event` happened, carrying `data` -- the publisher
    doesn't know or care which handlers (if any) are subscribed."""
    event_queue.put({'event': event, 'data': data})


def log_upload(user_id: int) -> None:
    """Stand-in for a real analytics call."""
    time.sleep(1)
    logger.info('user %d: upload event logged to analytics', user_id)


def notify_admin(user_id: int) -> None:
    """Stand-in for a real Slack webhook call."""
    time.sleep(2)
    logger.info('user %d: admin notified via Slack', user_id)


# event name -> handlers to run, in order, whenever that event is
# published. A new flow is a new dict entry, not a new queue/thread.
_SUBSCRIBERS = {
    'image_uploaded': [generate_thumbnail_for_user, log_upload, notify_admin],
}


def _run_worker(worker_name: str) -> None:
    while True:
        task = event_queue.get()
        event, data = task['event'], task['data']
        handlers = _SUBSCRIBERS.get(event, [])
        logger.info('%s picked up event %r for %r (%d subscriber(s))', worker_name, event, data, len(handlers))
        for handler in handlers:
            try:
                handler(data)
            except Exception:
                logger.exception("%s: handler %s failed for event %r (%r)", worker_name, handler.__name__, event, data)
        event_queue.task_done()


def worker1() -> None:
    _run_worker('worker1')


def worker2() -> None:
    _run_worker('worker2')


def start_worker() -> None:
    threading.Thread(target=worker1, daemon=True).start()
    threading.Thread(target=worker2, daemon=True).start()
