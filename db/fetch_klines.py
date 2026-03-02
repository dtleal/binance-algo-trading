"""Download historical klines from Binance Futures and insert directly into DB.

Replaces scripts/fetch_klines.py — no CSV files created.

Usage:
    poetry run python -m db.fetch_klines --symbol DOGEUSDT --days 365
    poetry run python -m db.fetch_klines --symbol DOGEUSDT --days 365 --timeframe 1m
"""
from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timezone

import asyncpg
import requests


_FAPI_URL = "https://fapi.binance.com/fapi/v1/klines"
_BATCH_INSERT = 5000
_REQUEST_DELAY = 0.25  # seconds between API calls (~4 req/sec)


def _fetch_batch(symbol: str, interval: str, start_ms: int, limit: int = 1000) -> list:
    """Fetch one page of klines from Binance Futures REST API."""
    resp = requests.get(
        _FAPI_URL,
        params={
            "symbol": symbol,
            "interval": interval,
            "startTime": str(start_ms),
            "limit": limit,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


async def fetch_and_insert(
    pool: asyncpg.Pool,
    symbol: str,
    interval: str,
    start_ms: int,
) -> int:
    """Download all klines from start_ms and insert into DB. Returns rows inserted."""
    total_inserted = 0
    current_start = start_ms

    while True:
        batch = await asyncio.to_thread(
            _fetch_batch, symbol, interval, current_start
        )
        if not batch:
            break

        rows = [
            (
                symbol.upper(),
                interval,
                datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),  # open_time
                float(k[1]),   # open
                float(k[2]),   # high
                float(k[3]),   # low
                float(k[4]),   # close
                float(k[5]),   # volume
                datetime.fromtimestamp(int(k[6]) / 1000, tz=timezone.utc),  # close_time
            )
            for k in batch
        ]

        # Insert in sub-batches
        for i in range(0, len(rows), _BATCH_INSERT):
            chunk = rows[i : i + _BATCH_INSERT]
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO klines
                        (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
                    """,
                    chunk,
                )
            total_inserted += len(chunk)

        print(f"  {symbol} {interval}: {total_inserted:,} rows…", end="\r", flush=True)

        if len(batch) < 1000:
            break

        # Advance past last close_time
        current_start = int(batch[-1][6]) + 1
        await asyncio.sleep(_REQUEST_DELAY)

    print(f"  {symbol} {interval}: {total_inserted:,} rows inserted" + " " * 10)
    return total_inserted


async def run(symbol: str, days: int, timeframe: str = "1m") -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool

    start_dt = datetime.now(timezone.utc).timestamp() - days * 86_400
    start_ms = int(start_dt * 1000)

    print(f"Fetching {symbol} {timeframe} — last {days} days…")

    pool = await init_pool()
    inserted = await fetch_and_insert(pool, symbol.upper(), timeframe, start_ms)
    await close_pool()

    print(f"Done — {inserted:,} klines inserted into DB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download klines → DB (no CSV)")
    parser.add_argument("--symbol", required=True, help="Symbol, e.g. DOGEUSDT")
    parser.add_argument("--days", type=int, default=365, help="Days of history (default 365)")
    parser.add_argument("--timeframe", default="1m", help="Kline interval (default 1m)")
    args = parser.parse_args()
    asyncio.run(run(args.symbol.upper(), args.days, args.timeframe))
