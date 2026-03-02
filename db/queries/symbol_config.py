"""Query symbol config from DB → SymbolConfig dataclass + strategy-specific extras."""
from __future__ import annotations

import asyncpg


async def get_symbol_config(pool: asyncpg.Pool, symbol: str):
    """Return (SymbolConfig, extras_dict) from DB.

    SymbolConfig: standard fields used by all bots.
    extras_dict: strategy-specific fields not in the dataclass
                 (ema_period, fast_period, slow_period, range_mins,
                  pdhl_prox_pct, max_trades_per_day, be_r, trail_step, leverage).

    Raises RuntimeError if symbol not found.
    """
    from trader.config import SymbolConfig

    row = await pool.fetchrow(
        "SELECT * FROM symbol_configs WHERE symbol = $1",
        symbol.upper(),
    )
    if not row:
        raise RuntimeError(f"Symbol '{symbol}' not found in symbol_configs table")

    cfg = SymbolConfig(
        symbol=row["symbol"],
        asset=row["asset"],
        tp_pct=float(row["tp_pct"]),
        sl_pct=float(row["sl_pct"]),
        min_bars=row["min_bars"],
        confirm_bars=row["confirm_bars"],
        vwap_prox=float(row["vwap_prox"]),
        entry_start_min=row["entry_start_min"],
        entry_cutoff_min=row["entry_cutoff_min"],
        eod_min=row["eod_min"],
        pos_size_pct=float(row["pos_size_pct"]),
        price_decimals=row["price_decimals"],
        qty_decimals=row["qty_decimals"],
        min_notional=float(row["min_notional"]),
        vol_filter=bool(row["vol_filter"]),
        interval=row["interval"],
        vwap_dist_stop=float(row["vwap_dist_stop"]),
    )

    extras = {
        "strategy_name":      row["strategy_name"],
        "ema_period":         row["ema_period"],
        "max_trades_per_day": row["max_trades_per_day"],
        "fast_period":        row["fast_period"],
        "slow_period":        row["slow_period"],
        "range_mins":         row["range_mins"],
        "pdhl_prox_pct":      float(row["pdhl_prox_pct"]) if row["pdhl_prox_pct"] else None,
        "be_r":               float(row["be_r"]) if row["be_r"] else None,
        "trail_step":         float(row["trail_step"]) if row["trail_step"] else None,
        "leverage":           row["leverage"] or 30,
        "active":             row["active"],
    }

    return cfg, extras
