import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text

from app import task_queue
from app.database import get_session
from app.leaderboard import get_leaderboard, manager as leaderboard_manager, sse_manager, submit_score
from app.repositories import UrlRepository, UserRepository
from app.socketio_server import broadcast_leaderboard as socketio_broadcast_leaderboard
from app.schemas import ShortenRequest, ShortenResponse, BatchShortenRequest, BatchShortenResponse, EditUrlRequest, PaginatedUrlsResponse, LookupResponse, ThumbnailStatusResponse, ScoreSubmission
from app.service import UrlService, UserService
from app.models import User

router = APIRouter()

UPLOAD_DIR = Path(__file__).resolve().parents[1] / 'uploads' / 'originals'

async_demo_logger = logging.getLogger('async_demo')
async_demo_logger.setLevel(logging.INFO)
if not async_demo_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
    async_demo_logger.addHandler(_handler)

upload_logger = logging.getLogger('upload')
upload_logger.setLevel(logging.INFO)
if not upload_logger.handlers:
    _upload_handler = logging.StreamHandler()
    _upload_handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
    upload_logger.addHandler(_upload_handler)


def _slow_task(label: str):
    time.sleep(3)
    async_demo_logger.info('%s task finished', label)


def get_service() -> UrlService:
    return UrlService(UrlRepository())
 

def get_user_service() -> UserService:
    return UserService(UserRepository())

    
def get_current_user(
    request: Request,
    user_service: Annotated[UserService, Depends(get_user_service)],
    x_api_key: Annotated[str | None, Header(...)] = None,
):
    state_user = getattr(request.state, "user", None)
    if state_user is not None:
        return state_user
    if not x_api_key:
        return None
    return user_service.get_user_by_api_key(x_api_key)


@router.post('/shorten', response_model=ShortenResponse)
def shorten(
    request: ShortenRequest,
    service: Annotated[UrlService, Depends(get_service)],
    user: Annotated[User | None, Depends(get_current_user)]
) -> ShortenResponse:
    code = service.shorten(request, user)
    return ShortenResponse(short_url=code)


@router.post('/shorten/batch')
def batchShorten(
    request: BatchShortenRequest,
    service: Annotated[UrlService, Depends(get_service)],
    user: Annotated[User | None, Depends(get_current_user)]
) -> BatchShortenResponse:
    return service.batchShorten(request, user)


@router.get('/redirect')
def redirect(code: str, service: Annotated[UrlService, Depends(get_service)], password: str | None = None):
    return service.redirect(code, password)


@router.delete('/urls/{code}', status_code=204)
def delete_url(
    code: str, 
    service: Annotated[UrlService, Depends(get_service)],
    user: Annotated[User | None, Depends(get_current_user)]
) -> None:
    service.delete_url(code, user)


@router.put('/urls/{code}')
def edit_url(
    code: str, 
    request: EditUrlRequest, 
    service: Annotated[UrlService, Depends(get_service)], 
    user: Annotated[User | None, Depends(get_current_user)]
) -> None:
    return service.edit_url(code, request, user)


@router.get('/lookup', response_model=LookupResponse)
def lookup(code: str, service: Annotated[UrlService, Depends(get_service)]) -> LookupResponse:
    """Benchmarking utility: fetch the URL for a code without redirecting,
    tracking clicks, or checking expiry/deleted/password status."""
    return LookupResponse(url=service.lookup(code))


@router.get('/urls')
def get_all_urls_by_user(
    service: Annotated[UrlService, Depends(get_service)],
    user: Annotated[User | None, Depends(get_current_user)],
    page: int = 1,
    size: int = 10
) -> PaginatedUrlsResponse:
    if not user:
        raise HTTPException(status_code=401, detail='Unauthorized')
    return service.get_all_urls_by_user(user.id, page, size)


@router.get('/sync')
def sync_task():
    """Blocking: the slow work happens before we respond, so the caller
    waits the full 3 seconds for the response itself."""
    async_demo_logger.info('/sync request received, starting slow task')
    _slow_task('/sync')
    async_demo_logger.info('/sync returning response')
    return {'message': 'Done'}


@router.get('/async')
def async_task():
    """Non-blocking: the slow work is handed to a background thread and
    we respond immediately -- the task finishes well after the response
    has already gone out, which the log timestamps make visible."""
    async_demo_logger.info('/async request received, spawning background task')
    threading.Thread(target=_slow_task, args=('/async',), daemon=True).start()
    async_demo_logger.info('/async returning response')
    return {'message': 'Accepted'}


@router.post('/users/me/image', status_code=202)
def upload_profile_image(
    user: Annotated[User | None, Depends(get_current_user)],
    file: UploadFile,
):
    """File upload and its follow-up work are decoupled: this saves the
    upload and enqueues an 'image_uploaded' event, then returns
    immediately -- it does not wait for the thumbnail, analytics log, or
    Slack notification the workers run in response. The log timestamps
    show the response going out well before the background workers
    finish."""
    if not user:
        raise HTTPException(status_code=401, detail='Unauthorized')

    upload_logger.info('user %d: upload received (%s)', user.id, file.filename)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    extension = Path(file.filename or '').suffix or '.png'
    image_path = UPLOAD_DIR / f'{user.id}{extension}'
    with open(image_path, 'wb') as f:
        f.write(file.file.read())
    upload_logger.info('user %d: image saved to %s', user.id, image_path)

    UserRepository().set_image_path(user.id, str(image_path))
    task_queue.publish('image_uploaded', user.id)
    upload_logger.info('user %d: image_uploaded event published, returning response', user.id)

    return {'status': 'uploaded', 'image_path': str(image_path)}


