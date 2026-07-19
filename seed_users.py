"""Seed three sample users and print their API keys.

Idempotent: re-running skips users whose email already exists.
Targets the DB from DATABASE_URL (defaults to local SQLite):

    python seed_users.py
    DATABASE_URL="postgresql://..." python seed_users.py
"""
import secrets

from sqlmodel import select

from app.database import get_session
from app.models import User

SAMPLE_USERS = [
    ("alice@example.com", "Alice"),
    ("bob@example.com", "Bob"),
    ("carol@example.com", "Carol"),
]


def seed() -> None:
    with get_session() as session:
        for email, name in SAMPLE_USERS:
            existing = session.exec(select(User).where(User.email == email)).first()
            if existing:
                print(f"{email:25} exists   api_key={existing.api_key}")
                continue
            user = User(email=email, name=name, api_key=secrets.token_hex(16))
            session.add(user)
            session.commit()
            print(f"{email:25} created  api_key={user.api_key}")


if __name__ == "__main__":
    seed()
