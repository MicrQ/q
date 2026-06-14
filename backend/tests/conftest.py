"""
Global test configuration and fixtures.
"""

import os
import pytest
from datetime import datetime, timezone

# Override environment variables for testing before importing anything
os.environ["SERVER_ID"] = "1"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["POSTGRES_DB"] = "test_q"
os.environ["POSTGRES_USER"] = "postgres"
os.environ["POSTGRES_PASSWORD"] = "postgres"
os.environ["REDIS_NODES"] = "localhost:6379,localhost:6380"

import fakeredis.aioredis
from app import cache, db



@pytest.fixture(autouse=True)
async def mock_redis(monkeypatch):
    """
    Automatically mock Redis node client connections during testing
    using fakeredis to keep tests fast and isolated.
    """
    fake_clients = {
        node: fakeredis.aioredis.FakeRedis(decode_responses=True)
        for node in ["localhost:6379", "localhost:6380"]
    }

    # Monkeypatch get_clients so cache operations use fakeredis instances
    monkeypatch.setattr(cache, "get_clients", lambda: fake_clients)
    
    yield fake_clients

    # Clean up all keys between tests
    for client in fake_clients.values():
        await client.flushall()


@pytest.fixture
def mock_db(monkeypatch):
    """
    Mock the db module operations with an in-memory dictionary
    to allow fast and isolated database queries without a real PostgreSQL connection.
    """
    db_store = {}

    async def mock_insert_link(code: str, original_url: str, expires_at: datetime):
        if code in db_store:
            raise Exception("duplicate key value violates unique constraint")
        db_store[code] = {
            "code": code,
            "original_url": original_url,
            "clicks": 0,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc)
        }

    async def mock_get_link(code: str):
        return db_store.get(code)

    async def mock_increment_clicks(code: str, count: int = 1):
        if code in db_store:
            db_store[code]["clicks"] += count

    monkeypatch.setattr(db, "insert_link", mock_insert_link)
    monkeypatch.setattr(db, "get_link", mock_get_link)
    monkeypatch.setattr(db, "increment_clicks", mock_increment_clicks)
    monkeypatch.setattr(db, "init_pool", lambda: None)
    monkeypatch.setattr(db, "close_pool", lambda: None)

    return db_store


