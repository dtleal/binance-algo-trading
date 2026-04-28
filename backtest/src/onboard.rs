//! Orquestrador end-to-end: fetch → sweep IS → gates → release.
//!
//! Decisões fixadas:
//! - Top-1 por Calmar (return_pct / max_dd_pct), filtrado por ruído estatístico.
//! - Sweep roda só no IS (70%); IS/OOS gate é genuíno.
//! - Walkforward no range completo, window_days = max(30, days/6).
//! - Sem flag --allow-fail: gates falharam → onboard aborta.

use anyhow::{anyhow, bail, Context, Result};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use postgres::Client;
use std::process::{Command, Stdio};
use std::time::Instant;
use uuid::Uuid;

use crate::{
    db, overfit, release, sweep, walkforward,
    Ctx, Symbol, Timeframe,
    exit::{Exit, build_exit},
    strategy::{Strategy, build_strategy},
    overfit::{Outcome, OverfitInput},
};

const FETCH_TIMEOUT_SECS: u64 = 900;        // 15 min

pub struct OnboardInput {
    pub symbol:        Symbol,
    pub timeframe:     Timeframe,
    pub days:          u32,
    pub strategies:    Vec<String>,
    pub exits:         Vec<String>,
    pub pos_size:      f64,
    pub released_by:   Option<String>,
}

pub struct OnboardOutput {
    pub preset_id:       i64,
    pub sweep_id:        Uuid,
    pub overfit_test:    Uuid,
    pub walkforward_id:  Uuid,
    pub sweep_result_id: i64,
}

