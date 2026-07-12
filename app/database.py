from sqlmodel import Session, SQLModel, create_engine

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///urls.db")
engine = create_engine(DATABASE_URL)


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)