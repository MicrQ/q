"""
Snowflake ID generator — produces globally unique, time-ordered IDs
across all server instances with no inter-server coordination.

Bit layout (53 bits total):
  41 bits — milliseconds since EPOCH (custom epoch: 2024-01-01 UTC)
   2 bits — server ID, 1–3 stored as 0–2 (supports up to 4 servers)
  10 bits — sequence number, 0–1023 per millisecond per server

53 bits → base62 → at most 9 characters per code.
Maximum throughput: 3 servers × 1 024 IDs/ms = ~3 million unique codes/second.
"""

import threading
import time
from datetime import datetime, timezone

# 2024-01-01 00:00:00 UTC in milliseconds
EPOCH_MS: int = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

_SERVER_ID_BITS = 2
_SEQUENCE_BITS = 10

_MAX_SERVER_ID = (1 << _SERVER_ID_BITS) - 1  # 3  (valid input range: 1–4)
_MAX_SEQUENCE = (1 << _SEQUENCE_BITS) - 1    # 1023

_SERVER_ID_SHIFT = _SEQUENCE_BITS                   # 10
_TIMESTAMP_SHIFT = _SEQUENCE_BITS + _SERVER_ID_BITS  # 12

BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _to_base62(n: int) -> str:
    if n == 0:
        return BASE62[0]
    chars: list[str] = []
    while n:
        chars.append(BASE62[n % 62])
        n //= 62
    return "".join(reversed(chars))


class SnowflakeGenerator:
    """Thread-safe Snowflake ID generator for a single server instance."""

    def __init__(self, server_id: int) -> None:
        """
        Args:
            server_id: Integer in range 1–4 (matches SERVER_ID env var).
        """
        if not 1 <= server_id <= _MAX_SERVER_ID + 1:
            raise ValueError(
                f"server_id must be between 1 and {_MAX_SERVER_ID + 1}, got {server_id}"
            )
        self._server_id = server_id - 1  # shift to 0-indexed for bit packing
        self._sequence = 0
        self._last_ms = -1
        self._lock = threading.Lock()

    def _now_ms(self) -> int:
        return int(time.time() * 1000) - EPOCH_MS

    def next_id(self) -> int:
        """Return the next unique integer Snowflake ID."""
        with self._lock:
            now = self._now_ms()

            if now == self._last_ms:
                self._sequence = (self._sequence + 1) & _MAX_SEQUENCE
                if self._sequence == 0:
                    # Sequence exhausted for this millisecond — busy-wait for next ms.
                    while now <= self._last_ms:
                        now = self._now_ms()
            else:
                self._sequence = 0

            self._last_ms = now

            return (
                (now << _TIMESTAMP_SHIFT)
                | (self._server_id << _SERVER_ID_SHIFT)
                | self._sequence
            )

    def next_code(self) -> str:
        """Return the next unique short code as a base62 string."""
        return _to_base62(self.next_id())
