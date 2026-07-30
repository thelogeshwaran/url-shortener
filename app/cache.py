"""In-memory cache for short_code -> redirect target, with write-behind
click tracking: a cache hit increments an in-memory delta instead of
writing to the database immediately; a periodic background task flushes
accumulated deltas to the database on an interval.

Trade-offs accepted for this exercise:
- Lost on server restart (no persistence).
- Per-process only -- not shared across multiple worker processes.
- Clicks accumulated since the last flush are lost on a hard crash (a
  graceful shutdown does one final flush to minimize this).
- The increment below isn't lock-protected; a lost increment under heavy
  concurrent hits on the exact same code is a theoretical, accepted risk
  for this exercise.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from app.repositories import UrlRepository

logger = logging.getLogger('cache')

FLUSH_INTERVAL_SECONDS = 30


@dataclass
class CacheEntry:
    original_url: str
    expires_at: datetime | None
    deleted_at: datetime | None
    password_hash: str | None
    user_id: int | None
    click_count: int = 0                        # pending, unflushed increments
    last_accessed_at: datetime | None = None


_cache: dict[str, CacheEntry] = {}
_flush_task: asyncio.Task | None = None


def get(code: str) -> CacheEntry | None:
    return _cache.get(code)


def set(
    code: str,
    original_url: str,
    expires_at: datetime | None,
    deleted_at: datetime | None,
    password_hash: str | None,
    user_id: int | None,
) -> None:
    _cache[code] = CacheEntry(
        original_url=original_url,
        expires_at=expires_at,
        deleted_at=deleted_at,
        password_hash=password_hash,
        user_id=user_id,
    )


def record_hit(code: str) -> None:
    """Increment the in-memory click delta for a cache hit -- no DB call."""
    entry = _cache.get(code)
    if entry is not None:
        entry.click_count += 1
        entry.last_accessed_at = datetime.utcnow()


def invalidate(code: str) -> None:
    """Evict a code's cache entry, flushing its pending click delta first
    so a delete/edit doesn't silently discard unflushed clicks. Must be
    called before the corresponding DB mutation, while the row is still
    in its pre-change state -- increment_click_stats requires
    deleted_at IS NULL, which would no longer match once the row itself
    has already been deleted/edited."""
    entry = _cache.pop(code, None)
    if entry is not None and entry.click_count > 0:
        UrlRepository().increment_click_stats(code, entry.click_count, entry.last_accessed_at)


def flush_pending_clicks() -> None:
    """Write every entry's accumulated click delta to the database and
    reset it to zero. Safe to call even when nothing is pending."""
    repository = UrlRepository()
    for code, entry in list(_cache.items()):
        if entry.click_count > 0:
            repository.increment_click_stats(code, entry.click_count, entry.last_accessed_at)
            entry.click_count = 0


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
