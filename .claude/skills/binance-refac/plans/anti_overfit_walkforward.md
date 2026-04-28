# Plano — Anti-overfit + Walkforward em Rust

Status: **planejado**. Tabelas (`overfit_tests`, `walkforward_runs`) já existem (migration 011). Falta o código que popula elas + gates no `release`.

## Correções identificadas antes de implementar

Revisão crítica achou 5 problemas no plano original:

### 1. Migration 012 — `presets.overfit_test_id` deveria ser UUID
Migration 011 criou `presets.overfit_test_id BIGINT`, mas `overfit_tests.test_uuid` é o que agrupa um teste (cada teste vira N rows, uma por check_type). BIGINT na presets não bate. **Migration 012** corrige:
```sql
ALTER TABLE presets DROP COLUMN overfit_test_id;
ALTER TABLE presets ADD COLUMN overfit_test_uuid UUID;
```
`walkforward_id UUID` já está correto.

### 2. Range strategy precisa de dispatch dedicado em `evaluate_combo`
`detail::build_entries` falha para range (entry+exit acoplados). `evaluate_combo` precisa:
```rust
match strategy {
    "range" => {
        let atr = atr_wilder(candles, ATR_PERIOD);
        let adx = adx_wilder(candles, ADX_PERIOD);
        let atr_pct = compute_atr_pct(candles, &atr);
        let p: RangeParams = serde_json::from_value(strategy_params.clone())?;
        Ok(range::run_range_backtest(candles, &adx, &atr_pct, p.adx_thresh, ...))
    }
    other => {
        // Path padrão: build_entries + evaluate per-entry
        let entries = detail::build_entries(other, candles, days, &params_to_detail(strategy_params))?;
        Ok(run_loop(entries, candles, exit_fn, pos_size))
    }
}
```

### 3. Outcome `inconclusive` não cabe em `passed BOOLEAN`
Schema atual de `overfit_tests` tem `passed BOOLEAN NOT NULL`. Mas plano fala em retornar inconclusive (ex.: param sensitivity com <4 vizinhos disponíveis). **Migration 012** adiciona:
```sql
ALTER TABLE overfit_tests ADD COLUMN outcome TEXT NOT NULL DEFAULT 'pending'
   CHECK (outcome IN ('pass', 'fail', 'inconclusive'));
-- passed BOOLEAN fica como compat — true se outcome='pass', false caso contrário.
-- Eventualmente DROP passed (migration futura).
```

### 4. Walkforward: janelas com 0 trades
Se uma janela não gera trades, `return_pct = 0`, `max_dd = 0`. Regra:
- **Excluir janelas vazias do denominador** do "% positivas".
- Se >50% das janelas forem vazias → outcome `inconclusive` (estratégia não dispara nessa frequência).
- Reportar contagem `(positive, negative, empty)` no summary JSONB.

### 5. IS/OOS com IS negativo
Se champion tem `is_return < 0`, ratio `oos/is` engana (números negativos bagunçam). Regra:
- Se `is_return < 0` → outcome `fail` direto (candidato é ruim mesmo no IS, esquece OOS).
- Se `is_return ≥ 0` → calcula ratio normalmente.

## Recap

Antes de codar:
1. Migration 012 (3 mudanças: presets.overfit_test_uuid, overfit_tests.outcome, presets backfill).
2. evaluate_combo dispatcher por strategy (range path separado).
3. TestResult tem 3 estados, não 2.
4. Walkforward conta janelas vazias separadamente.
5. IS/OOS rejeita IS negativo direto.

## Conceitos

### Anti-overfit
Dado um candidato (strategy + params + exit + params), responder: **"esse resultado é robusto ou foi sorte de overfitting na janela?"**

Dois checks independentes — **respondem perguntas diferentes, ambos importantes**:

1. **Param sensitivity (vizinhança)** — *"o champion é um pico isolado ou está num platô?"*
   - **Não re-roda backtest.** Consulta `sweep_results` direto pra achar combos vizinhos
     (mesma strategy/exit, params parecidos) e compara `return_pct`.
   - Vizinho = combo que difere em 1 param do champion (próximo valor da grade).
   - Se vizinhos têm retorno muito pior → champion está num pico estreito = overfit.
   - **Pass criteria**: mediana dos vizinhos ≥ 70% do retorno do champion (configurável).
   - 100x mais rápido que re-rodar; usa exatamente a grade que o user definiu.

2. **IS/OOS split** — *"o sweep generalizou pra dados que ele não viu?"*
   - Esse teste **só faz sentido se o sweep do champion cobriu o range inteiro** —
     o que é nosso caso atual.
   - Divide range em **train (primeiros 70%) + test (últimos 30%)**.
   - Roda backtest com os params do champion nos **dois pedaços separados**.
   - Se `is_return` foi alto e `oos_return` é muito menor → sweep aprendeu padrões
     do passado que não se repetem.
   - **Pass criteria**: `oos_return / is_return ≥ 0.5` E `oos_trades ≥ min_trades`.
   - É o único teste que pega "champion ficou ótimo porque o sweep enxergou o futuro".
   - Walkforward sozinho **não substitui isso** — ele mostra consistência em
     subperíodos do dado em que foi otimizado, não generalização pra dado novo.

