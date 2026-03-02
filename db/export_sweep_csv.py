"""Export klines from DB to a temporary CSV for the Rust sweep binary.

The Rust binary expects 11 columns:
  open_time_ms, open, high, low, close, volume, close_time_ms,
  quote_volume, trades, taker_buy_base_vol, taker_buy_quote_vol

Only the first 7 are used by the backtest logic; the rest are written as 0.

Usage:
    poetry run python -m db.export_sweep_csv --symbol DOGEUSDT --timeframe 5m
    poetry run python -m db.export_sweep_csv --symbol DOGEUSDT --timeframe 5m --output /tmp/doge_5m.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path


async def export(symbol: str, timeframe: str, output: str | None = None) -> Path:
    """Export klines for (symbol, timeframe) to a CSV. Returns the output path."""
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool, get_pool

    await init_pool()
    pool = get_pool()

    rows = await pool.fetch(
        """
        SELECT
            extract(epoch from open_time)::bigint * 1000  AS open_time_ms,
            open, high, low, close, volume,
            extract(epoch from close_time)::bigint * 1000 AS close_time_ms
        FROM klines
        WHERE symbol = $1 AND timeframe = $2
        ORDER BY open_time
        """,
        symbol.upper(),
        timeframe,
    )
    await close_pool()

    if not rows:
        raise RuntimeError(
            f"No klines found for {symbol} {timeframe} in DB. "
            "Run db.fetch_klines and db.aggregate_klines first."
        )

    if output is None:
        output = f"/tmp/{symbol.lower()}_{timeframe}_klines.csv"

    path = Path(output)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        # Header (matches scripts/fetch_klines.py output format)
        writer.writerow([
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base_vol", "taker_buy_quote_vol",
        ])
        for r in rows:
            writer.writerow([
                r["open_time_ms"],
                r["open"], r["high"], r["low"], r["close"], r["volume"],
                r["close_time_ms"],
                0, 0, 0, 0,  # unused columns
            ])

    print(f"  Exported {len(rows):,} candles → {path}")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export klines DB → CSV for Rust sweep")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True, choices=["1m", "5m", "15m", "30m", "1h"])
    parser.add_argument("--output", default=None, help="Output CSV path (default: /tmp/...)")
    args = parser.parse_args()
    asyncio.run(export(args.symbol.upper(), args.timeframe, args.output))
