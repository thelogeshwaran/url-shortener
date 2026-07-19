from fastapi import HTTPException

from app.utils import generate_code
from app.repositories import UrlRepository, UserRepository
from fastapi import responses
from app.models import User


class UrlService:
    def __init__(self, repository: UrlRepository):
        self.repository = repository

    def shorten(self, url: str, user: User | None) -> str:
        code = generate_code()
        while self.repository.code_exists(code):
            code = generate_code()
        self.repository.save_url(code, url, user.id if user else None)
        return code

    def delete_url(self, code: str, user: User | None) -> None:
        if user is None:
            raise HTTPException(status_code=401, detail='API key required')
        url = self.repository.get_url_by_code(code)
        if url and url.user_id is not None and url.user_id != user.id:
            raise HTTPException(status_code=403, detail='You are not authorized to delete this URL')
        deleted = self.repository.delete_url(code)
        if not deleted:
            raise HTTPException(status_code=404, detail='Short code not found')

    def redirect(self, code: str):
        original_url = self.repository.update_click_stats(code)
        if not original_url:
            raise HTTPException(status_code=404, detail='Short code not found')
        return responses.RedirectResponse(url=original_url)


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    def get_user_by_api_key(self, api_key: str) -> User | None:
        if not api_key:
            return None
        user = self.repository.get_user_by_api_key(api_key)
        if not user:
            raise HTTPException(status_code=401, detail='Invalid API key')
        return user