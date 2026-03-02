"""Performance query functions."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg


async def get_daily_pnl(
    pool: asyncpg.Pool,
    symbol: str | None = None,
    days: int = 30,
) -> list[dict]:
    """Return daily P&L aggregated from daily_performance table."""
    from datetime import date, timedelta
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)

    if symbol:
        rows = await pool.fetch(
            """
            SELECT symbol, trade_date, total_trades, winning_trades,
                   total_pnl, total_commission, gross_pnl
            FROM daily_performance
            WHERE symbol = $1 AND trade_date >= $2
            ORDER BY trade_date DESC
            """,
            symbol.upper(),
            cutoff,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT symbol, trade_date, total_trades, winning_trades,
                   total_pnl, total_commission, gross_pnl
            FROM daily_performance
            WHERE trade_date >= $1
            ORDER BY trade_date DESC, symbol
            """,
            cutoff,
        )

    return [
        {
            "symbol":           r["symbol"],
            "date":             r["trade_date"].isoformat(),
            "total_trades":     r["total_trades"],
            "winning_trades":   r["winning_trades"],
            "win_rate":         round(r["winning_trades"] / r["total_trades"] * 100, 1)
                                if r["total_trades"] > 0 else 0,
            "total_pnl":        float(r["total_pnl"]),
            "total_commission": float(r["total_commission"]),
            "gross_pnl":        float(r["gross_pnl"]),
        }
        for r in rows
    ]
