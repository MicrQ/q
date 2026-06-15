# Q — A lightweight, distributed URL shortening service: System Design Preparation

## Overview

Q is a production-grade, distributed URL shortening service designed for high throughput and horizontal scalability. It supports short URL generation, click analytics, and link expiry. The architecture is built with distribution in mind from day one — not bolted on later.

---

## Goals

- Shorten long URLs and redirect users with minimal latency
- Track click analytics per link
- Support link expiry (TTL), defaulting to 1 year from creation
- Scale horizontally across multiple server instances
- Guarantee unique short code generation across distributed servers without coordination
- Cache aggressively to minimize database reads

---

## Architecture Overview

```
                        ┌─────────────────┐
                        │   Nginx (LB)    │
                        └────────┬────────┘
               ┌─────────────────┼─────────────────┐
               ▼                 ▼                 ▼
        ┌────────────┐   ┌────────────┐   ┌────────────┐
        │  FastAPI   │   │  FastAPI   │   │  FastAPI   │
        │ Server 1   │   │ Server 2   │   │ Server 3   │
        │ (id=1)     │   │ (id=2)     │   │ (id=3)     │
        └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
              └────────────────┼─────────────────┘
                               │
              ┌────────────────┴──────────────────────┐
              ▼                                        ▼
       ┌─────────────┐                        ┌─────────────┐
       │   Redis 1   │◄── click stream        │   Redis 2   │
       │  (cache +   │    (clicks stream       │  (cache)    │
       │   stream)   │     lives here)         └─────────────┘
       └──────┬──────┘
              │                    ┌─────────────────────┐
              │ (XREAD)            │   Click Consumer    │
              └───────────────────►│  (standalone proc)  │
                                   └──────────┬──────────┘
                                              │ UPDATE clicks
                                              ▼
                                   ┌─────────────────────┐
                                   │      Postgres       │
                                   └─────────────────────┘
                                              ▲
                               (cache miss fallback + writes)
              ┌────────────────────────────────────────────┐
              │           All FastAPI Servers              │
              └────────────────────────────────────────────┘
```

---

## Components

### Nginx (Load Balancer)
- Distributes incoming requests across three FastAPI instances using round-robin
- Stateless — does not track which server handled which request
- No routing logic; all intelligence lives in the application layer

### FastAPI Servers (x3)
- Each server is assigned a unique server ID (1, 2, or 3) at startup via environment variable
- Uses Snowflake ID generation to produce unique short codes without coordination
- Applies consistent hashing on the short code to determine which Redis node holds the URL cache entry
- All DB reads and writes go to the single Postgres instance
- On redirect: publishes a click event to the `clicks` Redis Stream on `redis_1`

### Redis (x2, Cache Layer)
- Caches `url:{code}` → `{original_url, expires_at}` JSON mappings
- All servers use consistent hashing on the short code to decide which Redis node to query or write to
- Cache TTL is fixed at **7 days** for all entries
- The cached value includes `expires_at` so expiry can be enforced on cache hit without a DB round-trip
- On write (POST /shorten): server pre-populates the correct Redis node immediately after writing to Postgres
- On read (GET /{code}): server queries the correct Redis node first; on miss, falls back to Postgres and repopulates Redis
- `redis_1` also hosts the `clicks` Redis Stream used for async click counting

### Click Consumer (Standalone Process)
- A single long-running Python process that reads from the `clicks` stream on `redis_1`
- Uses Redis Streams consumer groups for at-least-once delivery (no lost increments on crash)
- Batches click events and issues bulk `UPDATE links SET clicks = clicks + N WHERE code = X` to Postgres
- Runs as its own Docker Compose service (`click_consumer`)

### Postgres (Single Instance)
- Source of truth for all link data
- All reads and writes go to the same instance
- Chosen over a replicated setup because the read load is handled by the Redis cache layer; Postgres only sees cache misses and writes

---

## Key Design Decisions

### Snowflake ID Generation
Each server generates short codes independently without talking to other servers.

The Snowflake ID encodes:
- **Timestamp** — milliseconds since epoch
- **Server ID** — unique per instance (1, 2, or 3)
- **Sequence number** — increments per millisecond to handle bursts