LONG_POLL_TIMEOUT_SECONDS = 25
LONG_POLL_INTERVAL_SECONDS = 0.5


def _thumbnail_status_response(db_user: User) -> ThumbnailStatusResponse:
    if db_user.thumbnail_path:
        filename = Path(db_user.thumbnail_path).name
        return ThumbnailStatusResponse(status='done', thumbnail_url=f'/uploads/thumbnails/{filename}')
    return ThumbnailStatusResponse(status='pending')


@router.get('/users/me/thumbnail-status', response_model=ThumbnailStatusResponse)
def get_thumbnail_status(user: Annotated[User | None, Depends(get_current_user)]) -> ThumbnailStatusResponse:
    """Long-polling: instead of answering 'pending' immediately and
    making the client re-ask every second or two, this holds the
    connection open and re-checks the DB itself every
    LONG_POLL_INTERVAL_SECONDS, returning the moment it's done -- or
    after LONG_POLL_TIMEOUT_SECONDS, whichever comes first, so a stuck
    or crashed worker doesn't leave the connection open forever. The
    client just calls this in a loop instead of a plain poll loop; each
    call either returns quickly (done) or after the timeout (still
    pending, call again).

    A user who's never uploaded an image at all is a distinct 404, not
    'pending' -- there's nothing in progress to wait on."""
    if not user:
        raise HTTPException(status_code=401, detail='Unauthorized')

    repository = UserRepository()
    db_user = repository.get_user_by_id(user.id)
    if not db_user.image_path:
        raise HTTPException(status_code=404, detail='No image uploaded')

    deadline = time.time() + LONG_POLL_TIMEOUT_SECONDS
    while not db_user.thumbnail_path and time.time() < deadline:
        time.sleep(LONG_POLL_INTERVAL_SECONDS)
        db_user = repository.get_user_by_id(user.id)

    return _thumbnail_status_response(db_user)


@router.post('/enqueue', status_code=202)
def enqueue_thumbnail_task(user: Annotated[User | None, Depends(get_current_user)]):
    """Queue-based version of the same background-work idea as /async,
    but durable-within-process across many callers instead of one
    thread per request: this publishes an 'image_uploaded' event onto
    the shared queue and the worker pool (started in main.py's
    lifespan) works through it. Deliberately only ever enqueues the
    caller's own account -- not a generic "run any function" endpoint,
    since accepting an arbitrary task/target from the request body
    would be a remote-code-execution hole, not a queue demo."""
    if not user:
        raise HTTPException(status_code=401, detail='Unauthorized')
    task_queue.publish('image_uploaded', user.id)
    return {'status': 'queued', 'user_id': user.id}


@router.post('/scores', status_code=202)
async def post_score(submission: ScoreSubmission):
    """Posting a score updates the in-memory leaderboard and pushes the
    new top-N to every currently-connected client on all three
    transports -- the native WebSocket route, Socket.IO, and SSE -- not
    just the player who posted it. That's the difference from polling:
    this one write fans out to everyone watching, instantly, instead of
    each client separately re-asking on a timer."""
    submit_score(submission.player, submission.score)
    await leaderboard_manager.broadcast({'leaderboard': get_leaderboard()})
    await socketio_broadcast_leaderboard()
    await sse_manager.broadcast({'leaderboard': get_leaderboard()})
    return {'status': 'accepted'}


@router.websocket('/ws/leaderboard')
async def leaderboard_ws(websocket: WebSocket):
    """Bidirectional connection, but this demo only ever pushes server
    -> client: on connect, send the current leaderboard once (so a
    client doesn't have to wait for someone else to score before
    seeing anything); after that, receive_text() just blocks until the
    client disconnects -- there's nothing for the client to say back,
    but the socket has to be read from or the disconnect is never
    noticed."""
    await leaderboard_manager.connect(websocket)
    await websocket.send_json({'leaderboard': get_leaderboard()})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        leaderboard_manager.disconnect(websocket)


@router.get('/sse/leaderboard')
async def leaderboard_sse():
    """One-way only, server -> client, over a plain HTTP response kept
    open rather than a protocol upgrade -- no accept handshake, no
    receiving from the client at all. Simpler than a WebSocket for
    exactly this kind of read-only live feed, at the cost of losing
    the client -> server direction entirely (this app's WS route
    doesn't use that direction either, but a real chat/game input
    channel would need it, and SSE just can't provide it)."""
    queue = sse_manager.subscribe()

    async def event_stream():
        try:
            yield f'data: {json.dumps({"leaderboard": get_leaderboard()})}\n\n'
            while True:
                message = await queue.get()
                yield f'data: {json.dumps(message)}\n\n'
        finally:
            sse_manager.unsubscribe(queue)

    return StreamingResponse(event_stream(), media_type='text/event-stream')


@router.get('/health')
def health():
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unreachable"},
        )
    
