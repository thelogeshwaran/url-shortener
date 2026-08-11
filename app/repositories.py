from datetime import datetime, timezone
from sqlalchemy.exc import OperationalError
from sqlmodel import or_, select, update

from app.circuit_breaker import circuit_breaker
from app.database import get_session
from app.models import Url, User
from app.retry import retry_with_backoff


class UrlRepository:
    @retry_with_backoff(OperationalError, max_retries=3, base_delay=0.5, max_delay=8.0)
    @circuit_breaker(OperationalError, failure_threshold=3, reset_timeout=10)
    def save_url(
        self,
        code: str,
        long_url: str,
        user_id: int | None = None,
        expires_at: datetime | None = None,
        password_hash: str | None = None
    ) -> None:
        with get_session() as session:
            session.add(Url(
                short_code=code,
                original_url=long_url,
                user_id=user_id,
                expires_at=expires_at,
                password_hash=password_hash
            ))
            session.commit()

    def get_url_by_code(self, code: str) -> Url | None:
        with get_session() as session:
            stmt = select(Url).where(Url.short_code == code)
            url = session.exec(stmt).first()
            return url
    
    def update_url(self, code: str, changes: dict) -> bool:
        with get_session() as session:
            stmt = (update(Url)
                    .where(Url.short_code == code, Url.deleted_at == None)
                    .values(**changes)
                    .returning(Url.id))
            result = session.exec(stmt).first()
            session.commit()
            return result is not None
          
    def delete_url(self, code: str) -> bool:
        with get_session() as session:
            stmt = (update(Url)
                    .where(Url.short_code == code, Url.deleted_at == None)
                    .values(deleted_at=datetime.now(timezone.utc))
                    .returning(Url.id))
            result = session.exec(stmt).first()
            session.commit()
            return result is not None

    def code_exists(self, code: str) -> bool:
        with get_session() as session:
            stmt = select(Url).where(Url.short_code == code)
            url = session.exec(stmt).first()
            return url is not None

    def update_click_stats(self, code: str) -> str | None:
        with get_session() as session:
            stmt = (
                update(Url)
                .where(Url.short_code == code,  Url.deleted_at == None, or_(
                    Url.expires_at.is_(None),
                    Url.expires_at > datetime.now(timezone.utc)
                ))
                .values(
                    click_count=Url.click_count + 1,
                    last_accessed_at=datetime.now(timezone.utc)
                )
                .returning(Url.original_url)
            )
            result = session.exec(stmt).first()
            session.commit()
            return result[0] if result else None
    
    def increment_click_stats(self, code: str, delta: int, last_accessed_at: datetime) -> None:
        """Flush an accumulated in-memory click delta to the database.
        Called from the periodic cache flush, not from a live request."""
        with get_session() as session:
            stmt = (
                update(Url)
                .where(Url.short_code == code, Url.deleted_at == None, or_(
                    Url.expires_at.is_(None),
                    Url.expires_at > datetime.now(timezone.utc)
                ))
                .values(
                    click_count=Url.click_count + delta,
                    last_accessed_at=last_accessed_at,
                )
            )
            session.exec(stmt)
            session.commit()

    def list_urls_by_user(self, user_id: int, page: int, size: int) -> list[Url]:
        with get_session() as session:
            stmt = select(Url).where(Url.user_id == user_id).offset((page - 1) * size).limit(size)
            urls = session.exec(stmt).all()
            return urls
    
    def count_urls_by_user(self, user_id: int) -> int:
        with get_session() as session:
            stmt = select(Url).where(Url.user_id == user_id)
            urls = session.exec(stmt).all()
            return len(urls)


class UserRepository: 
    def get_user_by_api_key(self, api_key: str) -> User | None: 
        with get_session() as session: 
            stmt = select(User).where(User.api_key == api_key)
            user = session.exec(stmt).first()
            return user