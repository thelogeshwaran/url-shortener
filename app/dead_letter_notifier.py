"""Watches a dead-letter queue and emails a developer for every message
that lands there -- the "this now needs a human" escalation once Q8's
retries are exhausted.

A separate worker, not something the last retry worker does inline
while pushing to dead-letter -- see the chat response for why. Uses
Resend (resend.com) as the concrete API; the shape (one POST, a bearer
token, a JSON body) is nearly identical across Resend/Postmark/Brevo,
so swapping providers is a few-line change, not a redesign.

A message is only removed from the dead-letter queue once its alert
has actually been sent. If the email fails even after its own
retries, the message goes right back onto the dead-letter queue rather
than vanishing -- the same "never silently lose a message" guarantee
from Q1, now extended one step further: a broken notification channel
must not make the underlying problem invisible too.
"""
import json
import logging
import os
import threading
import time

import httpx

from app.reliable_queue import BLOCK_TIMEOUT_SECONDS, _redis
from app.retry import retry_with_backoff

RESEND_API_URL = 'https://api.resend.com/emails'
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
ALERT_FROM_EMAIL = os.environ.get('DEAD_LETTER_ALERT_FROM', 'alerts@example.com')
ALERT_TO_EMAIL = os.environ.get('DEAD_LETTER_ALERT_TO')

RETRY_ON_SEND_FAILURE_DELAY_SECONDS = 1

logger = logging.getLogger('dead_letter_notifier')


class _TransientEmailError(Exception):
    """A 5xx or connection-level failure from the email API -- retryable."""


def _raise_if_transient(response: httpx.Response) -> None:
    if response.status_code >= 500:
        raise _TransientEmailError(f'{response.status_code} from Resend')
    response.raise_for_status()  # a 4xx (bad API key, bad payload) propagates immediately -- not retried


@retry_with_backoff((httpx.TransportError, _TransientEmailError), max_retries=3, base_delay=1, max_delay=10)
def _send_email(subject: str, body: str) -> None:
    response = httpx.post(
        RESEND_API_URL,
        headers={'Authorization': f'Bearer {RESEND_API_KEY}'},
        json={'from': ALERT_FROM_EMAIL, 'to': ALERT_TO_EMAIL, 'subject': subject, 'text': body},
        timeout=10,
    )
    _raise_if_transient(response)


def notify_developer(queue_name: str, message_id: str, attempt: int) -> bool:
    """Best-effort: retries transient email failures a few times, but
    never raises. Returns True if the alert was actually sent, False
    otherwise -- the caller decides what happens to the message based
    on that; this function's only job is sending the email."""
    if not RESEND_API_KEY or not ALERT_TO_EMAIL:
        logger.warning(
            "dead-letter alert for %r skipped -- RESEND_API_KEY/DEAD_LETTER_ALERT_TO not configured",
            message_id,
        )
        return False

    subject = f"[url-shortener] message {message_id!r} dead-lettered after {attempt} attempts"
    body = (
        f"Queue: {queue_name}\n"
        f"Message ID: {message_id}\n"
        f"Attempts: {attempt}\n\n"
        "This message could not be processed and needs manual intervention."
    )
    try:
        _send_email(subject, body)
        logger.info("dead-letter alert emailed for %r", message_id)
        return True
    except Exception:
        logger.critical("FAILED TO SEND DEAD-LETTER ALERT for %r -- this needs manual attention", message_id, exc_info=True)
        return False


def start_dead_letter_worker(dead_letter_queue_name: str) -> threading.Thread:
    """Its own dedicated loop, not reliable_queue.start_worker() --
    that helper's handler only ever receives the bare message id, but
    a useful alert needs the attempt count too, so this reads the
    envelope directly instead."""
    def loop():
        while True:
            popped = _redis.blpop(dead_letter_queue_name, timeout=BLOCK_TIMEOUT_SECONDS)
            if popped is None:
                continue
            envelope = json.loads(popped[1])
            sent = notify_developer(dead_letter_queue_name, envelope['id'], envelope['attempt'])
            if not sent:
                _redis.rpush(dead_letter_queue_name, popped[1])  # keep it visible, never just drop it
                time.sleep(RETRY_ON_SEND_FAILURE_DELAY_SECONDS)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
