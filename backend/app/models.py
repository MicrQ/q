"""
Pydantic schemas for request and response bodies.
"""

from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field


class ShortenRequest(BaseModel):
    url: AnyHttpUrl = Field(..., description="The original URL to shorten.")
    expires_at: datetime | None = Field(
        None,
        description=(
            "Optional expiry timestamp (timezone-aware). "
            "Defaults to one year from creation time if omitted."
        ),
    )


class ShortenResponse(BaseModel):
    code: str = Field(..., description="The generated short code.")
    short_url: str = Field(..., description="The full short URL (base URL + code).")
    expires_at: datetime = Field(..., description="When the link expires.")


class AnalyticsResponse(BaseModel):
    code: str
    original_url: str
    clicks: int
    expires_at: datetime
    created_at: datetime
