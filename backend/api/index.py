"""
FastAPI application entry point.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from app import db, cache, config
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
async def add_server_id_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Server-ID"] = str(config.SERVER_ID)
    return response


# Register routes. Note: links router must be included BEFORE redirect router
# so that the general /{code} wildcard doesn't hijack /shorten or /analytics/{code}
app.include_router(links.router)
app.include_router(redirect.router)


@app.get("/health", summary="Health check endpoint")
async def health():
    return {"status": "healthy", "server_id": config.SERVER_ID}
