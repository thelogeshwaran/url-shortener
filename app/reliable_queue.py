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

Q2/Q4 chained separate queues per retry level (main -> retry_1 ->
retry_2 -> ...). That doesn't scale: 5 retries means 6 queues, 6
start_worker() calls to wire up correctly, 6 things to monitor, and
"actually we want 10 retries" means restructuring code, not changing a
number. It also has no notion of "how many times has this actually
been tried" as data -- that's only ever implicit in which queue a
message currently happens to sit in.

Q5's fix: track the attempt count *on the message itself* -- the same
idea SQS's ApproximateReceiveCount or Celery's `max_retries` use --
instead of encoding it as position in a chain of queues. One retry
queue, one MAX_RETRIES constant, and a dead-letter queue for whatever's
still failing after that many attempts. "Retry 5 times" and "retry 50
times" now cost the exact same amount of infrastructure -- change a
number, not the queue topology. And because the message never lands
anywhere without moving on -- either retried, or dead-lettered -- the
Q1 guarantee (never silently lose a message) holds even once retries
run out; it just stops being *this* queue's problem and becomes
something a human/dashboard can go look at in the dead-letter queue.

Two *actual* workers, not one function called twice in a row:
start_worker() spins up its own daemon thread running an unbounded
`while True` loop, blocked on BLPOP (so it costs nothing while idle,
no busy-polling). Call it once for the main queue and once for the
retry queue and you get two independent threads, each waiting on its
own queue, genuinely concurrent -- not a bounded batch call followed
by another bounded batch call afterward.

Q7 adds exponential backoff before a retry becomes available at all --
1s before the 1st retry, 2s before the 2nd, 4s before the 3rd, doubling
each time. A plain Redis list can't express "not poppable until time T"
-- RPUSH makes an entry available immediately, always. So a failing
message doesn't go straight back onto the retry queue; it goes into a
sorted set (`<queue>:delayed`) scored by the timestamp it's allowed to
be retried, and a separate small "promoter" loop moves anything whose
time has come from that sorted set onto the real queue via RPUSH. This
is the standard way to do delayed re-delivery on top of Redis (the
same idea Sidekiq/RQ's scheduled-job sets use) -- there's no native
"deliver this later" primitive, so a sorted set doubling as a timer is
what stands in for one. The ZREM-then-RPUSH pairing, keyed on ZREM's
own return value, is what stops the same due entry from being promoted
twice if this were ever run with more than one promoter.
"""
import json
import logging
import os
import threading
import time

import redis

logger = logging.getLogger('reliable_queue')

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)

BLOCK_TIMEOUT_SECONDS = 1  # how long BLPOP waits before checking again -- keeps the loop stoppable, not a busy-loop
MAX_RETRIES = 5  # total attempts across main + retry queue before giving up to dead-letter
BACKOFF_BASE_SECONDS = 1  # 1st retry waits 1s, 2nd waits 2s, 3rd waits 4s, ... (2**(attempt-1))
DELAYED_SUFFIX = ':delayed'
PROMOTER_POLL_INTERVAL_SECONDS = 0.2


def enqueue(queue_name: str, message_id: str) -> None:
    _requeue(queue_name, message_id, attempt=0)


def _requeue(queue_name: str, message_id: str, attempt: int) -> None:
    _redis.rpush(queue_name, json.dumps({'id': message_id, 'attempt': attempt}))


def _requeue_with_backoff(queue_name: str, message_id: str, attempt: int) -> float:
    """Instead of an immediate RPUSH, park the message in a sorted set
    scored by when it's allowed to become available again. Returns the
    delay actually used, so callers can log it."""
    delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    payload = json.dumps({'id': message_id, 'attempt': attempt})
    _redis.zadd(f'{queue_name}{DELAYED_SUFFIX}', {payload: time.time() + delay})
    return delay


def _promote_due_delayed(queue_name: str) -> int:
    """Move any delayed entries for `queue_name` whose backoff has
    elapsed onto the real queue. Safe to call from more than one
    promoter: ZREM only succeeds for whoever gets there first, so the
    RPUSH after it only ever happens once per entry."""
    delayed_key = f'{queue_name}{DELAYED_SUFFIX}'
    due = _redis.zrangebyscore(delayed_key, '-inf', time.time())
    promoted = 0
    for payload in due:
        if _redis.zrem(delayed_key, payload):
            _redis.rpush(queue_name, payload)
            promoted += 1
    return promoted


def start_delay_promoter(queue_name: str) -> threading.Thread:
    """Runs forever, moving due delayed retries into `queue_name` once
    their backoff has elapsed. Start one of these per queue that ever
    receives backed-off retries (i.e. wherever _requeue_with_backoff
    targets)."""
    def loop():
        while True:
            _promote_due_delayed(queue_name)
            time.sleep(PROMOTER_POLL_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def _process_one(
    queue_name: str,
    handler,
    retry_queue_name: str | None = None,
    dead_letter_queue_name: str | None = None,
) -> bool:
    """Pop and process exactly one message, if there is one. Returns
    False (nothing to do) when the queue was empty for the whole
    BLOCK_TIMEOUT_SECONDS wait -- callers use that to stop looping in
    the bounded/batch case, or just to loop again in the always-on case.

    The attempt count travels with the message (not with the queue),
    so it survives being moved between queues -- unlike Q4's chain,
    where "how many times has this failed" was only ever implicit in
    which queue the message happened to be sitting in."""
    dead_letter = dead_letter_queue_name or f'{queue_name}:dead'
    popped = _redis.blpop(queue_name, timeout=BLOCK_TIMEOUT_SECONDS)
    if popped is None:
        return False

    envelope = json.loads(popped[1])
    message_id, attempt = envelope['id'], envelope['attempt']

    try:
        handler(message_id)
    except Exception:
        attempt += 1
        if attempt >= MAX_RETRIES:
            logger.exception("giving up on %r after %d attempts -- moving to dead-letter %r", message_id, attempt, dead_letter)
            _requeue(dead_letter, message_id, attempt)
        else:
            target = retry_queue_name or queue_name
            delay = _requeue_with_backoff(target, message_id, attempt)
            logger.exception(
                "failed to process %r (attempt %d/%d) -- retrying via %r in %gs",
                message_id, attempt, MAX_RETRIES, target, delay,
            )
    return True


def run_worker(
    queue_name: str,
    handler,
    max_iterations: int,
    retry_queue_name: str | None = None,
    dead_letter_queue_name: str | None = None,
) -> None:
    """Bounded, one-shot: processes up to `max_iterations` messages (or
    until the queue is empty) then returns. Useful for tests/demos, but
    calling this twice in a row for two different queues is still just
    one thread doing two things sequentially -- see start_worker() for
    two actually-concurrent workers."""
    for _ in range(max_iterations):
        if not _process_one(queue_name, handler, retry_queue_name, dead_letter_queue_name):
            break


def start_worker(
    queue_name: str,
    handler,
    retry_queue_name: str | None = None,
    dead_letter_queue_name: str | None = None,
) -> threading.Thread:
    """Unbounded and always-on: runs in its own daemon thread until the
    process exits. Call this once per queue (main and retry) to get
    two genuinely independent, concurrently-running workers, each
    blocked on its own queue via BLPOP rather than busy-polling."""
    def loop():
        while True:
            _process_one(queue_name, handler, retry_queue_name, dead_letter_queue_name)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
