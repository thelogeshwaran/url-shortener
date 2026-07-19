from datetime import datetime, timezone
from sqlmodel import select, update

from app.database import get_session
from app.models import Url, User


class UrlRepository: 
    def save_url(self, code: str, long_url: str, user_id: int | None = None) -> None:
        with get_session() as session:
            session.add(Url(short_code=code, original_url=long_url, user_id=user_id))
            session.commit()

    def get_url_by_code(self, code: str) -> Url | None:
        with get_session() as session:
            stmt = select(Url).where(Url.short_code == code)
            url = session.exec(stmt).first()
            return url
          
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
                .where(Url.short_code == code,  Url.deleted_at == None)
                .values(
                    click_count=Url.click_count + 1,
                    last_accessed_at=datetime.now(timezone.utc)
                )
                .returning(Url.original_url)
            )
            result = session.exec(stmt).first()
            session.commit()
            return result[0] if result else None


class UserRepository: 
    def get_user_by_api_key(self, api_key: str) -> User | None: 
        with get_session() as session: 
            stmt = select(User).where(User.api_key == api_key)
            user = session.exec(stmt).first()
            return user