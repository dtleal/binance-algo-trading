# Plano — Anti-overfit + Walkforward em Rust

Status: **planejado**. Tabelas (`overfit_tests`, `walkforward_runs`) já existem (migration 011). Falta o código que popula elas + gates no `release`.

## Conceitos

### Anti-overfit
Dado um candidato (strategy + params + exit + params), responder: **"esse resultado é robusto ou foi sorte de overfitting na janela?"**

Dois checks independentes nessa fase:

1. **Param sensitivity (vizinhança)**
   - Perturba cada param numérico (±10%, ±20%) e reroda backtest no mesmo dataset.
   - Se vizinhos têm `return_pct` muito pior → champion está num pico estreito = overfit.
   - **Pass criteria**: mediana dos vizinhos ≥ 70% do retorno do champion (configurável).

2. **IS/OOS split**
   - Divide o range em dois: train (primeiros 70%) e test (últimos 30%).
   - Roda backtest com mesmos params nos dois.
   - **Pass criteria**: `oos_return / is_return ≥ 0.5` E `oos_trades ≥ min_trades`.

### Walkforward (modo validação)
Janelas deslizantes train/test. Para cada janela, **mesmos params** (não re-otimiza), só mede consistência no OOS.

- `train_days = 90`, `test_days = 30`, `step_days = 30` por default.
- Para cada janela `(train_start, train_end, test_start, test_end)`: backtest completo em ambos.
- **Pass criteria**: ≥ 70% das janelas test têm `return_pct > 0` E nenhuma janela tem `max_dd > 30%`.

Modo "re-otimização" (clássico walkforward, sweep dentro de cada janela) **fica fora dessa fase**. Mais complexo, vem depois se necessário.

## Arquitetura

### Helper compartilhado

Refactor: extrair de `detail.rs::run_detail` a parte que só calcula métricas (sem popular Trade list):

```rust
// src/evaluate.rs
pub fn evaluate_combo(
    candles: &[Candle],
    days: &DayIndex,
    strategy: &str,
    strategy_params: &serde_json::Value,
    exit_name: &str,
    exit_params: &serde_json::Value,
    pos_size: f64,
) -> Result<RunMetrics>
```

Reusa `detail::build_entries` + monta `ExitFn` do JSON + roda loop. Tanto anti-overfit quanto walkforward chamam isso N vezes.

### Anti-overfit: `src/overfit.rs`

```rust
pub struct OverfitInput {
    pub symbol: Symbol,
    pub timeframe: Timeframe,
    pub strategy: String,
    pub strategy_params: serde_json::Value,
    pub exit_name: String,
    pub exit_params: serde_json::Value,
    pub pos_size: f64,
}

pub struct ParamSensitivityConfig {
    pub perturbation_pcts: Vec<f64>,        // [-0.20, -0.10, +0.10, +0.20]
    pub min_relative_return: f64,           // 0.70 = vizinhos têm que ter ≥70%
}

pub struct IsOosConfig {
    pub split_ratio: f64,                   // 0.70 = 70% train / 30% test
    pub min_oos_relative_return: f64,       // 0.50
    pub min_oos_trades: usize,              // 30
}

pub fn check_param_sensitivity(
    client: &mut Client,
    input: &OverfitInput,
    config: &ParamSensitivityConfig,
    candles_full: &[Candle], days_full: &DayIndex,
) -> Result<TestResult>;

pub fn check_is_oos(...) -> Result<TestResult>;

pub struct TestResult {
    pub test_uuid: Uuid,
    pub passed: bool,
    pub metrics: serde_json::Value,         // detalhes pra debug
}
```

Cada check insere row em `overfit_tests` com `test_type` apropriado.

**Como perturbar params JSONB?** Iterar campos numéricos e gerar variantes com `value * (1 + pct)`. Bools/strings não perturbam.

### Walkforward: `src/walkforward.rs`

