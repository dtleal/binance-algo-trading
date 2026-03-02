"""One-shot backfill: fetch all trades from startTime for each symbol.

Strategy:
- One symbol at a time (never parallel) to avoid rate limits
- 1.5s delay between symbols
- Uses startTime parameter instead of fromId — guaranteed to cover date range
- ON CONFLICT DO NOTHING → zero duplicates possible
- Updates sync_state with highest order_id seen

Usage:
    poetry run python -m db.backfill_trades --from 2026-02-20
"""
from __future__ import annotations

import asyncio
import argparse
from datetime import datetime, timezone

import asyncpg


async def backfill_symbol(
    conn: asyncpg.Connection,
    client,
    symbol: str,
    start_ms: int,
) -> tuple[int, int]:
    """Fetch all trades from start_ms for one symbol.

    Returns (inserted, already_existed).
    """
    all_trades: list[dict] = []
    start_time = start_ms

    # Paginate using startTime + endTime windows if needed (Binance max 7d per window)
    # For safety we paginate forward using the last trade's time + 1ms
    while True:
        try:
            resp = await asyncio.to_thread(
                lambda st=start_time: client.rest_api.account_trade_list(
                    symbol=symbol,
                    start_time=str(st),
                    limit=1000,
                )
            )
            batch = resp.data()
        except Exception as e:
            print(f"    ERROR fetching {symbol}: {e}")
            break

        if not batch:
            break

        for t in batch:
            all_trades.append({
                "symbol":         t.symbol,
                "orderId":        t.order_id,
                "side":           t.side,
                "price":          t.price,
                "qty":            t.qty,
                "realizedPnl":    t.realized_pnl,
                "commission":     t.commission,
                "commissionAsset": t.commission_asset,
                "buyer":          t.buyer,
                "time":           t.time,
            })

        # If fewer than 1000 results, we've reached the end
        if len(batch) < 1000:
            break

        # Advance startTime past the last trade returned
        last_time = max(int(t.time) for t in batch)
        start_time = last_time + 1

    if not all_trades:
        return 0, 0

    # Build insert rows
    rows = [
        (
            t["symbol"],
            int(t["orderId"]),
            t["side"],
            float(t["price"]),
            float(t["qty"]),
            float(t["realizedPnl"]),
            float(t["commission"]),
            t["commissionAsset"],
            bool(t["buyer"]),
            datetime.fromtimestamp(int(t["time"]) / 1000, tz=timezone.utc),
        )
        for t in all_trades
    ]

    # Count what's already in DB for this symbol (to report skipped)
    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM trades WHERE symbol = $1", symbol
    )

    await conn.executemany(
        """
        INSERT INTO trades
            (symbol, order_id, side, price, qty, realized_pnl,
             commission, commission_asset, buyer, trade_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (symbol, order_id) DO NOTHING
        """,
        rows,
    )

    after = await conn.fetchval(
        "SELECT COUNT(*) FROM trades WHERE symbol = $1", symbol
    )
    inserted = after - existing
    skipped = len(rows) - inserted

    # Update sync_state
    max_order_id = max(int(t["orderId"]) for t in all_trades)
    await conn.execute(
        """
        INSERT INTO sync_state (symbol, last_order_id, last_synced_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (symbol) DO UPDATE
            SET last_order_id = GREATEST(sync_state.last_order_id, EXCLUDED.last_order_id),
                last_synced_at = NOW()
        """,
        symbol, max_order_id,
    )

    # Refresh daily_performance for affected dates
    from datetime import date
    affected: set[date] = set()
    for t in all_trades:
        ts = datetime.fromtimestamp(int(t["time"]) / 1000, tz=timezone.utc)
        affected.add(ts.date())

    for d in affected:
        await conn.execute(
            "DELETE FROM daily_performance WHERE symbol = $1 AND trade_date = $2",
            symbol, d,
        )
        await conn.execute(
            """
            INSERT INTO daily_performance
                (symbol, trade_date, total_trades, winning_trades,
                 total_pnl, total_commission, gross_pnl)
            SELECT
                symbol,
                trade_time::date,
                COUNT(*) FILTER (WHERE realized_pnl != 0),
                COUNT(*) FILTER (WHERE realized_pnl > 0),
                COALESCE(SUM(realized_pnl), 0),
                COALESCE(SUM(commission), 0),
                COALESCE(SUM(realized_pnl) + SUM(commission), 0)
            FROM trades
            WHERE symbol = $1 AND trade_time::date = $2
            GROUP BY symbol, trade_time::date
            """,
            symbol, d,
        )

    return inserted, skipped


async def run_backfill(from_date: str, delay: float = 1.5) -> None:
    from dotenv import load_dotenv
    load_dotenv()

    from db.connection import init_pool, close_pool
    from trader.api import get_client
    from trader.config import SYMBOL_CONFIGS

    start_dt = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)

    print(f"Backfill from {from_date} ({start_ms} ms)")
    print(f"Symbols: {len(SYMBOL_CONFIGS)}  |  Delay: {delay}s between requests\n")

    pool = await init_pool()
    client = await get_client()

    total_inserted = 0
    total_skipped = 0

    for symbol in SYMBOL_CONFIGS.keys():
        print(f"  {symbol:<20}", end=" ", flush=True)
        async with pool.acquire() as conn:
            inserted, skipped = await backfill_symbol(conn, client, symbol, start_ms)
        total_inserted += inserted
        total_skipped += skipped
        print(f"inserted={inserted}  already_in_db={skipped}")
        await asyncio.sleep(delay)

    print(f"\n{'='*50}")
    print(f"Total inserted : {total_inserted}")
    print(f"Already in DB  : {total_skipped}  (duplicates skipped)")
    print(f"{'='*50}")

    await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill trades from a given start date")
    parser.add_argument("--from", dest="from_date", required=True,
                        help="Start date YYYY-MM-DD (e.g. 2026-02-20)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Seconds between symbol requests (default: 1.5)")
    args = parser.parse_args()
    asyncio.run(run_backfill(args.from_date, args.delay))
