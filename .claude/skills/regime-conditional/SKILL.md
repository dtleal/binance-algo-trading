---
name: regime-conditional
description: Sistema de seleção condicional de strategies baseado em regime de mercado — bot que ativa a strategy certa pra cada fase, em vez de operar uma única strategy o tempo todo
user_invocable: true
---

# /regime-conditional — Strategy switching condicionado a regime de mercado

## Premissa quantitativa (LEIA ANTES DE TUDO)

**Regime detection sozinho NÃO leva 1 preset de 8% pra 25%.** Math do PDHL ETH 15m:

| Cenário | Trades | Avg expectancy | Return anual (pos=0.10) |
|---|---|---|---|
| Preset always-on (atual) | 768 | 0.10% | ~8% |
| Skip 25% piores trades (regime gate ideal) | 576 | 0.13% | ~9.5% |
| Skip 50% piores | 384 | 0.15% | ~7% (perde demais trades) |

Slippage savings com gate ideal: ~0.5% extra. Plafond realista: **9-11%**, não 25%.

**A skill só vale a pena com ≥3 presets em portfolio com correlação aceitável.** Antes de implementar M1, precisa cumprir M0:
- ≥3 presets confirmados (passam os 3 gates), idealmente em strategies/símbolos diferentes
- Correlação par-a-par entre returns dos presets ≤ **0.7** (≥1 par <0.5 = ideal; todos >0.8 = aborta)

Caso contrário, o composite performa marginalmente melhor que best-single-preset, e o esforço não compensa.

## Problema central

Nenhuma strategy individual sobrevive a 3 anos de IS+OOS+walkforward com >20% retorno. Isso é estrutural — cada strategy só tem edge em **subconjunto de regimes**. Operar PDHL em range = fakeouts. Operar range em trend forte = stops.

Solução: **camada de meta-decisão acima das strategies** que ativa a certa pra cada regime.

## A pergunta operacional central

> "O mercado está em regime X — qual subset de strategies tem edge histórica em X?"

Resposta requer:
1. **Vetor de regime** (multi-dimensional, não binário trend/range)
2. **Fingerprint empírico** de cada strategy (em quais regimes ganhou, com confiança estatística)
3. **Score de match** (calibrado, não chutado)
4. **Composite backtest** que prove superioridade vs baselines

## Princípios não-negociáveis

Vêm do `CLAUDE.md` e da skill `/binance-refac`:

- **Fail fast** se feature não puder ser computada (insufficient candles, NaN, etc.). Não inferir defaults.
- **Sem fallback silencioso**: regime indefinido = strategy desativada (não opera com regime "default").
- **Data-driven com guarda de overfit**: thresholds vêm dos dados, mas validados em fingerprint-OOS antes de adotar.
- **Composite passa pelos 3 gates** tratado como meta-strategy (param_sensitivity, is_oos, walkforward) — sem exceção.
- **Tem que bater baselines explícitos** (ver `plans/00_baselines.md`). Passar gates sem bater baselines = falha.

## Anti-patterns proibidos

- Não criar features só pra "parecer ML". Cada feature precisa ter justificativa econômica + ortogonalidade testada.
- **Máximo 4 features.** Curse of dimensionality com nossos ~768 trades/preset.
- Não usar regime score pra GERAR sinal de entrada. Só ATIVA/DESATIVA strategies existentes.
- Não usar magic numbers (`scale=50`, threshold 0.4/0.6) sem calibração via grid + holdout.
- Não fazer ML opaco (random forest, NN) na primeira versão. Bucketing interpretável + filtro por bucket ruim.
- **Não persistir regime score em `sweep_results`** (não é métrica de combo). Tabela separada.
- **Não rodar M1-M4 antes de M0** (3+ presets). Sem portfolio, composite é overhead.

## Estrutura dos milestones

```
plans/
├── 00_baselines.md     ← M0: descobrir mais presets + definir o que composite precisa bater
├── 01_features.md      ← M1: 4 features ortogonais + collinearity check
├── 02_fingerprint.md   ← M2: fingerprint-IS/OOS validation, Bonferroni, TTL
├── 03_score.md         ← M3: scoring calibrado, confidence-aware
├── 04_composite.md     ← M4: engine + bot + correlação entre presets
└── 05_open_questions.md ← Lista de decisões pendentes / unknowns
```

## Estimativa total revisada

| Milestone | Esforço | Pré-requisito |
|---|---|---|
| M0 — Baselines + portfolio | 1-2 dias (sweeps + análise) | nenhum |
| M1 — Features (4) | 4-6h | M0 done |
| M2 — Fingerprint + validação OOS | 8-10h | M1 done |
| M3 — Score calibrado | 4-6h | M2 done |
| M4 — Composite + bot Python | 17-22h | M3 done |
| **Total** | **~5-6 dias** | |

(Estimativa anterior de 3 dias era otimista — não incluía M0 nem validações OOS.)

**Realismo**: software costuma demorar 1.5-2x estimado. Reservar **~10 dias úteis** pra execução completa. Se M0 demorar mais (poucos presets passam gates), pode dobrar.

## Critério de "skill done"

Composite tem que ter **TODAS** as condições simultaneamente:

1. Passa nos 3 gates (param_sensitivity ≥0.70, is_oos ≥0.50, walkforward ≥70% janelas positivas) tratado como meta-strategy
2. **Pareto-domina** as 4 baselines de `00_baselines.md` (vence em ≥3 das 5 métricas: return, Sharpe, Calmar, max DD, recovery time)
3. Bate o **best single preset** em retorno absoluto por ≥5%
4. Correlação entre presets ativos ≤0.7

Antes disso: não vai pra produção. Documentar em `composite_results.md`.
