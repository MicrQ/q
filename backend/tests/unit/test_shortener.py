"""
Unit tests for shortener schemas and model validation.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from app.models import ShortenRequest, ShortenResponse


def test_shorten_request_valid_url():
    req = ShortenRequest(url="https://google.com")
    assert str(req.url) == "https://google.com/"
    assert req.expires_at is None


def test_shorten_request_invalid_url():
    with pytest.raises(ValidationError):
        ShortenRequest(url="not-a-url")


def test_shorten_request_with_expiry():
    future = datetime.now(timezone.utc) + timedelta(days=10)
    req = ShortenRequest(url="https://google.com", expires_at=future)
    assert req.expires_at == future


def test_shorten_response_validation():
    future = datetime.now(timezone.utc) + timedelta(days=365)
    resp = ShortenResponse(
        code="aB3xYz",
        short_url="http://localhost/aB3xYz",
        expires_at=future
    )
    assert resp.code == "aB3xYz"
    assert resp.short_url == "http://localhost/aB3xYz"
    assert resp.expires_at == future
