"""Trade query functions — replace direct Binance API calls in api.py."""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg


async def get_trades(
    pool: asyncpg.Pool,
    symbol: str | None = None,
    days: int = 7,
) -> list[dict]:
    """Return trades from the DB within the last `days` days.

    Mirrors the shape returned by the old Binance API endpoint so existing
    callers in api.py need minimal changes.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86_400
    cutoff_ts = datetime.fromtimestamp(cutoff, tz=timezone.utc)

    if symbol:
        rows = await pool.fetch(
            """
            SELECT t.symbol, t.order_id, t.side, t.price, t.qty, t.realized_pnl,
                   t.commission, t.commission_asset, t.buyer, t.trade_time,
                   COALESCE(sc.strategy_name, 'Unknown') AS strategy_name
            FROM trades t
            LEFT JOIN symbol_configs sc ON sc.symbol = t.symbol
            WHERE t.symbol = $1 AND t.trade_time >= $2
            ORDER BY t.trade_time DESC
            """,
            symbol.upper(),
            cutoff_ts,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT t.symbol, t.order_id, t.side, t.price, t.qty, t.realized_pnl,
                   t.commission, t.commission_asset, t.buyer, t.trade_time,
                   COALESCE(sc.strategy_name, 'Unknown') AS strategy_name
            FROM trades t
            LEFT JOIN symbol_configs sc ON sc.symbol = t.symbol
            WHERE t.trade_time >= $1
            ORDER BY t.trade_time DESC
            """,
            cutoff_ts,
        )

    return [
        {
            "symbol":           r["symbol"],
            "order_id":         r["order_id"],
            "side":             r["side"],
            "price":            float(r["price"]),
            "qty":              float(r["qty"]),
            "realized_pnl":     float(r["realized_pnl"]),
            "commission":       float(r["commission"]),
            "commission_asset": r["commission_asset"],
            "buyer":            r["buyer"],
            "strategy":         r["strategy_name"],
            # Keep ms-epoch integer for backwards-compat with frontend
            "time":             int(r["trade_time"].timestamp() * 1000),
        }
        for r in rows
    ]


async def get_commissions(pool: asyncpg.Pool, days: int = 30) -> dict:
    """Aggregate commissions by asset, symbol, and daily USDT."""
    trades = await get_trades(pool, days=days)

    by_asset: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    daily: dict[str, float] = {}

    for t in trades:
        asset = t["commission_asset"]
        amt = t["commission"]
        sym = t["symbol"]
        date = datetime.fromtimestamp(t["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        by_asset[asset] = by_asset.get(asset, 0.0) + amt
        by_symbol[sym] = by_symbol.get(sym, 0.0) + amt
        if asset == "USDT":
            daily[date] = daily.get(date, 0.0) + amt

    sorted_daily = [
        {"date": d, "commission": round(v, 6)}
        for d, v in sorted(daily.items())
    ]
    return {
        "by_asset":   {k: round(v, 6) for k, v in by_asset.items()},
        "by_symbol":  {k: round(v, 6) for k, v in by_symbol.items()},
        "daily":      sorted_daily,
        "total_usdt": round(by_asset.get("USDT", 0.0), 6),
        "days":       days,
    }
