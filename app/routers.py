import logging
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import get_session
from app.repositories import UrlRepository, UserRepository
from app.schemas import ShortenRequest, ShortenResponse, BatchShortenRequest, BatchShortenResponse, EditUrlRequest, PaginatedUrlsResponse, LookupResponse
from app.service import UrlService, UserService
from app.models import User

router = APIRouter()

async_demo_logger = logging.getLogger('async_demo')
async_demo_logger.setLevel(logging.INFO)
if not async_demo_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter('%(asctime)s | %(message)s'))
    async_demo_logger.addHandler(_handler)


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
    
