"""Redis-backed cache for short_code -> redirect target, with write-behind
click tracking: a cache hit increments a counter atomically in Redis
instead of writing to the database immediately; accumulated deltas are
flushed to the database via two independent triggers, whichever fires
first:
- a periodic background task, every FLUSH_INTERVAL_SECONDS (time-based)
- a running count of hits since the last flush, the moment it crosses
  PENDING_FLUSH_THRESHOLD (count-based)

The count-based trigger exists for the opposite failure mode the pure
timer has: under a traffic spike, waiting the full interval could let an
unbounded number of hits pile up between flushes. Crossing the threshold
enqueues a flush onto the same background task queue used elsewhere in
this app (app/task_queue.py) rather than running it inline -- record_hit()
stays a fast, DB-free Redis write either way; the actual flush (and its
DB round trip) always happens off the request path.

Unlike the in-memory dict this replaces, the cache now survives a server
restart (Redis is a separate, persistent process), and click increments
are atomic at the Redis level (HINCRBY), removing the small race the
in-memory version had to accept under concurrent hits on the same code.

Trade-offs still accepted for this exercise:
- If Redis itself is unreachable, cache operations degrade to "always a
  miss" (fall through to the DB) rather than crashing the request -- the
  failure is logged, not silent.
- Clicks accumulated since the last flush are lost if Redis's own data
  is lost (e.g. no persistence configured on the Redis server itself) --
  that's a Redis configuration concern, out of scope here.
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime

import redis

from app import task_queue
from app.repositories import UrlRepository

logger = logging.getLogger('cache')

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
FLUSH_INTERVAL_SECONDS = 30
PENDING_FLUSH_THRESHOLD = 100

_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
_flush_task: asyncio.Task | None = None
_PENDING_HITS_KEY = 'cache:pending_hit_count'


@dataclass
class CacheEntry:
    original_url: str
    expires_at: datetime | None
    deleted_at: datetime | None
    password_hash: str | None
    user_id: int | None
    click_count: int = 0                        # pending, unflushed increments
    last_accessed_at: datetime | None = None


def _key(code: str) -> str:
    return f'url:{code}'


def _to_redis_hash(fields: dict) -> dict:
    """Convert Python values to Redis-hash-safe strings, omitting None
    fields entirely (a hash has no way to store None -- absence means it)."""
    out = {}
    for name, value in fields.items():
        if value is None:
            continue
        out[name] = value.isoformat() if isinstance(value, datetime) else str(value)
    return out


def _from_redis_hash(raw: dict) -> CacheEntry:
    def dt(field):
        return datetime.fromisoformat(raw[field]) if field in raw else None

    return CacheEntry(
        original_url=raw['original_url'],
        expires_at=dt('expires_at'),
        deleted_at=dt('deleted_at'),
        password_hash=raw.get('password_hash'),
        user_id=int(raw['user_id']) if 'user_id' in raw else None,
        click_count=int(raw.get('click_count', 0)),
        last_accessed_at=dt('last_accessed_at'),
    )


def get(code: str) -> CacheEntry | None:
    try:
        raw = _redis.hgetall(_key(code))
    except redis.RedisError:
        logger.exception('Redis unavailable on get() -- treating as a cache miss')
        return None
    return _from_redis_hash(raw) if raw else None


def set(
    code: str,
    original_url: str,
    expires_at: datetime | None,
    deleted_at: datetime | None,
    password_hash: str | None,
    user_id: int | None,
) -> None:
    fields = _to_redis_hash({
        'original_url': original_url,
        'expires_at': expires_at,
        'deleted_at': deleted_at,
        'password_hash': password_hash,
        'user_id': user_id,
    })
    try:
        key = _key(code)
        _redis.delete(key)  # clear any stale fields before writing the fresh set
        _redis.hset(key, mapping=fields)
    except redis.RedisError:
        logger.exception('Redis unavailable on set() -- cache write skipped')


def record_hit(code: str) -> None:
    """Atomically increment the pending click delta for a cache hit --
    no DB call, and HINCRBY means no race even under concurrent hits."""
    try:
        key = _key(code)
        if not _redis.exists(key):
            return
        _redis.hincrby(key, 'click_count', 1)
        _redis.hset(key, 'last_accessed_at', datetime.utcnow().isoformat())

        pending = _redis.incr(_PENDING_HITS_KEY)
        if pending >= PENDING_FLUSH_THRESHOLD:
            # Subtract exactly the threshold, not reset-to-zero -- a hit
            # recorded concurrently, between this check and the flush
            # actually running, must still count toward the *next*
            # threshold instead of being silently dropped (same
            # accounting the per-code counters below already use).
            _redis.decrby(_PENDING_HITS_KEY, PENDING_FLUSH_THRESHOLD)
            task_queue.enqueue(flush_pending_clicks)
    except redis.RedisError:
        logger.exception('Redis unavailable on record_hit() -- click not recorded')


def update_fields(code: str, **fields) -> None:
    """Write-through: patch specific fields of an existing cache entry in
    place, without evicting it or touching click_count/last_accessed_at
    (the pending click delta) at all -- a partial HSET simply never
    touches fields it isn't given, so nothing pending can be lost.

    A field passed as None means "clear it" (HDEL, e.g. removing a
    password or reactivating an expired link); a field simply not passed
    is left untouched. No-op if the code isn't currently cached -- there's
    nothing to write through to, and the next real read will populate a
    fresh, already-correct entry from the database."""
    key = _key(code)
    try:
        if not _redis.exists(key):
            return
        to_set = _to_redis_hash(fields)
        to_clear = [name for name, value in fields.items() if value is None]
        if to_set:
            _redis.hset(key, mapping=to_set)
        if to_clear:
            _redis.hdel(key, *to_clear)
    except redis.RedisError:
        logger.exception('Redis unavailable on update_fields()')


def invalidate(code: str) -> None:
    """Evict a code's cache entry, flushing its pending click delta first
    so a delete/edit doesn't silently discard unflushed clicks. Must be
    called before the corresponding DB mutation, while the row is still
    in its pre-change state -- increment_click_stats requires
    deleted_at IS NULL, which would no longer match once the row itself
    has already been deleted/edited."""
    key = _key(code)
    try:
        raw = _redis.hgetall(key)
        _redis.delete(key)
    except redis.RedisError:
        logger.exception('Redis unavailable on invalidate()')
        return

    if raw:
        pending = int(raw.get('click_count', 0))
        if pending > 0:
            last_accessed = (
                datetime.fromisoformat(raw['last_accessed_at'])
                if 'last_accessed_at' in raw else datetime.utcnow()
            )
            UrlRepository().increment_click_stats(code, pending, last_accessed)


def flush_pending_clicks() -> None:
    """Write every entry's accumulated click delta to the database.
    Subtracts exactly what was flushed (HINCRBY by a negative amount)
    rather than resetting to zero, so a click recorded in the gap
    between reading and flushing isn't lost."""
    try:
        keys = list(_redis.scan_iter(match='url:*'))
    except redis.RedisError:
        logger.exception('Redis unavailable on flush_pending_clicks()')
        return

    repository = UrlRepository()
    for key in keys:
        code = key.split(':', 1)[1]
        try:
            pending = int(_redis.hget(key, 'click_count') or 0)
            if pending <= 0:
                continue
            last_accessed_raw = _redis.hget(key, 'last_accessed_at')
        except redis.RedisError:
            logger.exception('Redis unavailable while flushing %s', code)
            continue

        last_accessed = datetime.fromisoformat(last_accessed_raw) if last_accessed_raw else datetime.utcnow()
        repository.increment_click_stats(code, pending, last_accessed)
        try:
            _redis.hincrby(key, 'click_count', -pending)
        except redis.RedisError:
            logger.exception('Redis unavailable resetting flushed count for %s', code)


async def _periodic_flush_loop() -> None:
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        try:
            flush_pending_clicks()
        except Exception:
            logger.exception('periodic cache flush failed')


def start_periodic_flush() -> None:
    global _flush_task
    _flush_task = asyncio.create_task(_periodic_flush_loop())


async def stop_periodic_flush() -> None:
    global _flush_task
    if _flush_task is not None:
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass
        _flush_task = None
    flush_pending_clicks()  # final flush so a graceful shutdown doesn't lose pending clicks
