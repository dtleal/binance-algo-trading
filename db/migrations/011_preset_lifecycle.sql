-- Migration 011: preset lifecycle (release pipeline) + JSONB params
--
-- Engloba a fundação do fluxo: sweep → backtest → anti-overfit → walkforward → release
-- Anti-overfit/walkforward implementations vêm em fases seguintes — tabelas criadas vazias.

-- ── 1. sweep_results: params como JSONB (substitui colunas tipadas legadas) ───
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS strategy_params JSONB,
    ADD COLUMN IF NOT EXISTS exit_params     JSONB;

CREATE INDEX IF NOT EXISTS idx_sweep_strategy_params
    ON sweep_results USING GIN (strategy_params);
CREATE INDEX IF NOT EXISTS idx_sweep_exit_params
    ON sweep_results USING GIN (exit_params);

-- ── 2. presets: append-only "use estes params live" ──────────────────────────
CREATE TABLE IF NOT EXISTS presets (
    id               BIGSERIAL    PRIMARY KEY,
    symbol           TEXT         NOT NULL,
    timeframe        TEXT         NOT NULL,
    strategy         TEXT         NOT NULL,
    exit_name        TEXT         NOT NULL,
    strategy_params  JSONB        NOT NULL,
    exit_params      JSONB        NOT NULL,
    -- Auditoria: de onde veio
    sweep_id         UUID,
    sweep_result_id  BIGINT       REFERENCES sweep_results(id),
    walkforward_id   UUID,
    overfit_test_id  BIGINT,
    -- Snapshot das métricas no momento do release
    return_pct       NUMERIC(10,4),
    win_rate         NUMERIC(8,4),
    trades           INT,
    max_dd_pct       NUMERIC(8,4),
    -- Lifecycle
    status           TEXT         NOT NULL DEFAULT 'active',
    released_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    released_by      TEXT,
    retired_at       TIMESTAMPTZ,
    retired_reason   TEXT,
    notes            TEXT,
    CHECK (status IN ('active', 'retired'))
);

-- Só 1 preset 'active' por (symbol, strategy)
CREATE UNIQUE INDEX IF NOT EXISTS uq_presets_active
    ON presets (symbol, strategy) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_presets_symbol ON presets (symbol);
CREATE INDEX IF NOT EXISTS idx_presets_sweep  ON presets (sweep_id) WHERE sweep_id IS NOT NULL;

-- ── 3. walkforward_runs (placeholder — implementação na fase seguinte) ───────
CREATE TABLE IF NOT EXISTS walkforward_runs (
    id              BIGSERIAL    PRIMARY KEY,
    walkforward_id  UUID         NOT NULL,
    symbol          TEXT         NOT NULL,
    timeframe       TEXT         NOT NULL,
    strategy        TEXT         NOT NULL,
    exit_name       TEXT         NOT NULL,
    strategy_params JSONB        NOT NULL,
    exit_params     JSONB        NOT NULL,
    train_start     DATE         NOT NULL,
    train_end       DATE         NOT NULL,
    test_start      DATE         NOT NULL,
    test_end        DATE         NOT NULL,
    train_return    NUMERIC(10,4),
    test_return     NUMERIC(10,4),
    train_trades    INT,
    test_trades     INT,
    test_max_dd     NUMERIC(8,4),
    test_win_rate   NUMERIC(8,4),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wf_runs_id     ON walkforward_runs (walkforward_id);
CREATE INDEX IF NOT EXISTS idx_wf_runs_symbol ON walkforward_runs (symbol, strategy);

-- ── 4. overfit_tests (placeholder) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS overfit_tests (
    id              BIGSERIAL    PRIMARY KEY,
    test_uuid       UUID         NOT NULL,
    symbol          TEXT         NOT NULL,
    strategy        TEXT         NOT NULL,
    exit_name       TEXT         NOT NULL,
    strategy_params JSONB        NOT NULL,
    exit_params     JSONB        NOT NULL,
    test_type       TEXT         NOT NULL,
    passed          BOOLEAN      NOT NULL,
    metrics         JSONB,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_overfit_uuid ON overfit_tests (test_uuid);
