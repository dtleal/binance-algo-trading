# Milestone — Reescrita do sweep Rust

Status: **planejado**. Engloba os planos 009 (klines bulk-ready) e 010 (sweep_results exit-aware) e adiciona o crate Rust unificado.

## Objetivo

Substituir os 3 binários atuais (`backtest_sweep/`, `backtest_sweep_v2/`, `backtest_sweep_range/` — ~3k linhas, ~700 duplicadas) por **1 crate enxuto** lendo Postgres direto, com strategies e exits combinados livremente no mesmo run.

## Arquitetura final

```
backtest/src/
├─ lib.rs                  # Candle, Symbol, Timeframe, Entry, RunMetrics, RunResult
├─ db.rs                   # postgres::Client (sync) — load_candles + write_sweep_results
├─ indicator/
│   ├─ mod.rs
│   └─ {vwap,ema,atr,adx}.rs            # funções puras
├─ strategy/
│   ├─ mod.rs              # trait Strategy + factory(name) -> Box<dyn Strategy>
│   └─ {momentum,vwap_pullback,ema_scalp,orb,pdhl,range}.rs
├─ exit/
│   ├─ mod.rs              # trait Exit + enum ExitFn (hot path) + factory
│   └─ {fixed_tp_sl,trailing_stop}.rs   # range NÃO está aqui de propósito
├─ sweep.rs                # ~30 linhas: cartesian + par_iter + write
└─ bin/backtest.rs         # clap + main
```

## Contratos

```rust
enum StrategyOutput {
    EntrySets(Vec<EntrySet>),       // path padrão: parear com exits externos
    Runs(Vec<MonolithicRun>),        // path Range: exit baked-in
}

trait Strategy: Send + Sync {
    fn name(&self) -> &'static str;
    fn build(&self, ctx: &Ctx) -> StrategyOutput;
}

trait Exit: Send + Sync {
    fn name(&self) -> &'static str;
    fn build_variants(&self, ctx: &Ctx) -> Vec<ExitVariant>;
}

struct EntrySet    { label: String, entries: Arc<Vec<Entry>> }
struct ExitVariant { label: String, eval: ExitFn }            // enum, não dyn

enum ExitFn {                                                   // hot path = enum monomórfico
    FixedTpSl { tp: f64, sl: f64 },
    Trailing  { sl: f64, be_r: f64, trail: f64, tp_r: f64 },
}
```

Range retorna `Runs` (exit baked-in); resto retorna `EntrySets` pareados com `ExitVariants` externos.

## CLI alvo

```bash
backtest \
  --symbol BTCUSDT --timeframe 5m \
  --from 2024-01-01 --until 2024-06-30 \
  --strategy vwap_pullback,orb \
  --exit fixed_tp_sl,trailing_stop
```

Sweep faz cartesiano (`entries(strategies) × variants(exits)`) automaticamente, escreve em `sweep_results` agrupado por `sweep_id`.

## Stack

`postgres` (sync — sem tokio), `rayon` (par_iter), `clap` (CLI), `serde+toml` (config opcional), `uuid` (sweep_id), `md5` (params_hash), `anyhow`/`thiserror`, `tracing`.

**Não usar**: `sqlx`, `tokio`, traits com associated types, `inventory!`/`linkme`, `Box<dyn>` em hot path.

## Dependências de DB (planos separados)

| # | Plano | O que muda |
|---|---|---|
| **009** | `plans/009_klines_bulk_ready.md` | `klines`: `DOUBLE PRECISION`, PK natural, `fillfactor=100`, CHECK em timeframe, +4 colunas Binance |
| **010** | `plans/010_sweep_results_exit_aware.md` | `sweep_results`: `exit_name`, `period_start/end`, `sweep_id`, `params_hash`, params de trailing/range, UQ robusto |

Ambas aditivas/seguras. Não bloqueiam o crate, mas o output só fica completo com a 010 aplicada.

## Princípios aplicados

