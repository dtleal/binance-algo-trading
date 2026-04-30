//! Correlação Pearson entre returns mensais de presets.
//!
//! M0b da skill /regime-conditional. Usado pra decidir se composite
//! agrega valor (presets descorrelacionados) ou se é over-engineering
//! (presets correlacionados >0.8 — vendo o mesmo regime).

use anyhow::{anyhow, Result};
use chrono::{DateTime, NaiveDate, Utc};
use postgres::Client;
use std::collections::BTreeMap;

use crate::db;
use crate::detail::{self, DetailParams, Trade};
use crate::params_row::{
    strategy_params_from_row, exit_params_from_row,
    SELECT_STRATEGY_PARAMS_COLS, SELECT_EXIT_PARAMS_COLS,
};
use crate::types::*;

/// Output: matriz N×N (Pearson sobre returns mensais).
pub struct CorrelationOutput {
    pub preset_ids: Vec<i64>,
    pub labels:     Vec<String>,    // "PDHL ETH 15m" etc.
    pub matrix:     Vec<Vec<f64>>,
    pub n_months:   Vec<usize>,     // per-preset: meses com dados
}

pub fn correlate_presets(client: &mut Client, preset_ids: &[i64], pos_size: f64) -> Result<CorrelationOutput> {
    if preset_ids.len() < 2 {
        return Err(anyhow!("need ≥2 presets to correlate, got {}", preset_ids.len()));
    }

    // 1. Pra cada preset, replay → trades → monthly returns
    let mut series: Vec<BTreeMap<NaiveDate, f64>> = Vec::with_capacity(preset_ids.len());
    let mut labels: Vec<String> = Vec::with_capacity(preset_ids.len());
    let mut n_months_each: Vec<usize> = Vec::with_capacity(preset_ids.len());

    for &pid in preset_ids {
        let (label, monthly) = replay_preset_monthly(client, pid, pos_size)?;
        n_months_each.push(monthly.len());
        labels.push(label);
        series.push(monthly);
    }

    // 2. Alinha índice temporal (união de todos meses)
    let all_months: Vec<NaiveDate> = {
        let mut s: std::collections::BTreeSet<NaiveDate> = Default::default();
        for m in &series { s.extend(m.keys().copied()); }
        s.into_iter().collect()
    };

    if all_months.len() < 3 {
        return Err(anyhow!("insufficient months in common: {}", all_months.len()));
    }

    // 3. Constrói vetores alinhados (NaN quando preset não tem dado naquele mês)
    let aligned: Vec<Vec<f64>> = series.iter()
        .map(|m| all_months.iter().map(|d| m.get(d).copied().unwrap_or(f64::NAN)).collect())
        .collect();

    // 4. Pearson par-a-par sobre meses comuns (ignora NaN)
    let n = preset_ids.len();
    let mut matrix = vec![vec![f64::NAN; n]; n];
    for i in 0..n {
        matrix[i][i] = 1.0;
        for j in (i+1)..n {
            let c = pearson_pairwise(&aligned[i], &aligned[j]);
            matrix[i][j] = c;
            matrix[j][i] = c;
        }
    }

    Ok(CorrelationOutput {
        preset_ids: preset_ids.to_vec(),
        labels,
        matrix,
        n_months: n_months_each,
    })
}

/// Carrega preset, replay strategy no histórico, agrega trades por mês.
fn replay_preset_monthly(
    client: &mut Client,
    preset_id: i64,
    pos_size: f64,
) -> Result<(String, BTreeMap<NaiveDate, f64>)> {
    let sql = format!(
        "SELECT symbol, timeframe, strategy, exit_name, \
         {SELECT_STRATEGY_PARAMS_COLS}, {SELECT_EXIT_PARAMS_COLS} \
         FROM presets WHERE id = $1 AND status = 'active'"
    );
    let row = client.query_opt(sql.as_str(), &[&preset_id])?
        .ok_or_else(|| anyhow!("preset {preset_id} not found or not active"))?;

    let symbol_s: String = row.get("symbol");
    let tf_s:     String = row.get("timeframe");
    let strategy: String = row.get("strategy");
    let exit_name: Option<String> = row.get("exit_name");
    let exit_name = exit_name.ok_or_else(|| anyhow!("preset {preset_id} has NULL exit_name"))?;

    let symbol = Symbol::new(&symbol_s);
    let timeframe = Timeframe::parse(&tf_s)
        .ok_or_else(|| anyhow!("invalid timeframe '{tf_s}'"))?;

    let sp = strategy_params_from_row(&row);
    let ep = exit_params_from_row(&row);

    let label = format!("{strategy} {symbol_s} {tf_s} (id={preset_id})");

    // Carrega candles do período do preset (presets não tem period — usa max do sweep).
    // Pega da row do sweep_results (se referenciado).
    let (from, until) = period_for_preset(client, preset_id)?;
    let candles = db::load_candles(client, &symbol, timeframe, Some(from), Some(until))?;
    if candles.is_empty() {
        return Err(anyhow!("preset {preset_id}: no candles loaded for {symbol_s} {tf_s}"));
    }

    // Replay com params do preset → trades
    let trades = replay_with_typed_params(&candles, &strategy, &exit_name, &sp, &ep, pos_size)?;

    // Agrega por mês (year-month → soma de pnl_pct em %)
    use chrono::Datelike;
    let mut monthly: BTreeMap<NaiveDate, f64> = BTreeMap::new();
    for t in &trades {
        let d = t.exit_time.date_naive();
        let key = NaiveDate::from_ymd_opt(d.year(), d.month(), 1)
            .ok_or_else(|| anyhow!("invalid date {d}"))?;
        *monthly.entry(key).or_insert(0.0) += t.pnl_pct;    // já em %
    }

    Ok((label, monthly))
}