```rust
pub struct WalkforwardInput {
    pub symbol: Symbol,
    pub timeframe: Timeframe,
    pub strategy: String,
    pub strategy_params: serde_json::Value,
    pub exit_name: String,
    pub exit_params: serde_json::Value,
    pub pos_size: f64,
    pub from: NaiveDate,
    pub until: NaiveDate,
    pub train_days: u32,
    pub test_days: u32,
    pub step_days: u32,
}

pub struct WalkforwardConfig {
    pub min_test_pct_positive: f64,         // 0.70 = ≥70% janelas com retorno positivo
    pub max_test_dd_pct: f64,               // 30.0
}

pub struct WalkforwardOutput {
    pub walkforward_id: Uuid,
    pub windows: Vec<WindowResult>,
    pub passed: bool,
    pub summary: serde_json::Value,
}

pub fn run_walkforward(
    client: &mut Client,
    input: &WalkforwardInput,
    config: &WalkforwardConfig,
) -> Result<WalkforwardOutput>;
```

Cada janela vira 1 row em `walkforward_runs` com `walkforward_id` agrupando.

### Subcomandos novos

```bash
backtest overfit-check \
   --sweep-result-id 4157 \
   [--check param-sensitivity|is-oos-split|all] \
   [--neighborhood-pct 0.10,0.20] \
   [--oos-split 0.7] \
   [--min-trades 30]

backtest walkforward \
   --sweep-result-id 4157 \
   [--from 2024-01-01 --until 2025-04-01] \
   [--train-days 90 --test-days 30 --step-days 30] \
   [--min-pct-positive 0.70 --max-dd 30]
```

Output:
- Stdout: tabela por janela + summary final passed/failed
- DB: rows em `overfit_tests` / `walkforward_runs`
- Print do `test_uuid` / `walkforward_id` pra usar depois no `release`

### Gates no `release`

Atualizar `release` pra aceitar gates opcionais:

```bash
backtest release \
   --sweep-result-id 4157 \
   --require-overfit-test 550e8400-... \   # FAIL se não passou
   --require-walkforward 6ba7b810-... \    # FAIL se não passou
   [--released-by diego]
```

Se passados, `release` faz query em `overfit_tests`/`walkforward_runs` e bloqueia se algum teste daquele uuid `passed = false`. Sem os flags, comportamento atual (sem gates) — flexibilidade pra dev/CI.

`presets.overfit_test_id` e `walkforward_id` ganham os UUIDs validados (audit trail completo).

## Mudanças no schema

Nenhuma — tabelas `overfit_tests` e `walkforward_runs` já existem desde 011.

## Fora de escopo

- ❌ Walkforward em **modo re-otimização** (sweep dentro de cada janela). Complexo, custo computacional alto, vem se necessário.
- ❌ Sharpe ratio, Sortino, etc. — só retorno/winrate/max_dd nessa fase.
- ❌ Auto-execução: nada do tipo `backtest validate-and-release` que roda os 3 em sequência. Manual primeiro, automatizar depois quando o fluxo estiver maduro.
- ❌ Param sensitivity para Range strategy — params interagem demais (zone_pct depende de range_lookback etc.). Excluído por enquanto.

## Ordem de implementação

1. Refactor: extrair `evaluate_combo` em `src/evaluate.rs` (helper compartilhado).
2. `src/overfit.rs`: param sensitivity + is_oos_split + insert em `overfit_tests`.
3. `src/walkforward.rs`: rolling windows + insert em `walkforward_runs`.
4. CLI subcommands `overfit-check` e `walkforward`.
5. Atualizar `release` com flags `--require-overfit-test` e `--require-walkforward`.
6. Makefile targets: `make overfit-check ID=...`, `make walkforward ID=...`.
7. Smoke test E2E:
   - sweep → escolhe candidato top
   - overfit-check (esperado: passou ou não, ambos casos)
   - walkforward (idem)
   - release com gates → bloqueia/permite
8. Commit + push.

## Pendências futuras (não nessa fase)

- Bots lendo `presets WHERE status='active'` em vez de `symbol_configs`.
- Deletar `apply_champion.py` e as colunas tipadas legacy de `sweep_results` (tp_pct, sl_pct, ema_period etc.) — substituídas pelo JSONB.
- Walkforward modo re-otimização.
- `release` com `--auto-validate` rodando overfit+walkforward na mesma chamada.
