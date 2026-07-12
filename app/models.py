from datetime import datetime
from sqlmodel import Field, SQLModel


class Url(SQLModel, table=True):
    __tablename__ = "urls"
    
    id: int | None = Field(default=None, primary_key=True)
    original_url: str = Field(index=True)
    short_code: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
