"""Import sweep result CSVs into the sweep_results table.

Scans data/sweeps/ for files matching SYMBOL_TF_sweep.csv, parses them,
and bulk-inserts into the DB.  Idempotent via ON CONFLICT DO NOTHING.
Marks the top row per (symbol, timeframe) as is_champion=TRUE.

CSV columns (from backtest_sweep Rust binary):
  strategy,tp_pct,sl_pct,rr_ratio,min_bars,vol_filter,confirm_bars,trend_filter,
  entry_window,vwap_prox,vwap_window,ema_period,max_trades_per_day,fast_period,
  slow_period,orb_range_mins,pdhl_prox_pct,max_hold,vwap_dist_stop,pos_size_pct,
  trades,wins,losses,eods,win_rate,return_pct,final_capital,max_dd_pct,max_consec_loss

Usage:
    poetry run python -m db.import_sweeps [--symbol SYMBOL] [--timeframe TF]
    make db-import-sweeps
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path

import asyncpg

log = logging.getLogger("db.import_sweeps")

_SWEEPS_DIR = Path(__file__).parent.parent / "data" / "sweeps"


def _nullable_int(v: str) -> int | None:
    return None if v in ("-", "", "None") else int(float(v))


def _nullable_float(v: str) -> float | None:
    return None if v in ("-", "", "None") else float(v)


def _nullable_bool(v: str) -> bool | None:
    if v in ("-", "", "None"):
        return None
    return v.lower() in ("true", "1", "yes")


def _parse_symbol_tf(filename: str) -> tuple[str, str] | None:
    name = filename.replace("_sweep.csv", "")
    parts = name.rsplit("_", 1)
    if len(parts) != 2:
        return None
    symbol, tf = parts
    valid_tfs = {"1m", "5m", "15m", "30m", "1h"}
    if tf not in valid_tfs:
        return None
    return symbol.upper(), tf


def _read_csv(path: Path, symbol: str, tf: str) -> list[tuple]:
    rows = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for line in reader:
            rows.append((
                symbol,
                tf,
                line["strategy"],
                _nullable_float(line["tp_pct"]),
                _nullable_float(line["sl_pct"]),
                _nullable_float(line["rr_ratio"]),
                _nullable_int(line["min_bars"]),
                _nullable_bool(line["vol_filter"]),
                _nullable_int(line["confirm_bars"]),
                _nullable_bool(line["trend_filter"]),
                line.get("entry_window") or None,
                _nullable_float(line["vwap_prox"]),
                line.get("vwap_window") or None,
                _nullable_float(line["pos_size_pct"]),
                _nullable_float(line["vwap_dist_stop"]),
                _nullable_int(line["max_trades_per_day"]),
                line.get("max_hold") or None,
                _nullable_int(line["ema_period"]),
                _nullable_int(line["fast_period"]),
                _nullable_int(line["slow_period"]),
                _nullable_int(line["orb_range_mins"]),
                _nullable_float(line["pdhl_prox_pct"]),
                int(line["trades"]),
                int(line["wins"]),
                int(line["losses"]),
                int(line["eods"]),
                _nullable_float(line["win_rate"]),
                _nullable_float(line["return_pct"]),
                _nullable_float(line["final_capital"]),
                _nullable_float(line["max_dd_pct"]),
                _nullable_int(line["max_consec_loss"]),
            ))
    return rows


async def import_file(pool: asyncpg.Pool, path: Path, symbol: str, tf: str) -> int:
    print(f"  Reading {path.name}…", end=" ", flush=True)
    rows = _read_csv(path, symbol, tf)
    print(f"{len(rows):,} configs", end=" → ", flush=True)

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO sweep_results (
                symbol, timeframe, strategy,
                tp_pct, sl_pct, rr_ratio, min_bars, vol_filter, confirm_bars,
                trend_filter, entry_window, vwap_prox, vwap_window,
                pos_size_pct, vwap_dist_stop, max_trades_per_day, max_hold,
                ema_period, fast_period, slow_period, orb_range_mins, pdhl_prox_pct,
                trades, wins, losses, eods,
                win_rate, return_pct, final_capital, max_dd_pct, max_consec_loss
            )
            VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31
            )
            ON CONFLICT (symbol, timeframe, strategy, tp_pct, sl_pct, min_bars,
                         confirm_bars, vwap_prox, ema_period, fast_period,
                         orb_range_mins, pdhl_prox_pct)
            DO NOTHING
            """,
            rows,
        )

        # Mark champion: highest return_pct per (symbol, timeframe)
        await conn.execute(
            """
            UPDATE sweep_results SET is_champion = FALSE
            WHERE symbol = $1 AND timeframe = $2
            """,
            symbol, tf,
        )
        await conn.execute(
            """
            UPDATE sweep_results SET is_champion = TRUE
            WHERE id = (
                SELECT id FROM sweep_results
                WHERE symbol = $1 AND timeframe = $2
                ORDER BY return_pct DESC NULLS LAST
                LIMIT 1
            )
            """,
            symbol, tf,
        )

    print(f"done")
    return len(rows)


async def import_all(
    pool: asyncpg.Pool,
    symbol_filter: str | None = None,
    tf_filter: str | None = None,
) -> None:
    files = sorted(_SWEEPS_DIR.glob("*_sweep.csv"))
    if not files:
        print(f"No sweep CSV files found in {_SWEEPS_DIR}")
        return

    total = 0
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
        total += n

    print(f"\nImport complete — {total:,} configs total")


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
    parser = argparse.ArgumentParser(description="Import sweep CSVs into PostgreSQL")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--timeframe", default=None)
    args = parser.parse_args()
    asyncio.run(_main(args.symbol, args.timeframe))
