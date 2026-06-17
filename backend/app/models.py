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
            "Optional expiry timestamp (ISO 8601, timezone-aware). "
            "Defaults to one year from creation time if omitted."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://github.com/MicrQ/q",
                },
                {
                    "url": "https://docs.python.org/3/library/asyncio.html",
                    "expires_at": "2027-01-01T00:00:00Z",
                },
            ]
        }
    }


class ShortenResponse(BaseModel):
    code: str = Field(..., description="The generated short code.")
    short_url: str = Field(..., description="The full short URL (base URL + code).")
    expires_at: datetime = Field(..., description="When the link expires.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "code": "2GxKp8mN",
                    "short_url": "http://localhost/2GxKp8mN",
                    "expires_at": "2027-06-17T19:00:00Z",
                }
            ]
        }
    }


class AnalyticsResponse(BaseModel):
    code: str = Field(..., description="The short code.")
    original_url: str = Field(..., description="The original URL this code points to.")
    clicks: int = Field(..., description="Total number of redirects recorded.")
    expires_at: datetime = Field(..., description="When the link expires.")
    created_at: datetime = Field(..., description="When the link was created.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "code": "2GxKp8mN",
                    "original_url": "https://github.com/MicrQ/q",
                    "clicks": 42,
                    "expires_at": "2027-06-17T19:00:00Z",
                    "created_at": "2026-06-17T19:00:00Z",
                }
            ]
        }
    }


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Human-readable error message.")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"detail": "Short code not found."},
            ]
        }
    }
