"""asyncpg connection pool — singleton pattern."""
import asyncio
import os

import asyncpg

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _dsn() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ["POSTGRES_DB"]
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def init_pool(min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    async with _pool_lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(_dsn(), min_size=min_size, max_size=max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    return _pool
