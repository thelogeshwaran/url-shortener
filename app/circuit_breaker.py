"""A simple circuit breaker: after too many consecutive failures calling
a flaky dependency, stop calling it at all for a cooldown period and
fail immediately instead. This protects two things retrying alone
doesn't -- the caller (no more piling up slow, doomed attempts against
a database that's already down) and the struggling dependency itself
(no more load arriving from callers who will never get a good answer
until it recovers on its own).

Three states, tracked per decorated function:
- CLOSED (normal): calls go through; failures are counted.
- OPEN: the failure threshold was hit -- every call fails instantly
  with CircuitBreakerOpen, without ever touching the real function,
  until `reset_timeout` has elapsed.
- HALF-OPEN (implicit): once the cooldown elapses, the next call is
  let through as a trial. Success resets back to CLOSED; failure
  reopens the circuit for another full cooldown.
"""
import time
from functools import wraps


class CircuitBreakerOpen(Exception):
    """Raised in place of the real call while the circuit is open."""


def circuit_breaker(expected_exception, failure_threshold=3, reset_timeout=10):
    def decorator(func):
        state = {'failures': 0, 'opened_at': None}

        @wraps(func)
        def wrapper(*args, **kwargs):
            if state['opened_at'] is not None:
                if time.time() - state['opened_at'] < reset_timeout:
                    raise CircuitBreakerOpen(
                        f"{func.__name__} circuit is open -- failing fast without calling it"
                    )
                # cooldown elapsed: let this one call through as a trial (half-open)

            try:
                result = func(*args, **kwargs)
            except expected_exception:
                state['failures'] += 1
                if state['failures'] >= failure_threshold:
                    state['opened_at'] = time.time()  # trip (or re-trip) the breaker
                raise
            else:
                state['failures'] = 0
                state['opened_at'] = None
                return result

        return wrapper
    return decorator
