-- Migration 010: sweep_results exit-aware + walk-forward
--
-- Aditiva. Não toca dados existentes; novas colunas NULL-able preservam backwards compat.
-- Suporta o crate Rust unificado (1 binário, múltiplos exits combinados no mesmo run).

-- Exit model como dimensão própria (antes: implícito no binário usado — v1=fixed, v2=trailing, range=range_tp_sl)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS exit_name TEXT;

-- Params do trailing-stop (sweep_v2)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS be_r       NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS trail_step NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS tp_r       NUMERIC(8,4);

-- Params da range strategy (hoje só vivem em range_sweep.csv)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS adx_thresh        NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS atr_pct_thresh    NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS range_lookback    INT,
    ADD COLUMN IF NOT EXISTS zone_pct          NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS tp_range_pct      NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS sl_range_pct      NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS recent_thresh_pct NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS max_orders        INT;

-- Walk-forward (NULL = full history)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS period_start DATE,
    ADD COLUMN IF NOT EXISTS period_end   DATE;

-- Origem do resultado (qual binário/versão gerou) + agrupamento por execução
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS source   TEXT,
    ADD COLUMN IF NOT EXISTS sweep_id UUID;

-- Hash determinístico de todos os params normalizados → dedup robusto
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS params_hash CHAR(32);

-- CHECKs em enums (NULL aceito para preservar dado legado)
ALTER TABLE sweep_results
    ADD CONSTRAINT sweep_results_exit_check
    CHECK (exit_name IS NULL OR exit_name IN ('fixed_tp_sl','trailing_stop','range_tp_sl'));

ALTER TABLE sweep_results
    ADD CONSTRAINT sweep_results_source_check
    CHECK (source IS NULL OR source IN ('sweep_v1','sweep_v2','sweep_range','backtest'));

-- Drop UQ antigo (10 colunas, frágil — NULL distinct quebra dedup)
ALTER TABLE sweep_results
    DROP CONSTRAINT IF EXISTS sweep_results_symbol_timeframe_strategy_tp_pct_sl_pct_min_b_key;

-- UQ robusto via expression index — COALESCE neutraliza NULLs
CREATE UNIQUE INDEX IF NOT EXISTS uq_sweep_results_dedup
    ON sweep_results (
        symbol, timeframe, strategy,
        COALESCE(exit_name, ''),
        COALESCE(period_start, DATE '1970-01-01'),
        COALESCE(period_end,   DATE '9999-12-31'),
        COALESCE(params_hash,  '')
    );

-- Índices para queries típicas do crate Rust
CREATE INDEX IF NOT EXISTS idx_sweep_results_sweep_id
    ON sweep_results (sweep_id) WHERE sweep_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sweep_results_period
    ON sweep_results (symbol, timeframe, period_start)
    WHERE period_start IS NOT NULL;
