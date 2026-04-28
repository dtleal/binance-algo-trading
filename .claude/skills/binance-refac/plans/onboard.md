# Plano — Onboard orquestrador

Status: **planejado**, decisões finais tomadas. Implementação opcional (não destrava nada novo, só orquestra steps existentes).

## Quando faz sentido implementar

- ✅ Vai rodar em CI/cron pra muitos símbolos.
- ✅ Quer eliminar erro humano na ordem dos passos.
- ❌ Apenas vai rodar manualmente algumas vezes — preferir comandos isolados.

## Decisões finais (resolveram problemas estatísticos do plano original)

### 1. Ranking dos candidatos: **Calmar** (`return_pct / max_dd_pct`)
- Penaliza drawdown alto.
- Champion com `return=10%, dd=2%` (Calmar=5) > champion com `return=15%, dd=8%` (Calmar=1.9).
- Implementação: `ORDER BY return_pct / NULLIF(max_dd_pct, 0) DESC NULLS LAST LIMIT 1`.

### 2. Quantos candidatos testar: **top-1 só**
- Evita viés de múltipla comparação ("se eu testar 100, algum passa por sorte").
- Se top-1 falhar nos gates → `onboard` retorna erro, user revisa grade do sweep manualmente.
- Sem flag `--allow-fail` — release inseguro deveria ser explicitamente manual via `make release` sem gates.

### 3. Sweep + IS/OOS: **sweep roda só no IS (70%)**
- IS/OOS só faz sentido se o sweep não viu o OOS.
- `onboard` faz: split range em IS (primeiros 70%) e OOS (últimos 30%) → sweep no IS → top-1 → testa no OOS.
- Custo: 1 sweep só (não 2). O OOS é avaliado isoladamente via `evaluate_combo`.
- Walkforward: roda no range completo (IS + OOS) — o gate dele é independente do IS/OOS.

### 4. Window_days adaptativo: `max(30, total_days / 6)`
- Garante pelo menos 6 janelas no walkforward (denominador estatístico mínimo razoável).
- 365 dias → 60d window, 6 janelas.
- 90 dias → 30d window, 3 janelas (provavelmente inconclusive — correto).
- 30 dias → 30d window, 1 janela (inconclusive correto).
- `step_days = window_days` (sem overlap, simplicidade).

## Algoritmo

```rust
fn run_onboard(input: OnboardInput) -> Result<OnboardOutput> {
    // 1. Verifica klines no DB
    let coverage = db::query_klines_coverage(&input.symbol, input.timeframe)?;
    let needed_from = today() - Duration::days(input.days);
    if coverage.min > needed_from || coverage.max < today() {
        // Falta dado: dispara subprocess Python
        run_python_fetch_klines(&input.symbol, input.timeframe, input.days)?;
    }

    // 2. Determina IS/OOS split (70/30) e datas dos sweeps
    let (is_from, is_until, oos_from, oos_until) = split_70_30(needed_from, today());

    // 3. Sweep no IS only
    let sweep_id = run_sweep_in_range(&input, is_from, is_until)?;

    // 4. Top-1 by Calmar
    let top = db::top_calmar(&sweep_id, 1)?
        .first()
        .ok_or_else(|| anyhow!("sweep produced no profitable candidates"))?;

    // 5. Anti-overfit: param sensitivity (vizinhos do mesmo sweep_id)
    let test_uuid = Uuid::new_v4();
    let sens = overfit::check_param_sensitivity(...)?;
    if sens.outcome != Pass {
        bail!("param sensitivity {} — onboard aborted", sens.outcome.as_str());
    }

    // 6. IS/OOS: avalia top-1 no OOS slice (NUNCA visto pelo sweep)
    let oos = overfit::check_is_oos_with_explicit_split(top, is_slice, oos_slice)?;
    if oos.outcome != Pass {
        bail!("IS/OOS {} — onboard aborted", oos.outcome.as_str());
    }

    // 7. Walkforward no range completo
    let window_days = max(30, input.days / 6);
    let wf_id = walkforward::run(top, full_range, window_days, window_days)?;
    if wf_id.outcome != Pass {
        bail!("walkforward {} — onboard aborted", wf_id.outcome.as_str());
    }

    // 8. Release com gates linkados
    let preset_id = release::release(top.id, Some(test_uuid), Some(wf_id))?;
    Ok(OnboardOutput { preset_id, sweep_id, test_uuid, walkforward_id: wf_id })
}
```

## Mudanças necessárias em código existente

### `db.rs`
- `query_klines_coverage(symbol, timeframe) → (min_open_time, max_open_time, count)`.
- `top_by_calmar(sweep_id, n) → Vec<SweepResultId>`.

### `overfit.rs`
- Refatorar `check_is_oos` para aceitar split explícito em vez de calcular dentro:
  ```rust
  pub fn check_is_oos_with_split(
      client, input,
      is_candles: &[Candle], oos_candles: &[Candle],
      cfg, test_uuid,
  ) -> Result<CheckResult>;
  ```
- Manter o `check_is_oos` atual como wrapper (pra CLI standalone que não passa split).

### Subprocess Python
- `std::process::Command::new("poetry").args(["run", "python", "-m", "db.fetch_klines", "--symbol", ..., "--days", ..., "--timeframe", ...])`.
- Working dir = repo root.
- Timeout: 10 min (Binance API rate limit faz 365d demorar ~3min).
- Stdout/stderr propagados via `tracing::info!`.

## CLI

```bash
backtest onboard \
   --symbol BTCUSDT --timeframe 5m \
   --days 365 \
   --strategy vwap_pullback,orb \
   --exit fixed_tp_sl,trailing_stop \
   --pos-size 0.10 \
   --released-by diego
```

Sem flags `--top-n`, `--allow-fail`, `--window-days`. Tudo derivado das decisões acima.

## Makefile

```
onboard: ## Full pipeline: fetch → sweep IS → gates → release (SYMBOL=x DAYS=365 [TIMEFRAME=5m])
ifndef SYMBOL
   @echo "Usage: make onboard SYMBOL=btcusdt DAYS=365 [TIMEFRAME=5m] [BY=diego]"
   @exit 1
else
   @./backtest/target/release/backtest onboard \
      --symbol $(SYMBOL) --timeframe $(or $(TIMEFRAME),5m) --days $(or $(DAYS),365) \
      $(if $(BY),--released-by $(BY),)
endif
```

## Pendências fora de escopo

- **Multi-symbol onboard**: rodar pra lista `[BTC, ETH, SOL]` em sequência. Pode ser shell loop ou outro target.
- **Re-onboarding agendado** (cron mensal): provavelmente faz sentido como `make onboard` no GitHub Actions ou similar.
- **Reotimização periódica**: `onboard` aposenta o preset anterior (já implementado em `release`). Logs em `presets` viram audit trail histórico.

## Confiança

Esse plano: ~90%. Os 10% restantes são edge cases que vão aparecer mexendo (ex.: subprocess timeout em Binance lenta, sweep que produz 0 candidatos rentáveis, range curto demais pra split 70/30 fazer sentido).
