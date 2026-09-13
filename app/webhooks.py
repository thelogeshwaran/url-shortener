"""Webhooks: HTTP callbacks WE make to notify an external system about
something that happened on our side -- the opposite direction from a
normal API call, where a client calls us. A third-party analytics
service isn't ours to call a function on directly; an HTTP POST to a
URL they gave us is the only integration point that exists.

Failures here must never surface as failures of whatever triggered
them (an image upload, in this app) -- the third party being down or
slow isn't something the original request should have to know or care
about. Retried with backoff for transient failures (reusing
retry_with_backoff, same as the DB retry in repositories.py); a 4xx
means our own request was wrong (bad payload, bad auth) and retrying
with the same data won't help, so that's raised immediately instead.
Logged, not raised, once retries are spent -- analytics delivery
failing shouldn't take down the feature that triggered it.
"""
import logging
import os

import httpx

from app.retry import retry_with_backoff

ANALYTICS_WEBHOOK_URL = os.environ.get('ANALYTICS_WEBHOOK_URL')
WEBHOOK_TIMEOUT_SECONDS = 5

logger = logging.getLogger('webhooks')


class _TransientWebhookError(Exception):
    """A 5xx or connection-level failure -- retryable."""


def _raise_if_transient(response: httpx.Response) -> None:
    if response.status_code >= 500:
        raise _TransientWebhookError(f'{response.status_code} from analytics webhook')
    response.raise_for_status()  # a 4xx propagates immediately -- not retried


@retry_with_backoff((httpx.TransportError, _TransientWebhookError), max_retries=3, base_delay=1, max_delay=10)
def _post_webhook(url: str, payload: dict) -> None:
    response = httpx.post(url, json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS)
    _raise_if_transient(response)


def send_analytics_webhook(event: str, data: dict) -> None:
    """Notify the configured third-party analytics service about
    `event`. A no-op (logged, not raised) if no URL is configured --
    that's a valid deployment state (the integration is optional), not
    an error. A delivery failure after retries is logged, not raised,
    for the same reason: analytics being unreachable must never break
    the feature that generated the event."""
    if not ANALYTICS_WEBHOOK_URL:
        logger.info("no ANALYTICS_WEBHOOK_URL configured, skipping webhook for '%s'", event)
        return

    payload = {'event': event, 'data': data}
    try:
        _post_webhook(ANALYTICS_WEBHOOK_URL, payload)
        logger.info("analytics webhook delivered for '%s'", event)
    except Exception:
        logger.exception("analytics webhook failed for '%s' after retries", event)
