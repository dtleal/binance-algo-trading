"""Flyway-style migration runner.

Scans db/migrations/0*.sql in numeric order, applies any version not yet
recorded in the schema_migrations table, and records each applied version.

Usage (CLI):
    poetry run python -m db.migrate

Usage (programmatic — called on server startup):
    from db.migrate import run
    await run(pool)  # pass existing pool, or omit to use get_pool()
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run(pool: asyncpg.Pool | None = None) -> None:
    from db.connection import get_pool, init_pool

    if pool is None:
        pool = get_pool()

    # Ensure schema_migrations table exists before querying it
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     TEXT        PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        rows = await conn.fetch("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in rows}

    migration_files = sorted(_MIGRATIONS_DIR.glob("0*.sql"))
    if not migration_files:
        print("[migrate] No migration files found in", _MIGRATIONS_DIR)
        return

    applied_count = 0
    for path in migration_files:
        version = path.stem  # e.g. "001_initial"
        if version in applied:
            continue

        sql = path.read_text()
        print(f"[migrate] Applying {path.name}…")
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )
        print(f"[migrate] Applied  {path.name}")
        applied_count += 1

    if applied_count == 0:
        print("[migrate] Schema up to date — no migrations applied")
    else:
        print(f"[migrate] Done — {applied_count} migration(s) applied")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    async def _main():
        from db.connection import init_pool, close_pool
        pool = await init_pool()
        await run(pool)
        await close_pool()

    asyncio.run(_main())
