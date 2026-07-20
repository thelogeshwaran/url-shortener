
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator, HttpUrl


class ShortenRequest(BaseModel):
    url: HttpUrl
    expires_at: datetime | None = None
    code: str | None = None

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_future(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        # normalize aware datetimes to naive UTC, matching the DB columns
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        if v <= datetime.utcnow():
            raise ValueError("expires_at must be in the future")
        return v


class ShortenResponse(BaseModel):
    short_url: str

    
