"""Apply the best sweep result to symbol_configs (the source of truth for bots).

Selects the sweep config with highest return_pct (min 20 trades) and upserts
into symbol_configs. Also fetches price/qty precision from Binance exchange info.

Usage:
    poetry run python -m db.apply_champion --symbol DOGEUSDT
    make db-apply-champion SYMBOL=dogeusdt
"""
from __future__ import annotations

import argparse
import asyncio

import requests
from trader.exchange_precision import decimals_from_step


_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

# Strategy name in DB → CLI command mapping (mirrors strategies table)
_STRATEGY_COMMANDS = {
    "VWAPPullback": "pullback",
    "MomShort":     "bot",
    "PDHL":         "pdhl",
    "ORB":          "orb",
    "EMAScalp":     "ema-scalp",
}

# Mapping from sweep column names to strategy names in DB
_SWEEP_STRATEGY_MAP = {
    "VWAPPullback": "VWAPPullback",
    "MomShort":     "MomShort",
    "MomLong":      "MomShort",   # treated as MomShort bot (bidirectional not used)
    "PDHL":         "PDHL",
    "ORB":          "ORB",
    "EMAScalp":     "EMAScalp",
    "RejShort":     "MomShort",
    "RejLong":      "MomShort",
}


def _get_exchange_info(symbol: str) -> dict:
    """Fetch price/qty precision and min notional from Binance exchange info."""
    resp = requests.get(_EXCHANGE_INFO_URL, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    for s in data.get("symbols", []):
        if s["symbol"] == symbol.upper():
            price_decimals = s.get("pricePrecision", 2)
            qty_decimals = s.get("quantityPrecision", 0)
            min_notional = 5.0
            for f in s.get("filters", []):
                filter_type = f.get("filterType")
                if filter_type == "PRICE_FILTER" and f.get("tickSize"):
                    price_decimals = decimals_from_step(f["tickSize"])
                elif filter_type == "LOT_SIZE" and f.get("stepSize"):
                    qty_decimals = decimals_from_step(f["stepSize"])
                elif filter_type == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 5.0))
            return {
                "price_decimals": price_decimals,
                "qty_decimals": qty_decimals,
                "min_notional": min_notional,
            }
    raise RuntimeError(f"Symbol {symbol} not found in Binance exchange info")


async def apply_champion(symbol: str, min_trades: int = 20) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from db.connection import init_pool, close_pool, get_pool

    await init_pool()
    pool = get_pool()

    # Select best champion across all timeframes
    champ = await pool.fetchrow(
        """
        SELECT *
        FROM sweep_results
        WHERE symbol = $1
          AND trades >= $2
        ORDER BY return_pct DESC NULLS LAST
        LIMIT 1
        """,
        symbol.upper(),
        min_trades,
    )

    if not champ:
        print(f"❌ No sweep results found for {symbol} (min_trades={min_trades})")
        await close_pool()
        return

    strategy_raw = champ["strategy"]
    strategy_name = _SWEEP_STRATEGY_MAP.get(strategy_raw, "VWAPPullback")
    timeframe = champ["timeframe"]
    asset = symbol.upper().replace("USDT", "").replace("1000", "")

    print(f"  Fetching exchange info for {symbol}…")
    exchange = await asyncio.to_thread(_get_exchange_info, symbol)

    # Build upsert values
    await pool.execute(
        """
        INSERT INTO symbol_configs (
            symbol, asset, strategy_name, interval,
            tp_pct, sl_pct, min_bars, confirm_bars,
            vwap_prox, vwap_dist_stop, vol_filter, pos_size_pct,
            entry_start_min, entry_cutoff_min, eod_min,
            price_decimals, qty_decimals, min_notional,
            ema_period, max_trades_per_day, fast_period, slow_period,
            range_mins, pdhl_prox_pct,
            champion_return_pct, champion_win_rate, champion_trades, champion_max_dd,
            active, updated_at
        )
        VALUES (
            $1,$2,$3,$4,
            $5,$6,$7,$8,
            $9,$10,$11,$12,
            60,1320,1430,
            $13,$14,$15,
            $16,$17,$18,$19,
            $20,$21,
            $22,$23,$24,$25,
            TRUE, NOW()
        )
        ON CONFLICT (symbol) DO UPDATE SET
            strategy_name       = EXCLUDED.strategy_name,
            interval            = EXCLUDED.interval,
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
            ema_period          = EXCLUDED.ema_period,
            max_trades_per_day  = EXCLUDED.max_trades_per_day,
            fast_period         = EXCLUDED.fast_period,
            slow_period         = EXCLUDED.slow_period,
            range_mins          = EXCLUDED.range_mins,
            pdhl_prox_pct       = EXCLUDED.pdhl_prox_pct,
            champion_return_pct = EXCLUDED.champion_return_pct,
            champion_win_rate   = EXCLUDED.champion_win_rate,
            champion_trades     = EXCLUDED.champion_trades,
            champion_max_dd     = EXCLUDED.champion_max_dd,
            updated_at          = NOW()
        """,
        # $1-$4
        symbol.upper(), asset, strategy_name, timeframe,
        # $5-$12
        float(champ["tp_pct"] or 0),
        float(champ["sl_pct"] or 0),
        champ["min_bars"] or 0,
        champ["confirm_bars"] or 0,
        float(champ["vwap_prox"] or 0),
        float(champ["vwap_dist_stop"] or 0),
        bool(champ["vol_filter"]) if champ["vol_filter"] is not None else False,
        float(champ["pos_size_pct"] or 0.40),
        # $13-$15
        exchange["price_decimals"],
        exchange["qty_decimals"],
        exchange["min_notional"],
        # $16-$21
        champ["ema_period"],
        champ["max_trades_per_day"],
        champ["fast_period"],
        champ["slow_period"],
        champ["orb_range_mins"],
        float(champ["pdhl_prox_pct"]) if champ["pdhl_prox_pct"] else None,
        # $22-$25
        float(champ["return_pct"] or 0),
        float(champ["win_rate"] or 0),
        champ["trades"],
        float(champ["max_dd_pct"] or 0),
    )

    await close_pool()

    print(
        f"✅ {symbol.upper()}: {strategy_name} {timeframe} | "
        f"return=+{champ['return_pct']:.1f}% | "
        f"WR={champ['win_rate']:.1f}% | "
        f"tp={champ['tp_pct']}% sl={champ['sl_pct']}% | "
        f"trades={champ['trades']}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Apply best sweep champion to symbol_configs"
    )
    parser.add_argument("--symbol", required=True, help="e.g. DOGEUSDT")
    parser.add_argument(
        "--min-trades", type=int, default=20,
        help="Minimum trades filter to avoid overfitting (default 20)",
    )
    args = parser.parse_args()
    asyncio.run(apply_champion(args.symbol.upper(), args.min_trades))
