"""Strategy activation query."""
from __future__ import annotations

import asyncpg


async def is_bot_active(
    pool: asyncpg.Pool,
    symbol: str,
    strategy_name: str,
) -> tuple[bool, str]:
    """Return (is_active, reason).

    Checks both symbol_configs.active and strategies.active.
    Fail-open: if symbol or strategy not found in DB, returns (True, "").
    """
    row = await pool.fetchrow(
        """
        SELECT
            COALESCE(sc.active, TRUE) AS sym_active,
            COALESCE(s.active,  TRUE) AS strat_active
        FROM (SELECT TRUE) t
        LEFT JOIN symbol_configs sc ON sc.symbol = $1
        LEFT JOIN strategies     s  ON s.name    = $2
        """,
        symbol.upper(),
        strategy_name,
    )
    if not row["strat_active"]:
        return False, f"estratégia '{strategy_name}' desativada"
    if not row["sym_active"]:
        return False, f"símbolo '{symbol}' desativado"
    return True, ""