This guarantees globally unique IDs across all servers with no coordination overhead. The ID is then base62-encoded (a–z, A–Z, 0–9) to produce a short, URL-safe code (e.g. `aB3xYz`).

### Consistent Hashing (Cache Distribution)
All three servers run the same consistent hashing algorithm to decide which Redis node holds a given URL cache key.

How it works:
1. Hash each Redis node's address onto a virtual ring (0–360)
2. Hash the short code onto the same ring
3. Move clockwise to find the nearest Redis node
4. That node owns the cache key

Result: `hash(short_code) → Redis node` is deterministic and identical on all servers. No coordination needed. Adding or removing a Redis node only remaps a fraction of keys.

Note: consistent hashing applies to URL cache keys only. The `clicks` stream is always on `redis_1` (hardcoded, not hashed).

### 7-Day Cache TTL with Repopulate-on-Miss
All Redis cache entries have a fixed 7-day TTL. The cached value is a JSON blob containing `original_url` and `expires_at`, so expiry can be checked in application code on a cache hit without touching Postgres.

On cache miss (key expired or evicted):
1. Read from Postgres
2. If link is expired → 410; do not repopulate
3. If link is valid → redirect; repopulate Redis with another 7-day TTL

### Link Expiry
- Default `expires_at` = `created_at + 1 year`
- On redirect: check `expires_at` against current time in application code (from cached value or Postgres fallback) → 410 Gone if expired
- No background pruning; expired links remain in Postgres until manually cleaned

### Redis Streams for Click Counting
On every successful redirect, the handling FastAPI server does:
```
XADD clicks * code <short_code>
```
This is non-blocking and does not add latency to the redirect path.

The standalone `click_consumer` process:
- Uses a Redis Streams consumer group (`click_workers`, consumer `consumer_1`)
- Reads batches of events with `XREADGROUP`
- Issues `UPDATE links SET clicks = clicks + 1 WHERE code = X` for each event
- Acknowledges events with `XACK` after successful DB write (at-least-once guarantee)

---

## Request Flows

### Creating a Short URL (`POST /shorten`)
1. Request hits Nginx → routed to any FastAPI server (e.g. server 2)
2. Server 2 generates a unique short code using its Snowflake ID
3. Sets `expires_at = now() + 1 year`
4. Writes `{code, original_url, expires_at}` to Postgres
5. Determines which Redis node owns this code via consistent hashing
6. Caches `url:{code} → {original_url, expires_at}` in that Redis node (TTL = 7 days)
7. Returns the short URL to the user

### Redirecting (`GET /{code}`)
1. Request hits Nginx → routed to any FastAPI server (e.g. server 3)
2. Server 3 hashes the code → determines Redis node (e.g. Redis 1)
3. Queries Redis 1 for `url:{code}`
4. **Cache hit** → parse `{original_url, expires_at}` from cached JSON
   - If `expires_at < now()` → 410 Gone
   - Otherwise → 302 redirect; `XADD clicks * code <code>` to Redis Stream (non-blocking)
5. **Cache miss** → query Postgres for the link
   - Not found → 404
   - Found but expired → 410 Gone (do not repopulate Redis)
   - Found and valid → 302 redirect; re-populate Redis (7-day TTL); `XADD clicks * code <code>`

### Analytics (`GET /analytics/{code}`)
1. Query Postgres directly for `clicks`, `created_at`, `expires_at`, `original_url`
2. Return stats JSON

---

## Data Model

```sql
CREATE TABLE links (
  id           BIGSERIAL PRIMARY KEY,
  code         TEXT UNIQUE NOT NULL,
  original_url TEXT NOT NULL,
  clicks       INTEGER DEFAULT 0,
  expires_at   TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT now()
);
```

Note: `expires_at` is `NOT NULL` — the application always sets a default of `now() + 1 year`.

---

## Project Structure

