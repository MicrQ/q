"""
API tests for /shorten endpoint.
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from api.index import app
from app import cache


def test_shorten_success(mock_db):
    client = TestClient(app)
    payload = {"url": "https://google.com"}
    response = client.post("/shorten", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert "code" in data
    assert "short_url" in data
    assert "expires_at" in data
    assert data["short_url"].endswith(data["code"])
    
    # Check that it is stored in database mock
    code = data["code"]
    assert code in mock_db
    assert mock_db[code]["original_url"] == "https://google.com/"


    # Default expiry should be ~1 year from now
    expires_at = datetime.fromisoformat(data["expires_at"])
    now = datetime.now(timezone.utc)
    expected_expiry = now + timedelta(days=365)
    # Check if they are within 1 minute of each other
    assert abs((expires_at - expected_expiry).total_seconds()) < 60


def test_shorten_with_custom_expiry(mock_db):
    client = TestClient(app)
    future_time = datetime.now(timezone.utc) + timedelta(days=10)
    # Convert to standard format that JSON can serialize
    expiry_str = future_time.isoformat()

    payload = {"url": "https://google.com", "expires_at": expiry_str}
    response = client.post("/shorten", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    
    response_expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    assert abs((response_expiry - future_time).total_seconds()) < 1
    
    code = data["code"]
    assert mock_db[code]["expires_at"] == future_time



def test_shorten_invalid_expiry_past(mock_db):
    client = TestClient(app)
    past_time = datetime.now(timezone.utc) - timedelta(days=1)
    
    payload = {"url": "https://google.com", "expires_at": past_time.isoformat()}
    response = client.post("/shorten", json=payload)
    
    # Validation error for past expiry
    assert response.status_code == 422


def test_shorten_invalid_url(mock_db):
    client = TestClient(app)
    payload = {"url": "not-a-valid-url"}
    response = client.post("/shorten", json=payload)
    
    assert response.status_code == 422
