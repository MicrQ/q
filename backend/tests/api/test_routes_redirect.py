"""
API tests for GET /{code} redirect endpoint.
"""

import pytest
import json
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from api.index import app
from app import cache


async def test_redirect_success_cache_miss(mock_db, mock_redis):
    client = TestClient(app)
    code = "aB3xYz"
    original_url = "https://example.com"
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    
    # 1. Populate mock database, leave cache empty
    mock_db[code] = {
        "code": code,
        "original_url": original_url,
        "clicks": 0,
        "expires_at": expiry,
        "created_at": datetime.now(timezone.utc)
    }
    
    # 2. Trigger redirect request
    # Allow redirects = False so we can inspect the 302 status code and headers
    response = client.get(f"/{code}", follow_redirects=False)
    
    assert response.status_code == 302
    assert response.headers["location"] == original_url

    # 3. Assert Redis cache was repopulated
    cached = await mock_redis["localhost:6379"].get(f"url:{code}")
    if cached is None:
        # Check node 2 because of consistent hashing ring
        cached = await mock_redis["localhost:6380"].get(f"url:{code}")
        
    assert cached is not None
    cached_data = json.loads(cached)
    assert cached_data["original_url"] == original_url
    
    # 4. Assert click event was written to clicks stream on redis_1
    stream_events = await mock_redis["localhost:6379"].xrange("clicks")
    assert len(stream_events) == 1
    assert stream_events[0][1]["code"] == code


async def test_redirect_success_cache_hit(mock_db, mock_redis):
    client = TestClient(app)
    code = "cacheHit"
    original_url = "https://cachehit.com"
    expiry = datetime.now(timezone.utc) + timedelta(days=5)

    # 1. Warm the cache directly
    # Consistent hash determines the node
    ring = cache.get_ring()
    node = ring.get_node(code)
    redis_client = mock_redis[node]
    
    payload = json.dumps({
        "original_url": original_url,
        "expires_at": expiry.isoformat()
    })
    await redis_client.set(f"url:{code}", payload)

    # 2. Trigger redirect request
    response = client.get(f"/{code}", follow_redirects=False)
    
    assert response.status_code == 302
    assert response.headers["location"] == original_url

    # 3. Assert click was recorded in stream on redis_1
    stream_events = await mock_redis["localhost:6379"].xrange("clicks")
    assert len(stream_events) == 1
    assert stream_events[0][1]["code"] == code


async def test_redirect_expired_link(mock_db, mock_redis):
    client = TestClient(app)
    code = "expired"
    original_url = "https://expired.com"
    past_expiry = datetime.now(timezone.utc) - timedelta(days=2)
    
    # Populate mock DB with expired link
    mock_db[code] = {
        "code": code,
        "original_url": original_url,
        "clicks": 0,
        "expires_at": past_expiry,
        "created_at": datetime.now(timezone.utc) - timedelta(days=10)
    }

    # Should return 410 Gone
    response = client.get(f"/{code}")
    assert response.status_code == 410


async def test_redirect_not_found(mock_db):
    client = TestClient(app)
    # Get random code not in DB or cache
    response = client.get("/nonexistent")
    assert response.status_code == 404
