# Plano — Migration 009: klines preparado para bulk-insert da Binance

Status: **planejado, não executado**. Roda depois que o projeto estiver instalado nesta máquina e Postgres up.

## Pré-condições

1. Repo clonado e `poetry install` rodado.
2. `.env` configurado com `POSTGRES_*`.
3. `docker compose up -d postgres` (healthcheck verde).
4. Migrations atuais aplicadas até 008:
   ```
   poetry run python -m db.migrate
   ```
5. Snapshot do row-count atual (sanity post-migration):
   ```
   make db-shell
   SELECT COUNT(*) FROM klines;
   ```

## Migration `db/migrations/009_klines_bulk_ready.sql`

Mudanças:
- `NUMERIC(20,8)` → `DOUBLE PRECISION` em OHLCV.
- Drop `id BIGSERIAL`; PK natural `(symbol, timeframe, open_time)`.
- `fillfactor = 100` (append-only).
- `CHECK` em `timeframe`.
- 4 colunas Binance opcionais: `quote_volume`, `trades`, `taker_buy_base`, `taker_buy_quote`.

Como é mudança de tipo + PK, faz via tabela nova + swap (tudo em transação — `migrate.py` já envelopa cada arquivo num BEGIN/COMMIT):

```sql
-- Migration 009: klines bulk-insert ready

CREATE TABLE klines_new (
    symbol           TEXT             NOT NULL,
    timeframe        TEXT             NOT NULL,
    open_time        TIMESTAMPTZ      NOT NULL,
    open             DOUBLE PRECISION NOT NULL,
    high             DOUBLE PRECISION NOT NULL,
    low              DOUBLE PRECISION NOT NULL,
    close            DOUBLE PRECISION NOT NULL,
    volume           DOUBLE PRECISION NOT NULL,
    close_time       TIMESTAMPTZ      NOT NULL,
    quote_volume     DOUBLE PRECISION,
    trades           INT,
    taker_buy_base   DOUBLE PRECISION,
    taker_buy_quote  DOUBLE PRECISION,
    PRIMARY KEY (symbol, timeframe, open_time),
    CHECK (timeframe IN ('1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d'))
) WITH (fillfactor = 100);

INSERT INTO klines_new
    (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
SELECT
    symbol, timeframe, open_time,
    open::double precision, high::double precision, low::double precision,
    close::double precision, volume::double precision, close_time
FROM klines
ON CONFLICT (symbol, timeframe, open_time) DO NOTHING;

DROP TABLE klines;
ALTER TABLE klines_new RENAME TO klines;

CREATE INDEX idx_klines_latest
    ON klines (symbol, timeframe, open_time DESC);
```

## Verificação pós-migration

```sql
-- Row count bate com o snapshot pré-migration
SELECT COUNT(*) FROM klines;

-- Schema novo está aplicado
\d klines

-- Esperado:
--   PK: (symbol, timeframe, open_time)
--   open/high/low/close/volume = double precision
--   CHECK em timeframe
--   fillfactor=100
--   índice idx_klines_latest

-- Spot check
SELECT * FROM klines ORDER BY open_time DESC LIMIT 3;

-- CHECK funcionando (deve falhar)
INSERT INTO klines (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
VALUES ('TEST', 'invalid', NOW(), 1, 1, 1, 1, 1, NOW());
-- ERROR: violates check constraint
```

## Recipe de bulk-insert

Padrão idiomático com asyncpg, vai virar helper em `db/bulk_insert_klines.py`:

```python
async def bulk_insert_klines(pool, rows: list[tuple]) -> int:
    """rows: [(symbol, timeframe, open_time, o, h, l, c, v, close_time,
              quote_volume, trades, taker_buy_base, taker_buy_quote), ...]
    Retorna número de rows novas inseridas (ignora duplicatas)."""
    if not rows:
        return 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                CREATE TEMP TABLE klines_stage (LIKE klines INCLUDING DEFAULTS)
                ON COMMIT DROP
            """)
            await conn.copy_records_to_table(
                'klines_stage',
                records=rows,
                columns=[
                    'symbol','timeframe','open_time',
                    'open','high','low','close','volume','close_time',
                    'quote_volume','trades','taker_buy_base','taker_buy_quote',
                ],
            )
            inserted = await conn.fetchval("""
                INSERT INTO klines SELECT * FROM klines_stage
                ON CONFLICT (symbol, timeframe, open_time) DO NOTHING
                RETURNING 1
            """)
    return inserted or 0
```

Notas:
- `copy_records_to_table` é binário, ~10-50x mais rápido que `executemany`.
- `TEMP TABLE … ON COMMIT DROP` evita WAL e some sozinha.
- `ON CONFLICT DO NOTHING` é idempotente — re-rodar download é seguro.
- Rows existentes (NUMERIC migradas) **não** vão ter as 4 colunas Binance preenchidas (NULL). Próximos downloads vão preencher.

## Benchmark recomendado (depois da migration, antes do bulk grande)

Para confirmar o ganho de DOUBLE vs NUMERIC nesta máquina:

```python
# Inserir 100k linhas dummy nas duas formas, medir.
# Resultado documenta o ganho real e justifica o trade-off.
```

Salvar resultado em `docs/benchmarks/numeric_vs_double.md`.

## Rollback

Se algo der errado:
- Volume `pg_data` é `external: true`, sobrevive a `docker compose down`.
- Para zerar e re-aplicar: `docker volume rm binance-algo-trading_pg_data && docker compose up -d postgres && make db-migrate` (re-roda 001..008, não roda 009 se ainda não existir o arquivo).
- Se a 009 já estava criada e quebrou no meio: como tudo está numa transação, o `BEGIN/ROLLBACK` do `migrate.py` reverte. `klines` original fica intacto.

## Pendências antes de executar

- [ ] Projeto instalado nesta máquina (poetry install).
- [ ] Postgres docker up + healthy.
- [ ] Migrations 001-008 aplicadas.
- [ ] Backup/snapshot do `pg_data` se já houver dados que importam.
- [ ] Decidir lista final de timeframes do CHECK (hoje proposto: `1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d`).

## Próximos passos depois da 009

1. Escrever `db/bulk_insert_klines.py` com a recipe acima.
2. Refatorar `db/fetch_klines.py` para usar o bulk insert no lugar do INSERT row-by-row atual.
3. Estender `db/export_sweep_csv.py` com `--start-date`/`--end-date` para walk-forward.
4. Decidir destino dos `data/klines/*.csv` legados (apagar quando Postgres for source of truth confirmado).
