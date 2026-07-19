from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.repositories import UrlRepository, UserRepository
from app.schemas import ShortenRequest, ShortenResponse
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
    code = service.shorten(str(request.url), user)
    return ShortenResponse(short_url=code)


@router.get('/redirect')
def redirect(code: str, service: Annotated[UrlService, Depends(get_service)]):
    return service.redirect(code)


@router.delete('/urls/{code}', status_code=204)
def delete_url(
    code: str, 
    service: Annotated[UrlService, Depends(get_service)],
    user: Annotated[User | None, Depends(get_current_user)]
) -> None:
    service.delete_url(code, user)
