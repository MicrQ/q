# Q

A distributed URL shortening service built with FastAPI, Redis, Postgres, and Nginx.

## Architecture

```
                    ┌──────────┐
         :80        │  Nginx   │
     ───────────►   │  (LB)    │
                    └────┬─────┘
              round-robin│
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     ┌──────────┐  ┌──────────┐  ┌──────────┐
     │  API  1  │  │  API  2  │  │  API  3  │
     │ :8000    │  │ :8000    │  │ :8000    │
     │+ratelimit│  │+ratelimit│  │+ratelimit│
     └────┬─────┘  └────┬─────┘  └────┬─────┘
          │              │              │
          └──────────────┼──────────────┘
                         │
          ┌──────────────┴──────────────┐
          │  Consistent Hash Ring       │
          │  (URL cache + rate-limit)   │
          ├──────────────┬──────────────┤
          ▼              ▼
     ┌──────────┐  ┌──────────┐
     │ Redis 1  │  │ Redis 2  │
     │ (cache   │  │ (cache)  │
     │ +stream) │  │          │
     │+ratelimit│  │+ratelimit│
     └────┬─────┘  └──────────┘
          │
          │ clicks stream        ┌──────────┐
          ▼                      │ Postgres │
     ┌──────────┐                │  (store) │
     │ Consumer │◄─── (read/write───────────┘
     │ (batch   │     direct,
     │  writer) │     not hashed)
     └──────────┘
```

**Key design decisions:**

- **Snowflake IDs** — each API node generates collision-free short codes using a timestamp + server ID + sequence scheme. No coordination needed.
- **Consistent hashing** — cache lookups are routed to the correct Redis node deterministically. Adding a node only remaps ~1/N of keys.
- **Click stream** — redirects publish to a Redis Stream; a standalone consumer batches writes to Postgres. This keeps redirect latency low.
- **Rate limiting** — per-IP sliding window counters distributed across all Redis nodes via the consistent hash ring. No single point of failure.

## Quickstart

**Prerequisites:** Docker and Docker Compose.

```bash
git clone https://github.com/MicrQ/q.git
cd q/backend
docker compose up --build -d
```

The service is available at `http://localhost`.

Verify all nodes are healthy:

```bash
curl http://localhost/health
```

## API

### Shorten a URL

```bash
curl -X POST http://localhost/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/some/long/path"}'
```

```json
{
  "code": "2GxKp8mN",
  "short_url": "http://localhost/2GxKp8mN",
  "expires_at": "2027-06-17T19:00:00Z"
}
```

Optional: pass `expires_at` (ISO 8601) to set a custom expiry.

### Redirect

```bash
curl -L http://localhost/2GxKp8mN
# → 302 redirect to https://example.com/some/long/path
```

### Analytics

```bash
curl http://localhost/analytics/2GxKp8mN
```

```json
{
  "code": "2GxKp8mN",
  "original_url": "https://example.com/some/long/path",
  "clicks": 42,
  "expires_at": "2027-06-17T19:00:00Z",
  "created_at": "2026-06-17T19:00:00Z"
}
```

## Rate Limiting

Every endpoint (except `/health`) enforces per-IP rate limits using a sliding window counter stored in Redis. Limits are distributed across both Redis nodes via consistent hashing on the client IP — no single point of failure.

| Endpoint | Limit |
|---|---|
| `POST /shorten` | 10 requests per minute |
| `GET /analytics/{code}` | 600 requests per minute |
| `GET /{code}` (redirect) | 6000 requests per minute |

All responses include rate-limit headers:

```http
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 5
X-RateLimit-Reset: 42
```

When the limit is exceeded, a **429 Too Many Requests** is returned:

```json
{
  "detail": "Too many requests. Please try again later."
}
```

## Tests

Requires [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
cd backend

# Install dev dependencies
uv sync

# Unit + API tests (no Docker needed)
uv run pytest tests/unit tests/api -v

# Distributed integration tests (Docker stack must be running)
uv run pytest tests/load -v
```

| Suite | What it covers |
|-------|---------------|
| `tests/unit/` | Snowflake ID generation, consistent hashing, request/response models |
| `tests/api/` | Route handlers with mocked Redis and Postgres |
| `tests/load/` | Round-robin distribution, Snowflake uniqueness under concurrency, end-to-end redirect→analytics pipeline |

## Project Structure

```
backend/
├── api/
│   └── index.py              # FastAPI app, middleware, route registration
├── app/
│   ├── config.py              # Centralized env var loading
│   ├── cache.py               # Redis client pool + consistent hash ring
│   ├── db.py                  # Postgres via asyncpg
│   ├── models.py              # Pydantic request/response schemas
│   ├── ratelimit.py           # Per-IP sliding window rate limiter
│   ├── snowflake.py           # Distributed ID generator
│   └── routes/
│       ├── links.py           # POST /shorten, GET /analytics/{code}
│       └── redirect.py        # GET /{code} → 302
├── consumer/
│   └── click_consumer.py      # Redis Stream → Postgres batch writer
├── nginx/
│   └── nginx.conf             # Load balancer config
├── postgres/
│   └── init.sql               # Schema migration
├── docker-compose.yml
├── Dockerfile                 # API image
├── Dockerfile.consumer        # Consumer image
└── tests/
    ├── unit/
    ├── api/
    └── load/
```

## License

MIT
