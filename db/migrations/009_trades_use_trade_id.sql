-- Migration 009: store Binance trade_id per fill instead of deduping by order_id
--
-- Futures orders can fill in multiple parts. Using only (symbol, order_id) as the
-- unique key collapses distinct fills and drops realized PnL / fees from the DB.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS trade_id BIGINT;

-- Preserve existing rows deterministically until a backfill repairs historical fills.
UPDATE trades
SET trade_id = order_id
WHERE trade_id IS NULL;

ALTER TABLE trades
    ALTER COLUMN trade_id SET NOT NULL;

ALTER TABLE trades
    DROP CONSTRAINT IF EXISTS trades_symbol_order_id_key;

ALTER TABLE trades
    ADD CONSTRAINT trades_symbol_trade_id_key UNIQUE (symbol, trade_id);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_order_id
    ON trades (symbol, order_id);
