# Milestone 4 — Composite backtest + bot Python integrado

**Output**: meta-strategy "composite" no engine + bot Python que lê presets e regime score em runtime + golden vector tests.

## Mudanças do plano original

1. **Correlation matrix entre presets** ANTES de combinar
2. **Real fee model** no backtest composite (cada preset paga taxa cheia)
3. **Calibração de thresholds via grid** + validação por holdout
4. **Golden vectors Python/Rust** pra parity (não só "porta as fórmulas")
5. **Risk policy é parâmetro do backtest**, não bolted-on (kill switch backtested)

## Parte A — Composite engine no Rust

### Subcomando

```bash
backtest composite-backtest \
  --symbol ETHUSDT --timeframe 15m \
  --presets 1,3,5 \
  --activate-threshold 0.4 \
  --full-position-threshold 0.65 \
  --max-total-pos-size 0.50 \
  --max-correlation 0.7 \
  --kill-switch-dd 0.05 \
  --from 2025-04-29 --until 2026-04-29
```

### Loop de execução (pseudo-código)

```rust
let regime_arrays = RegimeArrays::compute(&candles);
let fingerprints: Vec<StrategyFingerprint> = load_from_db(preset_ids);
let preset_runners: Vec<PresetRunner> = init_runners(presets);
let mut killed = false;

for i in 50..candles.len() {
    // 1. Existing positions: SEMPRE gerenciadas (regime score não fecha posição aberta)
    for runner in &mut preset_runners {
        runner.tick_existing_positions(i);
    }

    // 2. Kill switch state machine: pausa entradas, não fecha posições
    let dd = current_drawdown();
    if !killed && dd >= kill_switch_dd { killed = true; }
    if killed && dd <= kill_switch_dd * 0.5 { killed = false; }
    if killed { continue; }

    // 3. Compute regime
    let regime = match regime_arrays.at(i) {
        Some(r) => r,
        None => continue,    // candle sem regime → não abre, mas posições existentes seguem
    };

    // 4. Score por preset
    let scores: Vec<f64> = fingerprints.iter()
        .map(|fp| regime_match_score(&regime, fp))
        .collect();

    // 5. Pré-filtro por threshold
    let mut candidates: Vec<(usize, f64)> = scores.iter().enumerate()
        .filter(|(_, s)| **s >= activate_threshold)
        .map(|(i, s)| (i, if *s >= full_position_threshold { 1.0 } else { 0.5 }))
        .collect();

    // 6. Filtro por correlação (guloso por score desc)
    candidates = filter_by_correlation(candidates, &correlations, max_correlation);

    // 7. Cap total pos_size: se sum(pos_size_i * mult_i) > max, escala proporcionalmente
    candidates = cap_total_size(candidates, max_total_pos_size, &presets);

    // 8. Tenta abrir novas posições nos candidatos
    for (idx, mult) in candidates {
        if let Some(entry) = preset_runners[idx].signal_at(i) {
            execute_with_size(entry, presets[idx].pos_size * mult);
        }
    }
}
```

**Critical**: kill switch **pausa entradas mas não fecha posições**. Histericamente ressuscita quando DD volta pra 50% do limit. Isso é backtested, não bolted-on.

### Real fee model

Cada preset paga taxa **independentemente** (não somar net pnl). Composite final = soma dos returns por preset, com cada um descontando fee individual.

Já é o comportamento natural se cada `preset_runner` calcula seu próprio pnl com fee. Só validar que está implementado assim, não shortcut.

### Correlação entre presets

Carrega de `preset_correlation` (computado no M0):

```rust
fn filter_by_correlation(active: Vec<(usize, f64)>, corrs: &Matrix, max: f64) -> Vec<(usize, f64)> {
    // Algoritmo guloso: ordena por score desc, mantém preset i só se corr<max com TODOS ativos já decididos
    let mut sorted = active.clone();
    sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let mut kept = Vec::new();
    for (i, mult) in sorted {
        if kept.iter().all(|(j, _)| corrs[i][*j] < max) {
            kept.push((i, mult));
        }
    }
    kept
}
```

## Parte B — Calibração de thresholds (grid + holdout)

Os 4 params do composite (activate, full, max_total_pos, kill_switch) precisam ser calibrados sem overfit.

### Grid

| Param | Valores |
|---|---|
| activate_threshold | 0.30, 0.40, 0.50 |
| full_position_threshold | 0.55, 0.65, 0.75 |
| max_total_pos_size | 0.30, 0.50, 0.70 |
| kill_switch_dd | 0.03, 0.05, 0.10 |

Total: 81 combos.

### Validação

- Roda 81 combos em **train half** (primeiros 50% do histórico)
- Métrica de seleção: **Sharpe mensal** = `mean(monthly_returns) / std(monthly_returns) * sqrt(12)`
  (Sharpe anualizado; per-trade Sharpe é volátil; per-day excessivo)
- Pega top 5 por Sharpe mensal no train
- Re-roda top 5 em **test half** (últimos 50%)
- Champion = melhor no test (não no train)
- Adicional: descartar combo se Sharpe_test < 0.5 × Sharpe_train (overfit gritante)

