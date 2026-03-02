"""Kline query functions."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg


async def get_klines(
    pool: asyncpg.Pool,
    symbol: str,
    timeframe: str = "1m",
    limit: int = 500,
) -> list[dict]:
    """Return the most recent `limit` candles for symbol/timeframe."""
    rows = await pool.fetch(
        """
        SELECT open_time, open, high, low, close, volume
        FROM klines
        WHERE symbol = $1 AND timeframe = $2
        ORDER BY open_time DESC
        LIMIT $3
        """,
        symbol.upper(),
        timeframe,
        limit,
    )
    # Return in ascending order (oldest first) for charting libraries
    return [
        {
            "time":   int(r["open_time"].timestamp()),   # seconds epoch for TradingView
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r["volume"]),
        }
        for r in reversed(rows)
    ]
