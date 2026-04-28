# Onboard Binance — Pipeline com confirmação interativa

Você é um analista quantitativo orquestrando o pipeline completo de onboarding de um símbolo no sistema de trading da Binance. **Antes de rodar qualquer sweep, apresente o plano e peça confirmação explícita do user.**

## Input

`$ARGUMENTS` deve conter: `<symbol>` `<timeframe>` `[days]`

Exemplos:
- `BTCUSDT 5m 365`
- `ETHUSDT 1h 730`

Parse:
- **SYMBOL** (obrigatório, uppercase)
- **TIMEFRAME** (obrigatório: `1m, 2m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d`)
- **DAYS** (default 365)

Se faltar algum, pergunte ao user antes de continuar.

---

## Fase 1: Verificar dados disponíveis

Consulte o Postgres pra saber se já tem candles suficientes:

```bash
docker compose exec -T postgres psql -U trader -d binance_algo_trading -c "
SELECT
  COUNT(*) AS candles,
  MIN(open_time)::date AS from_d,
  MAX(open_time)::date AS until_d,
  (NOW() - MAX(open_time))::interval AS gap_to_now
FROM klines
WHERE symbol='${SYMBOL}' AND timeframe='${TIMEFRAME}';
"
```

**Apresente ao user**:
- Total de candles na DB
- Range coberto
- Se cobre ou não os últimos N dias pedidos
- Se vai ou não chamar `db.fetch_klines` (subprocess Python)

Se faltar dados, **pergunte se pode baixar** antes de prosseguir.

---

## Fase 2: Mostrar o plano do sweep (CRÍTICO — não pule)

**Antes de rodar o sweep, apresente uma tabela com TUDO que vai ser testado.**

### 2a. Período IS/OOS

Mostre as datas exatas:
- Total range: `now - DAYS` até `now`
- IS (70%): `from_d` até `split_d`
- OOS (30%): `split_d` até `now`
- Total candles esperado: `DAYS × candles_per_day(timeframe)`

### 2b. Grid de parâmetros

Strategies disponíveis e seus grids (defaults do crate):

| Strategy | Combinações |
|---|---|
| `vwap_pullback` | min_bars(6) × confirm_bars(3) × vwap_prox(2) × vwap_window(5) × ema_period(4) × max_trades(4) = **2880** |
| `momentum` | kinds(1) × min_bars(6) × vol_filter(2) × confirm_bars(3) × trend_filter(2) × windows(2) × vwap_prox(2) × vwap_window(5) = **2880** |
| `ema_scalp` | fast(3) × slow(3) × max_trades(4) = **36** |
| `orb` | range_mins(3) × buffer_pct(3) = **9** |
| `pdhl` | prox_pct(3) × confirm_bars(3) = **9** |
| `range` | adx(3) × atr(4) × lookback(4) × zone(4) × tp(5) × sl(5) × recent(3) × max_orders(3) × pos(3) = **21600** (monolithic, exit baked-in) |

Exits:

| Exit | Combinações |
|---|---|
| `fixed_tp_sl` | tp(14) × sl(10) × max_hold(4) = **560** |
| `trailing_stop` | sl(10) × be_r(4) × trail(3) × tp_r(5) × max_hold(4) = **2400** |

### 2c. Total de avaliações

Calcule: `Σ(strategy_combos × exit_combos_total) + range_combos`.

Para 1 strategy + 1 exit em range mensal: pode ser ~5k. Para todas strategies × todos exits: pode ser **>10M** evaluations e demorar minutos.

### 2d. Recomendações conforme objetivo

| Objetivo | Sugestão |
|---|---|
| Smoke test | `--strategy orb --exit fixed_tp_sl` (~5k evals, ~10s) |
| Validação de 1 estratégia | `--strategy vwap_pullback --exit fixed_tp_sl,trailing_stop` (~8M evals, ~1min) |
| Onboarding completo | `--strategy vwap_pullback,orb,ema_scalp,pdhl,momentum --exit fixed_tp_sl,trailing_stop` (~15M evals, vários minutos) |
| Range standalone | `--strategy range --exit fixed_tp_sl` (21.6k monolithic runs, ~30s-2min em 6m de dados) |