```
q/backend/
├── docker-compose.yml          # Full infrastructure definition
├── nginx/
│   └── nginx.conf              # Load balancer config
├── api/
│   └── index.py                # FastAPI entry point
├── app/
│   ├── db.py                   # Postgres client
│   ├── cache.py                # Redis client with consistent hashing + stream producer
│   ├── snowflake.py            # Unique ID generator
│   ├── models.py               # Pydantic schemas
│   └── routes/
│       ├── links.py            # POST /shorten, GET /analytics/{code}
│       └── redirect.py         # GET /{code}
├── consumer/
│   └── click_consumer.py       # Standalone Redis Streams consumer
├── tests/
│   ├── unit/
│   │   ├── test_snowflake.py
│   │   ├── test_consistent_hashing.py
│   │   └── test_shortener.py
│   ├── integration/
│   │   ├── test_cache.py
│   │   └── test_db.py
│   ├── api/
│   │   ├── test_routes_links.py
│   │   ├── test_routes_redirect.py
│   │   └── test_routes_analytics.py
│   └── load/
│       └── test_distributed.py
├── requirements.txt
└── .env
```

---

## Infrastructure (Docker Compose)

Services:
- `nginx` — load balancer, port 80
- `api_1`, `api_2`, `api_3` — FastAPI instances, each with `SERVER_ID` env var set
- `redis_1` — Redis cache node 1 + hosts the `clicks` stream
- `redis_2` — Redis cache node 2
- `postgres` — single Postgres instance (reads and writes)
- `click_consumer` — standalone Redis Streams consumer; depends on `redis_1` and `postgres`

---

## Testing Strategy

### Unit Tests
Test each component in isolation, no Docker needed.

- **`test_snowflake.py`** — assert uniqueness across server IDs, assert no collisions under burst (same millisecond), assert codes are valid base62
- **`test_consistent_hashing.py`** — assert same key always maps to same node, assert distribution is roughly even across nodes, assert minimal key remapping when a node is added/removed
- **`test_shortener.py`** — assert short code generation, assert default `expires_at` is ~1 year from now, assert expired links return 410

### Integration Tests
Spin up real Redis and Postgres (via Docker Compose or pytest fixtures), test the full read/write path.

- **`test_cache.py`** — assert cache is populated on write, assert cache hit returns correct `{original_url, expires_at}`, assert cache miss falls back to Postgres and repopulates Redis with 7-day TTL, assert expired links are not repopulated, assert `XADD` is called on redirect
- **`test_db.py`** — assert link is persisted to Postgres, assert click count increments correctly via consumer, assert `expires_at` defaults to 1 year when not provided

### API Tests
Use `httpx` + FastAPI's `TestClient` to test endpoints end-to-end.

- **`test_routes_links.py`** — `POST /shorten` returns a valid short code, duplicate URLs get unique codes, missing URL returns 422, links with explicit TTL set correct `expires_at`, default `expires_at` is ~1 year
- **`test_routes_redirect.py`** — valid code returns 302 with correct `Location` header, expired code returns 410 Gone, unknown code returns 404, cache miss path repopulates Redis
- **`test_routes_analytics.py`** — returns correct click count, `created_at`, `expires_at`, `original_url`

### Distributed / Load Tests
Validate behavior across all three server instances.

- Fire requests at Nginx and assert all three servers handle traffic (check `X-Server-ID` response header)
- Generate 1000 short codes across all servers, assert zero collisions
- Kill one server, assert Nginx routes around it and service stays up
- Write a link via server 1, read it via server 2 and server 3 — assert consistent results
- Simulate Redis node failure on `redis_2`, assert affected keys fall back to Postgres
- Verify click consumer processes stream events and increments Postgres click counts correctly

Tools: `pytest`, `httpx`, `pytest-asyncio` for async routes, `fakeredis` for unit-level Redis mocking, `fastapi.testclient` for endpoint tests, `conftest.py` fixtures for test data setup and teardown.

---

## Implementation Order

1. `docker-compose.yml` — define all services and networking first
2. `nginx.conf` — upstream block pointing to all three API servers
3. `snowflake.py` — ID generation logic
4. `cache.py` — Redis client with consistent hashing ring, 7-day TTL, stream producer (`XADD`)
5. `db.py` — Postgres client (single instance)
6. `routes/links.py` — shorten and analytics endpoints
7. `routes/redirect.py` — redirect endpoint with expiry enforcement and stream publish
8. `consumer/click_consumer.py` — Redis Streams consumer group, batch click updates
9. Unit, integration, and API tests
10. End-to-end validation across all containers