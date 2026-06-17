"""
Redirect endpoint.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from app import db, cache

router = APIRouter()

@router.get(
    "/{code}",
    include_in_schema=False,
)
async def redirect(code: str):
    now = datetime.now(timezone.utc)

    # 1. Try reading from cache
    try:
        cached = await cache.cache_get(code)
    except Exception:
        # Cache failure: fall back directly to DB
        cached = None

    if cached is not None:
        original_url = cached["original_url"]
        expires_at = datetime.fromisoformat(cached["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="This short link has expired."
            )

        # Publish click event asynchronously (fire-and-forget in background)
        try:
            await cache.publish_click(code)
        except Exception:
            # Click counting failure should not block the user's redirect
            pass

        return RedirectResponse(
            url=original_url,
            status_code=status.HTTP_302_FOUND
        )

    # 2. Cache Miss: fall back to Postgres
    record = await db.get_link(code)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Short code not found."
        )

    expires_at = record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This short link has expired."
        )

    original_url = record["original_url"]

    # Repopulate cache
    try:
        await cache.cache_set(
            code=code,
            original_url=original_url,
            expires_at=expires_at
        )
    except Exception:
        # Avoid crashing redirect on cache write failure
        pass

    # Publish click event
    try:
        await cache.publish_click(code)
    except Exception:
        pass

    return RedirectResponse(
        url=original_url,
        status_code=status.HTTP_302_FOUND
    )
