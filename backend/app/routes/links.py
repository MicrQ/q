"""
Routes for link creation and analytics.
"""

from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request, status

from app import db, cache, config
from app.snowflake import SnowflakeGenerator
from app.models import ShortenRequest, ShortenResponse, AnalyticsResponse

router = APIRouter()
snowflake = SnowflakeGenerator(config.SERVER_ID)

@router.post(
    "/shorten",
    response_model=ShortenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Shorten a long URL"
)
async def shorten(request: Request, body: ShortenRequest):
    # Set default expiry of 1 year if not provided
    created_at = datetime.now(timezone.utc)
    expires_at = body.expires_at
    if expires_at is None:
        expires_at = created_at + timedelta(days=365)
    else:
        # Ensure timezone-aware comparisons are correct
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= created_at:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Expiry date must be in the future."
            )

    code = snowflake.next_code()
    original_url = str(body.url)

    # 1. Persist to Postgres
    try:
        await db.insert_link(
            code=code,
            original_url=original_url,
            expires_at=expires_at
        )
    except Exception as e:
        # Handle potential DB errors/collisions (unlikely with Snowflake)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )

    # 2. Pre-populate cache on write
    try:
        await cache.cache_set(
            code=code,
            original_url=original_url,
            expires_at=expires_at
        )
    except Exception:
        # Do not fail request if cache fails, but log/ignore for resilience
        pass

    # Construct response short URL using the Host header
    # Or fall back to localhost if host header is missing
    host = request.headers.get("host", "localhost")
    scheme = request.url.scheme
    short_url = f"{scheme}://{host}/{code}"

    return ShortenResponse(
        code=code,
        short_url=short_url,
        expires_at=expires_at
    )

@router.get(
    "/analytics/{code}",
    response_model=AnalyticsResponse,
    summary="Get analytics for a short code"
)
async def get_analytics(code: str):
    record = await db.get_link(code)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Short code not found."
        )

    return AnalyticsResponse(
        code=record["code"],
        original_url=record["original_url"],
        clicks=record["clicks"],
        expires_at=record["expires_at"],
        created_at=record["created_at"]
    )
