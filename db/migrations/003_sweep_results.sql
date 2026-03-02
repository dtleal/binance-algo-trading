-- Migration 003: sweep results from backtest sweeps

CREATE TABLE IF NOT EXISTS sweep_results (
    id                  BIGSERIAL    PRIMARY KEY,
    -- Source info
    symbol              TEXT         NOT NULL,
    timeframe           TEXT         NOT NULL,
    -- Strategy
    strategy            TEXT         NOT NULL,
    -- Parameters (all strategies)
    tp_pct              NUMERIC(8,4),
    sl_pct              NUMERIC(8,4),
    rr_ratio            NUMERIC(8,4),
    min_bars            INT,
    vol_filter          BOOLEAN,
    confirm_bars        INT,
    trend_filter        BOOLEAN,
    entry_window        TEXT,           -- e.g. "01-22"
    vwap_prox           NUMERIC(10,6),
    vwap_window         TEXT,           -- e.g. "1d"
    pos_size_pct        NUMERIC(8,4),
    vwap_dist_stop      NUMERIC(10,6),
    max_trades_per_day  INT,
    max_hold            TEXT,           -- e.g. "EOD" or "4h"
    -- Strategy-specific params (nullable — only relevant for some strategies)
    ema_period          INT,            -- EMAScalp
    fast_period         INT,            -- EMAScalp
    slow_period         INT,            -- EMAScalp
    orb_range_mins      INT,            -- ORB
    pdhl_prox_pct       NUMERIC(8,4),   -- PDHL
    -- Results
    trades              INT          NOT NULL DEFAULT 0,
    wins                INT          NOT NULL DEFAULT 0,
    losses              INT          NOT NULL DEFAULT 0,
    eods                INT          NOT NULL DEFAULT 0,
    win_rate            NUMERIC(8,4),
    return_pct          NUMERIC(10,4),
    final_capital       NUMERIC(16,4),
    max_dd_pct          NUMERIC(8,4),
    max_consec_loss     INT,
    -- Champion flag
    is_champion         BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Import metadata
    imported_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, timeframe, strategy, tp_pct, sl_pct, min_bars, confirm_bars,
            vwap_prox, ema_period, fast_period, orb_range_mins, pdhl_prox_pct)
);

CREATE INDEX IF NOT EXISTS idx_sweep_symbol_tf
    ON sweep_results (symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_sweep_strategy
    ON sweep_results (strategy);
CREATE INDEX IF NOT EXISTS idx_sweep_return
    ON sweep_results (symbol, return_pct DESC);
CREATE INDEX IF NOT EXISTS idx_sweep_champion
    ON sweep_results (symbol, timeframe, is_champion) WHERE is_champion = TRUE;
