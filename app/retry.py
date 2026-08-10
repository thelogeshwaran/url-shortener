"""Retry-with-exponential-backoff for recoverable (transient) failures.

Only pass exception types that are actually transient -- a connection
drop, a timeout -- never ones that mean the request itself is wrong
(bad input, a constraint violation, an auth failure). Retrying those
just reproduces the identical failure and adds load for nothing.

Backoff is exponential with full jitter: each attempt waits a random
delay between 0 and `base_delay * 2**attempt` (capped at `max_delay`).
The jitter matters as much as the exponential growth -- without it,
every caller retrying the same outage backs off on the same fixed
schedule and they all hammer the recovering system in near-lockstep
on attempt 2, attempt 3, etc. (the "hug of death" scenario), instead
of spreading their retries out.
"""
import random
import time
from functools import wraps


def retry_with_backoff(retryable_exceptions, max_retries=3, base_delay=0.5, max_delay=8.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions:
                    attempt += 1
                    if attempt > max_retries:
                        raise
                    delay = random.uniform(0, min(base_delay * (2 ** (attempt - 1)), max_delay))
                    time.sleep(delay)
        return wrapper
    return decorator
