"""
Redis-backed sliding window rate limiter.

Uses the existing consistent hash ring to distribute rate-limit keys
across all Redis nodes, avoiding a single point of failure.
On Redis failure the limiter fails open — logs a warning and allows the request.

Algorithm: sliding window counter
----------------------------------
Two fixed-window counters per IP (current + previous window). The estimated
count for the current instant is:

    prev_count * (1 - elapsed_ratio) + curr_count

This gives a smooth approximation of a true sliding window with only 2 keys
per IP, vs O(N) for a sorted-set approach.
"""

import time
import logging
from dataclasses import dataclass, field

from app import cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RATE_LIMIT_PREFIX = "rl:"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RateLimitConfig:
    """Per-endpoint rate limit configuration."""
    limit: int
    window_seconds: int = 60


# Limiting strategy:
#   POST /shorten  — 10 req/min (write operation)
#   GET /analytics/{code} — 600 req/min
#   GET /{code} redirect — 6000 req/min (read-heavy, allow more)
LIMITS_BY_PREFIX: dict[str, RateLimitConfig] = {
    "/shorten":   RateLimitConfig(limit=10,   window_seconds=60),
    "/analytics": RateLimitConfig(limit=600,  window_seconds=60),
    "/":          RateLimitConfig(limit=6000, window_seconds=60),
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_client_ip(headers: dict, client_host: str | None) -> str:
    """Extract the real client IP from forwarded headers (Nginx reverse-proxy)."""
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = headers.get("x-real-ip", "")
    if real_ip:
        return real_ip
    return client_host or "unknown"


def resolve_config(path: str) -> tuple[str, RateLimitConfig]:
    """
    Match a request path to its rate limit config.

    Returns ``(matched_prefix, config)`` so the caller can log which rule applied.
    Falls back to the catch-all ``/`` (redirects) if nothing more specific matches.
    """
    for prefix in ("/shorten", "/analytics"):
        if path.startswith(prefix):
            return prefix, LIMITS_BY_PREFIX[prefix]
    return "/", LIMITS_BY_PREFIX["/"]


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


async def check_rate_limit(
    ip: str,
    limit: int,
    window_seconds: int = 60,
) -> tuple[bool, int, int]:
    """
    Check and record a request against the sliding window rate limit.

    Returns
    -------
    (allowed: bool, remaining: int, reset_seconds: int)

    On Redis failure the limiter fails open — returns ``(True, limit, window)``
    so the request proceeds.  A warning is logged.
    """
    now = time.time()
    now_int = int(now)
    window = now_int // window_seconds
    prev_window = window - 1

    # Hash the IP onto the consistent hash ring to pick the Redis node.
    try:
        ring = cache.get_ring()
        node = ring.get_node(ip)
        client = cache.get_clients()[node]
    except Exception:
        logger.warning("Rate limiter: Redis ring unreachable, failing open for %s", ip)
        return True, limit, window_seconds

    current_key = f"{RATE_LIMIT_PREFIX}{ip}:{window}"
    prev_key = f"{RATE_LIMIT_PREFIX}{ip}:{prev_window}"

    try:
        # Previous window count
        prev_raw = await client.get(prev_key)
        prev_count = int(prev_raw) if prev_raw is not None else 0

        # Increment current window
        curr_count = await client.incr(current_key)
        if curr_count == 1:
            # First request in this window — let Redis auto-clean after 2× the window
            await client.expire(current_key, window_seconds * 2)

        # Sliding window estimate
        elapsed = now - (window * window_seconds)
        weight = elapsed / window_seconds if window_seconds > 0 else 1.0
        estimated = prev_count * (1.0 - weight) + curr_count

        reset_in = window_seconds - (now_int % window_seconds)
        remaining = max(0, limit - int(estimated))
        allowed = estimated < limit

        return allowed, remaining, reset_in

    except Exception as e:
        logger.warning("Rate limiter: Redis error for %s, failing open: %s", ip, e)
        return True, limit, window_seconds
