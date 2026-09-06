"""Publisher side of the "image_uploaded" event -- and nothing else.

This module used to also own the three handlers (thumbnail, analytics
log, Slack notification) and subscribe them itself. Now each of those
lives in its own standalone service under services/, each with its own
Redis connection, subscribing independently:

    python3 -m services.thumbnail_service
    python3 -m services.analytics_service
    python3 -m services.notification_service

The web app has zero knowledge of how many services are listening, or
what they do -- it only ever calls publish(). That's the whole point:
a new concern (a warehouse service, an invoice service, whatever) is a
new standalone file that subscribes on its own -- nothing here changes.
"""
from app.redis_pubsub import RedisPubSub

pubsub = RedisPubSub()


def publish(event: str, data) -> None:
    """Announce that `event` happened, carrying `data` -- the publisher
    doesn't know or care which services (if any) are subscribed."""
    pubsub.publish(event, data)
