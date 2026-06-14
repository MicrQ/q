"""
Unit tests for app.snowflake.

All tests are local and require no external services.
Gate tests: must pass in < 2 s.
"""

import pytest

from app.snowflake import BASE62, SnowflakeGenerator


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_valid_server_ids_construct():
    for sid in (1, 2, 3, 4):
        gen = SnowflakeGenerator(sid)
        assert gen is not None


def test_server_id_zero_raises():
    with pytest.raises(ValueError):
        SnowflakeGenerator(0)


def test_server_id_too_large_raises():
    with pytest.raises(ValueError):
        SnowflakeGenerator(5)


# ---------------------------------------------------------------------------
# Code format
# ---------------------------------------------------------------------------


def test_code_is_base62():
    gen = SnowflakeGenerator(1)
    code = gen.next_code()
    assert len(code) > 0
    assert all(c in BASE62 for c in code), f"Non-base62 char in {code!r}"


def test_code_max_length():
    """53-bit integer encodes to at most 9 base62 characters."""
    gen = SnowflakeGenerator(1)
    for _ in range(100):
        assert len(gen.next_code()) <= 9


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_unique_within_single_generator():
    gen = SnowflakeGenerator(1)
    codes = [gen.next_code() for _ in range(1_000)]
    assert len(set(codes)) == 1_000


def test_no_collisions_across_server_ids():
    """Codes from different servers must never collide."""
    all_codes: set[str] = set()
    for sid in (1, 2, 3):
        gen = SnowflakeGenerator(sid)
        codes = [gen.next_code() for _ in range(200)]
        all_codes.update(codes)
    assert len(all_codes) == 600


def test_burst_uniqueness():
    """
    Generate IDs faster than 1 ms resolution.
    The generator must wait for the next millisecond rather than repeat a sequence value.
    """
    gen = SnowflakeGenerator(2)
    codes = [gen.next_code() for _ in range(2_000)]
    assert len(set(codes)) == 2_000


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_ids_are_monotonically_increasing():
    """IDs from the same server must be strictly increasing (time-ordered)."""
    gen = SnowflakeGenerator(1)
    ids = [gen.next_id() for _ in range(500)]
    assert ids == sorted(ids)
