"""
Postgres client — single instance, all reads and writes go here.

Uses asyncpg connection pools (one pool per process).
The pool is created once at application startup via ``init_pool()``
and closed at shutdown via ``close_pool()``.
"""

from datetime import datetime, timezone

import asyncpg

from app import config

# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        host=config.POSTGRES_HOST,
        port=config.POSTGRES_PORT,
        database=config.POSTGRES_DB,
        user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD,
        min_size=2,
        max_size=10,
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call init_pool() first.")
    return _pool


# ---------------------------------------------------------------------------
# Link operations
# ---------------------------------------------------------------------------


async def insert_link(
    code: str,
    original_url: str,
    expires_at: datetime,
) -> None:
    """Persist a new short link. Raises asyncpg.UniqueViolationError on duplicate code."""
    await get_pool().execute(
        """
        INSERT INTO links (code, original_url, expires_at)
        VALUES ($1, $2, $3)
        """,
        code,
        original_url,
        expires_at,
    )


async def get_link(code: str) -> asyncpg.Record | None:
    """
    Fetch a link record by short code.

    Returns a Record with columns: code, original_url, clicks, expires_at, created_at.
    Returns None if the code does not exist.
    """
    return await get_pool().fetchrow(
        """
        SELECT code, original_url, clicks, expires_at, created_at
        FROM links
        WHERE code = $1
        """,
        code,
    )


async def increment_clicks(code: str, count: int = 1) -> None:
    """Atomically increment the click counter for a short code."""
    await get_pool().execute(
        """
        UPDATE links SET clicks = clicks + $2 WHERE code = $1
        """,
        code,
        count,
    )
