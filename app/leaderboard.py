"""In-memory game leaderboard with WebSocket broadcast.

Polling for leaderboard updates means every client re-asks the server
on a timer, and closing the gap between "poll less often" and
"reasonably fresh" means polling more often -- more requests, more
server load, for data that's usually unchanged. A WebSocket flips this:
each client opens one connection and the server pushes an update only
when something actually changed -- no polling, no wasted requests, and
every connected client sees a new score the moment anyone posts one,
not just their own.
"""
from fastapi import WebSocket

_scores: dict[str, int] = {}  # player -> best score seen so far


def submit_score(player: str, score: int) -> None:
    _scores[player] = max(_scores.get(player, 0), score)


def get_leaderboard(top_n: int = 10) -> list[dict]:
    ranked = sorted(_scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [{'player': player, 'score': score} for player, score in ranked]


class LeaderboardConnectionManager:
    """Tracks every currently-connected WebSocket client so a single
    score submission can be broadcast to all of them at once."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        stale = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                # the client disconnected without a clean close handshake --
                # don't let one dead connection stop the others from
                # getting the update.
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


manager = LeaderboardConnectionManager()