pub fn run_onboard(input: OnboardInput) -> Result<OnboardOutput> {
    if input.days < 30 {
        bail!("range too short ({}d) for IS/OOS split — need ≥30 days", input.days);
    }
    if input.days < 90 {
        tracing::warn!(days = input.days,
            "walkforward will likely be inconclusive (≤3 windows); proceeding");
    }

    let mut client = db::connect_from_env()?;

    // 1. Verifica klines no DB; baixa via subprocess Python se faltar
    ensure_klines_available(&mut client, &input.symbol, input.timeframe, input.days)?;

    // 1b. Se Range strategy estiver no list, garante MTF (próximo TF maior) disponível
    if input.strategies.iter().any(|s| s == "range") {
        if let Some(mtf_tf) = input.timeframe.next_mtf() {
            ensure_klines_available(&mut client, &input.symbol, mtf_tf, input.days)?;
        }
    }

    // 2. Define IS/OOS dates (70/30)
    let now = Utc::now();
    let total_from = now - Duration::days(input.days as i64);
    let split_at   = total_from + Duration::seconds(
        (now - total_from).num_seconds() * 70 / 100
    );
    let is_from_d  = total_from.date_naive();
    let split_d    = split_at.date_naive();
    let until_d    = now.date_naive();

    tracing::info!(symbol=%input.symbol, tf=%input.timeframe,
        is = format!("{is_from_d}..{split_d}"),
        oos = format!("{split_d}..{until_d}"),
        "onboarding pipeline start"
    );

    // 3. Carrega candles e split em IS/OOS
    let candles_full = db::load_candles(
        &mut client, &input.symbol, input.timeframe,
        Some(date_to_dt(is_from_d, false)),
        Some(date_to_dt(until_d, true)),
    )?;
    if candles_full.is_empty() {
        bail!("no candles loaded for {} {}", input.symbol, input.timeframe);
    }
    let split_idx = candles_full.iter().position(|c| c.open_time >= split_at)
        .unwrap_or(candles_full.len() * 70 / 100);
    let is_candles  = &candles_full[..split_idx];
    let oos_candles = &candles_full[split_idx..];

    tracing::info!(
        is_candles = is_candles.len(),
        oos_candles = oos_candles.len(),
        "split candles for IS/OOS"
    );

    // 4. Sweep só no IS
    let sweep_id = run_sweep_in_range(
        &mut client, &input,
        is_candles, is_from_d, split_d,
    )?;

    // 5. Top-1 por Calmar
    let top_ids = db::top_by_calmar(&mut client, sweep_id, 1)?;
    let top_id = top_ids.into_iter().next().ok_or_else(|| anyhow!(
        "sweep produced no candidates with return_pct>0 AND trades≥10 AND max_dd≥0.01"
    ))?;
    tracing::info!(top_id, "top candidate by Calmar");

    let (overfit_input, _, _) = overfit::load_input_from_sweep_result(
        &mut client, top_id, input.pos_size,
    )?;

    // 6. Gate 1: param sensitivity
    let test_uuid = Uuid::new_v4();
    let cfg_sens = overfit::ParamSensitivityConfig::default();
    let r = overfit::check_param_sensitivity(&mut client, &overfit_input, &cfg_sens, test_uuid)?;
    tracing::info!(outcome = r.outcome.as_str(), "gate: param_sensitivity");
    if r.outcome != Outcome::Pass {
        bail!("param_sensitivity {} — onboard aborted", r.outcome.as_str());
    }

    // 7. Gate 2: IS/OOS com split explícito (mesmo test_uuid agrupa)
    let cfg_isoos = overfit::IsOosConfig::default();
    let r = overfit::check_is_oos_with_split(
        &mut client, &overfit_input,
        is_candles, oos_candles, &cfg_isoos, test_uuid,
    )?;
    tracing::info!(outcome = r.outcome.as_str(), "gate: is_oos");
    if r.outcome != Outcome::Pass {
        bail!("is_oos {} — onboard aborted", r.outcome.as_str());
    }

    // 8. Gate 3: walkforward (range completo)
    let window_days = (input.days / 6).max(30);
    let wf_in = walkforward::WalkforwardInput {
        input: overfit_input.clone(),
        from:  is_from_d,
        until: until_d,
        window_days,
        step_days: window_days,
    };
    let wf_out = walkforward::run_walkforward(
        &mut client, &wf_in, &walkforward::WalkforwardConfig::default(),
    )?;
    tracing::info!(outcome = wf_out.outcome.as_str(), n_windows = wf_out.windows.len(),
        "gate: walkforward");
    if wf_out.outcome != Outcome::Pass {
        bail!("walkforward {} — onboard aborted", wf_out.outcome.as_str());
    }

    // 9. Release com gates linkados
    let rel_out = release::release(&mut client, release::ReleaseInput {
        sweep_result_id: top_id,
        released_by:     input.released_by,
        notes: Some(format!(
            "onboard: is={is_from_d}..{split_d} oos={split_d}..{until_d} window_days={window_days}"
        )),
        require_overfit_test_uuid: Some(test_uuid),
        require_walkforward_id:    Some(wf_out.walkforward_id),
    })?;
    tracing::info!(preset_id = rel_out.preset_id,
        "✅ released preset (all gates passed)");

    Ok(OnboardOutput {
        preset_id:       rel_out.preset_id,
        sweep_id,
        overfit_test:    test_uuid,
        walkforward_id:  wf_out.walkforward_id,
        sweep_result_id: top_id,
    })
}

// ── helpers ──────────────────────────────────────────────────────────────────

fn ensure_klines_available(
    client: &mut Client,
    symbol: &Symbol,
    tf: Timeframe,
    days: u32,
) -> Result<()> {
    let needed_from = Utc::now() - Duration::days(days as i64);
    let (min_t, max_t, count) = db::query_klines_coverage(client, symbol, tf)?;

    // Tolerância: aceita até 1 candle de gap no início. Klines começam em
    // boundaries do timeframe (5m, 15m, etc.) — se needed_from cair no meio
    // de um candle, o primeiro candle disponível é o boundary seguinte.
    let tf_min = tf.minutes() as i64;
    let coverage_ok = match (min_t, max_t) {
        (Some(mn), Some(mx)) => {
            mn <= needed_from + Duration::minutes(tf_min)
                && mx + Duration::days(1) >= Utc::now()
        }
        _ => false,
    };
    if coverage_ok {
        tracing::info!(symbol=%symbol, %tf, days, count, "klines coverage OK, skip fetch");
        return Ok(());
    }

    tracing::info!(symbol=%symbol, %tf, days, current=count,
        "klines coverage insufficient — fetching from Binance");
    run_python_fetch_klines(symbol, tf, days)
}

