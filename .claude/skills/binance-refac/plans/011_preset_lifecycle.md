# Plano — Migration 011 + release pipeline (presets, walkforward, overfit)

Status: **planejado**, executando agora.

## Contexto

Pipeline alvo (do user):
```
sweep → backtest → anti-overfit → walkforward → release de preset → bots live
```

Estado atual: temos sweep + backtest. **`apply_champion` está quebrado** — escolhe maior `return_pct` cego. Substituir por `release` que requer (no futuro) anti-overfit + walkforward verde.

Esta fase entrega só a fundação:
- Schema das 3 novas tabelas (`presets`, `walkforward_runs`, `overfit_tests`)
- JSONB params em `sweep_results` (params hoje ficam só em label texto, NULL nas colunas tipadas)
- `backtest release --sweep-result-id N` minimalista (sem gates ainda)

Anti-overfit + walkforward + bot integration ficam pra fases seguintes.

## Migration 011

```sql
-- Sweep_results: params como JSONB (strategy + exit) — substitui colunas tipadas legadas
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS strategy_params JSONB,
    ADD COLUMN IF NOT EXISTS exit_params     JSONB;

CREATE INDEX IF NOT EXISTS idx_sweep_strategy_params
    ON sweep_results USING GIN (strategy_params);
CREATE INDEX IF NOT EXISTS idx_sweep_exit_params
    ON sweep_results USING GIN (exit_params);

-- Presets: append-only "use estes params live"
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

-- Walkforward (placeholder — implementação na fase seguinte)
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

-- Overfit tests (placeholder)
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
```

## Mudanças no crate Rust

1. **`Cargo.toml`**: adicionar `serde_json`.
2. **`types.rs`**:
   - `EntrySet` ganha `strategy_params: serde_json::Value`.
   - `ExitVariant` ganha `params: serde_json::Value`.
   - `RunResult` ganha `strategy_params` e `exit_params`.
3. **Cada strategy/exit**: além do `label`, produz `serde_json::json!({...})`.
4. **`sweep.rs`**: thread params do EntrySet/ExitVariant pro RunResult.
5. **`db.rs::write_sweep_results`**: insere `strategy_params` e `exit_params` JSONB.
6. **`bin/backtest.rs`**: novo subcommand `release`:
   ```bash
   backtest release --sweep-result-id 12345 [--notes "..."] [--released-by "..."]
   ```
   Logic:
   - SELECT da row em sweep_results
   - UPDATE presets SET status='retired' WHERE symbol/strategy + status='active'
   - INSERT INTO presets (...) com snapshot das métricas

## Fora de escopo desta fase

- ❌ `release --from-params` (versão dispatcher) — fica pra depois
- ❌ Anti-overfit gates
- ❌ Walkforward implementação (só tabela vazia)
- ❌ Bots lendo de `presets` (continuam em `symbol_configs`)
- ❌ Deletar `apply_champion.py` (deixa coexistir até bots migrarem)
- ❌ Backfill de dados antigos no JSONB (rows antigas ficam strategy_params=NULL)

## Pós-implementação imediata

`backtest sweep` agora popula JSONB. `backtest release` desbloqueia o ciclo "sweep → preset". Bots ainda leem `symbol_configs`, mas humanos podem fazer o release com auditoria via `presets` table.
