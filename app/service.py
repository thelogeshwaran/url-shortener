from fastapi import HTTPException

from app.utils import generate_code
from app.repositories import UrlRepository


class UrlService:
    def __init__(self, repository: UrlRepository):
        self.repository = repository

    def shorten(self, url: str) -> str:
        existing = self.repository.get_code_by_url(url)
        if existing:
            return existing
        code = generate_code()
        while self.repository.code_exists(code):
            code = generate_code()
        self.repository.save_url(code, url)
        return code

    def delete_url(self, code: str) -> None:
        deleted = self.repository.delete_url(code)
        if not deleted:
            raise HTTPException(status_code=404, detail='Short code not found')

    def get_original_url(self, code: str) -> str:
        url = self.repository.get_url_by_code(code)
        if url is None:
            raise HTTPException(status_code=404, detail='Short code not found')
        return url