- **Trait fora, enum dentro**: extensão fácil (adicionar exit = arquivo + linha) + zero vtable no hot loop (`evaluate` é match em `ExitFn`).
- **Implícito > explícito**: defaults (source=Postgres via env, exit=`fixed_tp_sl` se não especificado, sweep_id auto-gerado).
- **Funções pequenas**: cada indicator é função pura sobre `&[Candle]`; cada `find_entries` em arquivo próprio; `sweep.rs` < 100 linhas.
- **Phase 1 / Phase 2 explícitas**: precompute entries sequencial + par_iter sobre cartesiano — preserva perf atual.
- **Newtypes**: `Symbol(String)`, `Timeframe` enum, `R(f64)` para R-multiples — erros viram falha de compilação.
- **Async só nas pontas (e nem isso)**: como `postgres` é sync, o binário inteiro é síncrono. Rayon cuida do paralelismo CPU.

## Ordem de execução

1. Instalar projeto na máquina + Postgres up + migrations 001-008 aplicadas.
2. **Aplicar migration 009** (`klines` bulk-ready).
3. **Aplicar migration 010** (`sweep_results` exit-aware).
4. Bulk-load 3 anos de klines via `db.bulk_insert_klines` (tooling Python a escrever — recipe no plano 009).
5. Criar crate `backtest/` com a estrutura acima. Começar por:
   - `lib.rs` (tipos)
   - `db.rs` (load + write)
   - `indicator/` (todos)
   - `strategy/vwap_pullback.rs` + `exit/fixed_tp_sl.rs`
   - `sweep.rs` + `bin/backtest.rs`
   
   **Validar end-to-end com 1 strategy + 1 exit antes de portar o resto.**
6. Migrar strategies restantes uma por uma, comparando output com sweep antigo (TOP 30 por símbolo; equivalência numérica como gate).
7. Migrar `trailing_stop` exit. Deletar `backtest_sweep_v2/`.
8. Migrar Range. Deletar `backtest_sweep_range/`.
9. Deletar `backtest_sweep/`. Ajustar `Makefile`.
10. Apagar `data/klines/*.csv` (Postgres = source of truth).

## Gate de equivalência (cada porta de strategy)

Para cada strategy migrada:
- Rodar sweep antigo (binário original) e novo (crate unificado) sobre **mesmo CSV**/mesma slice.
- Comparar `RunResult.return_pct`, `trades`, `wins`, `losses`, `max_dd_pct` por combo de params.
- Tolerância: zero (mesma lógica → mesmos números). Se divergir, é regressão.
- Só apagar binário antigo depois que todas as strategies dele estiverem com gate verde no novo.

## Pendências de decisão antes do código

- [ ] Confirmar lista final de timeframes pro CHECK da 009.
- [ ] Confirmar nomes finais de `exit_name` e `source` na 010.
- [ ] Decidir nome do crate (`backtest/` é minha sugestão; alternativas: `sweep/`, `engine/`).
- [ ] Walk-forward: orquestrador em Python ou subcomando do próprio binário Rust? Recomendação: Python externo, Rust foca em "rodar 1 sweep dado um range".
- [ ] Manter `backtest_sweep/` como referência durante a migração ou apagar de cara? Recomendação: manter até gate verde de cada strategy.

## O que NÃO está no escopo desse milestone

- Não migra os bots de live trading (`trader/bot_*.py`) — são consumers, não geradores de sweep.
- Não toca `db/seed_configs.py` ou `db/apply_champion.py` — leem de `sweep_results`, podem precisar de update mínimo se UQ mudar mas não é bloqueante.
- Não substitui `db/import_sweeps.py` (CSV → DB legado) — pode ser deletado depois que ninguém mais gerar CSVs.
- Não adiciona Parquet/Arrow — DB direto resolve walk-forward; Parquet entra só se perf de load virar problema (não vai virar).

## Localização

Este milestone vive em `.claude/skills/binance-refac/plans/milestone_sweep_rewrite.md`. Os planos de migration que ele referencia estão na mesma pasta:
- `009_klines_bulk_ready.md`
- `010_sweep_results_exit_aware.md`
