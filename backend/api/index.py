"""
FastAPI application entry point.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db, cache, config, ratelimit
from app.routes import links, redirect


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB pool
    await db.init_pool()
    yield
    # Shutdown: Close DB and Redis client connections
    await db.close_pool()
    await cache.close_clients()


app = FastAPI(
    title="Q — A lightweight, distributed URL shortening service",
    version="1.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Enforce per-IP sliding-window rate limits using Redis."""
    # Exempt health checks from rate limiting
    if request.url.path == "/health":
        return await call_next(request)

    ip = ratelimit.get_client_ip(
        dict(request.headers),
        request.client.host if request.client else None,
    )
    _prefix, cfg = ratelimit.resolve_config(request.url.path)

    allowed, remaining, reset_in = await ratelimit.check_rate_limit(
        ip, cfg.limit, cfg.window_seconds
    )

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please try again later."},
            headers={
                "X-RateLimit-Limit": str(cfg.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_in),
                "Retry-After": str(reset_in),
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(cfg.limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)
    return response


@app.middleware("http")
async def add_server_id_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Server-ID"] = str(config.SERVER_ID)
    return response


# Register routes. Note: links router and health check must be included BEFORE redirect router
# so that the general /{code} wildcard doesn't hijack /shorten, /analytics/{code}, or /health
app.include_router(links.router)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "healthy", "server_id": config.SERVER_ID}


app.include_router(redirect.router)

