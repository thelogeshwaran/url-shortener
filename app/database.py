from sqlmodel import Session, SQLModel, create_engine

engine = create_engine("sqlite:///urls.db")


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)