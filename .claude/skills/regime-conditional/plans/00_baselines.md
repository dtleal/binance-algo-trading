# Milestone 0 — Portfolio de presets + baselines

**Output**: ≥3 presets validados em DB + arquivo `baselines.md` com métricas que o composite precisa bater.

## Por que existe

**Premissa**: regime detection sobre 1 preset rende ~10% no melhor caso. Pra hit 25%, precisa de **portfolio descorrelacionado**.

**Sem M0, M1-M4 viram exercício acadêmico.** O composite só tem espaço pra brilhar se há presets a alocar entre regimes.

## Sub-tarefa 0a — Descobrir mais presets

### Plano de varredura

Rodar `onboard` em todas as combinações que ainda não testamos:

| Strategy | TF a testar | Símbolos |
|---|---|---|
| ema_scalp | 5m, 15m, 30m | BTC, ETH, SOL, BNB, XRP |
| vwap_pullback | 30m, 1h | ETH, SOL, BNB |
| momentum | 30m, 1h, 4h | BTC, ETH, SOL |
| pdhl | 30m, 1h, 4h | BTC, ETH, SOL, BNB, ADA, XRP |
| range | 30m, 1h | SOL, BNB, ADA, XRP, AVAX |

Total: ~35 onboards. Cada um ~5-30s pra strategies de grid pequeno (orb/pdhl/ema_scalp/range), 5-10min pra grid grande (vwap, momentum). Estimativa total: 2-3h compute.

### Critério de "preset confirmado"

Já definido pelo `onboard` pipeline:
- param_sensitivity: PASS (ratio ≥0.70)
- is_oos: PASS (ratio ≥0.50, OOS trades ≥30)
- walkforward: PASS (≥70% janelas positivas)

Cada preset que passa vira row em `presets` com `status='active'`.

### Meta numérica

Após M0a: **≥3 presets** ativos em `presets` table.

Se conseguir <3: revisar premissa. Talvez o mercado atual (pós-2026-01) é hostil pra todas strategies do toolkit. Aí o caminho é esperar mais dados ou criar strategy nova (fora de escopo dessa skill).

## Sub-tarefa 0b — Correlação entre presets

### Computar matriz de returns por janela

Pra cada preset, gerar **série temporal de returns mensais** (replay do histórico):

```sql
-- Pseudo-schema (gerado por subcomando 'correlate')
CREATE TABLE preset_monthly_returns (
    preset_id BIGINT NOT NULL,
    month     DATE NOT NULL,
    return_pct NUMERIC(10,4),
    n_trades  INT,
    PRIMARY KEY (preset_id, month)
);
```

Subcomando: `backtest correlate --preset-ids 1,2,3,4`.

**Método explícito**: correlação **Pearson** sobre série de **returns mensais** de cada preset. Mensal porque:
- Daily é ruidoso demais (muito 0% em dias sem trade)
- Anual tem só 1 ponto, não dá pra correlacionar
- Mensal balanceia ruído vs amostras (12 pontos/ano por preset)

Se preset rodou <12 meses no histórico, fallback pra correlação **Spearman** (rank-based, robusto pra amostra pequena).

### Critério aceito (alinhado com SKILL.md)

- **Pelo menos 1 par com correlação <0.5**: portfolio bem descorrelacionado, vale composite.
- **Todos pares ≤0.7**: aceitável, composite ainda agrega valor.
- **Algum par >0.7 mas ≤0.8**: tolerável; composite faz risk management mais que diversificação.
- **Algum par >0.8**: aborta — esse par "vê" o mesmo regime, escolher presets diferentes.

## Sub-tarefa 0c — Definir baselines

Composite só vai pra produção se bater **TODAS** estas baselines.

### Baseline 1: Best single preset always-on

```
return_baseline_1 = max(preset.return_pct for preset in active_presets)
```

### Baseline 2: Equal-weight portfolio (sem regime detection)

Aloca capital igualmente entre N presets, cada um com `pos_size = pos_size_original / N`.

Se baseline_2 já dá 25%+ sozinho, **regime detector é over-engineering**. Deploy equal-weight e encerra a skill.

### Baseline 3: Random regime gate (sanity check)

Pra cada preset, **aleatoriamente** ativa/desativa com prob=0.5. Replay 100 vezes, computa return médio + std.

Se composite render igual ou pior que random + 1σ → regime detector é ruído.

### Baseline 4: Naive ADX gate (heurística simples)

- PDHL/momentum/orb/pdhl ativa só quando ADX>20
- range ativa só quando ADX<20
- Sem outras features

Composite com 4 features tem que justificar complexidade vs essa heurística simples.

### Critério de "vencer baselines" (consistente com SKILL.md)

Composite domina **Pareto** as 4 baselines em pelo menos 3 das 5 métricas:
- Return anual
- Sharpe (períodos mensais)
- Calmar (return/max_dd)
- Max drawdown
- Recovery time (dias até voltar pro peak)

Adicionalmente: **return absoluto ≥ Baseline 1 + 5%**.

### Tabela final do M0

Antes de M1, gerar `baselines.md` no repo:

| Baseline | Return anual | Sharpe | Calmar | Max DD | Recovery |
|---|---|---|---|---|---|
| Best single preset | ? | ? | ? | ? | ? |
| Equal-weight portfolio | ? | ? | ? | ? | ? |
| Random gate (n=100 mean ± σ) | ? | ? | ? | ? | ? |
| Naive ADX gate | ? | ? | ? | ? | ? |

Composite deve Pareto-dominar essa tabela em ≥3 métricas, E render ≥(Best single + 5%).

## Critério de "M0 done"

- ≥3 presets em `presets` (sub-tarefa 0a)
- Matriz de correlação computada e documentada (0b)
- 4 baselines com números concretos (0c)
- Decision gate: prosseguir pra M1 OU declarar que regime detector não vale a pena com presets atuais

## O que NÃO é M0

- Não construir features ainda (M1)
- Não construir fingerprint (M2)
- Não escrever código novo no Rust crate além do subcomando `correlate`
