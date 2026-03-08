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
            int(t["tradeId"]),
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

    # Use RETURNING to count actually-inserted rows (ON CONFLICT DO NOTHING skips dups)
    inserted = 0
    for row in rows:
        result = await conn.fetchval(
            """
            INSERT INTO trades
                (symbol, trade_id, order_id, side, price, qty, realized_pnl,
                 commission, commission_asset, buyer, trade_time)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (symbol, trade_id) DO NOTHING
            RETURNING 1
            """,
            *row,
        )
        if result:
            inserted += 1
    return inserted


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


async def handle_order_trade_update(pool: asyncpg.Pool, event) -> None:
    """Process a UserDataStream OrderTradeUpdate event and insert into DB immediately.

    Called from api.py's UDS callback — gives real-time trade capture the moment
    any order is filled, without waiting for the polling sync loop.
    """
    try:
        from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import OrderTradeUpdate
    except ImportError:
        return

    actual = getattr(event, "actual_instance", None)
    if not isinstance(actual, OrderTradeUpdate) or not actual.o:
        return

    o = actual.o
    # Only process FILLED or PARTIALLY_FILLED — skip NEW/CANCELED/etc.
    order_status = getattr(o, "X", None)
    if order_status not in ("FILLED", "PARTIALLY_FILLED"):
        return

    trade_time_ms = getattr(o, "T", None) or getattr(actual, "T", None)
    if not trade_time_ms:
        trade_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    trade = {
        "symbol":          getattr(o, "s", None),
        "tradeId":         getattr(o, "t", None) or getattr(o, "i", None),
        "orderId":         getattr(o, "i", None),
        "side":            getattr(o, "S", None),   # uppercase S = BUY/SELL
        "price":           getattr(o, "L", None) or getattr(o, "ap", "0"),  # last fill price
        "qty":             getattr(o, "l", None) or "0",   # last fill qty
        "realizedPnl":     getattr(o, "rp", None) or "0",
        "commission":      getattr(o, "n", None) or "0",
        "commissionAsset": getattr(o, "N", None) or "USDT",
        "buyer":           getattr(o, "S", None) == "BUY",
        "time":            trade_time_ms,
    }

    if not trade["symbol"] or trade["orderId"] is None or trade["tradeId"] is None:
        return

    affected_date = datetime.fromtimestamp(int(trade_time_ms) / 1000, tz=timezone.utc).date()

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _insert_trades(conn, [trade])
                await _refresh_daily_performance(conn, trade["symbol"], {affected_date})
        pnl = float(trade["realizedPnl"])
        if pnl != 0:
            log.info("UDS trade: %s %s qty=%s pnl=%.4f", trade["symbol"], trade["side"],
                     trade["qty"], pnl)
    except Exception as e:
        log.warning("handle_order_trade_update failed for %s: %s", trade["symbol"], e)


async def sync_symbol(pool: asyncpg.Pool, client, symbol: str) -> int:
    """Sync all new trades for one symbol.  Returns count of new trades inserted.

    Uses start_time anchored to the last known trade_time in the DB.
    ON CONFLICT DO NOTHING handles any overlap without creating duplicates.
    """
    # Find the timestamp of the most recent trade already in DB for this symbol
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT (EXTRACT(epoch FROM MAX(trade_time)) * 1000)::bigint AS last_ms "
            "FROM trades WHERE symbol = $1",
            symbol,
        )
    last_ms = row["last_ms"] if row and row["last_ms"] else None

    kwargs: dict = {"symbol": symbol, "limit": 1000}
    if last_ms:
        # Start 1 second before the last known trade to ensure no gap
        kwargs["start_time"] = last_ms - 1000

    all_new_trades: list[dict] = []

    try:
        resp = await asyncio.to_thread(
            lambda kw=kwargs: client.rest_api.account_trade_list(**kw)
        )
        batch = resp.data()
    except Exception as e:
        log.warning("sync_symbol %s failed: %s", symbol, e)
        return 0

    if not batch:
        return 0

    for t in batch:
        all_new_trades.append({
            "symbol":          t.symbol,
            "tradeId":         getattr(t, "id", None) or getattr(t, "trade_id", None) or t.order_id,
            "orderId":         t.order_id,
            "side":            t.side,
            "price":           t.price,
            "qty":             t.qty,
            "realizedPnl":     t.realized_pnl,
            "commission":      t.commission,
            "commissionAsset": t.commission_asset,
            "buyer":           t.buyer,
            "time":            t.time,
        })

    if not all_new_trades:
        return 0

    affected_dates: set[date] = set()
    for t in all_new_trades:
        ts = datetime.fromtimestamp(int(t["time"]) / 1000, tz=timezone.utc)
        affected_dates.add(ts.date())

    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await _insert_trades(conn, all_new_trades)
            max_order_id = max(int(t["orderId"]) for t in all_new_trades)
            await _upsert_sync_state(conn, symbol, max_order_id)
            if affected_dates:
                await _refresh_daily_performance(conn, symbol, affected_dates)

    if inserted > 0:
        log.info("sync_symbol %s: %d new trades inserted", symbol, inserted)
    return inserted


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
