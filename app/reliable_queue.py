"""A durable FIFO queue (a Redis list) with a worker that never
silently drops a message on failure.

Contrast with app/redis_pubsub.py: Pub/Sub has no persistence at all --
a message with no listener, or a failing handler, is just gone, with
no queue to even put it back into. A Redis list is a real queue:
RPUSH enqueues, LPOP/BLPOP dequeues, and critically, the list lives in
Redis itself -- independent of this worker process. If the worker
restarts, whatever's still in the list is untouched, exactly as the
reading describes. The only thing that can still be lost is a message
that was already popped off (handed to the worker) when something
goes wrong processing it -- which is exactly the gap this closes:
catch the failure and re-enqueue it before moving on, rather than
letting one bad message vanish while the worker keeps running.

Re-enqueuing onto the *same* queue (Q1's version) traded one problem
for two others:
- Order: a failing message keeps cycling back into the queue it just
  came from, interleaved with everything after it.
- Infinite loop risk: if the failure is the message's own fault (bad
  data) rather than a transient worker-side blip (a network drop),
  retrying it changes nothing -- it fails again, gets re-enqueued
  again, forever, right in the middle of the main pipeline.

The fix (Q2): a failing message goes to a *separate* retry queue with
its *own* worker, instead of back onto the main queue. The main
queue's throughput for everything else is no longer affected by one
message's retries at all. A message that's truly, permanently corrupt
can still loop forever -- but now that loop is confined to the retry
queue, not blocking the main pipeline. (Breaking that remaining loop
needs a retry limit / dead-letter queue -- a problem for later.)

Q4 chains this one level further: two retries instead of one, by
pointing each queue's `retry_queue_name` at the next queue in line --
main -> retry_1 -> retry_2 -- rather than any queue re-enqueuing onto
itself. No code changes were needed for this; `retry_queue_name`
already generalizes to any number of stages, one start_worker() call
per queue:

    start_worker(MAIN,    handler, retry_queue_name=RETRY_1)
    start_worker(RETRY_1, handler, retry_queue_name=RETRY_2)
    start_worker(RETRY_2, handler)  # no further queue -- falls back to itself, same as Q2

A message now gets up to 3 total attempts (main, retry_1, retry_2)
before it's stuck cycling in retry_2 alone -- still not a true retry
*limit* (nothing stops retry_2 itself from looping forever on a
permanently-bad message), just one more stage of isolation before that
happens.

Two *actual* workers, not one function called twice in a row: start_worker()
spins up its own daemon thread running an unbounded `while True` loop,
blocked on BLPOP (so it costs nothing while idle, no busy-polling).
Call it once for the main queue and once for the retry queue and you
get two independent threads, each waiting on its own queue,
genuinely concurrent -- not a bounded batch call followed by another
bounded batch call afterward.
"""
import logging
import os
import threading

import redis

logger = logging.getLogger('reliable_queue')

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)

BLOCK_TIMEOUT_SECONDS = 1  # how long BLPOP waits before checking again -- keeps the loop stoppable, not a busy-loop


def enqueue(queue_name: str, message_id: str) -> None:
    _redis.rpush(queue_name, message_id)


def _process_one(queue_name: str, handler, retry_queue_name: str | None) -> bool:
    """Pop and process exactly one message, if there is one. Returns
    False (nothing to do) when the queue was empty for the whole
    BLOCK_TIMEOUT_SECONDS wait -- callers use that to stop looping in
    the bounded/batch case, or just to loop again in the always-on case."""
    target = retry_queue_name or queue_name
    popped = _redis.blpop(queue_name, timeout=BLOCK_TIMEOUT_SECONDS)
    if popped is None:
        return False
    _, message_id = popped
    try:
        handler(message_id)
    except Exception:
        logger.exception("failed to process %r from %r -- moving to %r, not lost", message_id, queue_name, target)
        _redis.rpush(target, message_id)
    return True


def run_worker(queue_name: str, handler, max_iterations: int, retry_queue_name: str | None = None) -> None:
    """Bounded, one-shot: processes up to `max_iterations` messages (or
    until the queue is empty) then returns. Useful for tests/demos, but
    calling this twice in a row for two different queues is still just
    one thread doing two things sequentially -- see start_worker() for
    two actually-concurrent workers."""
    for _ in range(max_iterations):
        if not _process_one(queue_name, handler, retry_queue_name):
            break


def start_worker(queue_name: str, handler, retry_queue_name: str | None = None) -> threading.Thread:
    """Unbounded and always-on: runs in its own daemon thread until the
    process exits. Call this once per queue (main and retry) to get
    two genuinely independent, concurrently-running workers, each
    blocked on its own queue via BLPOP rather than busy-polling."""
    def loop():
        while True:
            _process_one(queue_name, handler, retry_queue_name)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
