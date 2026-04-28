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

## Refinamentos de edge cases (resolveram fricções identificadas)

### 1. Subprocess Python (`fetch_klines`)

```rust
fn run_python_fetch_klines(symbol: &Symbol, tf: Timeframe, days: u32) -> Result<()> {
    use std::process::{Command, Stdio};

    // Resolve repo root: tenta CARGO_MANIFEST_DIR/.. (assume backtest/ é child)
    let repo_root = std::env::var("REPO_ROOT")
        .ok()
        .or_else(|| {
            std::env::current_dir().ok()
                .and_then(|p| p.parent().map(|x| x.to_string_lossy().to_string()))
        })
        .unwrap_or_else(|| ".".to_string());

    tracing::info!(symbol=%symbol, %tf, days, "fetching klines via subprocess");

    let mut child = Command::new("poetry")
        .args(&[
            "run", "python", "-m", "db.fetch_klines",
            "--symbol", symbol.as_str(),
            "--days", &days.to_string(),
            "--timeframe", tf.as_str(),
        ])
        .current_dir(&repo_root)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .context("spawning fetch_klines subprocess (poetry not in PATH?)")?;

    // Timeout: 15 min — Binance API + rate-limit pra 365d × 1m gasta ~3min,
    // 365d × 1m de múltiplos símbolos pode ir pra 12min.
    let start = std::time::Instant::now();
    loop {
        match child.try_wait()? {
            Some(status) if status.success() => return Ok(()),
            Some(status) => bail!("fetch_klines exited with status {status}"),
            None if start.elapsed() > Duration::from_secs(900) => {
                let _ = child.kill();
                bail!("fetch_klines timed out after 15min");
            }
            None => std::thread::sleep(Duration::from_secs(2)),
        }
    }
}
```

**Pré-condição**: `poetry` no PATH e `cwd` no repo root. Documentado no `--help`.

### 2. Filtro de candidatos pra evitar `max_dd=0` falso

Antes de calcular Calmar, **filtros de ruído estatístico**:
```sql
WHERE return_pct > 0
  AND trades >= 10           -- pelo menos 10 trades = sample mínimo
  AND max_dd_pct >= 0.01     -- ignora "DD zero" (geralmente sample size 1-2)
ORDER BY (return_pct / max_dd_pct) DESC
LIMIT 1
```
Se nenhum candidato passa nesse filtro → `onboard` aborta com mensagem específica:
"sweep produced no candidates with ≥10 trades and meaningful drawdown".

### 3. Sweep no IS-only com tracking explícito de range

```rust
// onboard chama internamente:
let sweep_id = Uuid::new_v4();
sweep::run_sweep(
    strategies, exits,
    &Ctx { ..., candles: &is_candles_only },
    pos_size, sweep_id,
);
db::write_sweep_results(client, sweep_id,
    Some(is_from_date), Some(is_until_date),  // period_start/end populados
    &results)?;
```
`period_start`/`period_end` em `sweep_results` viram a fonte de verdade do range
do sweep. `param_sensitivity` filtra `WHERE sweep_id = $1` (vizinhos só do mesmo
sweep) — garantindo que vizinhança seja real e contemporânea.

### 4. Comportamento explícito por range curto

| `--days` | Comportamento |
|---|---|
| < 30 | aborta: "range too short for IS/OOS split, need ≥30 days" |
| 30-89 | warning: "walkforward will likely be inconclusive (≤3 windows)" e segue |
| 90+ | normal |

`window_days = max(30, days / 6)`. Com `days=90` → 30d windows × 3 → inconclusive
provável. Com `days=180` → 30d × 6 → mínimo aceitável. Com `days=365` → 60d × 6.

### 5. Lock advisory por (symbol, strategy)

Postgres `pg_advisory_xact_lock` evita 2 onboards simultâneos pro mesmo combo:
```sql
SELECT pg_advisory_xact_lock(
    hashtext($1 || ':' || $2)::bigint   -- $1=symbol, $2=strategy
);
```
Chamado dentro de uma transação `BEGIN` no início do `onboard`. Se outro processo
já segura o lock, espera (não falha imediatamente). Liberado automaticamente no
`COMMIT`/`ROLLBACK`.

Decisão pragmática: **não usar lock**. Conflitos são raros (poucos onboards por
hora, manual ou cron 1×/mês). Se acontecer, `presets` UQ `(symbol, strategy)
WHERE status='active'` rejeita o segundo INSERT — não corrompe, só erra. Mais
simples, suficiente.

## Confiança

Esse plano refinado: ~95%. Os 5% restantes são surpresas só descobertas
implementando — ex.: `poetry` em diferentes versões com diferentes flags,
sweep que toma muito tempo num símbolo de baixa atividade, comportamento exato
do `pg_advisory_xact_lock` se eu mudar de ideia.
