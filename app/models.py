from datetime import datetime
from sqlmodel import Field, SQLModel


class Url(SQLModel, table=True):
    __tablename__ = "urls"
    
    id: int | None = Field(default=None, primary_key=True)
    original_url: str = Field(index=True)
    short_code: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    click_count: int = Field(default=0)
    last_accessed_at: datetime | None = None
    deleted_at: datetime | None = None
    user_id: int | None = Field(default=None, foreign_key="users.id")
    expires_at: datetime | None = None


class User(SQLModel, table=True):
    __tablename__ = "users"
    
    id: int | None = Field(default=None, primary_key=True)
    name: str | None = Field(default=None)
    email: str = Field(unique=True, index=True)
    api_key: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
