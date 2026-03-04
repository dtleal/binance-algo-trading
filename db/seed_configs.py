"""Seed strategies and symbol_configs tables from trader/config.py.

Reads SYMBOL_CONFIGS directly — always in sync with Python source of truth.
Upserts, so it's safe to re-run after adding new symbols or changing params.

Usage:
    poetry run python -m db.seed_configs
    make db-seed
"""
from __future__ import annotations

import asyncio

import asyncpg

# ── Strategy definitions ──────────────────────────────────────────────────────

STRATEGIES = [
    {
        "name": "VWAPPullback",
        "description": "Bidirectional pullback to VWAP with EMA trend filter. "
                       "Long above EMA, short below EMA.",
        "bot_command": "pullback",
        "direction": "BOTH",
    },
    {
        "name": "MomShort",
        "description": "Momentum short: consolidation near VWAP followed by "
                       "breakdown candle with volume confirmation.",
        "bot_command": "bot",
        "direction": "SHORT",
    },
    {
        "name": "PDHL",
        "description": "Previous Day High/Low rejection entries. "
                       "Fades price approaching prior day extremes.",
        "bot_command": "pdhl",
        "direction": "BOTH",
    },
    {
        "name": "ORB",
        "description": "Opening Range Breakout. Trades breakouts of the "
                       "opening range defined in the first session candles.",
        "bot_command": "orb",
        "direction": "BOTH",
    },
    {
        "name": "EMAScalp",
        "description": "EMA cross scalping strategy. Enters on EMA crossover "
                       "with trend confirmation.",
        "bot_command": "ema-scalp",
        "direction": "BOTH",
    },
]


# ── Champion strategy mapping ─────────────────────────────────────────────────
# Extracted from comments in trader/config.py and Bots.tsx CHAMPION map

CHAMPION_INFO: dict[str, dict] = {
    "AXSUSDT":      {"strategy": "VWAPPullback", "ret": None,   "wr": None,   "trades": None, "max_dd": None},
    "SANDUSDT":     {"strategy": "MomShort",     "ret": 27.61,  "wr": 34.0,   "trades": 250,  "max_dd": None},
    "MANAUSDT":     {"strategy": "MomShort",     "ret": 30.54,  "wr": 52.9,   "trades": 295,  "max_dd": None},
    "GALAUSDT":     {"strategy": "VWAPPullback", "ret": 34.85,  "wr": 52.1,   "trades": 357,  "max_dd": None},
    "DOGEUSDT":     {"strategy": "VWAPPullback", "ret": 42.75,  "wr": 52.5,   "trades": 322,  "max_dd": 6.09},
    "1000SHIBUSDT": {"strategy": "VWAPPullback", "ret": 37.51,  "wr": 53.1,   "trades": 354,  "max_dd": None},
    "ETHUSDT":      {"strategy": "VWAPPullback", "ret": 31.87,  "wr": 51.0,   "trades": 251,  "max_dd": 3.95},
    "SOLUSDT":      {"strategy": "MomShort",     "ret": 28.13,  "wr": 53.3,   "trades": 302,  "max_dd": None},
    "AVAXUSDT":     {"strategy": "VWAPPullback", "ret": 31.12,  "wr": None,   "trades": None, "max_dd": None},
    "APTUSDT":      {"strategy": "VWAPPullback", "ret": 19.66,  "wr": 64.6,   "trades": 65,   "max_dd": 2.07},
    "XRPUSDT":      {"strategy": "VWAPPullback", "ret": 30.15,  "wr": 45.0,   "trades": 351,  "max_dd": None},
    "XAUUSDT":      {"strategy": "VWAPPullback", "ret": 7.67,   "wr": 49.1,   "trades": 53,   "max_dd": None},
    "LTCUSDT":      {"strategy": "PDHL",         "ret": 50.76,  "wr": 57.1,   "trades": 1003, "max_dd": 15.16},
    "LINKUSDT":     {"strategy": "PDHL",         "ret": 115.87, "wr": 49.8,   "trades": 876,  "max_dd": 15.14},
    "BCHUSDT":      {"strategy": "PDHL",         "ret": 68.46,  "wr": 53.8,   "trades": 954,  "max_dd": 23.32},
    "XMRUSDT":      {"strategy": "VWAPPullback", "ret": 35.76,  "wr": 52.1,   "trades": 349,  "max_dd": 7.15},
    "DASHUSDT":     {"strategy": "VWAPPullback", "ret": 22.06,  "wr": 53.8,   "trades": 171,  "max_dd": 2.84},
    "UNIUSDT":      {"strategy": "VWAPPullback", "ret": 31.71,  "wr": 43.2,   "trades": 287,  "max_dd": 7.72},
    "1000PEPEUSDT": {"strategy": "VWAPPullback", "ret": 38.86,  "wr": 58.1,   "trades": 198,  "max_dd": 3.36},
    "ZECUSDT":      {"strategy": "VWAPPullback", "ret": 25.55,  "wr": 53.9,   "trades": 280,  "max_dd": 9.04},
    "KSMUSDT":      {"strategy": "ORB",          "ret": 31.95,  "wr": 49.6,   "trades": 468,  "max_dd": 5.63},
    "MAGICUSDT":    {"strategy": "PDHL",         "ret": 90.75,  "wr": 53.9,   "trades": 618,  "max_dd": 11.76},
    "AAVEUSDT":     {"strategy": "VWAPPullback", "ret": 42.04,  "wr": 57.4,   "trades": 310,  "max_dd": 7.67},
    "THETAUSDT":    {"strategy": "MomShort",     "ret": 41.68,  "wr": 60.5,   "trades": 286,  "max_dd": 3.69},
}


