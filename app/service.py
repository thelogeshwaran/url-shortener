from fastapi import HTTPException

from app.utils import generate_code
from app.repositories import UrlRepository
from fastapi import responses


class UrlService:
    def __init__(self, repository: UrlRepository):
        self.repository = repository

    def shorten(self, url: str) -> str:
        code = generate_code()
        while self.repository.code_exists(code):
            code = generate_code()
        self.repository.save_url(code, url)
        return code

    def delete_url(self, code: str) -> None:
        deleted = self.repository.delete_url(code)
        if not deleted:
            raise HTTPException(status_code=404, detail='Short code not found')

    def redirect(self, code: str):
        original_url = self.repository.update_click_stats(code)
        if not original_url:
            raise HTTPException(status_code=404, detail='Short code not found')
        return responses.RedirectResponse(url=original_url)