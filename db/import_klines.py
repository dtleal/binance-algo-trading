"""Import historical kline CSVs into the klines table.

Scans data/klines/ for files matching SYMBOL_TF_klines.csv, parses them,
and bulk-inserts into the DB.  Idempotent — uses ON CONFLICT DO NOTHING.

CSV format (from fetch_klines.py):
    open_time,open,high,low,close,volume,close_time,...

Usage:
    poetry run python -m db.import_klines [--symbol SYMBOL] [--timeframe TF]
    make db-import-klines
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

log = logging.getLogger("db.import_klines")

_DATA_DIR = Path(__file__).parent.parent / "data" / "klines"
_BATCH_SIZE = 5_000   # rows per executemany call


def _parse_symbol_tf(filename: str) -> tuple[str, str] | None:
    """Extract (SYMBOL, TF) from a filename like axsusdt_1m_klines.csv."""
    name = filename.replace("_klines.csv", "")
    parts = name.rsplit("_", 1)
    if len(parts) != 2:
        return None
    symbol, tf = parts
    valid_tfs = {"1m", "5m", "15m", "30m", "1h"}
    if tf not in valid_tfs:
        return None
    return symbol.upper(), tf


def _read_csv(path: Path, symbol: str, tf: str) -> list[tuple]:
    """Parse CSV and return list of row-tuples ready for asyncpg executemany."""
    rows = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for line in reader:
            open_time = datetime.fromtimestamp(
                int(line["open_time"]) / 1000, tz=timezone.utc
            )
            close_time = datetime.fromtimestamp(
                int(line["close_time"]) / 1000, tz=timezone.utc
            )
            rows.append((
                symbol,
                tf,
                open_time,
                float(line["open"]),
                float(line["high"]),
                float(line["low"]),
                float(line["close"]),
                float(line["volume"]),
                close_time,
            ))
    return rows


async def import_file(
    pool: asyncpg.Pool, path: Path, symbol: str, tf: str
) -> int:
    """Import one CSV file.  Returns number of rows inserted."""
    print(f"  Reading {path.name}…", end=" ", flush=True)
    rows = _read_csv(path, symbol, tf)
    print(f"{len(rows):,} rows", end=" → ", flush=True)

    inserted = 0
    async with pool.acquire() as conn:
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            await conn.executemany(
                """
                INSERT INTO klines
                    (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
                """,
                batch,
            )
            inserted += len(batch)

    print(f"done (up to {inserted:,} inserted)")
    return inserted


async def import_all(
    pool: asyncpg.Pool,
    symbol_filter: str | None = None,
    tf_filter: str | None = None,
) -> None:
    """Import all matching CSV files from data/klines/."""
    files = sorted(_DATA_DIR.glob("*_klines.csv"))
    if not files:
        print(f"No kline CSV files found in {_DATA_DIR}")
        return

    total_inserted = 0
    for path in files:
        parsed = _parse_symbol_tf(path.name)
        if parsed is None:
            continue
        symbol, tf = parsed
        if symbol_filter and symbol != symbol_filter.upper():
            continue
        if tf_filter and tf != tf_filter:
            continue
        n = await import_file(pool, path, symbol, tf)
        total_inserted += n

    print(f"\nImport complete — total rows inserted (approx): {total_inserted:,}")


async def _main(symbol: str | None, tf: str | None) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool
    from db.migrate import run as migrate

    pool = await init_pool()
    await migrate(pool)
    await import_all(pool, symbol_filter=symbol, tf_filter=tf)
    await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import kline CSVs into PostgreSQL")
    parser.add_argument("--symbol", default=None, help="Only import this symbol (e.g. AXSUSDT)")
    parser.add_argument("--timeframe", default=None, help="Only import this timeframe (e.g. 1m)")
    args = parser.parse_args()
    asyncio.run(_main(args.symbol, args.timeframe))
