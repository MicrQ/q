"""
Central settings loaded from environment variables (or .env file).
All modules import from here — nothing reads os.environ directly.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Server identity ---
SERVER_ID: int = int(os.environ["SERVER_ID"])

# --- Postgres ---
POSTGRES_HOST: str = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_DB: str = os.environ.get("POSTGRES_DB", "q")
POSTGRES_USER: str = os.environ.get("POSTGRES_USER", "q")
POSTGRES_PASSWORD: str = os.environ.get("POSTGRES_PASSWORD", "q")

# --- Redis ---
# Comma-separated list of "host:port" strings, one per node.
# Order is significant: the consistent hashing ring is built from this list.
REDIS_NODES: list[str] = os.environ.get("REDIS_NODES", "localhost:6379").split(",")
