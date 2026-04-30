# Status — /regime-conditional

**Última atualização**: 2026-04-30
**Branch**: main (commits 2f963cb, 25ab073, 2143e62, fceea7a, e6725f5, c8bfbc6, f4cf43f, 5de08ca, 5f47666, 681cb17)

## Onde estamos

| Milestone | Status | Próximo passo se retomar |
|---|---|---|
| M0a — descobrir presets | ✅ **DONE** | — |
| M0b — correlation matrix | ✅ **DONE** | — |
| M0c — 4 baselines | ⚠ **PARCIAL** | Implementar random-gate + naive-ADX gate baselines |
| M1 — 4 features | ✅ **DONE** | — |
| M2 — fingerprint | ✅ **DONE** | — |
| M3 — score function | ✅ **DONE** | — |
| M4a — composite engine Rust | ✅ **DONE** | — |
| M4b — calibração grid 81 combos | ⏸ **PENDENTE** | Decisão prévia: vale fazer? (regime gate hurt PDHL) |
| M4c — validar composite nos 3 gates | ⏸ **PENDENTE** | Depende de M4b ter resultado positivo |
| M4d — bot Python `bot_pdhl.py` | ⏸ **PENDENTE** | Caminho mais rápido pra produção |
| M4e — golden vectors CI | ⏸ **PENDENTE** | Depende de M4d |

## Presets ativos (deployable)

```sql
SELECT id, symbol, timeframe, strategy, return_pct::float, max_dd_pct::float, trades, win_rate::float
FROM presets WHERE status='active' ORDER BY id;
```

| id | symbol  | tf  | strategy | return IS | max DD | trades | win_rate | exit |
|----|---------|-----|----------|-----------|--------|--------|----------|------|
| 1  | ETHUSDT | 15m | pdhl     | +5.13%    | 0.13%  | 523    | 51.2%    | trailing_stop sl=0.001 be_r=1.0 trail=0.25 tp_r=0 |
| 2  | BTCUSDT | 1h  | pdhl     | +4.27%    | 0.10%  | 446    | 50.7%    | trailing_stop |
| 3  | ETHUSDT | 30m | pdhl     | +7.01%    | 0.07%  | 374    | 63.6%    | trailing_stop |

## Resultados-chave

### Correlation matrix (M0b)
```
         [1] ETH 15m  [2] BTC 1h  [3] ETH 30m
[1]      1.000        0.733       0.297
[2]      0.733        1.000       0.257
[3]      0.297        0.257       1.000
```
- Min |corr|: 0.257 → **portfolio descorrelacionado, vale composite**
- Max |corr|: 0.733 (ETH 15m × BTC 1h, limítrofe — esperado pra crypto)

### Composite backtest (M4a) — sem regime gate (always-on)
Sum dos returns dos 3 presets independentes: **+16.42%** anual, max DD 0.13%, 1342 trades.
- preset 1: +5.13% (DD 0.13%)
- preset 2: +4.27% (DD 0.10%)
- preset 3: +7.01% (DD 0.07%)

### Composite com regime gate
| Thresholds (activate/full) | Sum return | Max DD | Trades |
|---|---|---|---|
| 0.0/0.0 (always-on) | **+16.42%** | 0.13% | 1342 |
| 0.40/0.65 (default) | +13.36% | 0.08% | 1148 |
| 0.50/0.75 | +10.19% | 0.06% | 946 |

**Regime gate REDUZ retorno** pra portfolio PDHL. Detector funciona mas PDHL é regime-stable (9-11 de 12 buckets úteis), então gating filtra trades bons junto com marginais.

### Fingerprints (M2) — buckets úteis por preset
- Preset 1 (PDHL ETH 15m): 9/12 (atr_pct low, rsi mid, bb low — únicos não-úteis)
- Preset 2 (PDHL BTC 1h): 9/12
- Preset 3 (PDHL ETH 30m): 11/12 (mais regime-stable ainda)

## Insights e decisões implícitas

1. **PDHL não é boa candidata pra regime gating**. Buckets úteis demais — score discriminação fraca. Adicionar strategies regime-instáveis (range, vwap_pullback) ao portfolio daria mais signal ao detector.

