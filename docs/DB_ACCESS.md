# Database Access Guide

Last updated: 2026-03-04

## Overview

This project uses:
- PostgreSQL for persistent trading data (trades, configs, performance, klines)
- Redis for real-time bot state/events/log streams

Environment variables are loaded from `.env`.

## PostgreSQL Access

Required env vars:
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

Default local values in this repo:
- host: `localhost`
- port: `5432`
- user: `trader`
- db: `binance_trader`

### Connect with psql

```bash
export PGPASSWORD=trader_password
psql -h localhost -p 5432 -U trader -d binance_trader
```

### One-off SQL command

```bash
export PGPASSWORD=trader_password
psql -h localhost -p 5432 -U trader -d binance_trader -c "SELECT now();"
```

## Redis Access

Default URL:
- `redis://localhost:6379`

Quick check:

```bash
redis-cli ping
```

Read bot states:

```bash
redis-cli HGETALL bot:states
```

## High-Value Tables (Postgres)

- `trades`: one row per fill; closing fills have non-zero `realized_pnl`
- `daily_performance`: daily aggregated metrics per symbol
- `symbol_configs`: runtime strategy parameters (DB-first, config.py fallback)
- `strategies`: strategy metadata and active flags
- `klines`: stored candle data
- `sync_state`: tracking for incremental sync jobs

## Ready-to-Use Queries

### 1) Loss trades today (BRT)

```sql
SELECT symbol, side, order_id, price, qty, realized_pnl, commission, trade_time
FROM trades
WHERE realized_pnl < 0
  AND (trade_time AT TIME ZONE 'America/Sao_Paulo')::date =
      (now() AT TIME ZONE 'America/Sao_Paulo')::date
ORDER BY trade_time DESC;
```

### 2) Loss summary by symbol today (BRT)

```sql
SELECT
  symbol,
  COUNT(*) AS loss_trades,
  ROUND(SUM(realized_pnl)::numeric, 8) AS total_pnl,
  ROUND(SUM(commission)::numeric, 8) AS total_fees,
  ROUND((SUM(realized_pnl) - SUM(commission))::numeric, 8) AS pnl_after_fees
FROM trades
WHERE realized_pnl < 0
  AND (trade_time AT TIME ZONE 'America/Sao_Paulo')::date =
      (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY symbol
ORDER BY total_pnl ASC;
```

### 3) Net PnL today by symbol (BRT)

```sql
SELECT
  symbol,
  COUNT(*) FILTER (WHERE realized_pnl != 0) AS closed_trades,
  ROUND(SUM(realized_pnl)::numeric, 8) AS gross_pnl,
  ROUND(SUM(commission)::numeric, 8) AS fees,
  ROUND((SUM(realized_pnl) - SUM(commission))::numeric, 8) AS net_pnl
FROM trades
WHERE (trade_time AT TIME ZONE 'America/Sao_Paulo')::date =
      (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY symbol
ORDER BY net_pnl ASC;
```

### 4) Recent trades for one symbol

```sql
SELECT symbol, side, price, qty, realized_pnl, commission, trade_time
FROM trades
WHERE symbol = 'DOGEUSDT'
ORDER BY trade_time DESC
LIMIT 50;
```

### 5) Active strategy configs from DB

```sql
SELECT symbol, strategy_name, interval, tp_pct, sl_pct, pos_size_pct, leverage, mode
FROM symbol_configs
ORDER BY symbol;
```

## Timezone Convention

- Exchange/bot timestamps are UTC.
- For daily operational reporting in this project, prefer `America/Sao_Paulo` date filters.

## Practical Notes

- If Postgres is unreachable, check if container/service is running (`docker compose ps`).
- Runtime bots load config from DB first; fallback is `trader/config.py`.
- For strict reproducibility in support/debug sessions, include timezone in every date filter.
