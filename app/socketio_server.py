"""Socket.IO version of the leaderboard broadcast -- the same feature
as app/leaderboard.py's native `WS /ws/leaderboard`, reimplemented over
Socket.IO instead of a raw WebSocket.

What Socket.IO gives you that the native version had to hand-build, or
doesn't have at all:

- No manual connection roster. LeaderboardConnectionManager exists
  purely because a raw WebSocket server has no built-in idea of "every
  client currently connected" -- we had to track that list and iterate
  it ourselves, pruning dead entries on send failure. Socket.IO's
  server already tracks every connected client; sio.emit(event, data)
  with no `to=`/`room=` broadcasts to everyone, for free.

- Named events instead of raw text/JSON. The native version sends
  bare JSON blobs and the client has to know what shape to expect on
  every message. Socket.IO multiplexes named events
  ('leaderboard_update', 'submit_score', ...) over one connection --
  closer to a pub/sub bus than a single anonymous byte stream.

- Genuine bidirectionality without a separate REST call. The native
  route only ever pushes server -> client; submitting a score still
  goes through `POST /scores`. Here, `submit_score` below lets a
  Socket.IO client emit a score directly over the same connection.

- Automatic reconnection and transport fallback (client-side). A
  native WebSocket that fails to connect (a corporate proxy blocking
  the Upgrade handshake, a flaky network) just fails -- reconnecting
  and picking a different transport is code you'd write yourself. The
  Socket.IO client retries with backoff automatically and falls back
  to HTTP long-polling if a real WebSocket can't be established, all
  transparent to application code.

- A built-in heartbeat (ping/pong) to detect dead connections, instead
  of only noticing a client is gone when a send() to it throws.

None of this is free at the protocol level, though -- see this
module's own docstring comparison notes in the chat response for the
cost side (a non-standard protocol on the wire, a larger client
dependency, and a Socket.IO client can't be replaced with `curl` or a
bare WebSocket client the way the native route can).
"""
import socketio

from app.leaderboard import get_leaderboard
from app.leaderboard import submit_score as _submit_score

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')


@sio.event
async def connect(sid, environ):
    # Same reasoning as the native route: a newly connected client
    # shouldn't have to wait for someone else to score before seeing
    # the current leaderboard.
    await sio.emit('leaderboard_update', {'leaderboard': get_leaderboard()}, to=sid)


@sio.event
async def submit_score(sid, data):
    """A client can emit a score directly over the socket -- no
    separate POST /scores call needed, unlike the native WS route."""
    _submit_score(data['player'], data['score'])
    await broadcast_leaderboard()


async def broadcast_leaderboard() -> None:
    """Broadcast to every connected Socket.IO client. No connection
    list to maintain by hand -- omitting `to=`/`room=` already means
    "everyone", since the server tracks connected clients internally."""
    await sio.emit('leaderboard_update', {'leaderboard': get_leaderboard()})
