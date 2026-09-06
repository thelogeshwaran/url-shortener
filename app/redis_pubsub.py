"""Redis-backed Pub/Sub. Same subscribe(event, fn)/publish(event, data)
interface as app/pubsub.py's in-memory PubSub -- but messages travel
through Redis instead of a local dict, so publish() and subscribe()
can live in entirely separate processes.

What this actually buys you, and what it doesn't:

- Gained: a subscriber running in a different process (a separate
  worker service, a different machine entirely) sees events published
  from this one, as long as they share the same Redis instance. The
  in-memory PubSub could never do that -- it only ever existed inside
  one Python process's memory.

- NOT gained: durability. Plain Redis Pub/Sub buffers or persists
  nothing -- if publish() fires while no one is subscribed yet (a
  listener hasn't started, or is momentarily reconnecting), that
  message is gone forever, silently. The in-memory version at least
  survived a busy worker for as long as the process itself stayed up;
  this swap trades that away for cross-process reach. Redis *Streams*
  (a different data structure, not Pub/Sub) is what adds real
  persistence and replay -- "Redis makes it production-ready" is only
  true with that caveat attached.

- A gotcha at real scale: Pub/Sub is a broadcast, not a work queue.
  If two separate processes both subscribe to 'image_uploaded' to
  split load the way this app's old worker1/worker2 did, *both*
  processes get *every* message -- duplicate processing, not shared
  processing. Splitting work across many workers needs Redis Streams'
  consumer groups (or Kafka, SQS, etc.), not plain Pub/Sub.
"""
import json
import logging
import os
import threading

import redis

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

logger = logging.getLogger('redis_pubsub')


class RedisPubSub:
    def __init__(self, redis_url: str = REDIS_URL):
        self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        self._subscribers: dict[str, list] = {}
        self._thread = None
        self._lock = threading.Lock()

    def subscribe(self, event: str, fn) -> None:
        self._subscribers.setdefault(event, []).append(fn)
        # redis-py maps one callback per channel; re-subscribing on every
        # call just replaces it with an equivalent one, since the callback
        # always reads the current list from self._subscribers rather than
        # closing over a snapshot -- harmless to call subscribe() N times
        # for N handlers on the same event.
        self._pubsub.subscribe(**{event: self._dispatch(event)})
        self._ensure_listening()

    def publish(self, event: str, data) -> None:
        """Fire-and-forget: hands off to Redis and returns immediately,
        whether or not anyone is subscribed."""
        self._redis.publish(event, json.dumps(data))

    def _dispatch(self, event: str):
        def handle_message(message):
            try:
                data = json.loads(message['data'])
            except (TypeError, ValueError):
                data = message['data']
            for fn in self._subscribers.get(event, []):
                try:
                    fn(data)
                except Exception:
                    logger.exception('subscriber %s failed for event %r', getattr(fn, '__name__', fn), event)
        return handle_message

    def _ensure_listening(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = self._pubsub.run_in_thread(sleep_time=0.01, daemon=True)
