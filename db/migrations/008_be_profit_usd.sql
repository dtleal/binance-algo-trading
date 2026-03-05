-- Migration 008: configurable auto-breakeven threshold (USD)
-- When unrealized PnL reaches this value, bots move SL to entry (0a0).

ALTER TABLE symbol_configs
  ADD COLUMN IF NOT EXISTS be_profit_usd NUMERIC(10,4) NOT NULL DEFAULT 0.50;

-- Safety backfill for rows created before this migration/default.
UPDATE symbol_configs
SET be_profit_usd = 0.50
WHERE be_profit_usd IS NULL;
