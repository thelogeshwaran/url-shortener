from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import get_session
from app.repositories import UrlRepository, UserRepository
from app.schemas import ShortenRequest, ShortenResponse, BatchShortenRequest, BatchShortenResponse, EditUrlRequest, PaginatedUrlsResponse
from app.service import UrlService, UserService
from app.models import User

router = APIRouter()


def get_service() -> UrlService:
    return UrlService(UrlRepository())
 

def get_user_service() -> UserService:
    return UserService(UserRepository())


def get_current_user(
    user_service: Annotated[UserService, Depends(get_user_service)],
    x_api_key: Annotated[str | None, Header(...)] = None
): 
    if not x_api_key:
        return None
    user = user_service.get_user_by_api_key(x_api_key)
    return user
    

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
    user: Annotated[User | None, Depends(get_current_user)]) -> None:
    return service.edit_url(code, request, user)


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
    
