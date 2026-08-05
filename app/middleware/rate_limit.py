"""IP-based rate limiting: a fixed-window counter per client IP, backed
by Redis, that resets automatically via TTL.

Fixed-window by design, matching "basic" rate limiting -- a client can
burst up to ~2x the limit across a window boundary (e.g. 100 requests in
the last second of one window plus 100 in the first second of the next).
A sliding-window or token-bucket algorithm closes that gap at the cost of
real extra complexity; out of scope here.

request.client.host is the direct peer's IP -- behind a reverse proxy or
CDN, every caller would appear to share the proxy's IP. A production
deployment behind one would need to read X-Forwarded-For instead (with
the caveat that it's spoofable unless the trusted proxy overwrites it).
"""
import os

import redis
from fastapi.responses import JSONResponse

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)

RATE_LIMIT_MAX_REQUESTS = 100
RATE_LIMIT_WINDOW_SECONDS = 60
SHORTEN_RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_API_WINDOW_SECONDS = 1
REDIRECT_RATE_LIMIT_MAX_REQUESTS = 50

exclued_path = [
    "/health",
]

included_path = [
    '/shorten',
    '/redirect'
]


async def rate_limit(request, call_next):
    if request.url.path in exclued_path:
        return await call_next(request)

    ip = request.client.host if request.client else 'unknown'
    key = f'ratelimit:{ip}'

    try:
        count = _redis.incr(key)
        if count == 1:
            _redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)  # only on the window's first request

        if count > RATE_LIMIT_MAX_REQUESTS:
            ttl = _redis.ttl(key)
            retry_after = ttl if ttl and ttl > 0 else RATE_LIMIT_WINDOW_SECONDS
            return JSONResponse(
                status_code=429,
                content={'detail': 'Too many requests'},
                headers={'Retry-After': str(retry_after)},
            )
    except redis.RedisError:
        # fail open: a Redis outage must not block all traffic, same
        # philosophy as blacklist.py and cache.py
        pass

    return await call_next(request)


async def rate_limit_api(request, call_next):
    api_key = request.headers.get('x-api-key')
    if request.url.path not in included_path or not api_key:
        return await call_next(request)

    key = f'ratelimit:{api_key}:{request.url.path}'

    limit = 0
    limit_window = RATE_LIMIT_API_WINDOW_SECONDS

    if request.url.path == '/shorten':
        limit = SHORTEN_RATE_LIMIT_MAX_REQUESTS
    elif request.url.path == '/redirect':
        limit = REDIRECT_RATE_LIMIT_MAX_REQUESTS

    try:
        count = _redis.incr(key)
        if count == 1:
            _redis.expire(key, limit_window)  # only on the window's first request

        if count > limit:
            ttl = _redis.ttl(key)
            retry_after = ttl if ttl and ttl > 0 else RATE_LIMIT_API_WINDOW_SECONDS
            return JSONResponse(
                status_code=429,
                content={'detail': 'Too many requests'},
                headers={'Retry-After': str(retry_after)},
            )
    except redis.RedisError:
        # fail open: a Redis outage must not block all traffic, same
        # philosophy as blacklist.py and cache.py
        pass

    return await call_next(request)