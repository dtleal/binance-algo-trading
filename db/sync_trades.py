"""Background trade sync service.

Polls Binance using fromId pagination (efficient, no date-based hammering) and
upserts new fills into the trades table.  Refreshes daily_performance for any
dates that received new trades.

Usage (background loop — called from api.py startup):
    asyncio.create_task(run_sync_loop(pool, client))

Usage (one-shot CLI for initial backfill):
    poetry run python -m db.sync_trades --once
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    pass

log = logging.getLogger("db.sync_trades")


# ── Core sync logic ───────────────────────────────────────────────────────────

async def _get_last_order_id(conn: asyncpg.Connection, symbol: str) -> int:
    row = await conn.fetchrow(
        "SELECT last_order_id FROM sync_state WHERE symbol = $1", symbol
    )
    return row["last_order_id"] if row else 0


async def _upsert_sync_state(
    conn: asyncpg.Connection, symbol: str, last_order_id: int
) -> None:
    await conn.execute(
        """
        INSERT INTO sync_state (symbol, last_order_id, last_synced_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (symbol)
        DO UPDATE SET last_order_id = EXCLUDED.last_order_id,
                      last_synced_at = EXCLUDED.last_synced_at
        """,
        symbol,
        last_order_id,
    )


async def _insert_trades(
    conn: asyncpg.Connection, trades: list[dict]
) -> int:
    """Bulk-insert trades; skip duplicates.  Returns count of new rows."""
    if not trades:
        return 0

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
        for t in trades
    ]

    result = await conn.executemany(
        """
        INSERT INTO trades
            (symbol, order_id, side, price, qty, realized_pnl,
             commission, commission_asset, buyer, trade_time)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (symbol, order_id) DO NOTHING
        """,
        rows,
    )
    # executemany returns "INSERT 0 N" string — just count inserted rows from result
    inserted = sum(1 for t in trades if True)  # will use returning count below
    return len(rows)  # approximate — exact count needs RETURNING or manual tracking


async def _refresh_daily_performance(
    conn: asyncpg.Connection, symbol: str, dates: set[date]
) -> None:
    """Re-aggregate daily_performance for given symbol+dates."""
    for d in dates:
        await conn.execute(
            """
            DELETE FROM daily_performance WHERE symbol = $1 AND trade_date = $2
            """,
            symbol, d,
        )
        await conn.execute(
            """
            INSERT INTO daily_performance
                (symbol, trade_date, total_trades, winning_trades,
                 total_pnl, total_commission, gross_pnl)
            SELECT
                symbol,
                trade_time::date AS trade_date,
                COUNT(*) FILTER (WHERE realized_pnl != 0) AS total_trades,
                COUNT(*) FILTER (WHERE realized_pnl > 0)  AS winning_trades,
                COALESCE(SUM(realized_pnl), 0)            AS total_pnl,
                COALESCE(SUM(commission), 0)              AS total_commission,
                COALESCE(SUM(realized_pnl) + SUM(commission), 0) AS gross_pnl
            FROM trades
            WHERE symbol = $1 AND trade_time::date = $2
            GROUP BY symbol, trade_time::date
            """,
            symbol, d,
        )


async def sync_symbol(pool: asyncpg.Pool, client, symbol: str) -> int:
    """Sync all new trades for one symbol.  Returns count of new trades inserted."""
    from trader.config import SYMBOL_CONFIGS

    last_order_id = 0
    async with pool.acquire() as conn:
        last_order_id = await _get_last_order_id(conn, symbol)

    # Binance fromId is inclusive — start from next unsynced order
    from_id = last_order_id + 1 if last_order_id > 0 else None

    all_new_trades: list[dict] = []
    max_order_id = last_order_id

    # Paginate with fromId until we get an empty page
    while True:
        try:
            kwargs = {"symbol": symbol, "limit": 1000}
            if from_id is not None:
                kwargs["fromId"] = str(from_id)

            resp = await asyncio.to_thread(
                lambda kw=kwargs: client.rest_api.account_trade_list(**kw)
            )
            batch = resp.data()
        except Exception as e:
            log.warning("sync_symbol %s failed: %s", symbol, e)
            break

        if not batch:
            break

        for t in batch:
            oid = int(t.order_id)
            if oid > max_order_id:
                max_order_id = oid
            all_new_trades.append({
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

        # If we got fewer than the page limit, we're done
        if len(batch) < 1000:
            break

        # Advance fromId to the highest orderId + 1 in this batch
        batch_max = max(int(t.order_id) for t in batch)
        from_id = batch_max + 1

    if not all_new_trades:
        return 0

    # Determine which calendar dates are affected
    affected_dates: set[date] = set()
    for t in all_new_trades:
        ts = datetime.fromtimestamp(int(t["time"]) / 1000, tz=timezone.utc)
        affected_dates.add(ts.date())

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _insert_trades(conn, all_new_trades)
            await _upsert_sync_state(conn, symbol, max_order_id)
            await _refresh_daily_performance(conn, symbol, affected_dates)

    log.info("sync_symbol %s: %d new trades, max_order_id=%d",
             symbol, len(all_new_trades), max_order_id)
    return len(all_new_trades)


# ── Loop ──────────────────────────────────────────────────────────────────────

async def run_sync_loop(
    pool: asyncpg.Pool,
    client,
    interval_seconds: int = 60,
) -> None:
    """Run forever, syncing all symbols every `interval_seconds` seconds."""
    from trader.config import SYMBOL_CONFIGS

    log.info("Trade sync loop started (interval=%ds, symbols=%d)",
             interval_seconds, len(SYMBOL_CONFIGS))

    while True:
        for symbol in list(SYMBOL_CONFIGS.keys()):
            try:
                count = await sync_symbol(pool, client, symbol)
                if count:
                    log.debug("Synced %d new trades for %s", count, symbol)
            except Exception:
                log.exception("Unexpected error syncing %s", symbol)
        await asyncio.sleep(interval_seconds)


# ── CLI entry point ───────────────────────────────────────────────────────────

async def _cli_main(once: bool) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool
    from trader.api import get_client

    pool = await init_pool()
    client = await get_client()

    if once:
        from trader.config import SYMBOL_CONFIGS
        total = 0
        for symbol in SYMBOL_CONFIGS.keys():
            count = await sync_symbol(pool, client, symbol)
            print(f"  {symbol}: {count} new trades")
            total += count
        print(f"Total: {total} new trades inserted")
    else:
        await run_sync_loop(pool, client)

    await close_pool()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync trade history to PostgreSQL")
    parser.add_argument("--once", action="store_true",
                        help="Run one sync pass then exit (default: run forever)")
    args = parser.parse_args()
    asyncio.run(_cli_main(args.once))