fn run_python_fetch_klines(symbol: &Symbol, tf: Timeframe, days: u32) -> Result<()> {
    let repo_root = std::env::var("REPO_ROOT")
        .ok()
        .or_else(|| {
            std::env::current_dir().ok().and_then(|p| {
                if p.join("pyproject.toml").exists() {
                    Some(p.to_string_lossy().into_owned())
                } else {
                    p.parent().map(|x| x.to_string_lossy().into_owned())
                }
            })
        })
        .unwrap_or_else(|| ".".into());

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
        .context("spawning fetch_klines (poetry not in PATH or repo root wrong)")?;

    let start = Instant::now();
    loop {
        match child.try_wait()? {
            Some(status) if status.success() => return Ok(()),
            Some(status) => bail!("fetch_klines exited with {status}"),
            None if start.elapsed().as_secs() > FETCH_TIMEOUT_SECS => {
                let _ = child.kill();
                bail!("fetch_klines timed out after {}s", FETCH_TIMEOUT_SECS);
            }
            None => std::thread::sleep(std::time::Duration::from_secs(2)),
        }
    }
}

fn run_sweep_in_range(
    client: &mut Client,
    input: &OnboardInput,
    is_candles: &[crate::Candle],
    period_start: NaiveDate,
    period_end: NaiveDate,
) -> Result<Uuid> {
    let strategies: Vec<Box<dyn Strategy>> = input.strategies.iter()
        .map(|n| build_strategy(n, ""))
        .collect::<Result<_>>()?;
    let exits: Vec<Box<dyn Exit>> = input.exits.iter()
        .map(|n| build_exit(n, ""))
        .collect::<Result<_>>()?;

    // MTF candles (próximo TF maior) — só se 'range' está nos strategies
    let mtf_candles = if input.strategies.iter().any(|s| s == "range") {
        match input.timeframe.next_mtf() {
            Some(mtf_tf) => {
                let from_dt  = is_candles.first().map(|c| c.open_time);
                let until_dt = is_candles.last().map(|c| c.open_time);
                let mtf = db::load_candles(client, &input.symbol, mtf_tf, from_dt, until_dt)?;
                if mtf.is_empty() {
                    anyhow::bail!("range MTF enabled but no {} candles for {} — fetch failed?", mtf_tf, input.symbol);
                }
                Some(mtf)
            }
            None => None,
        }
    } else { None };

    let days_idx = db::group_by_day(is_candles);
    let ctx = Ctx {
        symbol: &input.symbol,
        timeframe: input.timeframe,
        candles: is_candles,
        days: &days_idx,
        mtf_candles: mtf_candles.as_deref(),
    };
    let sweep_id = Uuid::new_v4();
    let results = sweep::run_sweep(&strategies, &exits, &ctx, input.pos_size, sweep_id);
    tracing::info!(n_results = results.len(), "IS sweep done");
    let n = db::write_sweep_results(client, sweep_id, Some(period_start), Some(period_end), &results)?;
    tracing::info!(rows_written = n, %sweep_id, "IS sweep_results persisted");
    Ok(sweep_id)
}

fn date_to_dt(d: NaiveDate, end_of_day: bool) -> DateTime<Utc> {
    let nt = if end_of_day { d.and_hms_opt(23, 59, 59).unwrap() } else { d.and_hms_opt(0, 0, 0).unwrap() };
    nt.and_utc()
}