Split fixo 70/30 (não configurável nessa fase) — simplifica e evita gaming.

### Walkforward (modo validação)
Pergunta diferente: *"performance é estável ao longo do tempo ou foi sorte de uma janela?"*

- **Modo validação**: params já estão fixos (vêm do champion). Não há "train" porque não treinamos nada — só medimos performance do mesmo combo em vários subperíodos.
- **CLI**: `--window-days 30 --step-days 30` (só janelas test deslizantes).
- Para cada janela `[start, start + window_days)`: `evaluate_combo` → métricas. Insere row em `walkforward_runs` (campos `train_*` deixados NULL ou == test_*).
- **Pass criteria**: ≥ 70% das janelas com `return_pct > 0` E nenhuma com `max_dd > 30%`.

Modo "re-otimização" (sweep dentro de cada janela, clássico walkforward) **fica fora dessa fase**. Mais complexo, custo computacional alto, vem depois se necessário.

### Diferença entre os 3 testes

| Teste | Pergunta | Sinal de overfit |
|---|---|---|
| Param sensitivity | "vizinhos da grade têm retorno parecido?" | Pico isolado, vizinhos ruins |
| IS/OOS split | "params escolhidos sobre todo o histórico generalizam pra um pedaço que o sweep não viu?" | OOS muito pior que IS |
| Walkforward | "performance é estável em subperíodos?" | Maioria das janelas negativa ou DD alto |

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
    pub min_relative_return: f64,           // 0.70 = mediana dos vizinhos ≥ 70%
    pub min_neighbors: usize,               // 4 — se acharmos menos, retorna inconclusive
}

pub struct IsOosConfig {
    pub split_ratio: f64,                   // 0.70 (fixo nessa fase)
    pub min_oos_relative_return: f64,       // 0.50
    pub min_oos_trades: usize,              // 30
}

pub fn check_param_sensitivity(
    client: &mut Client,
    input: &OverfitInput,
    sweep_id_or_recent: Option<Uuid>,       // opcional: limita escopo dos vizinhos
    config: &ParamSensitivityConfig,
) -> Result<TestResult>;

pub fn check_is_oos(
    client: &mut Client,
    input: &OverfitInput,
    candles_full: &[Candle], days_full: &DayIndex,
    config: &IsOosConfig,
) -> Result<TestResult>;

pub struct TestResult {
    pub test_uuid: Uuid,
    pub passed: bool,
    pub metrics: serde_json::Value,         // detalhes pra debug
}
```

Cada check insere row em `overfit_tests` com `test_type` apropriado.

**Como achar vizinhos no `sweep_results`?**
- Para cada param numérico do champion, busca rows com mesmo strategy + exit_name +
  todos os outros params iguais, mas esse param 1 step mais alto OU mais baixo na
  grade (descobre os steps disponíveis via `SELECT DISTINCT strategy_params->'param'
  FROM sweep_results WHERE strategy=...`).
- Se o sweep não cobriu vizinhança suficiente (`min_neighbors=4`), retorna
  `inconclusive` em vez de pass/fail (rodar sweep com grade mais densa antes).
- Bools/strings (`vol_filter`, `kind`, etc.) não geram vizinhos numéricos —
  ignorados na análise.

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
    pub window_days: u32,                   // tamanho de cada janela
    pub step_days: u32,                     // quanto desliza entre janelas
}

pub struct WalkforwardConfig {
    pub min_pct_positive: f64,              // 0.70 = ≥70% janelas com retorno positivo
    pub max_window_dd_pct: f64,             // 30.0
}

pub struct WindowResult {
    pub window_idx: usize,
    pub start: NaiveDate,
    pub end: NaiveDate,
    pub return_pct: f64,
    pub win_rate: f64,
    pub trades: usize,
    pub max_dd_pct: f64,
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

Sem train/test split: params são fixos, só medimos performance em cada janela.

**Schema da tabela `walkforward_runs`** já tem `train_*` e `test_*` (criada em 011).
Como não há train no modo validação, populamos `train_*` com NULL e `test_*` com
os valores da janela. Migration 012 (futura) pode tornar `train_*` nullable.

Cada janela vira 1 row em `walkforward_runs` com `walkforward_id` agrupando.

### Subcomandos novos

```bash
backtest overfit-check \
   --sweep-result-id 4157 \
   [--check param-sensitivity|is-oos|all] \
   [--min-relative-return 0.70] \      # param-sensitivity: mediana vizinhos
   [--min-oos-relative 0.50] \         # is-oos: oos/is ratio
   [--min-trades 30]

backtest walkforward \
   --sweep-result-id 4157 \
   [--from 2024-01-01 --until 2025-04-01] \
   [--window-days 30 --step-days 30] \
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

- ❌ Walkforward em **modo re-otimização** (sweep dentro de cada janela). Custo computacional alto, vem se necessário.
- ❌ IS/OOS com split configurável. Fixo 70/30 nessa fase.
- ❌ Param sensitivity com **perturbação artificial** (±10%). Usamos só vizinhos da grade real do sweep.
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
