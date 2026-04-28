# Plano — Migration 010: sweep_results preparado para exit-aware + walk-forward

Status: **planejado, não executado**. Roda depois da 009 e da reescrita do crate Rust de sweep.

## Por que

Tabela atual (migration 003) foi feita assumindo:
- 1 binário (sweep_v1) → modelo fixed TP/SL implícito.
- 1 modo de exit por binário → coluna `strategy` conflata strategy+exit.
- Sweep sobre o histórico inteiro → sem campos de período.
- Sem range strategy → colunas ADX/ATR/zone ausentes.

A reescrita Rust gera output com:
- `exit_name` separado de `strategy_name` (mesma estratégia × múltiplos exits no mesmo run).
- Params de trailing (`be_r`, `trail_step`, `tp_r`) em `sweep_results`.
- Params de range (`adx_thresh`, `atr_pct_thresh`, …) em `sweep_results`.
- Período do walk-forward (`period_start`, `period_end`).
- `sweep_id` agrupando todos os results de uma execução.
- `params_hash` para dedup robusto (UQ atual é frágil — `NULL` distinct em UNIQUE).

## Migration `db/migrations/010_sweep_results_exit_aware.sql`

Aditiva. Não toca dados existentes; novas colunas NULL-able preservam backwards compat.

```sql
-- Migration 010: sweep_results exit-aware + walk-forward

-- Exit model como dimensão própria (antes: implícito no binário usado)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS exit_name TEXT;

-- Params do trailing-stop (sweep_v2)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS be_r       NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS trail_step NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS tp_r       NUMERIC(8,4);

-- Params da range strategy
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

-- Origem do resultado (qual binário/versão gerou)
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS source   TEXT,        -- 'sweep_v1' | 'sweep_v2' | 'sweep_range' | 'backtest'
    ADD COLUMN IF NOT EXISTS sweep_id UUID;        -- agrupa results de uma execução

-- Hash determinístico de todos os params normalizados → dedup robusto
ALTER TABLE sweep_results
    ADD COLUMN IF NOT EXISTS params_hash CHAR(32); -- md5 hex

-- CHECKs em enums
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

-- Índices para queries típicas
CREATE INDEX IF NOT EXISTS idx_sweep_results_sweep_id
    ON sweep_results (sweep_id) WHERE sweep_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sweep_results_period
    ON sweep_results (symbol, timeframe, period_start)
    WHERE period_start IS NOT NULL;
```

## Convenções de uso

- **`sweep_id`**: gerado pelo binário no startup (`Uuid::new_v4()`). Todos os runs daquela execução compartilham o mesmo. Permite "mostre todos os resultados do sweep de ontem 14h".
- **`params_hash`**: md5 hex (32 chars) de uma representação canônica dos params (ordem alfabética de chave, valores arredondados a 6 casas). Re-rodar mesmo sweep com mesmos params = mesmo hash = `ON CONFLICT DO NOTHING`.
- **`period_start`/`period_end`**: NULL para sweep "full history". Preenchido para walk-forward mensal.
- **`exit_name = NULL`**: dado legado da v1 (sem `exit_name` separado). Não pode entrar em CHECK obrigatório.

## Bulk-write idiomático no Rust

```rust
// db.rs
pub fn write_sweep_results(
    client: &mut postgres::Client,
    sweep_id: Uuid,
    rows: &[RunResult],
) -> Result<usize> {
    // COPY binário pra staging UNLOGGED → INSERT ON CONFLICT DO NOTHING
    let mut tx = client.transaction()?;
    tx.execute(
        "CREATE TEMP TABLE sweep_stage (LIKE sweep_results INCLUDING DEFAULTS)
         ON COMMIT DROP", &[])?;

    let writer = tx.copy_in(
        "COPY sweep_stage (symbol, timeframe, strategy, exit_name, ...,
                           period_start, period_end, sweep_id, params_hash, source)
         FROM STDIN WITH BINARY")?;
    // …escreve rows…

    let n = tx.execute(
        "INSERT INTO sweep_results SELECT * FROM sweep_stage
         ON CONFLICT DO NOTHING", &[])?;
    tx.commit()?;
    Ok(n as usize)
}
```

## Pendências antes de executar

- [ ] 009 aplicada (não-bloqueante mas faz sentido na ordem).
- [ ] Validar nomes finais de `exit_name`/`source` se quiser mais variantes.
- [ ] Decidir se runs antigos (sweep_v1) ganham backfill de `source = 'sweep_v1'` num seed separado, ou se ficam como NULL.

## O que **não** está nesse plano

- Não dropa nem reescreve dados existentes.
- Não mexe no índice `idx_sweep_champion` (continua útil).
- Não adiciona partição (defer; só vira problema com 10M+ rows).
- Não obriga `exit_name`/`params_hash` (NULL-able pra preservar legado).
