-- Migration 001: initial schema
-- Tables: schema_migrations, trades, sync_state, klines, daily_performance, equity_snapshots

-- ── Migration tracker ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT        PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Trades ───────────────────────────────────────────────────────────────────
-- One row per fill (closing fills have realized_pnl != 0, opening fills have 0)
CREATE TABLE IF NOT EXISTS trades (
    id               BIGSERIAL    PRIMARY KEY,
    symbol           TEXT         NOT NULL,
    order_id         BIGINT       NOT NULL,
    side             TEXT         NOT NULL,          -- BUY or SELL
    price            NUMERIC(20,8) NOT NULL,
    qty              NUMERIC(20,8) NOT NULL,
    realized_pnl     NUMERIC(20,8) NOT NULL DEFAULT 0,
    commission       NUMERIC(20,8) NOT NULL DEFAULT 0,
    commission_asset TEXT         NOT NULL DEFAULT 'USDT',
    buyer            BOOLEAN      NOT NULL DEFAULT FALSE,
    trade_time       TIMESTAMPTZ  NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, order_id)
);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_time
    ON trades (symbol, trade_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_time
    ON trades (trade_time DESC);
-- Partial index: only closing fills (where realized_pnl is meaningful)
CREATE INDEX IF NOT EXISTS idx_trades_closing_fills
    ON trades (symbol, trade_time DESC)
    WHERE realized_pnl != 0;

-- ── Sync state ───────────────────────────────────────────────────────────────
-- Tracks the highest order_id synced per symbol for efficient fromId pagination
CREATE TABLE IF NOT EXISTS sync_state (
    symbol          TEXT        PRIMARY KEY,
    last_order_id   BIGINT      NOT NULL DEFAULT 0,
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Klines (OHLCV candles) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS klines (
    id          BIGSERIAL    PRIMARY KEY,
    symbol      TEXT         NOT NULL,
    timeframe   TEXT         NOT NULL,          -- 1m, 5m, 15m, 30m, 1h
    open_time   TIMESTAMPTZ  NOT NULL,
    open        NUMERIC(20,8) NOT NULL,
    high        NUMERIC(20,8) NOT NULL,
    low         NUMERIC(20,8) NOT NULL,
    close       NUMERIC(20,8) NOT NULL,
    volume      NUMERIC(24,8) NOT NULL,
    close_time  TIMESTAMPTZ  NOT NULL,
    UNIQUE (symbol, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_sym_tf_time
    ON klines (symbol, timeframe, open_time DESC);

-- ── Daily performance ─────────────────────────────────────────────────────────
-- Pre-aggregated daily P&L per symbol — refreshed by the sync service
CREATE TABLE IF NOT EXISTS daily_performance (
    symbol            TEXT         NOT NULL,
    trade_date        DATE         NOT NULL,
    total_trades      INT          NOT NULL DEFAULT 0,
    winning_trades    INT          NOT NULL DEFAULT 0,
    total_pnl         NUMERIC(20,8) NOT NULL DEFAULT 0,
    total_commission  NUMERIC(20,8) NOT NULL DEFAULT 0,
    gross_pnl         NUMERIC(20,8) NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_perf_date
    ON daily_performance (trade_date DESC);

-- ── Equity snapshots ─────────────────────────────────────────────────────────
-- Mirrors the Redis equity:history stream with persistent storage
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id             BIGSERIAL    PRIMARY KEY,
    snapshot_time  TIMESTAMPTZ  NOT NULL UNIQUE,
    total_equity   NUMERIC(20,8) NOT NULL,
    unrealized_pnl NUMERIC(20,8) NOT NULL,
    total_balance  NUMERIC(20,8) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_time
    ON equity_snapshots (snapshot_time DESC);
