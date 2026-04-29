-- 013: Substitui jsonb (strategy_params, exit_params) por colunas tipadas em
-- sweep_results e presets. Wide-table — ~30 colunas, NULLs para params
-- não-aplicáveis ao strategy/exit do row.
--
-- Motivação: jsonb com GIN era bottleneck de bulk-write (10-100x mais lento
-- que typed). Sem GIN ainda é 2-5x mais lento + 2x storage. Para 6 strategies
-- com params estáveis, wide-table é mais limpo e mais rápido.
--
-- Pré-condição: sweep_results e presets foram TRUNCATE-d. Migração não
-- migra dados — só altera schema.

BEGIN;

-- ── sweep_results ──

ALTER TABLE sweep_results
  ADD COLUMN IF NOT EXISTS mtf_enabled           BOOLEAN,
  ADD COLUMN IF NOT EXISTS close_at_opposite     BOOLEAN,
  ADD COLUMN IF NOT EXISTS close_on_range_break  BOOLEAN,
  ADD COLUMN IF NOT EXISTS mtf_timeframe         TEXT,
  ADD COLUMN IF NOT EXISTS vwap_window_days      INTEGER,
  ADD COLUMN IF NOT EXISTS entry_window_start    INTEGER,
  ADD COLUMN IF NOT EXISTS entry_window_end      INTEGER,
  ADD COLUMN IF NOT EXISTS max_hold_min          INTEGER,
  ADD COLUMN IF NOT EXISTS buffer_pct            NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS kind                  TEXT,
  ADD COLUMN IF NOT EXISTS prox_pct              NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS range_mins            INTEGER,
  ADD COLUMN IF NOT EXISTS pos_size              NUMERIC(8,4);

ALTER TABLE sweep_results
  DROP COLUMN IF EXISTS entry_window,
  DROP COLUMN IF EXISTS vwap_window,
  DROP COLUMN IF EXISTS max_hold,
  DROP COLUMN IF EXISTS rr_ratio,
  DROP COLUMN IF EXISTS vwap_dist_stop,
  DROP COLUMN IF EXISTS orb_range_mins,
  DROP COLUMN IF EXISTS pdhl_prox_pct,
  DROP COLUMN IF EXISTS pos_size_pct,
  DROP COLUMN IF EXISTS strategy_params,
  DROP COLUMN IF EXISTS exit_params;

-- ── presets ──

ALTER TABLE presets
  ADD COLUMN IF NOT EXISTS min_bars              INTEGER,
  ADD COLUMN IF NOT EXISTS confirm_bars          INTEGER,
  ADD COLUMN IF NOT EXISTS vwap_prox             NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS vwap_window_days      INTEGER,
  ADD COLUMN IF NOT EXISTS ema_period            INTEGER,
  ADD COLUMN IF NOT EXISTS max_trades_per_day    INTEGER,
  ADD COLUMN IF NOT EXISTS kind                  TEXT,
  ADD COLUMN IF NOT EXISTS vol_filter            BOOLEAN,
  ADD COLUMN IF NOT EXISTS trend_filter          BOOLEAN,
  ADD COLUMN IF NOT EXISTS entry_window_start    INTEGER,
  ADD COLUMN IF NOT EXISTS entry_window_end      INTEGER,
  ADD COLUMN IF NOT EXISTS fast_period           INTEGER,
  ADD COLUMN IF NOT EXISTS slow_period           INTEGER,
  ADD COLUMN IF NOT EXISTS range_mins            INTEGER,
  ADD COLUMN IF NOT EXISTS buffer_pct            NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS prox_pct              NUMERIC(10,6),
  ADD COLUMN IF NOT EXISTS adx_thresh            NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS atr_pct_thresh        NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS range_lookback        INTEGER,
  ADD COLUMN IF NOT EXISTS zone_pct              NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS tp_range_pct          NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS sl_range_pct          NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS recent_thresh_pct     NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS max_orders            INTEGER,
  ADD COLUMN IF NOT EXISTS pos_size              NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS mtf_enabled           BOOLEAN,
  ADD COLUMN IF NOT EXISTS close_at_opposite     BOOLEAN,
  ADD COLUMN IF NOT EXISTS close_on_range_break  BOOLEAN,
  ADD COLUMN IF NOT EXISTS mtf_timeframe         TEXT,
  ADD COLUMN IF NOT EXISTS tp_pct                NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS sl_pct                NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS max_hold_min          INTEGER,
  ADD COLUMN IF NOT EXISTS be_r                  NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS trail_step            NUMERIC(8,4),
  ADD COLUMN IF NOT EXISTS tp_r                  NUMERIC(8,4);

ALTER TABLE presets
  DROP COLUMN IF EXISTS strategy_params,
  DROP COLUMN IF EXISTS exit_params;

COMMIT;