fn period_for_preset(client: &mut Client, preset_id: i64) -> Result<(DateTime<Utc>, DateTime<Utc>)> {
    let row = client.query_opt(
        "SELECT s.period_start, s.period_end
         FROM presets p JOIN sweep_results s ON p.sweep_result_id = s.id
         WHERE p.id = $1",
        &[&preset_id],
    )?.ok_or_else(|| anyhow!("preset {preset_id} has no linked sweep_result"))?;

    let ps: NaiveDate = row.get(0);
    let pe: NaiveDate = row.get(1);
    let from  = ps.and_hms_opt(0, 0, 0).unwrap().and_utc();
    let until = pe.and_hms_opt(23, 59, 59).unwrap().and_utc();
    Ok((from, until))
}

fn replay_with_typed_params(
    candles: &[Candle],
    strategy: &str,
    exit_name: &str,
    sp: &StrategyParamsRow,
    ep: &ExitParamsRow,
    pos_size: f64,
) -> Result<Vec<Trade>> {
    let days = db::group_by_day(candles);

    // Constrói DetailParams a partir de StrategyParamsRow
    let entry_window = match (sp.entry_window_start, sp.entry_window_end) {
        (Some(s), Some(e)) => Some((s as u16, e as u16)),
        _ => None,
    };
    let dp = DetailParams {
        min_bars:           sp.min_bars.map(|x| x as usize),
        confirm_bars:       sp.confirm_bars.map(|x| x as usize),
        vwap_prox:          sp.vwap_prox,
        vwap_window:        sp.vwap_window_days.map(|x| x as u32),
        ema_period:         sp.ema_period.map(|x| x as usize),
        max_trades_per_day: sp.max_trades_per_day.map(|x| x as usize),
        fast_period:        sp.fast_period.map(|x| x as usize),
        slow_period:        sp.slow_period.map(|x| x as usize),
        range_mins:         sp.range_mins.map(|x| x as u16),
        buffer_pct:         sp.buffer_pct,
        prox_pct:           sp.prox_pct,
        momentum_kind:      sp.kind.clone(),
        vol_filter:         sp.vol_filter,
        trend_filter:       sp.trend_filter,
        entry_window,
    };

    let entries = detail::build_entries(strategy, candles, &days, &dp)?;
    let exit_fn = build_exit_fn(exit_name, ep)?;

    let result = detail::run_detail(&entries, candles, exit_fn, pos_size);
    Ok(result.trades)
}

fn build_exit_fn(exit_name: &str, ep: &ExitParamsRow) -> Result<ExitFn> {
    match exit_name {
        "fixed_tp_sl" => Ok(ExitFn::FixedTpSl {
            tp_pct:       ep.tp_pct.ok_or_else(|| anyhow!("fixed_tp_sl needs tp_pct"))?,
            sl_pct:       ep.sl_pct.ok_or_else(|| anyhow!("fixed_tp_sl needs sl_pct"))?,
            max_hold_min: ep.max_hold_min.unwrap_or(0) as u16,
        }),
        "trailing_stop" => Ok(ExitFn::Trailing {
            sl_pct:       ep.sl_pct.ok_or_else(|| anyhow!("trailing_stop needs sl_pct"))?,
            be_r:         ep.be_r.unwrap_or(1.0),
            trail_step:   ep.trail_step.unwrap_or(0.25),
            tp_r:         ep.tp_r.unwrap_or(0.0),
            max_hold_min: ep.max_hold_min.unwrap_or(0) as u16,
        }),
        other => Err(anyhow!("exit_name '{other}' not supported in correlate (range monolithic)")),
    }
}

/// Pearson pairwise: ignora pares (i,j) com NaN em qualquer um.
fn pearson_pairwise(a: &[f64], b: &[f64]) -> f64 {
    let pairs: Vec<(f64, f64)> = a.iter().zip(b.iter())
        .filter(|(x, y)| !x.is_nan() && !y.is_nan())
        .map(|(x, y)| (*x, *y))
        .collect();
    let n = pairs.len() as f64;
    if pairs.len() < 3 { return f64::NAN; }
    let mean_a = pairs.iter().map(|(x, _)| x).sum::<f64>() / n;
    let mean_b = pairs.iter().map(|(_, y)| y).sum::<f64>() / n;
    let mut num = 0.0;
    let mut den_a = 0.0;
    let mut den_b = 0.0;
    for (x, y) in &pairs {
        let dx = x - mean_a;
        let dy = y - mean_b;
        num   += dx * dy;
        den_a += dx * dx;
        den_b += dy * dy;
    }
    if den_a < 1e-12 || den_b < 1e-12 { return f64::NAN; }
    num / (den_a.sqrt() * den_b.sqrt())
}
