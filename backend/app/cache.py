"""
Redis cache layer with consistent hashing.

Responsibilities:
  - Route URL cache reads/writes to the correct Redis node via consistent hashing.
  - Store {original_url, expires_at} JSON blobs with a fixed 7-day TTL.
  - Publish click events to the `clicks` stream on redis_1 (always the first node).

Consistent hashing ring
-----------------------
Each Redis node is placed on a virtual ring [0, 2^32) using MD5.
To find the node for a key: hash the key, walk clockwise to the nearest node.
All servers run identical logic → same key always maps to the same node.
Adding a node only remaps ~1/N of existing keys.
"""

import hashlib
import json
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from app import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
CACHE_KEY_PREFIX = "url:"
CLICKS_STREAM = "clicks"

# Number of virtual nodes per real Redis node on the ring.
# Higher = more even distribution but more memory for the ring structure.
VIRTUAL_NODES = 150

# ---------------------------------------------------------------------------
# Consistent hashing ring
# ---------------------------------------------------------------------------


def _hash(key: str) -> int:
    """Map an arbitrary string to a position on the ring [0, 2^32)."""
    return int(hashlib.md5(key.encode(), usedforsecurity=False).hexdigest(), 16) % (2**32)


class ConsistentHashRing:
    """
    Immutable consistent hash ring built from a list of node addresses.
    Thread-safe for concurrent reads (no mutation after construction).
    """

    def __init__(self, nodes: list[str], virtual_nodes: int = VIRTUAL_NODES) -> None:
        self._ring: dict[int, str] = {}
        self._sorted_keys: list[int] = []

        for node in nodes:
            for i in range(virtual_nodes):
                point = _hash(f"{node}#{i}")
                self._ring[point] = node
        self._sorted_keys = sorted(self._ring)

    def get_node(self, key: str) -> str:
        """Return the node address responsible for the given key."""
        if not self._ring:
            raise RuntimeError("Hash ring is empty")
        point = _hash(key)
        idx = bisect_right(self._sorted_keys, point) % len(self._sorted_keys)
        return self._ring[self._sorted_keys[idx]]


# ---------------------------------------------------------------------------
# Redis client pool — one connection per node
# ---------------------------------------------------------------------------

_ring: ConsistentHashRing | None = None
_clients: dict[str, aioredis.Redis] = {}


def _build_clients(nodes: list[str]) -> dict[str, aioredis.Redis]:
    return {
        node: aioredis.from_url(f"redis://{node}", decode_responses=True)
        for node in nodes
    }


def get_ring() -> ConsistentHashRing:
    global _ring
    if _ring is None:
        _ring = ConsistentHashRing(config.REDIS_NODES)
    return _ring


def get_clients() -> dict[str, aioredis.Redis]:
    global _clients
    if not _clients:
        _clients = _build_clients(config.REDIS_NODES)
    return _clients


def _client_for_key(key: str) -> aioredis.Redis:
    node = get_ring().get_node(key)
    return get_clients()[node]


def _stream_client() -> aioredis.Redis:
    """The clicks stream always lives on the first node (redis_1)."""
    return get_clients()[config.REDIS_NODES[0]]


# ---------------------------------------------------------------------------
# Cache operations
# ---------------------------------------------------------------------------


async def cache_get(code: str) -> dict[str, Any] | None:
    """
    Fetch the cached entry for a short code.

    Returns a dict with keys ``original_url`` and ``expires_at`` (ISO string),
    or None on a cache miss.
    """
    client = _client_for_key(code)
    raw = await client.get(f"{CACHE_KEY_PREFIX}{code}")
    if raw is None:
        return None
    return json.loads(raw)


async def cache_set(code: str, original_url: str, expires_at: datetime) -> None:
    """
    Store a URL mapping in the correct Redis node with a 7-day TTL.

    ``expires_at`` is stored inside the payload so expiry can be enforced on
    cache hits without a Postgres round-trip.
    """
    client = _client_for_key(code)
    payload = json.dumps(
        {
            "original_url": original_url,
            "expires_at": expires_at.isoformat(),
        }
    )
    await client.set(f"{CACHE_KEY_PREFIX}{code}", payload, ex=CACHE_TTL_SECONDS)


async def cache_delete(code: str) -> None:
    """Remove a cached entry (e.g. when a link is explicitly expired)."""
    client = _client_for_key(code)
    await client.delete(f"{CACHE_KEY_PREFIX}{code}")


# ---------------------------------------------------------------------------
# Click stream producer
# ---------------------------------------------------------------------------


async def publish_click(code: str) -> None:
    """
    Append a click event to the Redis Stream on redis_1.
    Non-blocking from the caller's perspective — fire and move on.
    """
    client = _stream_client()
    await client.xadd(CLICKS_STREAM, {"code": code})


# ---------------------------------------------------------------------------
# Lifecycle helpers (called from FastAPI lifespan)
# ---------------------------------------------------------------------------


async def close_clients() -> None:
    """Close all Redis connections gracefully on shutdown."""
    for client in get_clients().values():
        await client.aclose()
