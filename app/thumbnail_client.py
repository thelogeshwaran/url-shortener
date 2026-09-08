"""Basic long-polling client for GET /users/me/thumbnail-status.

Calls the status endpoint in a loop:
- status == 'done'    -> return it, we're finished.
- status == 'pending' -> the server's own long-poll timeout just fired
  (a normal response, not a failure) -- call again immediately.
- the request itself fails (dropped connection, timeout, 5xx) -- retry
  with exponential backoff instead of hammering the server right away.
- gives up after max_attempts request failures, or after
  max_wait_seconds of total polling time, whichever comes first.
"""
import time

import httpx

MAX_RETRY_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 15

# Not a retry delay -- a floor. Normally the server's own ~25s long-poll
# hold already paces re-polling; this only matters if it ever answers
# 'pending' fast (a proxy that doesn't hold the connection, a bug).
# Without it, a fast 'pending' response busy-loops this client as tight
# as the network allows.
MIN_SECONDS_BETWEEN_POLLS = 0.5


class ThumbnailNotReadyError(Exception):
    """The thumbnail still wasn't done when max_wait_seconds ran out."""


def poll_until_done(base_url: str, api_key: str, max_wait_seconds: float = 120) -> dict:
    deadline = time.time() + max_wait_seconds
    failures = 0

    while time.time() < deadline:
        poll_started_at = time.time()
        try:
            response = httpx.get(
                f'{base_url}/users/me/thumbnail-status',
                headers={'X-API-Key': api_key},
                timeout=30,
            )
            response.raise_for_status()
        except (httpx.TransportError, httpx.HTTPStatusError):
            failures += 1
            if failures > MAX_RETRY_ATTEMPTS:
                raise
            backoff = min(BASE_BACKOFF_SECONDS * (2 ** (failures - 1)), MAX_BACKOFF_SECONDS)
            time.sleep(backoff)
            continue

        failures = 0  # that request succeeded, so past failures don't count anymore
        body = response.json()
        if body['status'] == 'done':
            return body
        # status == 'pending' -- expected, not a failure. Poll again right
        # away, but never faster than MIN_SECONDS_BETWEEN_POLLS.
        elapsed = time.time() - poll_started_at
        if elapsed < MIN_SECONDS_BETWEEN_POLLS:
            time.sleep(MIN_SECONDS_BETWEEN_POLLS - elapsed)

    raise ThumbnailNotReadyError(f'thumbnail still not ready after {max_wait_seconds}s of polling')
