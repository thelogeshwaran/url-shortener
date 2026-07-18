from typing import Annotated

from fastapi import APIRouter, Depends

from app.repositories import UrlRepository
from app.schemas import ShortenRequest, ShortenResponse
from app.service import UrlService

router = APIRouter()


def get_service() -> UrlService:
    return UrlService(UrlRepository())


@router.post('/shorten', response_model=ShortenResponse)
def shorten(
    request: ShortenRequest,
    service: Annotated[UrlService, Depends(get_service)]
) -> ShortenResponse:
    code = service.shorten(str(request.url))
    return ShortenResponse(short_url=code)


@router.get('/redirect')
def redirect(code: str, service: Annotated[UrlService, Depends(get_service)]):
    return service.redirect(code)


@router.delete('/urls/{code}', status_code=204)
def delete_url(code: str, service: Annotated[UrlService, Depends(get_service)]) -> None:
    service.delete_url(code)
