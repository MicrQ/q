"""
API tests for /analytics/{code} endpoint.
"""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from api.index import app


def test_analytics_success(mock_db):
    client = TestClient(app)
    code = "analyticsCode"
    original_url = "https://analytics.com"
    expiry = datetime.now(timezone.utc) + timedelta(days=365)
    created = datetime.now(timezone.utc) - timedelta(days=5)

    mock_db[code] = {
        "code": code,
        "original_url": original_url,
        "clicks": 42,
        "expires_at": expiry,
        "created_at": created
    }

    response = client.get(f"/analytics/{code}")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == code
    assert data["original_url"] == original_url
    assert data["clicks"] == 42
    
    # Parse back to compare timezone-aware datetimes
    resp_expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    resp_created = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    assert abs((resp_expires - expiry).total_seconds()) < 1
    assert abs((resp_created - created).total_seconds()) < 1



def test_analytics_not_found(mock_db):
    client = TestClient(app)
    response = client.get("/analytics/notfound")
    assert response.status_code == 404