2. **Equal-weight always-on é a estratégia operacional candidata**. Sem regime gate, com 3 presets descorrelacionados PDHL: ~16% anual, DD 0.13%. Com pos_size escalado pra 0.15: ~24% anual, DD ~0.20%. Hit do alvo de 25% via diversificação + leverage moderada.

3. **Premissa do SKILL.md confirmada**: regime detection sobre 1 preset NÃO ajuda; só com portfolio descorrelacionado faz sentido. E mesmo aí, depende da diversidade de strategies.

4. **3 features (não 7)** foi a decisão certa — fingerprints estatisticamente significativos com Bonferroni-bootstrap.

5. **Bug encontrado durante execução**: unique constraint de `presets` era `(symbol, strategy)` — fixei pra `(symbol, strategy, timeframe)` na migration 015. Sem isso, presets de mesma strategy em TFs diferentes se atropelavam.

6. **Filtro de pathological rows** em `db.rs::write_sweep_results` (commit c8bfbc6): rows com `trades=0` causavam edge cases em metrics que violavam check constraint. Filtro simples resolve.

## Code shipped nessa sessão

| Commit | Mudança |
|---|---|
| 2f963cb | docs(skill): plano /regime-conditional (7 arquivos, 1217 linhas) |
| 25ab073 | feat(db): typed columns refactor — drop strategy_params/exit_params jsonb, ~35 cols tipados |
| 2143e62 | revert(range): drop visual/geometric experiments, keep MQL5 alignment |
| c8bfbc6 | fix(db): skip pathological rows (trades=0 com strings vazias) |
| fceea7a | feat: M0b — correlate subcommand |
| e6725f5 | feat: M1 — 4 regime features (rsi.rs, bb_squeeze.rs, regime/) |
| f4cf43f | feat: M2 — fingerprint subcommand + migration 014 |
| 5de08ca | feat: M3 — score function |
| 5f47666 | feat: M4a — composite engine V1 |
| 681cb17 | fix: presets unique constraint inclui timeframe (migration 015) |

## Como retomar

### Opção A — Caminho rápido pra produção (recomendado)
1. Implementar `trader/bot_pdhl.py` Python lendo de `presets WHERE status='active'`
2. Dry-run multi-preset por 1-2 semanas
3. Deploy real com pos_size=0.10 (~16% expected)
4. Pular M4b/M4c (regime gate não vale pra PDHL atual)
5. Documentar decisão em `composite_results.md`

### Opção B — Completar M0c + M4 formalmente
1. Implementar baseline 3 (random regime gate, 100 replicas) e baseline 4 (naive ADX gate)
2. Rodar M4b grid de 81 combos calibrando (activate, full, max_pos, kill_switch)
3. Validar composite nos 3 gates (M4c)
4. Decidir baseado nos números: composite vai ou não pra produção

### Opção C — Expandir portfolio (longo prazo)
1. Adicionar strategy regime-instável ao mix (range, vwap_pullback)
2. Rodar onboard procurando presets dessas strategies em ETH/BTC
3. Voltar ao composite — agora com strategies de regime-favorability variável,
   o detector teria mais signal pra trabalhar
4. Aí sim faz sentido M4b (calibrar) e M4c (validar)

## Comandos úteis pra próxima sessão

```bash
# Confirmar presets ativos
docker compose exec postgres psql -U trader -d binance_algo_trading -c \
  "SELECT id, symbol, timeframe, strategy FROM presets WHERE status='active'"

# Re-rodar correlate
./backtest/target/release/backtest correlate --preset-ids 1,2,3

# Re-rodar composite always-on baseline
./backtest/target/release/backtest composite-backtest \
  --preset-ids 1,2,3 --activate-threshold 0 --full-position-threshold 0

# Re-rodar fingerprint pra qualquer preset
./backtest/target/release/backtest fingerprint --preset-id 2

# Build + tests
cd backtest && cargo build --release && cargo test --release --lib regime
```

## Pendências menores

- [ ] `composite_results.md` final com tabela completa (Pareto, métricas, decisão)
- [ ] Doc inline em algum dos plans atualizando o que foi descoberto durante execução (M4 não vale pra PDHL puro)
- [ ] Performance benchmark do `RegimeArrays::compute` (target <50ms para 35k candles — mencionado em 01_features.md, nunca medido)
