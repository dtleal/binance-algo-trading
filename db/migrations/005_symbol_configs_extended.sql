-- Migration 005: add strategy-specific columns to symbol_configs
-- These come from sweep results and allow bots to read full config from DB

ALTER TABLE symbol_configs
    ADD COLUMN IF NOT EXISTS ema_period         INT,            -- VWAPPullback: EMA trend filter period
    ADD COLUMN IF NOT EXISTS max_trades_per_day INT,            -- VWAPPullback, ORB, PDHL
    ADD COLUMN IF NOT EXISTS fast_period        INT,            -- EMAScalp: fast EMA period
    ADD COLUMN IF NOT EXISTS slow_period        INT,            -- EMAScalp: slow EMA period
    ADD COLUMN IF NOT EXISTS range_mins         INT,            -- ORB: opening range minutes
    ADD COLUMN IF NOT EXISTS pdhl_prox_pct      NUMERIC(8,4),   -- PDHL: proximity %
    ADD COLUMN IF NOT EXISTS be_r               NUMERIC(8,4),   -- ORB/PDHL/EMAScalp: breakeven R multiple
    ADD COLUMN IF NOT EXISTS trail_step         NUMERIC(8,4),   -- ORB/PDHL/EMAScalp: trail step R
    ADD COLUMN IF NOT EXISTS leverage           INT NOT NULL DEFAULT 30;
