import os

import redis
from fastapi.responses import JSONResponse

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
_redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)

RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60

included_path = [
    '/shorten/batch',
]


async def rate_limit_tier(request, call_next):
    user = getattr(request.state, "user", None)
    if request.url.path not in included_path or user.tier != 'free':
        return await call_next(request)

    key = f'{user.id}:{request.url.path}'

    try:
        count = _redis.incr(key)
        if count == 1:
            _redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        if count > RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={'detail': 'Too many requests'},
            )
    except redis.RedisError:
        pass
    
    return await call_next(request)
