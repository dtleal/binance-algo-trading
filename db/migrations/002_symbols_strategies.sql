-- Migration 002: symbol configurations and strategies

-- ── Strategies ────────────────────────────────────────────────────────────────
-- Catalogue of all available strategies in the system
CREATE TABLE IF NOT EXISTS strategies (
    id          SERIAL      PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,     -- e.g. "VWAPPullback"
    description TEXT        NOT NULL DEFAULT '', -- human-readable description
    bot_command TEXT        NOT NULL DEFAULT '', -- CLI sub-command: pullback, bot, pdhl, orb, ema-scalp
    direction   TEXT        NOT NULL DEFAULT 'BOTH' CHECK (direction IN ('LONG', 'SHORT', 'BOTH'))
);

-- ── Symbol configurations ─────────────────────────────────────────────────────
-- Champion config per symbol — mirrors trader/config.py SymbolConfig
-- Source of truth is still Python, this table is populated by db/seed_configs.py
CREATE TABLE IF NOT EXISTS symbol_configs (
    symbol              TEXT        PRIMARY KEY,
    asset               TEXT        NOT NULL,
    strategy_name       TEXT        NOT NULL REFERENCES strategies(name),
    -- Timeframe & scheduling
    interval            TEXT        NOT NULL DEFAULT '1m',
    entry_start_min     INT         NOT NULL DEFAULT 60,
    entry_cutoff_min    INT         NOT NULL DEFAULT 1320,
    eod_min             INT         NOT NULL DEFAULT 1430,
    -- Strategy parameters
    tp_pct              NUMERIC(8,4) NOT NULL,
    sl_pct              NUMERIC(8,4) NOT NULL,
    min_bars            INT         NOT NULL DEFAULT 0,
    confirm_bars        INT         NOT NULL DEFAULT 0,
    vwap_prox           NUMERIC(10,6) NOT NULL DEFAULT 0,
    vwap_dist_stop      NUMERIC(10,6) NOT NULL DEFAULT 0,
    vol_filter          BOOLEAN     NOT NULL DEFAULT FALSE,
    -- Position sizing
    pos_size_pct        NUMERIC(8,4) NOT NULL DEFAULT 0.40,
    -- Precision
    price_decimals      INT         NOT NULL DEFAULT 2,
    qty_decimals        INT         NOT NULL DEFAULT 0,
    min_notional        NUMERIC(10,4) NOT NULL DEFAULT 5.0,
    -- Champion backtest stats (from sweep results)
    champion_return_pct NUMERIC(8,4),   -- e.g. 42.75
    champion_win_rate   NUMERIC(8,4),   -- e.g. 52.5
    champion_trades     INT,            -- e.g. 322
    champion_max_dd     NUMERIC(8,4),   -- max drawdown %
    -- Metadata
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_symbol_configs_strategy
    ON symbol_configs (strategy_name);
CREATE INDEX IF NOT EXISTS idx_symbol_configs_active
    ON symbol_configs (active) WHERE active = TRUE;
