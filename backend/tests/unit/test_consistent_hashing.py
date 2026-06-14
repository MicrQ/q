"""
Unit tests for the consistent hash ring in app.cache.

No external services required — the ring is pure Python.
Gate tests: must pass in < 2 s.
"""

import pytest

from app.cache import ConsistentHashRing


NODES = ["redis_1:6379", "redis_2:6379"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_key_always_maps_to_same_node():
    ring = ConsistentHashRing(NODES)
    node = ring.get_node("aB3xYz")
    for _ in range(1_000):
        assert ring.get_node("aB3xYz") == node


def test_different_ring_instances_agree():
    ring_a = ConsistentHashRing(NODES)
    ring_b = ConsistentHashRing(NODES)
    for key in ("abc", "xyz", "hello", "world", "123456"):
        assert ring_a.get_node(key) == ring_b.get_node(key)


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------


def test_distribution_is_roughly_even():
    """
    With 150 virtual nodes per real node, no node should own more than
    70 % of 10 000 random keys (expected ~50 % each).
    """
    ring = ConsistentHashRing(NODES)
    counts: dict[str, int] = {n: 0 for n in NODES}
    keys = [f"code_{i}" for i in range(10_000)]
    for key in keys:
        counts[ring.get_node(key)] += 1
    for node, count in counts.items():
        assert count < 7_000, f"{node} owns {count}/10 000 keys — too skewed"


# ---------------------------------------------------------------------------
# Node membership
# ---------------------------------------------------------------------------


def test_returned_node_is_always_in_node_list():
    ring = ConsistentHashRing(NODES)
    for i in range(500):
        assert ring.get_node(f"key_{i}") in NODES


def test_empty_ring_raises():
    ring = ConsistentHashRing([])
    with pytest.raises(RuntimeError):
        ring.get_node("anything")


# ---------------------------------------------------------------------------
# Minimal remapping on node addition
# ---------------------------------------------------------------------------


def test_adding_node_remaps_minority_of_keys():
    """
    Adding a third node should remap roughly 1/3 of keys.
    We assert fewer than 60 % of keys change — well above the ~33 % theoretical bound,
    giving a generous margin for statistical variance.
    """
    original_nodes = ["redis_1:6379", "redis_2:6379"]
    expanded_nodes = ["redis_1:6379", "redis_2:6379", "redis_3:6379"]

    ring_before = ConsistentHashRing(original_nodes)
    ring_after = ConsistentHashRing(expanded_nodes)

    keys = [f"code_{i}" for i in range(10_000)]
    remapped = sum(
        1 for k in keys if ring_before.get_node(k) != ring_after.get_node(k)
    )
    assert remapped < 6_000, f"{remapped}/10 000 keys remapped — too many"