Garante que os thresholds escolhidos generalizam.

## Parte C — Validação contra os 3 gates

Composite tratado como meta-strategy. Roda:

1. **param_sensitivity** sobre os 4 params calibrados:
   - Perturba cada um pros vizinhos no grid
   - Mediana dos retornos vizinhos / champion ≥ 0.70
2. **is_oos**: split 70/30 do histórico, ratio ≥ 0.50
3. **walkforward**: 6+ janelas mensais, ≥70% positivas

Composite tem que passar nos 3. Se não passar, não vai pra produção.

## Parte D — Bot Python integrado

### Estrutura

```
trader/
├── regime/
│   ├── __init__.py
│   ├── features.py     # porta os 4 indicadores
│   ├── score.py        # porta scoring
│   └── fingerprint.py  # carrega de DB
├── bot_composite.py    # orquestra
└── ...
```

### Workflow do bot

A cada candle 15m fechado (via WebSocket Binance):
1. Append candle ao buffer (manter últimos 200)
2. Compute current regime vector
3. Pra cada preset ativo no DB (`presets WHERE status='active'`):
   - Carrega fingerprint (cache em RAM, refresh a cada 1h)
   - Computa score
4. Decide ativações (mesmo algoritmo do Rust composite)
5. Pra cada preset ativado: avalia entry signal, executa via Binance API

### Posição-aware

Se preset tem posição aberta e score cai abaixo do threshold:
- **Mantém posição** até exit normal (não fecha por mudança de regime)
- Não abre novas posições enquanto score baixo

Justificativa: regime score muda lento, não muda decisões já tomadas.

## Parte E — Golden vectors (Rust ↔ Python parity)

Pra evitar divergência entre engine de backtest (Rust) e bot live (Python):

### Geração

Subcomando Rust: `backtest golden-vectors --symbol ETHUSDT --timeframe 15m --output goldens.json`

Output: pra cada candle de uma fixture, salva:
- Timestamp
- Regime vector (4 valores)
- Score pra cada preset ativo
- Decisão (active/inactive, pos_size_mult)

### CI Python

Test em `trader/tests/test_regime_parity.py`:
1. Carrega `goldens.json` + mesma fixture de candles
2. Roda Python pipeline em modo **candle-fechado** (mesmo que o bot live: só processa candle quando totalmente fechado)
3. Compara cada output com tolerância:
   - Features: diff < 1e-6
   - Score: diff < 1e-4
   - Decisão (active/inactive): match exato

Falha o CI se divergir. Sem isso, bot Python pode tomar decisões diferentes do backtest.

**Importante**: golden vectors são gerados em modo **bar-close** (replay do backtest). Bot live também só decide em bar-close (não intra-bar). Estados intra-candle (real-time price ticks dentro do candle 15m corrente) **não influenciam** decisão — só o close oficial do candle anterior. Se isso mudar (e.g. bot for pra TF mais baixa com lookahead intra-bar), goldens precisam regenerar.

## Risk policy (parâmetro, não bolted-on)

Kill switch é param calibrado, não hard-coded:
- `kill_switch_dd`: 0.03, 0.05, 0.10 (no grid de calibração)
- Se DD da conta passa esse valor, composite **desativa todos presets** até DD voltar pra <50% do limit
- Comportamento backtested, não heurístico

## Tests

- Composite backtest roda em <2min pra 1 ano de candles 15m
- Com 1 preset ativo: composite reproduz exato o backtest do preset (sanity check)
- Com 2 presets independentes: composite return = soma dos returns individuais (quando ambos ativos)
- Calibração: top 5 do train batem train pelo menos por margem mínima

## Cronograma do M4

| Sub-task | Esforço | Critério |
|---|---|---|
| 4a. Composite engine Rust | 5-6h | subcomando funciona |
| 4b. Calibração de thresholds | 4-5h | 81 combos rodados (~3h compute) + análise + holdout |
| 4c. Validação 3 gates | 1-2h | passa ou diagnóstico claro |
| 4d. Bot Python | 5-6h | decisão idêntica ao Rust no fixture |
| 4e. Golden vectors + CI | 2-3h | test passa |
| **Total M4** | **~17-22h** | |

(Atualiza estimativa em SKILL.md se passar de 18h.)

## Critério de "M4 done" (alinhado com SKILL.md)

- Composite passa nos 3 gates como meta-strategy
- Composite **Pareto-domina as 4 baselines** em ≥3 das 5 métricas (return, Sharpe, Calmar, max DD, recovery time)
- Composite render absoluto ≥ Best Single Preset + 5%
- Correlação entre presets ativos no composite ≤0.7
- Bot Python: golden vectors em CI (decisão match exato, score diff <1e-4, features diff <1e-6)
- Documento `composite_results.md` com tabela completa

## O que NÃO é M4

- Deploy real (próximo passo, não dentro dessa skill)
- Adicionar features além das 4
- Features ML (random forest etc.)
