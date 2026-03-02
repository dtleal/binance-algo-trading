"""Aggregate 1m klines in DB to higher timeframes using SQL.

Replaces scripts/aggregate_klines.py — no CSV files. Reads from and writes
to the klines table directly.

Usage:
    poetry run python -m db.aggregate_klines --symbol DOGEUSDT
    poetry run python -m db.aggregate_klines --symbol ALL   # all symbols with 1m data
"""
from __future__ import annotations

import argparse
import asyncio

import asyncpg


_TIMEFRAMES = {
    "5m":  5,
    "15m": 15,
    "30m": 30,
    "1h":  60,
}

_AGG_SQL = """
INSERT INTO klines (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
SELECT
    $1 AS symbol,
    $2 AS timeframe,
    to_timestamp(
        floor(extract(epoch from open_time) / ($3 * 60)) * ($3 * 60)
    ) AT TIME ZONE 'UTC'                                   AS bucket_open,
    (array_agg(open  ORDER BY open_time))[1]               AS open,
    MAX(high)                                              AS high,
    MIN(low)                                               AS low,
    (array_agg(close ORDER BY open_time DESC))[1]          AS close,
    SUM(volume)                                            AS volume,
    MAX(close_time)                                        AS close_time
FROM klines
WHERE symbol = $1 AND timeframe = '1m'
GROUP BY bucket_open
ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
"""


async def aggregate_symbol(pool: asyncpg.Pool, symbol: str) -> dict[str, int]:
    """Aggregate all higher timeframes for one symbol. Returns {tf: rows_inserted}."""
    results: dict[str, int] = {}

    # Check 1m data exists
    count_1m = await pool.fetchval(
        "SELECT COUNT(*) FROM klines WHERE symbol = $1 AND timeframe = '1m'",
        symbol,
    )
    if not count_1m:
        print(f"  {symbol}: no 1m data in DB — skipping")
        return {}

    async with pool.acquire() as conn:
        for tf, minutes in _TIMEFRAMES.items():
            # Count before
            before = await conn.fetchval(
                "SELECT COUNT(*) FROM klines WHERE symbol = $1 AND timeframe = $2",
                symbol, tf,
            )
            await conn.execute(_AGG_SQL, symbol, tf, minutes)
            after = await conn.fetchval(
                "SELECT COUNT(*) FROM klines WHERE symbol = $1 AND timeframe = $2",
                symbol, tf,
            )
            inserted = after - before
            results[tf] = inserted
            print(f"  {symbol} {tf}: {inserted:,} new candles ({after:,} total)")

    return results


async def run(symbol: str) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool

    pool = await init_pool()

    if symbol.upper() == "ALL":
        symbols = [
            r["symbol"]
            for r in await pool.fetch(
                "SELECT DISTINCT symbol FROM klines WHERE timeframe = '1m' ORDER BY symbol"
            )
        ]
        print(f"Aggregating {len(symbols)} symbols…")
        for sym in symbols:
            await aggregate_symbol(pool, sym)
    else:
        await aggregate_symbol(pool, symbol.upper())

    await close_pool()
    print("Aggregation complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate 1m klines → higher TFs in DB")
    parser.add_argument(
        "--symbol", required=True,
        help="Symbol (e.g. DOGEUSDT) or ALL for every symbol with 1m data",
    )
    args = parser.parse_args()
    asyncio.run(run(args.symbol))
