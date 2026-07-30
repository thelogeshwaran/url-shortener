from fastapi import HTTPException

from app.utils import generate_code
from app.repositories import UrlRepository, UserRepository
from fastapi import responses
from app.models import User
from app.schemas import ShortenRequest, BatchShortenRequest, BatchShortenResponse, BatchItemResult, EditUrlRequest, PaginatedUrlsResponse
from datetime import datetime
from bcrypt import hashpw, gensalt, checkpw
from app import cache

class UrlService:
    def __init__(self, repository: UrlRepository):
        self.repository = repository

    def shorten(self, urlRequest: ShortenRequest, user: User | None) -> str:
        code = generate_code() if not urlRequest.code else urlRequest.code
        if urlRequest.code and self.repository.code_exists(code):
            raise HTTPException(status_code=409, detail='Short code already exists')
        while self.repository.code_exists(code):
            code = generate_code()
        password_hash = hashpw(urlRequest.password.encode('utf-8'), gensalt()).decode('utf-8') if urlRequest.password else None
        self.repository.save_url(code, str(urlRequest.url), user.id if user else None, urlRequest.expires_at if urlRequest.expires_at else None, password_hash)
        return code
    
    def batchShorten(self, request: BatchShortenRequest, user: User | None) -> BatchShortenResponse:
        results = []
        for req in request.urls:
            try:
                code = self.shorten(req, user)
                results.append(BatchItemResult(url=str(req.url), short_url=code))
            except HTTPException as e:
                results.append(BatchItemResult(url=str(req.url), error=str(e.detail)))
        return BatchShortenResponse(results=results)

    def delete_url(self, code: str, user: User | None) -> None:
        url = self.repository.get_url_by_code(code)
        if url and url.user_id is not None and url.user_id != user.id:
            raise HTTPException(status_code=403, detail='You are not authorized to delete this URL')
        cache.invalidate(code)
        deleted = self.repository.delete_url(code)
        if not deleted:
            raise HTTPException(status_code=404, detail='Short code not found')

    def redirect(self, code: str, password):
        cached = cache.get(code)
        if cached:
            self._check_redirect_validity(cached, password)
            cache.record_hit(code)  # in-memory only -- no DB call on a cache hit
            return responses.RedirectResponse(url=cached.original_url)

        url = self.repository.get_url_by_code(code)
        self._check_redirect_validity(url, password)
        if url:
            cache.set(code, url.original_url, url.expires_at, url.deleted_at, url.password_hash, url.user_id)

        original_url = self.repository.update_click_stats(code)
        if original_url:
            return responses.RedirectResponse(url=original_url)
        raise HTTPException(status_code=404, detail='Short code not found')

    def lookup(self, code: str) -> str:
        """Plain code -> URL fetch, no redirect, no click tracking, no
        expiry/deleted/password checks. Exists to isolate cache
        performance measurements to a single DB read per miss, separate
        from /redirect's extra click-tracking write."""
        cached = cache.get(code)
        if cached:
            return cached.original_url

        url = self.repository.get_url_by_code(code)
        if url is None:
            raise HTTPException(status_code=404, detail='Short code not found')
        cache.set(code, url.original_url, url.expires_at, url.deleted_at, url.password_hash, url.user_id)
        return url.original_url

    def _check_redirect_validity(self, url, password) -> None:
        if (
            url is not None
            and url.deleted_at is None
            and url.expires_at is not None
            and url.expires_at <= datetime.utcnow()
        ):
            raise HTTPException(status_code=410, detail='Short code expired')

        if url and url.deleted_at:
            raise HTTPException(status_code=404, detail='Short code not found')
        if url and url.password_hash and not password:
            raise HTTPException(status_code=401, detail='Password required')
        if url and url.password_hash and password:
            if not checkpw(password.encode('utf-8'), url.password_hash.encode('utf-8')):
                raise HTTPException(status_code=401, detail='Invalid password')

    def edit_url(self, code: str, request: EditUrlRequest, user: User | None) -> None:
        url = self.repository.get_url_by_code(code)
        if url and url.user_id is not None and url.user_id != user.id:
            raise HTTPException(status_code=403, detail='You are not authorized to edit this URL')

        changes = request.model_dump(exclude_unset=True)
        cache.invalidate(code)
        if 'url' in changes:
            changes['original_url'] = str(changes.pop('url'))
        if 'password' in changes:
            new_password = changes.pop('password')
            changes['password_hash'] = hashpw(new_password.encode('utf-8'), gensalt()).decode('utf-8') if new_password else None

        edited = self.repository.update_url(code, changes)
        if not edited:
            raise HTTPException(status_code=404, detail='Short code not found')
        return edited
    
    def get_all_urls_by_user(self, user_id: int, page: int, size: int) -> PaginatedUrlsResponse:
        urls = self.repository.list_urls_by_user(user_id, page, size)
        total = self.repository.count_urls_by_user(user_id)
        return PaginatedUrlsResponse(urls=urls, total=total, page=page, size=size)


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