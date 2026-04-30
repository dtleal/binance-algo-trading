-- 014: Per-strategy regime fingerprint (M2 da skill /regime-conditional).
--
-- Duas tabelas:
-- 1. regime_feature_quantiles: Q33/Q67 das 4 features sobre o universo de
--    candles de uma janela. Usado pra binning em low/mid/high. Calcula 1×
--    por (símbolo, TF, período), reusa pra todos presets do contexto.
-- 2. strategy_regime_profile: 12 rows por preset (4 features × 3 buckets).
--    Cada row contém stats agregados + validação fp_a/fp_b + bootstrap IC +
--    flag is_useful (consumido pelo score function do M3).

BEGIN;

CREATE TABLE IF NOT EXISTS regime_feature_quantiles (
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    feature_name   TEXT NOT NULL,    -- 'adx14' | 'atr_pct' | 'rsi14' | 'bb_squeeze'
    period_start   DATE NOT NULL,
    period_end     DATE NOT NULL,
    q33            DOUBLE PRECISION NOT NULL,
    q67            DOUBLE PRECISION NOT NULL,
    n_observations INTEGER          NOT NULL,
    computed_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, timeframe, feature_name, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS strategy_regime_profile (
    id              BIGSERIAL PRIMARY KEY,
    preset_id       BIGINT NOT NULL REFERENCES presets(id) ON DELETE CASCADE,
    feature_name    TEXT   NOT NULL,    -- 4 valores possíveis
    bucket          TEXT   NOT NULL CHECK (bucket IN ('low', 'mid', 'high')),
    -- Stats agregados (todo o período)
    n_trades        INTEGER NOT NULL,
    n_wins          INTEGER NOT NULL,
    win_rate        NUMERIC(8,4),
    avg_pnl_pct     NUMERIC(10,4),
    sum_pnl_pct     NUMERIC(10,4),
    -- Time-split persistência (FP-A primeiro 50%, FP-B último 50%)
    fp_a_n_trades   INTEGER,
    fp_a_avg_pnl    NUMERIC(10,4),
    fp_b_n_trades   INTEGER,
    fp_b_avg_pnl    NUMERIC(10,4),
    persistence_ok  BOOLEAN,
    -- Bonferroni com bootstrap (IC 99.6% via 1000 reamostragens)
    bootstrap_ic_lo NUMERIC(10,4),
    bootstrap_ic_hi NUMERIC(10,4),
    bonferroni_ok   BOOLEAN,
    -- Decisão final
    is_useful       BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (preset_id, feature_name, bucket)
);

CREATE INDEX IF NOT EXISTS idx_regime_profile_preset
    ON strategy_regime_profile(preset_id);
CREATE INDEX IF NOT EXISTS idx_regime_profile_useful
    ON strategy_regime_profile(preset_id) WHERE is_useful = TRUE;

COMMIT;