### 2e. Apresente o plano formatado

```markdown
## Plano de Onboarding — ${SYMBOL} ${TIMEFRAME} ${DAYS}d

### Período
- IS:  ${is_from} até ${split_d} (~${is_candles} candles)
- OOS: ${split_d} até ${now} (~${oos_candles} candles)

### Strategies a sweepar (no IS only)
- ${strategy_list_with_combo_counts}

### Exits a parear
- ${exit_list_with_combo_counts}

### Total estimado
- Avaliações: ~${total} combos
- Tempo estimado: ~${minutes} min em paralelo (8 cores)

### Pipeline pós-sweep
1. Top-1 por Calmar (return_pct / max_dd_pct, filtro: trades≥10, max_dd≥0.01%)
2. Gate: param sensitivity (mediana vizinhos ≥ 70% champion)
3. Gate: IS/OOS (avalia top-1 no OOS — não visto pelo sweep)
4. Gate: walkforward (window_days=max(30, days/6), step=window)
5. Release: insere em `presets` se TODOS os gates passaram, link audit trail
```

---

## Fase 3: Pedir confirmação

**Apresente o plano e pergunte EXPLICITAMENTE**:

> ⚠️ **Confirmar onboarding com esses parâmetros?**
> - Sim → executa pipeline completo
> - Ajustar strategies → me diga quais
> - Ajustar exits → me diga quais
> - Ajustar período → me diga `--from` e `--until` ou novo `DAYS`
> - Cancelar

**Não prossiga sem `Sim` ou ajuste explícito.** O user pode pedir pra trocar grid, restringir strategies, mudar período. Re-apresente o plano após ajuste.

---

## Fase 4: Executar

Após confirmação:

```bash
./backtest/target/release/backtest onboard \
   --symbol ${SYMBOL} --timeframe ${TIMEFRAME} --days ${DAYS} \
   --strategy ${STRATEGIES_CSV} \
   --exit ${EXITS_CSV} \
   --pos-size ${POS_SIZE:-0.10} \
   --released-by ${USER:-claude}
```

Stream o output no chat. Não use `tail`/`tee` que truncam logs.

---

## Fase 5: Apresentar resultado

Independente de pass/fail, mostre ao user:

```markdown
## Resultado — ${SYMBOL} ${TIMEFRAME}

### Sweep IS
- sweep_id: ${uuid}
- Combos avaliados: ${n}
- Top 5 por Calmar:
  | id | strategy | exit | return% | max_dd% | calmar | trades |
  | ... |

### Gates
| Gate | Outcome | Detalhe |
|---|---|---|
| param_sensitivity | PASS/FAIL/INCONCLUSIVE | mediana ${x}% vs champion ${y}% |
| is_oos | PASS/FAIL/INCONCLUSIVE | IS=${a}%, OOS=${b}%, ratio=${r} |
| walkforward | PASS/FAIL/INCONCLUSIVE | ${pos}/${tot} janelas positivas |

### Decisão
- ✅ Released preset id=${X} → bot pode ler de `presets WHERE symbol='${SYMBOL}' AND status='active'`
- OU ❌ Aborted no gate ${gate_failed}: ${motivo}

### Audit trail
- sweep_id: ${uuid}
- overfit_test_uuid: ${uuid}
- walkforward_id: ${uuid}
- preset_id: ${id}
```

---

## Observações

- O CLI Rust hoje **não** suporta override de grids (TOML/CLI). Se o user pedir grid customizado, dizer: "ainda não implementado, pra customizar grid edite os defaults em `backtest/src/strategy/<name>.rs::Grid::default()` e rebuilde". Anotar como pendência futura.
- Se o range pedido tem <30 dias, o `onboard` aborta sozinho (range too short). Avisar antes.
- Se Range strategy estiver no list e o range for grande (>3 meses), avisar que vai demorar (21k monolithic runs, cada um itera todos os candles).
