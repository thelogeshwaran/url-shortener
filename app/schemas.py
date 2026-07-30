
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator, HttpUrl, Field, model_validator


class ShortenRequest(BaseModel):
    url: HttpUrl
    expires_at: datetime | None = None
    code: str | None = None
    password: str | None = None

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

    @field_validator("password")
    @classmethod
    def password_must_be_long_enough(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) < 4:
            raise ValueError("Password must be at least 4 characters long.")
        return v


class ShortenResponse(BaseModel):
    short_url: str

    
class BatchShortenRequest(BaseModel):
    urls: list[ShortenRequest] = Field(..., min_length=1, max_length=100)


class BatchItemResult(BaseModel):
    url: str
    short_url: str | None = None
    error: str | None = None


class BatchShortenResponse(BaseModel):
    results: list[BatchItemResult]
    

class EditUrlRequest(BaseModel):
    url: HttpUrl | None = None
    expires_at: datetime | None = None
    password: str | None = None

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, v: datetime | None) -> datetime | None:
        # no future-date requirement here: a past value is the deliberate
        # deactivate mechanism (edit expiry into the past == "delete-lite")
        if v is not None and v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> 'EditUrlRequest':
        if 'url' not in self.model_fields_set and 'expires_at' not in self.model_fields_set and 'password' not in self.model_fields_set:
            raise ValueError("At least one of field must be provided")
        return self

    @field_validator("password")
    @classmethod
    def password_must_be_long_enough(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) < 4:
            raise ValueError("Password must be at least 4 characters long.")
        return v


class UrlResponse(BaseModel):
    model_config = {"from_attributes": True}
    original_url: str
    short_code: str
    created_at: datetime
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    click_count: int = 0


class PaginatedUrlsResponse(BaseModel):
    urls: list[UrlResponse]
    total: int
    page: int
    size: int


class LookupResponse(BaseModel):
    url: str