# ── Seed functions ─────────────────────────────────────────────────────────────

async def seed_strategies(conn: asyncpg.Connection) -> None:
    for s in STRATEGIES:
        await conn.execute(
            """
            INSERT INTO strategies (name, description, bot_command, direction)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (name) DO UPDATE
                SET description = EXCLUDED.description,
                    bot_command  = EXCLUDED.bot_command,
                    direction    = EXCLUDED.direction
            """,
            s["name"], s["description"], s["bot_command"], s["direction"],
        )
    print(f"  Seeded {len(STRATEGIES)} strategies")


async def seed_symbol_configs(conn: asyncpg.Connection) -> None:
    from trader.config import SYMBOL_CONFIGS

    count = 0
    for symbol, cfg in SYMBOL_CONFIGS.items():
        champ = CHAMPION_INFO.get(symbol, {})
        strategy = champ.get("strategy", "VWAPPullback")

        await conn.execute(
            """
            INSERT INTO symbol_configs (
                symbol, asset, strategy_name, interval,
                entry_start_min, entry_cutoff_min, eod_min,
                tp_pct, sl_pct, min_bars, confirm_bars,
                vwap_prox, vwap_dist_stop, vol_filter,
                pos_size_pct, price_decimals, qty_decimals, min_notional,
                leverage,
                champion_return_pct, champion_win_rate, champion_trades, champion_max_dd,
                active, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,TRUE,NOW())
            ON CONFLICT (symbol) DO UPDATE SET
                asset               = EXCLUDED.asset,
                strategy_name       = EXCLUDED.strategy_name,
                interval            = EXCLUDED.interval,
                entry_start_min     = EXCLUDED.entry_start_min,
                entry_cutoff_min    = EXCLUDED.entry_cutoff_min,
                eod_min             = EXCLUDED.eod_min,
                tp_pct              = EXCLUDED.tp_pct,
                sl_pct              = EXCLUDED.sl_pct,
                min_bars            = EXCLUDED.min_bars,
                confirm_bars        = EXCLUDED.confirm_bars,
                vwap_prox           = EXCLUDED.vwap_prox,
                vwap_dist_stop      = EXCLUDED.vwap_dist_stop,
                vol_filter          = EXCLUDED.vol_filter,
                pos_size_pct        = EXCLUDED.pos_size_pct,
                price_decimals      = EXCLUDED.price_decimals,
                qty_decimals        = EXCLUDED.qty_decimals,
                min_notional        = EXCLUDED.min_notional,
                leverage            = EXCLUDED.leverage,
                champion_return_pct = EXCLUDED.champion_return_pct,
                champion_win_rate   = EXCLUDED.champion_win_rate,
                champion_trades     = EXCLUDED.champion_trades,
                champion_max_dd     = EXCLUDED.champion_max_dd,
                updated_at          = NOW()
            """,
            symbol, cfg.asset, strategy, cfg.interval,
            cfg.entry_start_min, cfg.entry_cutoff_min, cfg.eod_min,
            cfg.tp_pct, cfg.sl_pct, cfg.min_bars, cfg.confirm_bars,
            cfg.vwap_prox, cfg.vwap_dist_stop, cfg.vol_filter,
            cfg.pos_size_pct, cfg.price_decimals, cfg.qty_decimals, cfg.min_notional,
            cfg.leverage,
            champ.get("ret"), champ.get("wr"), champ.get("trades"), champ.get("max_dd"),
        )
        print(f"  {symbol:<20} {cfg.interval:>3}  {strategy}")
        count += 1

    print(f"\n  Total: {count} symbol configs seeded")


async def _main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool
    from db.migrate import run as migrate

    pool = await init_pool()
    await migrate(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            print("Seeding strategies…")
            await seed_strategies(conn)
            print("\nSeeding symbol configs…")
            await seed_symbol_configs(conn)

    print("\nDone.")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
