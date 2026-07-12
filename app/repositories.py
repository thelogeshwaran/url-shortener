from sqlmodel import select

from app.database import get_session
from app.models import Url


class UrlRepository: 
    def save_url(self, code: str, long_url: str) -> None:
        with get_session() as session:
            session.add(Url(short_code=code, original_url=long_url))
            session.commit()

    def get_url_by_code(self, code: str) -> str | None:
        with get_session() as session:
            stmt = select(Url).where(Url.short_code == code)
            url = session.exec(stmt).first()
            return url.original_url if url else None

    def get_code_by_url(self, url: str) -> str | None:
        with get_session() as session:
            stmt = select(Url).where(Url.original_url == url)
            url = session.exec(stmt).first()
            return url.short_code if url else None
          
    def delete_url(self, code: str) -> bool:
        with get_session() as session:
            stmt = select(Url).where(Url.short_code == code)
            url = session.exec(stmt).first()
            if url is None:
                return False
            session.delete(url)
            session.commit()
            return True

    def code_exists(self, code: str) -> bool:
        with get_session() as session:
            stmt = select(Url).where(Url.short_code == code)
            url = session.exec(stmt).first()
            return url is not None